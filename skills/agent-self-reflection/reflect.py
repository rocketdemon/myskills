#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""self-reflection 报告采集器（可移植版）。

只吃两件事：
  1) messages.jsonl —— 每行 {"message_id","session_id","role","text","ts"}
  2) sessions.jsonl —— 每行 {"session_id","title","started_at","session_source"}

不读数据库、不读系统日志、不联网、不依赖任何特定 agent 框架。
判据逻辑逐字来自上游实现（未改写），只替换了数据层。

未包含的检查（属特定运行环境的运维检查，本版不适用）：
  · 进程/服务崩溃分类与重启次数（需要 systemd journal）
  · 宿主会话守卫（需要 Windows 侧日志）
  · 窗口在线占比（需要 journal 保留段）
  · 消息投递失败（需要平台投递表）
  · 记忆缺口对账（需要记忆后端，见 --memory-file）

用法：
  python3 explore.py --messages messages.jsonl --sessions sessions.jsonl \
      [--since 2026-10-11T00:00:00] [--label 10-11起] [--memory-file MEMORY.md]
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta

# ── 角色名归一：不同 agent 的导出用不同叫法，这里统一成 user / assistant / tool ──
ROLE_MAP = {
    "user": "user", "human": "user", "prompt": "user",
    "assistant": "assistant", "ai": "assistant", "model": "assistant",
    "bot": "assistant", "gpt": "assistant",
    "tool": "tool", "function": "tool", "observation": "tool", "system": "system",
}


def norm_role(r):
    r = (r or "").strip().lower()
    return ROLE_MAP.get(r, r)


def norm_ts(v):
    """时间戳归一：epoch 秒 / epoch 毫秒 / ISO8601（含 +08:00 或 Z）。"""
    if isinstance(v, (int, float)):
        return float(v) / 1000.0 if v > 1e11 else float(v)
    s = str(v).strip()
    try:
        f = float(s)
        return f / 1000.0 if f > 1e11 else f
    except ValueError:
        pass
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return dt.timestamp()


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                sys.exit(f"❌ {path}:{ln} 不是合法 JSON：{e}")
    return rows


ap = argparse.ArgumentParser()
ap.add_argument("--messages", required=True, help="messages.jsonl")
ap.add_argument("--sessions", required=True, help="sessions.jsonl")
ap.add_argument("--since", default=None, help="窗口起点，ISO8601 或 epoch；缺省=最近 24 小时")
ap.add_argument("--label", default="", help="报告标题里显示的窗口说明")
ap.add_argument("--memory-file", default=None,
                help="可选：记忆文件（如 MEMORY.md）。不提供则该检查标注「未启用」")
ap.add_argument("--state-file", default=None, help="扫描连续性锚点文件（缺省=临时文件）")
args = ap.parse_args()

raw_msgs = load_jsonl(args.messages)
raw_sess = load_jsonl(args.sessions)
SINCE_TS = norm_ts(args.since) if args.since else time.time() - 86400

# ── 建立临时库：只含通用契约字段，让后续判据零改动可跑 ──
TMPDB = os.path.join(tempfile.mkdtemp(prefix="reflect-"), "session.db")
conn = sqlite3.connect(TMPDB)
conn.row_factory = sqlite3.Row
conn.executescript("""
CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL, source TEXT);
CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
                       content TEXT, timestamp REAL);
""")
_sess_seen = set()
for s in raw_sess:
    sid = s.get("session_id") or s.get("id")
    if not sid or sid in _sess_seen:
        continue
    _sess_seen.add(sid)
    st = s.get("started_at")
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                 (sid, s.get("title") or "", norm_ts(st) if st else 0.0,
                  (s.get("session_source") or s.get("source") or "")))
_roles_seen = set()
_ts_kinds = set()
for m in raw_msgs:
    sid = m.get("session_id")
    role = norm_role(m.get("role"))
    _roles_seen.add(m.get("role"))
    _ts_kinds.add(type(m.get("ts")).__name__)
    conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                 (sid, role, m.get("text") or m.get("content") or "", norm_ts(m.get("ts"))))
# 会话记录缺失时补一行，避免 LEFT JOIN 丢消息
conn.execute("INSERT OR IGNORE INTO sessions (id, title, started_at, source) "
             "SELECT DISTINCT session_id, '', 0.0, '' FROM messages "
             "WHERE session_id NOT IN (SELECT id FROM sessions)")
conn.commit()
conn.execute("CREATE INDEX idx_m ON messages(session_id, timestamp)")
STATE_DB = TMPDB
# 扫描连续性锚点等状态文件的落点（原脚本用 REFLECT_HOME，此处换成通用可配置目录）
REFLECT_HOME = os.environ.get("REFLECT_HOME") or os.path.join(
    tempfile.gettempdir(), "reflect-state")
os.makedirs(REFLECT_HOME, exist_ok=True)

tz = timezone(timedelta(hours=8), "CST")
WINDOW_OFFLINE = False      # 本版无 journal 在线占比 ⇒ 永不触发该降级
WINDOW_UNJUDGED = False
WINDOW_RATIO = None

print("=== 反省数据采集（可移植版）===")
print(f"时间: {datetime.now(tz):%Y-%m-%d %H:%M} CST"
      + (f" · 窗口: {args.label}" if args.label else ""))
print(f"数据源: {os.path.basename(args.messages)} {len(raw_msgs)} 条 / "
      f"{os.path.basename(args.sessions)} {len(raw_sess)} 条")
print(f"⚪ 角色名归一：原始值 {sorted(x for x in _roles_seen if x)} → 映射后 "
      f"user {conn.execute('SELECT count(*) FROM messages WHERE role=?', ('user',)).fetchone()[0]} 条")
print(f"⚪ 时间戳归一：原始类型 {sorted(_ts_kinds)}")
print("⚪ 环境专属检查未启用（本版不适用）：崩溃分类 / 宿主守卫 / 在线占比 / 投递失败")

# ========================================================================
# 以下为通用工具函数（逐字来自上游，未改写）
# ========================================================================
def rating_line(total, window_offline, window_unjudged, window_ratio=None,
                min_online_h=6.0):
    """结尾评级行（抽成纯函数便于双向单测 —— 见 test-daily-reflection-rating.py）。

    2026-09-26 修：旧版只拦 `WINDOW_OFFLINE`，而「窗口覆盖读不到（❓）」走 else 分支
    照打 🟢 —— 输出上「没数据」与「今天很平静」同形（检测器盲区族 09-08 / 09-13 /
    09-16 / 09-19 之后的第 6 次；当日实例：开机后 journalctl --list-boots 读取失败
    → ❓ 无法判定 + 🟢 平静）。规则：**读不到就不评级**，⚪ 不是「平静」的同义词。

    双向都钉住（漏报/误报在此处不同形）：
      * 覆盖不足/读不到 + 0 信号 → ⚪（不许 🟢）；
      * 覆盖不足 + **有真信号** → 照报 🟡/🔴（信号本身是证据），覆盖问题只作旁注 ——
        否则「修掉假平静」会变成「把真信号也吃掉」的漏报。
    单元测试：test-daily-reflection-rating.py。
    """
    caveat = ""
    if window_offline:
        caveat = (f"（⚠️ 窗口覆盖：24h 内 distro 在线仅 {(window_ratio or 0) * 100:.1f}%，"
                  f"低于 {min_online_h:g}h 下限 ⇒ 上面的计数只是窗口内那一小段的样本）")
    elif window_unjudged:
        caveat = "（⚠️ 窗口覆盖读不到 ⇒ 上面的计数只是「读到的部分」，别当全天状况）"
    if total > 0:
        head = "🔴 需关注" if total >= 3 else "🟡 有信号"
        return f"评级: {head}{caveat}"
    if window_offline:
        return (f"评级: ⚪ 数据不足（24h 窗口内 distro 在线仅 {(window_ratio or 0) * 100:.1f}%，"
                f"低于 {min_online_h:g}h 下限）"
                " —— 「无信号」是没数据，不是状态干净；本次不评级")
    if window_unjudged:
        return ("评级: ⚪ 无法判定（窗口覆盖读不到：journal boot 区间自检失败 →"
                "本次「无信号」无背书；先看上面 SECTION 0c 的失败原因，"
                "别把它读成「今天很平静」）")
    return "评级: 🟢 平静"


def scan_window_hole(prev_end, since_ts, tol=60.0):
    """上一轮扫描终点 → 本轮扫描起点之间，是否存在「任何一轮都不扫」的区间。

    本脚本窗口恒为 [now-86400, now]，而漏跑后的 catch-up 轮次把窗口锚在**执行时刻**
    （不是漏跑那次的 scheduled 时刻）⇒ 两轮相隔 > 24h 时中间那段落在所有窗口之外，
    任何一轮都不会扫到（2026-10-08 全量核对 08-10 起 61 轮窗口并集：并集之外
    71 条唯一真实用户发言 / raw 77 行，含 08-29「脚本报错了，你能看到吗」这类核验信号）。

    返回 (kind, hole_start, hole_end)：
      * ('none',    None, None) 两轮衔接（差值在 tol 容差内，覆盖正常）
      * ('found',   a,    b)    存在缺口 [a, b)
      * ('unknown', None, None) 无上一轮记录 / 值非数字 ⇒ **不许读作「连续」**
    """
    try:
        prev_end = float(prev_end)
        since_ts = float(since_ts)
    except (TypeError, ValueError):
        return ("unknown", None, None)
    if prev_end >= since_ts - tol:
        return ("none", None, None)
    return ("found", prev_end, since_ts)



# ========================================================================
# 会话选取（逐字来自上游）
# ========================================================================
# ⚠️ 2026-09-26 修（**会话级盲区**，实测当日扫描覆盖 1/16 = 6%）：旧版按
#   `sessions.started_at > SINCE_TS` 选会话 ⇒ **窗口之前就已开始、窗口内仍在用**的长会话整条不被扫描
#   （实测：09-24 起的主会话在窗口内有 329 条消息 / 15 条 user 发言，全部漏掉；而 5 个检测段落
#   ——纠正/核验追问/虚假声称/手动介入/重复失败——都只遍历 sessions）。跨窗口长会话是常态
#   （一个会话可用数天）⇒ 旧口径等于「只要用户不新开会话，就永远报 0 信号」。
#   正确口径 = **窗口内有消息的会话**（started_at 无关）；按最后发言时间排序，别被 cron 会话挤掉。
#   ⚠️ 2026-09-30 再修（**主体口径盲区**，同日实测）：`sessions` 里混着 `source='subagent'` 的
#   **delegate_task 子会话** —— 它们的 `role='user'` 消息是 **agent 自己写的委派提示**（实测当日
#   10 个子会话各 1 条，占「用户发言」分母 38 条的 26%）。这既是假纠正的词源风险（提示里出现
#   「不要弄错」之类即假命中），也让覆盖率分母虚高、会话列表看起来像重复会话。
#   ⇒ 口径收紧为：`s.source <> 'subagent'`；被排除的会话与条数**必须留痕**（过滤不静默）。
SESSION_SCAN_LIMIT = 20
cur = conn.execute("""
    SELECT s.id, s.title, s.started_at,
           (SELECT max(m.timestamp) FROM messages m WHERE m.session_id = s.id) AS last_msg,
           (SELECT count(*) FROM messages m
             WHERE m.session_id = s.id AND m.timestamp > ?) AS n_in_window
    FROM sessions s
    WHERE s.source <> 'subagent'
      AND EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id AND m.timestamp > ?)
    ORDER BY last_msg DESC
    LIMIT ?
""", (SINCE_TS, SINCE_TS, SESSION_SCAN_LIMIT))
sessions = cur.fetchall()

# 窗口内确有消息、却因 LIMIT 被挤掉的会话（留痕：不许静默截断 ⇒ 缺了谁的 1 段样本）
# 分母口径与上面的列表一致：LEFT JOIN 让「sessions 行缺失」的消息仍计入 ⇒ 那类会走
# _sess_truncated/SCAN_PARTIAL 被标出来（fail-closed），而不是被静默丢掉。
_n_sess_total = conn.execute(
    "SELECT count(*) FROM (SELECT DISTINCT m.session_id FROM messages m "
    "LEFT JOIN sessions s ON s.id = m.session_id "
    "WHERE m.timestamp > ? AND COALESCE(s.source, '') <> 'subagent')",
    (SINCE_TS,)).fetchone()[0]
_sess_truncated = max(0, _n_sess_total - len(sessions))

# 被排除的子任务会话留痕（09-30）：不打印就等于「今天没有子任务会话」与「过滤生效」同形。
_sub_excluded = conn.execute("""
    SELECT s.id,
           (SELECT count(*) FROM messages m
             WHERE m.session_id = s.id AND m.timestamp > ? AND m.role = 'user') AS n_user
    FROM sessions s
    WHERE s.source = 'subagent'
      AND EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id AND m.timestamp > ?)
""", (SINCE_TS, SINCE_TS)).fetchall()
_sub_excluded_user = sum(r["n_user"] or 0 for r in _sub_excluded)
_sub_excluded_ids = {r["id"] for r in _sub_excluded}

print(f"\n--- 窗口内有消息的会话：{len(sessions)} 个"
      f"（口径=窗口内有消息，非「窗口内新开」；按最后发言时间排序）---")
if _sess_truncated:
    print(f"  ⚠️ 另有 {_sess_truncated} 个会话超出扫描上限 {SESSION_SCAN_LIMIT} 未扫描"
          f" ⇒ 下面的计数**只是部分样本**，不得读作全天状况")
if _sub_excluded:
    print(f"  ⚪ 已排除 {len(_sub_excluded)} 个 subagent 子会话（delegate_task 子任务；"
          f"其 role='user' 消息 = agent 自己写的委派提示，共 {_sub_excluded_user} 条，"
          f"**不计入「用户发言」**）")
title_hints = ["问题", "还是", "太简", "不对", "错了", "不是之前", "重写", "合并"]
for s in sessions:
    ts = datetime.fromtimestamp(s["started_at"], tz).strftime("%m-%d %H:%M")
    title = s["title"] or "无标题"
    sid = s["id"][:16] if s["id"] else "???"
    _cross = ""
    if s["started_at"] < SINCE_TS:
        _cross = "（跨窗口长会话）"
    flag = ""
    if "每日自我反省" not in title:
        for th in title_hints:
            if th in title:
                flag = f" ⚠️标题含「{th}」"
                break
    print(f"  [{ts}]{_cross} {title}  ({sid}) 窗口内消息 {s['n_in_window']}{flag}")

if not sessions and (WINDOW_OFFLINE or WINDOW_UNJUDGED):
    _cov_state = (f"distro 仅在线 {(WINDOW_RATIO or 0) * 100:.1f}%"
                  if WINDOW_OFFLINE else "窗口覆盖无法判定")
    print(f"  ℹ️ 窗口内 0 会话 + {_cov_state} → 这个 0 是"
          "「没开机 / 没数据」，不是「今天很平静」（见上方窗口覆盖段）")

# ============================================================

# ========================================================================
# SECTION 1 / 1b / 1c / 1d / 2 / 3 / 4（逐字来自上游）
# ========================================================================
# SECTION 1: Correction signals (v2 logic, enhanced keywords)
# ============================================================
keywords = [
    # Direct negation
    "不对", "错了", "说错了", "搞错了", "弄错了", "搞错", "弄错",
    "你说得不对", "你没理解", "你没听懂", "你没明白",
    # Command correction
    "我让你", "我不是让你", "不是让你",
    # Quality criticism
    "怎么还", "怎么又", "又犯", "老毛病",
    "光说不做", "没实际", "只是给了",
    # Warning signals
    "你想清楚", "不要跳", "不要编", "不要想当然",
    # Meta
    "误报", "骗自己", "根本没", "实际上没",
    "你确定吗", "真的吗",
    # New v3 additions
    "问题蛮大的", "问题很大", "你这样问题",
    "不是跟你说", "我说的是",
    # Quality negation (2026-08-15: 漏报"效果还是不好"+编号缺陷清单)
    "效果不好", "还是不好", "还是不行", "缺少",
    # 2026-08-18 漏报："有几个问题"+"太简略"+"不绝对"+编号缺陷（第4次复发）
    "几个问题", "太简略", "太简单", "太粗糙", "不绝对",
]

skip_patterns = [
    "[IMPORTANT:", "The user has invoked", "skill content is loaded",
    "feishu-inventory-management", "每日自我反省", "反省数据采集",
    "MCP server", "MCP 薄封装",
]

# ── harness 注入文本判定（2026-09-21 新增，假纠正根因）────────────────────
# role='user' 这一列里混着**平台/框架注入**的文本，不是用户本人说的话：
#   ① 上下文压缩摘要 `[CONTEXT COMPACTION …]`（可达 22KB）
#   ② `[PRIOR CONTEXT …]` 包装 / cron 任务提示 `[IMPORTANT: You are running as …]`
#   ③ 后台进程回执 `[IMPORTANT: Background process … exited]`
#   ④ 附件转写 `[The user sent a text document: …]`
# 它们会命中纠正词表 ⇒ **假纠正**。09-21 实例：18:41 那条 22,084 字的压缩摘要里
# 出现「结论：文档没错，助手"不准确"判断错了」与「发现没有 distro 在跑」，
# 被读成 1 次 🔴 纠正 + 2 次虚假声称，把当日评级从 🟡 推成 🔴。
# 判据用**前缀**（注入文本必然在开头），不用子串——用户复述这些字样时不该被吞掉。
HARNESS_PREFIXES = (
    "[CONTEXT COMPACTION",
    "[PRIOR CONTEXT",
    "[END OF PRIOR CONTEXT",
    "[IMPORTANT:",
    "[The user sent a text document",
    # 2026-09-26：附件形状漏了一种 —— 实测 20:16「[The user sent a document: '某项目.docx'. It is
    #   saved at: …」不被判 harness ⇒ 被当成一条用户发言计入覆盖率分母（17 里混了 1 条注入）。
    #   前缀表要按**平台实际出现过的形状**逐个收口，不能只收当时见过的那几个。
    "[The user sent a document",
    "[The user sent an image",
    "[The user sent a voice",
    # 2026-09-22：后台 subagent 完成回执也是注入的 role='user' 文本（实测 09-22 两条
    # 2,900 字，内容是 subagent 汇报，会提到「问题/失败/错误」⇒ 潜在假纠正）。
    # 上面注释里的类别③「后台进程回执」当时只覆盖了 [IMPORTANT: Background process …]，
    # 漏了这条形状 —— 补上。
    # 2026-10-12：同族**第 3 种形状**漏了 —— fan-out 批量回执
    #   `[ASYNC DELEGATION BATCH COMPLETE — deleg_x]`（实测 18,378 字，正文=子代理汇报，
    #   必然含「不对/我自己」等词）。全量扫库实测 6 条此类回执全部漏过旧前缀，
    #   10-11 19:46 那条被计成 1 次 🔴 纠正 + 1 次 🟠 手动介入（把当日评级推成 🟡）。
    #   修法不是再补一个完整字面量，而是**收窄到族名**：覆盖 COMPLETE / BATCH COMPLETE /
    #   以后新增的变体（PARTIAL 等）。前缀仍锚在开头 ⇒ 不会吞掉用户复述。
    "[ASYNC DELEGATION",
    # 2026-10-12：断流续写指令 `[System: The previous response was cut off by a network
    #   error mid-stream. Continue exactly where you left off. …]`（实测 id=38818，176 字）。
    #   同族第 4 种形状，同样混在 role='user' 列里。
    "[System:",
    "每日自我反省",
    "反省数据采集",
)

# ── 已知「用户侧包装」形状（2026-10-12 新增，全表扫描时发现）─────────────────────
# 这些形状**不判 harness、保留为真人发言** —— 因为它们带的是**用户自己打的字**：
#   ① `[System note: Your previous turn was interrupted …]\n\n<用户打的字>`
#      34 条（05-22 ~ 06-23），余文实测如「approve」「按安全的方式执行」「说错了，是5月29日」。
#   ② `[Replying to: "<被引用的消息>"]\n\n<用户打的字>`
#      2 条（05-18，飞书引用回复），余文实测「你是谁」「你用的什么模型」。
#   ③ `[OUT-OF-BAND USER MESSAGE — a direct message from the user …]\n<Gateway 元数据 JSON>\n\n<用户打的字>`
#      2026-10-15 实测 id=48899（平台契约写死：这类文本**有用户同等授权**，不是注入）：
#      余文「实际叫什么名字来着」。它此前不在任何白名单 ⇒ 「未收录注入形状」警告天天报它
#      （对已知形状狼来了，会让人忽略这行提示）；登记后该提示只在真出现新形状时响。
#      ⚠️ 只登记为「用户侧包装」，**不**收进 HARNESS_PREFIXES —— 它带的是用户自己打的字。
# **量测再决策**（45 词词表过 36 行）：1 行的**余文**命中词表 —— id=5600「说错了，是5月29日」
#   就是真纠错 ⇒ 整行判 harness 会吞掉真实信号，所以**故意不收进 HARNESS_PREFIXES**。
# 它们在这里的唯一用途：把「未收录形状」提示的**白名单**打开，别对已知形状狼来了。
# ⚠️ 已知残漏（未修，诚实标注）：②的**被引用正文**参与关键词匹配 —— 若引用的 bot 消息里含
#   「不对/错了」等词，仍会假纠正。要精确修得在 5 个 SECTION 的关键词匹配前剥掉引用段
#   （改一处不生效，须同时扫 5 处计数点），本轮未做。
KNOWN_USER_WRAPPER_PREFIXES = ("[System note:", "[Replying to:", "[OUT-OF-BAND USER MESSAGE")


def is_harness_text(text):
    """True = 平台/框架注入的 role='user' 文本，不是用户本人发言。

    过滤必须与「漏报」可区分：调用方应打印 ⚪ 留痕（见各 SECTION），
    否则「过滤成功」与「今天真没信号」在输出上同形。
    """
    t = (text or "").lstrip()
    return any(t.startswith(p) for p in HARNESS_PREFIXES)


def unlisted_injection_shape(text):
    """疑似**未收录**的注入形状（2026-10-12 新增，只留痕、不过滤）。

    为什么需要它：HARNESS_PREFIXES 是「按平台出现过的形状逐个收口」，新形状出现时
    **不会报错**，只会静默被当成一条用户发言 —— 直接变成假纠正。
    10-12 实测：`[ASYNC DELEGATION BATCH COMPLETE …]`（一次 fan-out 的批量回执，
    最长 18,378 字，正文全是子代理汇报）共 6 条，全都不在表里 ⇒ 其中 10-11 19:46 那条
    被计成 1 次 🔴 纠正 + 1 次 🟠 手动介入（词都出在子代理的报告正文里：
    「不对被技术淘汰者写深度悲悯」「我自己露馅两处」）。
    ⇒ 与其等下一次再补一个字面量，不如让「未收录」在报告里**可见**：
    凡 role='user'、以 '[' 开头、又没被判 harness 的正文都列出来（数字归一化后分组）。
    返回形状头（None = 正常的真人发言 / 已知的用户侧包装）。
    """
    t = (text or "").lstrip()
    if not t.startswith("[") or is_harness_text(t):
        return None
    # 已知「用户侧包装」（带用户自己打的字）不算未收录 —— 对已知形状狼来了会让人忽略这行提示
    if any(t.startswith(p) for p in KNOWN_USER_WRAPPER_PREFIXES):
        return None
    return re.sub(r"\d", "#", t[:60].replace("\n", " "))


def dedup_key(session_id, timestamp, content):
    """重复行判据（2026-09-22）：同一句发言在库里的同一性 = session + 时间戳 + 正文。

    交互长会话的历史会被**重插**：实测 09-22 会话 20260922_104525_42c26764 里，
    同一句 user 发言以 3 个不同 id、**完全相同 timestamp** 落库（全会话 15 次时间戳倒退、
    27.6 万字重复内容；同会话 user 行 30 / 去重后 14）。SECTION 1/1b/2 都是「逐行扫、
    命中即 +1」，不去重则一次发言被计成 N 次纠正 / N 次虚假声称 —— 计数被放大。
    真实用户不可能在同一毫秒发两条完全相同的消息，故 (session, ts, content) 全同 = 重复行。
    留痕规则：去重必须打印计数（各 SECTION 的 ⚪ 行），否则「去重成功」与「漏报」同形。
    """
    return (session_id, round(float(timestamp or 0), 3), (content or "").strip())


# ── 「报错了」= 报告故障 vs 追责（2026-09-24 新增，误报侧第 3 次）────────────
# 误报实例（09-24 21:31）：
#   「我运行 hermes update --check 好像报错了，你能看到报什么错吗」
#   ⇒ 命中词表「错了」被计为 1 次 🔴（severity=89），把当日评级从 🟢 推成 🟡。
#   实际语义 = **报告某条命令失败 + 请求协查**，不是否定 agent。
#   事后物证：命令本身没写错（`--check` 确在 `subcommands/update.py` 参数表内，
#   升级前后两版都在）；报错文本来源是到 GitHub 的间歇性网络故障
#   （`GnuTLS recv error (-110)`）→ 命中源码里「✗ Network error」分支 = **预期输出**。
# 判据（三条全中才降级，任一不中 ⇒ 按真纠正计）：
#   ① 命中的关键词就是「错了」（命中「不对」等其它词 ⇒ 不适用本判据）；
#   ② 该「错了」出自**故障报告套语**（报错/出错/抛错 + 了，且前面不是「你」）；
#   ③ 全文无追责标记（你错了/你说错/你搞错/你怎么/怎么又/怎么还/我不是让/不是让你/…）。
#   ③ 是防「把真追责当故障报告」的关键：真追责几乎都点人（你…）或带「又/怎么还」。
_REPORT_ERR_IDIOM_RE = r"(?<!你)(?:报|出|抛)错了"
_REPORT_BLAME_MARKS = ("你错了", "你说错", "你搞错", "你弄错", "你怎么", "怎么又", "怎么还",
                       "我不是让", "不是让你", "我让你", "跟你说", "我说的是", "又犯")


def _correction_report_context(content, matched_kw):
    """命中「错了」但该字样出自**故障报告套语**的，返回原因串；否则 None。

    降级后**不计入 total 评级**，原文仍以 ⚪ 桶打印在报告里（留给复核 agent 判断）——
    理由同 `_correction_spec_context`：不静默过滤，避免把误报修成漏报盲区。

    ⚠️ 已知残漏（诚实标注）：中间夹字的写法（「报了 5 个错」「报了个错」）与
       英文/其它语言措辞不覆盖，仍会被计为 🔴 —— 复核时先读原文。
    """
    if matched_kw != "错了":
        return None
    if not re.search(_REPORT_ERR_IDIOM_RE, content):
        return None
    if any(m in content for m in _REPORT_BLAME_MARKS):
        return None
    return "报告故障（命令/脚本报错，非追责）"


# ── 「命令纠正」词族的**规格 vs 反驳**判别（2026-09-23 新增，误报侧）──────────
# 误报实例（09-23 18:11）：
#   「给你个长任务，**不是让你**直接用这个网站的复制功能，**而是**读取『某项目』项目里的所有信息，
#     将信息填入（包括上传图片）『某项目RE』的每个对应位置…」
#   ⇒ 命中词表「不是让你」被计成 1 次 🔴 纠正，把当日评级从「🟢 平静」推成「🟡 有信号」。
#   实际语义 = **定义任务边界**（否定式对比定义），不是否定已发生的动作 —— 该动作 agent 压根没做过
#   （平台本无「复制功能」，「无复制功能」正是 agent 前一轮自己核实并报告的结论）。
# 真信号样本（必须保留，不能被本判据吃掉）：
#   06-02「什么意思，**我不是让你**关闭宿主机吗？」—— 反问句 + 「什么意思」= 在追责**过去**的动作。
_CMD_NEG_KEYWORDS = ("不是让你", "我不是让你")
_SPEC_REBUTTAL_MARKS = ("？", "?", "吗", "什么意思", "你怎么", "为什么",
                        "我不是说", "不是跟你说", "我说的是")


def _correction_spec_context(content):
    """命中「命令纠正」词族但语义是**任务规格/口径定义**的，返回原因串；否则 None。

    三条判据全中才降级（任一不中 ⇒ 按真纠正计）：
      ① 命中否定式命令词（「不是让你」/「我不是让你」；正向「我让你」不适用本判据）；
      ② 有对比结构「而是」（把被否定的做法放进一个完整的任务定义里）；
      ③ 无反问/追责标记（？ ? 吗 什么意思 你怎么 为什么 我不是说 不是跟你说 我说的是）——
         ③ 是防「把真反驳当规格」的关键：真反驳几乎都是反问句或直接指向过去的动作。
    降级后**不计入 total 评级**，但原文仍打印在报告里（留给复核 agent 判断）——
    同 09-14 手动介入「语境提示」的做法，避免把误报修成漏报盲区。

    ⚠️ 已知残漏（诚实标注，别当成零风险）：陈述式追责且不带反问标记的写法会被误降级，
       例「我不是让你删这个表，而是那张表」。它仍会出现在 ⚪ 桶里（含原文），复核时须逐条看过。
    """
    if not any(k in content for k in _CMD_NEG_KEYWORDS):
        return None
    if "而是" not in content:
        return None
    if any(m in content for m in _SPEC_REBUTTAL_MARKS):
        return None
    return "任务规格/口径定义（否定式对比，无反问或追责标记）"


print(f"\n--- 用户纠正信号 ---")
found_corrections = 0
_harness_skipped_corr = []   # harness 注入文本的留痕（过滤不静默，09-21）
_dup_corr_rows = []          # 重复行留痕（去重不静默，09-22）
_corr_spec_rows = []         # 任务规格词族留痕（降级不静默，09-23）
_corr_report_rows = []       # 故障报告套语留痕（降级不静默，09-24）

for sess in sessions:
    sid = sess["id"]
    if not sid:
        continue

    cur = conn.execute("""
        SELECT id, role, content, timestamp
        FROM messages
        WHERE session_id = ?
          AND role = 'user'
          AND timestamp > ?
        ORDER BY timestamp ASC
    """, (sid, SINCE_TS))

    user_msgs = cur.fetchall()
    _seen_corr_rows = set()   # 本会话已计数的发言（2026-09-22 去重）

    for msg in user_msgs:
        content = msg["content"] or ""

        # harness 注入文本（压缩摘要/cron 提示/后台回执/附件转写）不是用户发言（09-21）
        if is_harness_text(content):
            _harness_skipped_corr.append(
                (datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M"), content)
            )
            continue

        skip = False
        for sp in skip_patterns:
            if sp in content:
                skip = True
                break
        if skip:
            continue

        matched_kw = None
        for kw in keywords:
            if kw in content:
                matched_kw = kw
                break

        if not matched_kw:
            continue

        # 重复行去重（2026-09-22）：历史重插 ⇒ 同一句发言会被命中 N 次、纠正数被放大
        # （实测 09-22 同一句「那批已完成段落移到归档文件了吗？」在库里 3 份）。
        # 判据见 dedup_key；过滤留痕在下方 ⚪ 行打印，不静默。
        _dk = dedup_key(sid, msg["timestamp"], content)
        if _dk in _seen_corr_rows:
            _dup_corr_rows.append((datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M"),
                                   content[:60].replace("\n", " ")))
            continue
        _seen_corr_rows.add(_dk)

        # 任务规格/口径定义判别（09-23）：命中否定式命令词 ≠ 被纠正
        # （实例：「不是让你用复制功能，而是读取…填入」= 定义任务，不是追责过去的动作）
        _spec = _correction_spec_context(content)
        if _spec:
            _corr_spec_rows.append(
                (datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M"), _spec,
                 content[:80].replace("\n", " "))
            )
            continue

        # 故障报告 vs 追责（09-24）：「报错了」是报告命令失败 + 请求协查，不是否定 agent
        _rep = _correction_report_context(content, matched_kw)
        if _rep:
            _corr_report_rows.append(
                (datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M"), _rep,
                 content[:80].replace("\n", " "))
            )
            continue

        cur2 = conn.execute("""
            SELECT role, content, timestamp
            FROM messages
            WHERE session_id = ?
              AND timestamp < ?
            ORDER BY timestamp DESC
            LIMIT 3
        """, (sid, msg["timestamp"]))
        before = list(reversed(cur2.fetchall()))

        cur3 = conn.execute("""
            SELECT role, content, timestamp
            FROM messages
            WHERE session_id = ?
              AND timestamp > ?
            ORDER BY timestamp ASC
            LIMIT 3
        """, (sid, msg["timestamp"]))
        after = cur3.fetchall()

        ts = datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M")
        excerpt = content[:300].replace("\n", " ")

        severity = len(content)
        if matched_kw in ("不对", "错了", "说错了", "搞错", "你没理解"):
            severity += 50
        if "？？" in content:
            severity += 100

        print(f"\n  [{ts}] 🔴 命中: \"{matched_kw}\" (severity={severity})")
        print(f"  用户: {excerpt}")

        for b in before:
            if b["role"] == "assistant":
                b_excerpt = (b["content"] or "")[:200].replace("\n", " ")
                if b_excerpt.strip():
                    print(f"  ← 之前助手说: {b_excerpt}")
                break

        for a in after:
            if a["role"] == "assistant":
                a_excerpt = (a["content"] or "")[:200].replace("\n", " ")
                if a_excerpt.strip():
                    print(f"  → 之后助手回: {a_excerpt}")
                break

        found_corrections += 1

if found_corrections == 0:
    print("  今日无纠正信号")

# 过滤留痕（09-21）：harness 注入文本被跳过 —— 不打印就等于「过滤成功」与「今日真无信号」同形
if _harness_skipped_corr:
    print(f"  ⚪ 另有 {len(_harness_skipped_corr)} 条 role='user' 的 **harness 注入文本**已排除"
          f"（非用户发言；判定=前缀匹配，见 is_harness_text）：")
    for _ts, _c in _harness_skipped_corr[:3]:
        print(f"     [{_ts}] {_c[:70].replace(chr(10), ' ')} …（共 {len(_c)} 字）")

# 去重留痕（09-22）：重复行不再计数 —— 不打印就等于「去重成功」与「漏报」同形
if _dup_corr_rows:
    print(f"  ⚪ 另有 {len(_dup_corr_rows)} 行 role='user' 为**重复行**"
          f"（同 session+时间戳+正文，历史重插所致）已去重，未重复计入纠正数：")
    for _ts, _c in _dup_corr_rows[:3]:
        print(f"     [{_ts}] {_c} …")

# 任务规格留痕（09-23）：降级不静默 —— 不打印就等于「误报已过滤」与「漏报」同形
if _corr_spec_rows:
    print(f"  ⚪ 另有 {len(_corr_spec_rows)} 条命中「命令纠正」词族但属**任务规格/口径定义**"
          f"（未计入纠正数；判据=_correction_spec_context，须人工核实）：")
    for _ts, _why, _c in _corr_spec_rows[:3]:
        print(f"     [{_ts}] {_why}: {_c} …")

# 故障报告留痕（09-24）：同上，降级不静默
if _corr_report_rows:
    print(f"  ⚪ 另有 {len(_corr_report_rows)} 条命中「错了」但属**故障报告套语**"
          f"（命令/脚本报错，非追责；未计入纠正数，判据=_correction_report_context）：")
    for _ts, _why, _c in _corr_report_rows[:3]:
        print(f"     [{_ts}] {_why}: {_c} …")

# ============================================================
# SECTION 1b: 核验追问扫描（2026-09-19 新增，**不计入纠正数**）
# ============================================================
# 09-19 漏报实测：当日词表 0 命中、脚本报「0 纠正 / 🟢 平静」，但窗口内真有信号——
#   19:05「抽查做了吗？」→ agent 自认「抽查只做了一半，漏了最关键的一项（片段↔锚点归属）」
#   21:07「复查这次修改memory有没有失误和遗漏」→ 复查出审计脚本把异常计数当信息、4 处下游副本待修
# 这类话**不含否定词**（用户没说你错，只要求你拿证据），SECTION 1 的关键词表天然认不出
# →「无纠正信号」冒充「今天很平静」（检测器盲区族：09-08 / 09-13 / 09-16 / 09-19）。
# 处理：单列一节**只列不计数**（不并入总数、不改评级），交给反省 agent 人工判断
# 「上一条宣称是否真被验证过」——避免为凑信号而误报。
VERIFY_PROBE_PATTERNS = ["抽查", "复查", "有没有遗漏", "有没有失误", "核一遍",
                         "再核一", "验证一下", "拿证据", "证据呢"]
print("\n--- 核验追问（用户要证据，不否定；不计入纠正数 —— 须人工判断上一条宣称是否真被验证）---")
_probe_hits = 0
_probe_harness_skipped = 0
_probe_dup_rows = 0
for sess in sessions:
    _sid = sess["id"]
    if not _sid:
        continue
    _seen_probe_rows = set()   # 2026-09-22 去重：同一句追问重复落库时只列一次
    cur = conn.execute("""
        SELECT content, timestamp
        FROM messages
        WHERE session_id = ?
          AND role = 'user'
          AND timestamp > ?
        ORDER BY timestamp ASC
    """, (_sid, SINCE_TS))
    for msg in cur.fetchall():
        content = msg["content"] or ""
        if is_harness_text(content):   # 09-21：压缩摘要里含「复查」⇒ 假核验追问
            _probe_harness_skipped += 1
            continue
        if any(sp in content for sp in skip_patterns):
            continue
        hit = None
        for p in VERIFY_PROBE_PATTERNS:
            if p in content:
                hit = p
                break
        if not hit:
            continue
        # 重复行去重（2026-09-22）：同一句追问被重插多次时只列一次
        _pk = dedup_key(_sid, msg["timestamp"], content)
        if _pk in _seen_probe_rows:
            _probe_dup_rows += 1
            continue
        _seen_probe_rows.add(_pk)
        ts = datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M")
        print(f"  [{ts}] 🔎 「{hit}」: {content[:150].replace(chr(10), ' ')}")
        _probe_hits += 1

if _probe_hits == 0:
    print("  无（≠「今天没被追问」的证明：只说明这些短语一条都没匹配上）")
if _probe_harness_skipped:
    print(f"  ⚪ 另有 {_probe_harness_skipped} 条 harness 注入文本已排除（非用户发言）")
if _probe_dup_rows:
    print(f"  ⚪ 另有 {_probe_dup_rows} 行为**重复行**（同 session+时间戳+正文）已去重，"
          f"同一句追问只列一次")

# ============================================================
# SECTION 1c: 术语/机制解释追问（2026-09-26 新增，**不计入纠正数、不改评级**）
# ============================================================
# 用户档案里的既有模式：「遇到生造/内部术语（『报过一次就静默』『去重口径』『判据形状』）会直接追问
#   『这是什么意思』」。09-26 实测：agent 用 `attr-*`（只对 agent 有意义的通配名）指代机制并提议
#   「挂进夜间任务常驻对账」，用户回「这段话具体说i的是什么意思？」—— 当日纠正词表 0 命中。
# 这类追问**不是否定 agent**，而是「解释不到位」的信号 ⇒ 与核验追问同规格：只列不计数，交人工判。
# 2026-09-29 实测漏报（同族第 2 次）：「临时 job是指什么，当时为什么创建它？」—— agent 用自己起的
#   job 名（「看门狗投递测试（临时·用完即删）」）当指代却没解释，用户追问；词表只有「是什么意思」，
#   漏掉「是指什么」这一形状 ⇒ 当日 SECTION 1c 照打「无」。补词 + 回归见
#   test-daily-reflection-explain-probe.py（双向样本，含这条真实原文）。
# 2026-10-06 实测漏报（同族第 3 次）：「两份 6-12 旧原型，**是指的什么**，具体说明」—— 09-29 补的
#   「是指什么」是**紧邻子串**，用户插一个「的」就失配 ⇒ 当日 SECTION 1c 又照打「无」。登记 3 个
#   同族形状（指/说的·是/的·什么 的插字变体），回归样本同步扩。规则：这类词表按**族**收（同一问法
#   的插字/换字变体一起进），不要一次只补当次原句 —— 否则下一次换个助词就再漏一遍。
# 2026-10-12 实测漏报（词表侧第 4 次，与 07 月的「卡点1」行为侧那次分开计数）：
#   「拆 references,是指从"skill.md"中搬到"references"中吗」—— 前三轮补的全是「…是指**什么**」族，
#   而这一次「是指」后面接的是**具体对象**（不是「什么」）⇒ 整族失配，SECTION 1c 又照打「无」。
#   实测全库 19 行含「是指」的 role='user'（剔 harness 后 10 条）**每条都是释义追问、零误报**
#   ⇒ 词族标记其实是裸「是指」；但「是指导/令/标…」（指导、指令、指标、指南）不是追问 ⇒ 加尾字守卫。
#   裸「是指」放表末当兜底：前面更具体的词先命中，保留既有显示标签。
EXPLAIN_PROBE_PATTERNS = ["是什么意思", "什么意思", "是指什么", "指的什么", "指的是什么",
                          "具体说的是什么", "说的是什么",
                          "没听懂", "看不懂", "什么概念", "白话", "说人话",
                          "是指"]
# 裸「是指」的尾字守卫：紧跟这些字则属复合词（指导/指令/指标/指南…），不算释义追问。
EXPLAIN_PROBE_TAIL_GUARD = "导令标南数挥明纹点派出使责教"


def explain_probe_hit(content):
    """命中即返回探针词（表内顺序=优先级）；裸「是指」须逐处过尾字守卫，否则普通请求会误报。"""
    for p in EXPLAIN_PROBE_PATTERNS:
        if p not in content:
            continue
        if p != "是指":
            return p
        i = -1
        while True:
            i = content.find(p, i + 1)
            if i < 0:
                break
            tail = content[i + len(p):i + len(p) + 1]
            if tail == "" or tail not in EXPLAIN_PROBE_TAIL_GUARD:
                return p
    return None
print("\n--- 术语/机制解释追问（信息性，不计入纠正数 —— 人工判：是否 agent 用了未解释的内部术语）---")
_explain_hits = 0
for sess in sessions:
    _sid = sess["id"]
    if not _sid:
        continue
    _seen_ex = set()
    cur = conn.execute("""
        SELECT content, timestamp
        FROM messages
        WHERE session_id = ?
          AND role = 'user'
          AND timestamp > ?
        ORDER BY timestamp ASC
    """, (_sid, SINCE_TS))
    for msg in cur.fetchall():
        content = msg["content"] or ""
        if is_harness_text(content):
            continue
        if any(sp in content for sp in skip_patterns):
            continue
        _hit = explain_probe_hit(content)
        if not _hit:
            continue
        _ek = dedup_key(_sid, msg["timestamp"], content)
        if _ek in _seen_ex:
            continue
        _seen_ex.add(_ek)
        _ts = datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M")
        print(f"  [{_ts}] ❓ 「{_hit}」: {content[:200].replace(chr(10), ' ')}")
        _explain_hits += 1
if _explain_hits == 0:
    print("  无（≠「解释都到位」的证明：只说明这些短语一条都没匹配上）")

# ============================================================
# SECTION 1d: 疑问式抱怨（2026-10-19 新增，**不计入纠正数、不改评级**）
# ============================================================
# 10-19 漏报实测：15:23 用户「开机检查为什么不先做」—— 这是真纠正（agent 在有现成 13:03
#   报告的情况下反问用户），但 SECTION 1 的词表只认否定词（不对/错了/没做），疑问句式一条
#   都不匹配 ⇒ 日报照打「今日无纠正信号 / 🟢 平静」。同族复发：09-19（核验追问无否定词）、
#   09-26（「是指什么」形状漏掉）。根因同一：**词表按见过的形状逐个收口，没见过的句式静默漏**。
# 处理与 1b/1c 同规格：**只列不计数、不改评级**，交反省 agent 人工判「agent 是否确实漏做了
#   应做的事」。与 SECTION 1 重叠的样本仍以 SECTION 1 为准（本节不重复计数）。
# 口径是宽的（「为什么不X」也可能是纯信息提问，如「为什么不建议调长超时？」）—— 实测 120 天
#   真人发言 2,400 条里命中 25 条（≈1 条/5 天），噪声可接受。判据见
#   test-daily-reflection-complaint-probe.py（双向：真抱怨必中、普通请求不中）。
COMPLAINT_PROBE_PATTERNS = [
    r"为什么(不|没)",
    r"怎么(不|没)(做|发|跑|推|回|给|执行|处理|检查)",
    r"是不是(忘|漏)",
    r"怎么还没",
    r"没发(?!现|版|挥)",
]


def complaint_probe_hit(content):
    """疑问式抱怨探针：返回命中的模式串，未命中返回 None。"""
    for p in COMPLAINT_PROBE_PATTERNS:
        if re.search(p, content):
            return p
    return None


print("\n--- 疑问式抱怨（不含否定词，不计入纠正数 —— 须人工判：agent 是否确实漏做了应做的事）---")
_cmpl_hits = 0
_cmpl_harness = 0
_cmpl_dup = 0
for sess in sessions:
    _sid = sess["id"]
    if not _sid:
        continue
    _seen_cmpl = set()
    cur = conn.execute("""
        SELECT content, timestamp
        FROM messages
        WHERE session_id = ?
          AND role = 'user'
          AND timestamp > ?
        ORDER BY timestamp ASC
    """, (_sid, SINCE_TS))
    for msg in cur.fetchall():
        content = msg["content"] or ""
        if is_harness_text(content):
            _cmpl_harness += 1
            continue
        if any(sp in content for sp in skip_patterns):
            continue
        _chit = complaint_probe_hit(content)
        if not _chit:
            continue
        _ck = dedup_key(_sid, msg["timestamp"], content)
        if _ck in _seen_cmpl:
            _cmpl_dup += 1
            continue
        _seen_cmpl.add(_ck)
        _cts = datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M")
        print(f"  [{_cts}] ❓ 「{_chit}」: {content[:150].replace(chr(10), ' ')}")
        _cmpl_hits += 1
if _cmpl_hits == 0:
    print("  无（≠「今天没被抱怨」的证明：只说明这些句式一条都没匹配上）")
if _cmpl_harness:
    print(f"  ⚪ 另有 {_cmpl_harness} 条 harness 注入文本已排除（非用户发言）")
if _cmpl_dup:
    print(f"  ⚪ 另有 {_cmpl_dup} 行为**重复行**（同 session+时间戳+正文）已去重")

# ============================================================
# SECTION 2: False-success detection
# Agent claimed "成功/完成/替换/接管" but user later contradicted
# ============================================================
print(f"\n--- 虚假成功/未验证声称 ---")

# ── 话题连续性校验（2026-09-08：修复跨话题误报，根治=话题校验而非堆关键词）──
# 提取「专有锚点」：文件名/路径、camelCase/snake_case 组合标识符、4+ 位数字串。
# 常见英文词（gateway/docker/config 等）不作为锚点——两个不同任务都可能提到，无区分度。
_ANCHOR_STOPWORDS = {
    "the", "and", "for", "this", "that", "with", "from", "have", "will",
    "your", "are", "not", "you", "was", "has", "all", "can", "one", "two",
    "gateway", "docker", "config", "file", "files", "data", "user", "message",
    "messages", "session", "sessions", "hermes", "python", "error", "errors",
    "time", "date", "content", "state", "system", "check", "test", "list",
    "self", "true", "false", "none", "null",
}

def extract_anchors(text):
    """提取专有锚点：文件名/路径、组合标识符、长数字串。用于判断两条消息是否同一话题。"""
    if not text:
        return set()
    anchors = set()
    # 1) 文件名/路径（带常见扩展名）
    for m in re.findall(r'[A-Za-z0-9_\-./]+\.(?:md|yaml|yml|py|sh|json|docx|pdf|db|sql|txt|toml|ini|cfg|css|js|ts|go|rs|cpp|h|c|log|bak)\b', text, re.IGNORECASE):
        anchors.add(m.lower())
    # 2) camelCase / snake_case / 连字符组合标识符（含分隔符的组合词，避开停用词）
    for m in re.findall(r'\b[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+\b', text, re.IGNORECASE):
        low = m.lower()
        if low not in _ANCHOR_STOPWORDS:
            anchors.add(low)
    # 3) 4+ 位数字串（端口/ID/版本/行号）
    for m in re.findall(r'\b\d{4,}\b', text):
        anchors.add(m)
    return anchors

# 「动作回指」词：用户在指代我方上一步动作（改了/修了/试了…），是「紧邻 = 真反驳」的正面特征。
# 名单刻意收紧：不含「重启了」——「你重启了」与「我重启了」方向相反、字面同形，分不开，宁可不判。
ANAPHOR_RE = re.compile(r"(改了|修了|试了|做了|弄了|动过|改完|修完|按你说的)")

def is_cross_topic(claim_text, deny_text, user_idx):
    """
    判断 deny 是否跨话题（非对 claim「完成」的反驳）。保守策略：只过滤高置信度跨话题。
    返回 True = 跨话题（过滤，不作为虚假成功信号）；False = 保留（交给反省 agent 核实）。
    """
    ca = extract_anchors(claim_text)
    da = extract_anchors(deny_text)
    # 1) deny 是 claim 后紧邻的第一条 user 消息 —— 旧版无条件判「真反驳」，2026-09-16 发现漏洞：
    #    claim 讲 A 话题（且带专有锚点/文件路径），deny 讲完全无关的 B 话题（一个锚点都没有）时，
    #    也会被算成反驳。实例（09-16）：claim＝「MEMORY 精简 7,865→7,000 完成 …」，
    #    紧邻 user＝「今天的 wsl 是我手动启动的，不是自动拉起来的」→ 误报为虚假成功
    #    （MEMORY 那笔数字有 verify-memory-no-loss.py 物证，根本没被反驳）。
    #    现判据：claim 有锚点 + deny 无锚点 + deny 无「动作回指」→ 跨话题；
    #    deny 有动作回指（「改了」「修了」…指代我方上一步动作）→ 仍按紧邻真反驳保留。
    if user_idx == 0:
        if ca and not da and not ANAPHOR_RE.search(deny_text):
            return True
        return False
    # 2) claim 有专有锚点、deny 完全没有 → 跨话题（deny 纯中文新话题，与 claim 技术对象无关）
    if ca and not da:
        return True
    # 3) 双方都有锚点但零交集 → 跨话题
    if ca and da and not (ca & da):
        return True
    # 其余（双方都无锚点 / 有交集）→ 保守保留
    return False


# ── 疑问句里的「没有」不是反驳（2026-09-28 新增，误报侧第 4 次）───────────────
# 误报实例（09-28 17:46/17:47 两条 claim 被同一句 deny 各记 1 次）：
#   claim ①「比对完成 —— 结论是「矛盾不存在」」（**无锚点** ⇒ 跨话题过滤器需要 claim
#           有锚点才生效，ca 为空时直接落到「保守保留」）
#   claim ②「比对做完了…config 值 ═ engine 值」（有 trace_log 等锚点，但 deny 恰好也在
#           问 trace_log ⇒ 锚点交集非空 ⇒ 同样放行）
#   deny 「把 MEMORY 那句改成精确版…｜查 trace_log 的写入来源与级别控制｜今日首次全检,
#          以前包含MCP的检查，**为什么今天没有**？」
#   ⇒ 语义 = 问「今天的全检为什么少了 MCP 这一项」，**不是**否定比对结论（当日 0 纠正）。
# 判据：命中词所在**分句**以 ？/ ? 收尾 ⇒ 疑问句 ⇒ 不计入，降级打印 ⚪（不静默）。
# 只对「没有」生效（刻意保守）：`你确定`/`为什么你说`/`根本没` 本身就是反问或追责形状，
#   一并过滤会把真信号吃掉（例：「你为什么根本没做？」）。
_CLAUSE_ENDS = ("。", "！", "!", "？", "?", "\n", "；", ";", "，", ",")


def is_interrogative_hit(text, kw):
    """命中词出现的那个分句是否以问号收尾（= 疑问句，非反驳）。

    保守：找不到分句边界时不判疑问句（交回原逻辑保留）。
    「，」也算边界，避免把「你重启了没有生效，这是怎么回事？」整句的问号看成命中的问句。
    """
    start = 0
    while True:
        i = text.find(kw, start)
        if i < 0:
            return False
        after = i + len(kw)
        best = -1
        for ch in _CLAUSE_ENDS:
            j = text.find(ch, after)
            if j != -1 and (best == -1 or j < best):
                best = j
        if best != -1 and text[best] in ("？", "?"):
            return True
        start = after


# ── 引用段内的否定词不是用户的反驳（2026-10-15 新增，误报侧第 5 次）────────────
# 误报实例（10-15 22:09 claim → 22:13 user，被计 1 次虚假声称、当日评级 🟡）：
#   claim＝「✅ 分类移动完成 …」（真完成，sha256 与 skills_list 双验过）
#   user ＝「修好映射后重跑闸门，仍报 **2 项「本机有、公开侧没有、且未声明理由」**（非本轮引入）：…
#           这到底什么意思？为什么会这样？」
#   ⇒ 命中词「没有」出在**用户引用的闸门输出标签**「本机有、公开侧没有…」里 ——
#     用户在问这句话什么意思，**没有否定任何东西**。
# 判据：该关键词在**剥掉引用段之后**不再出现 ⇒ 它只存在于引用里 ⇒ 降级 ⚪（不静默）。
# 量测（120 天 role='user' 真发言、harness 已剔，探针 cache/scratch/probe-quote-guard-scope.py）：
#   SECTION2 反驳词表 130 条命中里只有 2 条是「引用段内唯一命中」，两条都是误报
#   （10-15 22:13 本条；07-27 23:58 是用户引用我上一轮报告正文后追问「为什么你用 verify-conclusion 复查这段」）；
#   SECTION1 纠正词表 0 条、SECTION3 手动词表 0 条 ⇒ 只在本节加守卫，其余计数点无需改。
# 为什么不把整条判成「不是反驳」：用户可以在引用之外自己加否定（引用 + 自己的话），
#   那种情况剥掉引用后仍会命中 ⇒ 真反驳不受影响。
# 与「[Replying to:] 的被引用正文参与匹配」那条已知残漏同源；本次只收「引用段内唯一命中」一种形状。
QUOTE_SPAN_RE = re.compile(
    r"「[^」]{0,2000}」|『[^』]{0,2000}』|“[^”]{0,2000}”|\"[^\"]{0,2000}\"|`[^`]{0,2000}`|【[^】]{0,2000}】")


def negation_only_in_quotes(text, kw):
    """kw 在 text 里出现，但**只出现在引用段内** ⇒ True（用户在引用别人的话，不是否定）。

    引用段 = 「」『』“”""``【】。
    """
    if kw not in (text or ""):
        return False
    return kw not in QUOTE_SPAN_RE.sub(" ", text)


success_keywords = ["成功", "完成", "替换", "接管", "重连", "全部 .venv", "切换完成"]
contradiction_keywords = ["还是不行", "没有", "没成功", "又是我", "手动重启",
                          "手动启动", "根本没", "实际上没", "你确定", "为什么你说"]

found_false = 0
_harn_notes = []   # SECTION 2 的 harness 留痕（去重后打印）
_dup_claim_rows = []   # SECTION 2 的重复行留痕（2026-09-22）
_interrog_notes = []   # SECTION 2 的疑问句「没有」留痕（2026-09-28）
_quote_notes = []      # SECTION 2 的「引用段内唯一命中」留痕（2026-10-15）

for sess in sessions:
    sid = sess["id"]
    if not sid:
        continue

    # Get ALL messages (assistant + user) chronologically
    cur = conn.execute("""
        SELECT id, role, content, timestamp
        FROM messages
        WHERE session_id = ?
          AND timestamp > ?
        ORDER BY timestamp ASC
    """, (sid, SINCE_TS))

    msgs = cur.fetchall()
    _seen_claim_rows = set()   # 2026-09-22 去重：同一条 claim 被重插时只计一次

    for i, msg in enumerate(msgs):
        if msg["role"] != "assistant":
            continue
        content = msg["content"] or ""

        # Check if assistant claimed success
        claimed = None
        for kw in success_keywords:
            if kw in content:
                claimed = kw
                break
        if not claimed:
            continue

        # 重复行去重（2026-09-22）：同一条「成功/完成」声称被重插 ⇒ 会被计成 N 次虚假声称
        _ck = dedup_key(sid, msg["timestamp"], content)
        if _ck in _seen_claim_rows:
            _dup_claim_rows.append((datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M"),
                                    content[:60].replace("\n", " ")))
            continue
        _seen_claim_rows.add(_ck)

        # 遍历 claim 后的消息，找反驳（校验话题连续性，过滤跨话题误报）
        user_idx = -1      # claim 后第几条 user 消息（0 = 紧邻第一条）
        matched = False
        for j in range(i+1, min(i+10, len(msgs))):
            future = msgs[j]
            if future["role"] != "user":
                continue
            fcontent = future["content"] or ""
            # harness 注入文本不是「用户反驳」（09-21：22KB 压缩摘要里的「没有 distro 在跑」
            # 被读成对 claim 的反驳 ⇒ 2 次假虚假声称）。
            # 注意：跳过时**不**递增 user_idx —— 它占据时间线但不构成一次用户发言，
            # 紧邻判断应落在下一条**真人**发言上。过滤留痕照打，避免静默。
            if is_harness_text(fcontent):
                ts_h = datetime.fromtimestamp(future["timestamp"], tz).strftime("%H:%M")
                _harn_notes.append((ts_h, len(fcontent),
                                    fcontent[:80].replace(chr(10), " ")))
                continue
            user_idx += 1
            for ck in contradiction_keywords:
                if ck not in fcontent:
                    continue
                # "有没有"是疑问句（"有没有遗漏/有没有能优化"），不是对"完成"的反驳（09-08 误报）
                if ck == "没有" and "有没有" in fcontent:
                    continue
                ts_claim = datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M")
                ts_deny = datetime.fromtimestamp(future["timestamp"], tz).strftime("%H:%M")
                # 疑问句里的「没有」不是反驳（09-28，判据见 is_interrogative_hit 注释）。
                # 与「有没有」同族：都是「没有」子串被当成否定词。过滤同样打 ⚪ 留痕。
                if ck == "没有" and is_interrogative_hit(fcontent, ck):
                    _interrog_notes.append((ts_claim, ts_deny,
                                            content[:80].replace("\n", " "),
                                            fcontent[:150].replace("\n", " ")))
                    break   # 该反驳词不成立，继续看后续 user 消息
                # 引用段内的否定词不是反驳（2026-10-15，判据见 negation_only_in_quotes 注释）。
                # 过滤同样打 ⚪ 留痕，与上面两条同规。
                if negation_only_in_quotes(fcontent, ck):
                    _quote_notes.append((ts_claim, ts_deny,
                                         content[:80].replace("\n", " "),
                                         fcontent[:150].replace("\n", " ")))
                    break
                # 话题连续性校验：跨话题的「没有/不对」不算对「完成」的反驳（09-02 / 09-16 教训）
                # 过滤但**不静默**：打印 ⚪ 行，让读报告的 agent 知道「这条被排除了、为什么」——
                # 「无信号」与「漏报」同形，静默过滤会被读成「今日无虚假声称」。
                if is_cross_topic(content, fcontent, user_idx):
                    claim_head = content[:80].replace("\n", " ")
                    print(f"\n  [{ts_deny}] ⚪ 疑似跨话题（已过滤，未计入虚假声称）")
                    print(f"  用户: {fcontent[:150]}")
                    print(f"  ↳ claim 摘要[{ts_claim}]: {claim_head}")
                    break  # 跨话题，跳过此反驳词，继续看后续消息
                claim_excerpt = content[:150].replace("\n", " ")
                deny_excerpt = fcontent[:150].replace("\n", " ")
                print(f"\n  [{ts_claim}] 🟡 声称 \"{claimed}\"")
                print(f"  助手: {claim_excerpt}")
                print(f"  [{ts_deny}] ← 用户反驳 \"{ck}\"")
                print(f"  用户: {deny_excerpt}")
                found_false += 1
                matched = True
                break
            if matched:
                break  # one match per claim

if found_false == 0:
    print("  今日无虚假声称")
# 过滤留痕（去重后打印一次）：同一 harness 消息会被多个 claim 命中，逐条打会刷屏
if _harn_notes:
    _seen_h = set()
    print(f"  ⚪ 另有 {len({n[0] + n[2] for n in _harn_notes})} 条 harness 注入文本已排除"
          f"（非用户发言，未计入虚假声称）：")
    for _ts, _ln, _head in _harn_notes:
        _key = _ts + _head
        if _key in _seen_h:
            continue
        _seen_h.add(_key)
        print(f"     [{_ts}] {_head} …（{_ln} 字）")

# 疑问句「没有」留痕（09-28）：过滤不静默 —— 否则「误报已过滤」与「漏报」同形
if _interrog_notes:
    _seen_iq = set()
    _iq_rows = []
    for _tc, _td, _ch, _dh in _interrog_notes:
        if (_td, _dh) in _seen_iq:
            continue
        _seen_iq.add((_td, _dh))
        _iq_rows.append((_tc, _td, _ch, _dh))
    print(f"  ⚪ 另有 {len(_iq_rows)} 条**疑问句形式的「没有」**已过滤（未计入虚假声称；"
          f"判据=is_interrogative_hit，须人工核实）：")
    for _tc, _td, _ch, _dh in _iq_rows[:3]:
        print(f"     [{_td}] 用户: {_dh} …")
        print(f"        ↳ claim 摘要[{_tc}]: {_ch}")

# 引用段内否定词留痕（2026-10-15）：过滤不静默 —— 否则「误报已过滤」与「漏报」在输出上同形
if _quote_notes:
    _seen_q = set()
    _q_rows = []
    for _tc, _td, _ch, _dh in _quote_notes:
        if (_td, _dh) in _seen_q:
            continue
        _seen_q.add((_td, _dh))
        _q_rows.append((_tc, _td, _ch, _dh))
    print(f"  ⚪ 另有 {len(_q_rows)} 条命中词**只出现在引用段内**已过滤（未计入虚假声称；"
          f"判据=negation_only_in_quotes，用户引用第三方输出/我上一轮正文，不是反驳；须人工核实）：")
    for _tc, _td, _ch, _dh in _q_rows[:3]:
        print(f"     [{_td}] 用户: {_dh} …")
        print(f"        ↳ claim 摘要[{_tc}]: {_ch}")

# 去重留痕（09-22）
if _dup_claim_rows:
    print(f"  ⚪ 另有 {len(_dup_claim_rows)} 条**重复的 claim 行**（同 session+时间戳+正文，"
          f"历史重插所致）已去重，未重复计入虚假声称：")
    for _ts, _c in _dup_claim_rows[:3]:
        print(f"     [{_ts}] {_c} …")

# ============================================================
# SECTION 3: Manual intervention detection
# User explicitly says they had to fix things themselves
# ============================================================
print(f"\n--- 手动介入信号 ---")
manual_keywords = ["手动重启", "手动启动", "我手动", "我又手动",
                   "我自己", "我重启", "我去启动"]

found_manual = 0
found_manual_soft = 0   # 语境提示（提问句 / 回答我方提问）：不是失败信号，仅计数提示
_dup_manual_rows = []   # 重复行留痕（2026-09-22）


def _manual_nonfailure_context(sid, ts_epoch, content):
    """命中「手动」关键词但属于非失败语境的，返回原因字符串；否则 None。

    2026-09-14 教训（双向样本）：
      误报① 提问句 —— 08-29「阶段2也需要我手动执行吗？」= 在问安排，不是协助失败
      误报② 回答我方提问 —— 09-14 我问「15:54 那次是你手动开的吗」→ 用户答「15:54 那次是我手动开的」
    真信号（必须保留，不能被上面两条吃掉）:
      09-10「今天开机启动异常的慢，…WSL今天第一次启动后还自动退出了，我手动又运行一次WSL命令，才开机的」
    判据：① 以问号结尾 → 提问，必排除；② 短句(<=30 字) 且上一条非空 assistant 消息含问号 → 判为答复，排除。
    真信号是长叙述(55 字)，两条判据都不命中 → 保留。
    """
    s = content.strip().rstrip("。！!~～ ")
    if s.endswith("？") or s.endswith("?"):
        return "提问句"
    if len(s) <= 30:
        row = conn.execute("""
            SELECT content FROM messages
            WHERE session_id = ? AND role = 'assistant'
              AND timestamp < ? AND content IS NOT NULL AND length(content) > 0
            ORDER BY timestamp DESC LIMIT 1
        """, (sid, ts_epoch)).fetchone()
        if row and ("？" in (row["content"] or "") or "?" in (row["content"] or "")):
            return "回答我方提问"
    return None


for sess in sessions:
    sid = sess["id"]
    if not sid:
        continue

    cur = conn.execute("""
        SELECT id, role, content, timestamp
        FROM messages
        WHERE session_id = ?
          AND role = 'user'
          AND timestamp > ?
        ORDER BY timestamp ASC
    """, (sid, SINCE_TS))

    _seen_manual_rows = set()   # 2026-09-22 去重
    for msg in cur.fetchall():
        content = msg["content"] or ""
        
        # Skip system instruction text (same filter as SECTION 1)
        if is_harness_text(content):   # 09-21：harness 注入文本非用户发言
            continue
        skip = False
        for sp in skip_patterns:
            if sp in content:
                skip = True
                break
        if skip:
            continue
        
        matched = None
        for mk in manual_keywords:
            if mk in content:
                matched = mk
                break
        if not matched:
            continue

        # 重复行去重（2026-09-22）：同一句「我手动…」被重插 N 份 ⇒ 手动介入数被放大
        _mk = dedup_key(sid, msg["timestamp"], content)
        if _mk in _seen_manual_rows:
            _dup_manual_rows.append((datetime.fromtimestamp(msg["timestamp"], tz).strftime("%H:%M"),
                                     content[:60].replace("\n", " ")))
            continue
        _seen_manual_rows.add(_mk)

        ts_epoch = msg["timestamp"]
        ts = datetime.fromtimestamp(ts_epoch, tz).strftime("%H:%M")
        excerpt = content[:300].replace("\n", " ")

        soft = _manual_nonfailure_context(sid, ts_epoch, content)
        if soft:
            print(f"\n  [{ts}] ⚪ 手动介入（语境：{soft}，不计入失败信号）")
            print(f"  用户: {excerpt}")
            found_manual_soft += 1
            continue

        print(f"\n  [{ts}] 🟠 手动介入 \"{matched}\"")
        print(f"  用户: {excerpt}")
        found_manual += 1

if found_manual == 0:
    print("  今日无手动介入信号")
if found_manual_soft:
    print(f"  （另有 {found_manual_soft} 条语境提示：提问句/回答我方提问，已排除在失败计数外）")
if _dup_manual_rows:
    print(f"  ⚪ 另有 {len(_dup_manual_rows)} 行为**重复行**（同 session+时间戳+正文）已去重，"
          f"未重复计入手动介入：")
    for _ts, _c in _dup_manual_rows[:3]:
        print(f"     [{_ts}] {_c} …")

# ============================================================
# SECTION 4: Repeated failure pattern
# Same operation triggered 3+ times with crashes between them
# ============================================================
print(f"\n--- 重复失败模式 ---")
# Count gateway --replace / venv switch attempts across sessions
replace_attempts = 0
_dup_repl_rows = []   # 重复行留痕（2026-09-22）
for sess in sessions:
    sid = sess["id"]
    if not sid:
        continue
    cur = conn.execute("""
        SELECT content, timestamp FROM messages
        WHERE session_id = ?
          AND role = 'assistant'
          AND timestamp > ?
    """, (sid, SINCE_TS))

    _seen_repl_rows = set()   # 2026-09-22 去重
    for row in cur.fetchall():
        content = row["content"] or ""
        if "gateway run --replace" in content or ".venv/bin/python -m hermes_cli.main gateway run" in content:
            # 重复行去重（2026-09-22）：同一条提及被重插 N 份 ⇒ 凑出假 🔴（阈值 3 次）
            _rk = dedup_key(sid, row["timestamp"], content)
            if _rk in _seen_repl_rows:
                _dup_repl_rows.append((datetime.fromtimestamp(row["timestamp"], tz).strftime("%H:%M"),
                                       content[:60].replace("\n", " ")))
                continue
            _seen_repl_rows.add(_rk)
            replace_attempts += 1

if replace_attempts >= 3:
    print(f"  🔴 今日 {replace_attempts} 次尝试 gateway --replace / venv 切换")
    print(f"  ⚠️ 这通常意味着第一次尝试就应验证完毕，而非反复试错")
elif replace_attempts > 0:
    print(f"  🟡 今日 {replace_attempts} 次尝试 gateway --replace / venv 切换")
else:
    print("  未检测到重复操作模式")
if _dup_repl_rows:
    print(f"  ⚪ 另有 {len(_dup_repl_rows)} 条**重复行**（同 session+时间戳+正文）已去重，"
          f"未凑进「重复操作」次数：")
    for _ts, _c in _dup_repl_rows[:3]:
        print(f"     [{_ts}] {_c} …")

conn.close()

# ============================================================

# ========================================================================
# SECTION 7 覆盖率自检 / SECTION 8 窗口连续性（逐字来自上游）
# ========================================================================
# SECTION 7: 扫描覆盖率自检（2026-09-26 新增 —— 防「会话级盲区」静默复发）
# ============================================================
# 教训（同日实测）：5 个检测段落都遍历 `sessions`，而 sessions 的选取口径一旦偏窄
#   （旧版=只取窗口内新开的会话），长会话里**全部**用户发言都不会被扫，脚本仍照输出
#   「今日无纠正信号 / 🟢 平静」。⇒ 必须在报告里打印「扫描了几条 / 窗口内共几条」，
#   覆盖率不为 100% 时**不许给 🟢**（0 信号只是部分样本）。
#   注意：本段直接按 messages 表统计（不依赖上面的会话选取），是独立的第二判据。
SCAN_PARTIAL = False
_scanned_user = 0
_tot_user = 0
_missed_sess = []
_sub7 = 0
_harness7 = 0
_unrec_shapes = {}      # 2026-10-12：未收录的注入形状（数字归一化 → 条数）
_scan_err = None
try:
    # 注意：主连接 conn 已在 SECTION 5 之后关闭 ⇒ 本段自建只读连接（口径与 conn 一致）
    _conn7 = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=10)
    _conn7.row_factory = sqlite3.Row
    try:
        _scanned_ids = {s["id"] for s in sessions}
        # 去重口径（2026-09-26 修复，🔁 09-22「修一处计数点必须扫全部计数点」）：同一句发言被重插时
        #   会以多个 id、**完全相同 timestamp** 落库。本段 09-26 才新增，当时漏了去重 ⇒ 实测今日
        #   4 条重插把「唯一发言 13 条」报成「17 条」（覆盖率分子/分母同时虚高，比例凑巧仍是 100%）。
        #   修法：DISTINCT(session, content, timestamp) —— 与 dedup_key 语义等价的 SQL 形式；放在 SQL
        #   里而不是 Python 里，单测才能从源文件抽 SQL 在合成库上验证。
        _cur7 = _conn7.execute(
            "SELECT DISTINCT session_id, content, timestamp FROM messages "
            "WHERE role='user' AND timestamp > ?", (SINCE_TS,))
        _by_sess7 = {}
        for _r in _cur7.fetchall():
            _by_sess7.setdefault(_r["session_id"], []).append(_r["content"])
        _uniq7 = sum(len(v) for v in _by_sess7.values())
        _raw7 = _conn7.execute(
            "SELECT count(*) FROM messages WHERE role='user' AND timestamp > ?",
            (SINCE_TS,)).fetchone()[0]
        _dedup_delta7 = max(0, _raw7 - _uniq7)
        for _sid, _contents in _by_sess7.items():
            # 2026-10-12：未收录注入形状留痕（子任务会话跳过 —— 那里的 role='user' 是
            #   agent 自己写的委派提示，本来就不是注入文本）
            if _sid not in _sub_excluded_ids:
                for _c in _contents:
                    _shape = unlisted_injection_shape(_c)
                    if _shape:
                        _unrec_shapes[_shape] = _unrec_shapes.get(_shape, 0) + 1
            _n = sum(1 for _c in _contents if not is_harness_text(_c or ""))
            _harness7 += len(_contents) - _n
            # 09-30：子任务会话（source='subagent'）的 role='user' 是 agent 自己写的委派提示，
            #   不是用户发言 ⇒ 不计入分子/分母；但要留痕（否则「过滤生效」与「本来就没有」同形）。
            if _sid in _sub_excluded_ids:
                _sub7 += _n
                continue
            _tot_user += _n
            if _sid in _scanned_ids:
                _scanned_user += _n
            elif _n:
                _missed_sess.append((_sid, _n))
    finally:
        _conn7.close()
    if _missed_sess or _sess_truncated:
        SCAN_PARTIAL = True
except Exception as _e:
    SCAN_PARTIAL = True
    _scan_err = f"{type(_e).__name__}: {_e}"

print(f"\n--- 扫描覆盖率（本脚本实际扫到的用户发言 / 窗口内全部）---")
if _scan_err:
    print(f"  ❓ 覆盖率自检失败（**无法判定，不得读作「全覆盖」**）：{_scan_err}")
else:
    _pct = 100.0 * _scanned_user / max(_tot_user, 1)
    print(f"  {'✅' if not SCAN_PARTIAL else '⚠️'} 已扫描 {_scanned_user}/{_tot_user} 条"
          f"用户发言（{_pct:.0f}%）"
          + ("（含 harness 注入文本已排除；口径=窗口内有消息的**全部**顶层会话，子任务会话另计）"
             if not SCAN_PARTIAL else " ⇒ **有会话未被扫描，下面的计数只是部分样本**"))
    for _sid, _n in _missed_sess[:5]:
        print(f"     ⚠️ 未扫描会话 {_sid}: 窗口内 {_n} 条用户发言")
    # 去重留痕（2026-09-26 新增）：不打这行的话，「去重生效」与「本来就没重复」在输出上同形，
    #   而重复行被静默计入时又会把覆盖率数字吹大（本次实测 13→17）。
    if _dedup_delta7:
        print(f"     ⚪ 去重留痕：窗口内 raw {_raw7} 行 → 唯一发言 {_uniq7} 条"
              f"（{_dedup_delta7} 行为重插重复行，已剔除；上面的 N/N 用唯一数）")
    else:
        print(f"     ⚪ 去重留痕：窗口内 raw {_raw7} 行 = 唯一发言 {_uniq7} 条（今日无重插行）")
    # 子任务会话留痕（09-30 新增）：打印它才能把「raw/唯一」与「上面的 N/N」对上账
    #   （raw/唯一 含子任务会话的委派提示与 harness 文本，N/N 已剔除 ⇒ 差额必须一眼可还原）。
    if _sub7 or _harness7:
        print(f"     ⚪ 子任务会话留痕：唯一发言 {_uniq7} 条 − harness 注入 {_harness7} 条 − "
              f"subagent 子会话委派提示 {_sub7} 条（{len(_sub_excluded_ids)} 个会话，agent 自己写的）"
              f" = 上面的分子/分母 {_tot_user} 条")
    # 未收录注入形状留痕（2026-10-12 新增）：不打印这行的话，「表已收全」与「有个新形状
    #   正被当成用户发言」在输出上同形 —— 后者会直接变成假纠正（见 unlisted_injection_shape）。
    if _unrec_shapes:
        print(f"     ⚠️ 未收录的注入形状 {sum(_unrec_shapes.values())} 条（role='user' 且以 '[' 开头、"
              f"未过 is_harness_text ⇒ 可能被算成用户发言、污染上面的计数与各 SECTION 的命中；"
              f"请核对后决定是否收进 HARNESS_PREFIXES）：")
        for _h, _c in sorted(_unrec_shapes.items(), key=lambda x: -x[1])[:6]:
            print(f"        {_c}x {_h}")
    else:
        print("     ⚪ 未收录注入形状检查：0 条（窗口内 '[' 开头的 role='user' 正文"
              "要么已收录、要么不是注入形状）")

# ============================================================
# SECTION 8: 扫描窗口连续性（2026-10-08 新增 —— 防「窗口并集之外」静默漏扫）
# ============================================================
# 根因：窗口恒为 [now-86400, now]，漏跑后的 catch-up 轮次把窗口锚在**执行时刻**，
#   两轮相隔 > 24h 时中间那段落在所有窗口之外 ⇒ 任何一轮都不扫。
# 实测（同日全量核对 08-10 起 61 轮窗口并集）：并集之外有 71 条唯一真实用户发言
#   （raw 77 行），含 08-29「脚本报错了 脚本报错了，你能看到吗」「再重新检查一遍脚本还有没有2疏漏」、
#   09-08 全天 32 条 —— 历次报告照打「无纠正信号」（检测器没瞎，是样本没进来）。
# 本段只做**检测 + 留痕**：不改扫描窗口语义、不追补历史；缺口内有发言时禁止评 🟢。
# 状态文件 = 本轮实际扫描区间，下一轮据此判缺口；路径可用环境变量覆盖（供双向单测隔离）。
SCAN_HOLE_MSGS = False
_hole_kind, _hole_a, _hole_b = "unknown", None, None
_hole_n = 0
SCAN_STATE_PATH = (os.environ.get("REFLECTION_SCAN_STATE_PATH")
                   or os.path.join(REFLECT_HOME, "data", "reflection-scan-window.json"))
_prev_scan = {}
try:
    with open(SCAN_STATE_PATH, "r", encoding="utf-8") as _fh8:
        _prev_scan = json.load(_fh8)
    if not isinstance(_prev_scan, dict):
        _prev_scan = {"_error": f"内容不是 JSON 对象（{type(_prev_scan).__name__}）"}
except FileNotFoundError:
    _prev_scan = {}
except Exception as _e8:
    _prev_scan = {"_error": f"{type(_e8).__name__}: {_e8}"}

print("\n--- 扫描窗口连续性（上一轮终点 → 本轮起点；落在两者之间的任何一轮都不扫）---")
_hole_kind, _hole_a, _hole_b = scan_window_hole(_prev_scan.get("window_end"), SINCE_TS)
try:
    _prev_end_txt = datetime.fromtimestamp(float(_prev_scan.get("window_end")), tz).strftime(
        "%m-%d %H:%M:%S")
except (TypeError, ValueError):
    _prev_end_txt = f"无（{_prev_scan.get('window_end')!r}）"
_now_start_txt = datetime.fromtimestamp(SINCE_TS, tz).strftime("%m-%d %H:%M:%S")
if _prev_scan.get("_error"):
    print(f"  ❓ 上一轮扫描记录读不到/损坏（**不许读作「连续」**）：{_prev_scan['_error']}")
elif _hole_kind == "unknown":
    print(f"  ❓ 无上一轮扫描记录（{SCAN_STATE_PATH} 缺 window_end）"
          f" ⇒ 分不清「首轮运行」与「记录丢失」，本节无法判定")
elif _hole_kind == "none":
    print(f"  ✅ 与上一轮衔接：上一轮扫描终点 {_prev_end_txt}，本轮起点 {_now_start_txt}")
else:
    try:
        _conn8 = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=10)
        try:
            _rows8 = _conn8.execute(
                "SELECT DISTINCT m.session_id, m.content, m.timestamp "
                "FROM messages m LEFT JOIN sessions s ON s.id = m.session_id "
                "WHERE m.role='user' AND m.timestamp > ? AND m.timestamp <= ? "
                "AND COALESCE(s.source, '') <> 'subagent' ORDER BY m.timestamp",
                (_hole_a, _hole_b)).fetchall()
        finally:
            _conn8.close()
        _hole_msgs = [(sid, ts, (c or "").replace("\n", " ")[:60])
                      for sid, c, ts in _rows8 if not is_harness_text(c or "")]
        _hole_n = len(_hole_msgs)
    except Exception as _e8b:
        _hole_msgs = []
        print(f"  ❓ 缺口内发言计数失败（**不许读作 0 条**）：{type(_e8b).__name__}: {_e8b}")
    print(f"  {'🔴' if _hole_n else '⚠️'} 扫描缺口 "
          f"{datetime.fromtimestamp(_hole_a, tz):%m-%d %H:%M:%S} → "
          f"{datetime.fromtimestamp(_hole_b, tz):%m-%d %H:%M:%S}"
          f"（{(_hole_b - _hole_a) / 3600.0:.1f}h）内任何一轮都没扫；"
          f"缺口内 user 发言 {_hole_n} 条"
          + ("（无发言落入 ⇒ 没丢样本）" if not _hole_n else "（已排除 harness 注入文本）"))
    for _sid8, _t8, _head8 in _hole_msgs[:5]:
        print(f"     • {datetime.fromtimestamp(_t8, tz):%m-%d %H:%M:%S} "
              f"[{(_sid8 or '')[:16]}] {_head8}")
    if _hole_n > 5:
        print(f"     … 另 {_hole_n - 5} 条（历史全量清单：scripts/reflection-scan-holes.py）")
    SCAN_HOLE_MSGS = bool(_hole_n)


print("\n⚪ 记忆缺口检查：未启用（本版不依赖记忆后端）" if not args.memory_file
      else f"\n⚪ 记忆缺口检查：已提供 {os.path.basename(args.memory_file)}，"
           f"但缺口对账需快照机制，本版不实现（仅列出文件行数 {sum(1 for _ in open(args.memory_file, encoding='utf-8'))}）")

# ============================================================
# Summary（本版改写：上游尾部引用了被删除段落的变量 —— 投递失败、缺口补扫）
# ============================================================
total = found_corrections + found_false + found_manual + (1 if replace_attempts >= 3 else 0)
print(f"\n{'='*50}")
print(f"总计: {found_corrections} 纠正 + {found_false} 虚假声称 + {found_manual} 手动介入"
      f" + {replace_attempts} 次重复操作")
print("⚪ 未送达条数：本版不适用（需平台投递表，属环境专属检查）")

MIN_ONLINE_H = 6.0   # 窗口在线时长下限（小时）；本版无在线占比数据 ⇒ 该降级不会触发
if total == 0 and SCAN_HOLE_MSGS:
    print(f"评级: ⚪ 数据不足（存在未扫描区间：上一轮终点 {_prev_end_txt} → 本轮起点 "
          f"{_now_start_txt}，其中 {_hole_n} 条用户发言任何一轮都没扫到 ——"
          f"「0 信号」是部分样本，不是「平静」）")
elif SCAN_PARTIAL and total == 0:
    print(f"评级: ⚪ 数据不足（扫描覆盖 {_scanned_user}/{_tot_user} 条用户发言"
          f" —— 有会话/样本未被扫描 ⇒ 0 信号只是部分样本，不得读作平静）")
else:
    print(rating_line(total, WINDOW_OFFLINE, WINDOW_UNJUDGED, WINDOW_RATIO, MIN_ONLINE_H))
    if SCAN_PARTIAL:
        print(f"  ⚠️ 扫描覆盖不全（{_scanned_user}/{_tot_user}）：上面的计数只是部分样本")
    if SCAN_HOLE_MSGS:
        print(f"  ⚠️ 另有未扫描区间（上一轮终点 {_prev_end_txt} → 本轮起点，"
              f"{_hole_n} 条用户发言从未被任何一轮扫到）：上面的计数只是部分样本")
    if _unrec_shapes:
        print(f"  ⚠️ 另有 {sum(_unrec_shapes.values())} 条未收录的 '[' 开头 role='user' 正文"
              f"（形状见上面「扫描覆盖率」段）⇒ 上面的命中**可能不是用户说的**，逐条读原文再采信")
