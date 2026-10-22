#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reflect.py 自检：用合成语料跑端到端，双向断言（既不许漏报也不许误报）。

合成语料刻意包含这些「同形但语义相反」的对子：
  1) 真纠正 vs 报错求助（「我运行命令报错了」不是说你错）
  2) 真纠正 vs 任务边界定义（「不是让你用X，而是读取Y」不是追责）
  3) 命中词只出现在引用段里（用户引用了别人的输出，不是反驳）
  4) 同一句发言被重插两份（历史重插，只能计一次）
  5) harness 注入文本（压缩摘要等，不算用户发言）
  6) subagent 子会话的委派提示（是 agent 自己写的，不算用户发言）

跑法：python3 selftest.py
退出码 0 = 全部断言通过。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EXPLORE = os.path.join(HERE, "reflect.py")
T0 = 1792000000.0      # 固定基准时间，避免夹具依赖当天日期


def msgs():
    M = []

    def add(sid, role, text, dt, mid=None):
        M.append({"message_id": mid or len(M) + 1, "session_id": sid,
                  "role": role, "text": text, "ts": T0 + dt})

    # 会话 A：正常对话
    add("s_main", "user", "帮我看下配置文件的第 3 行", 10)
    add("s_main", "assistant", "config.yaml 已修改完成，验证通过。", 20)
    add("s_main", "user", "config.yaml 没有改，还是旧的值", 30)   # 纯否认（含专有锚点 config.yaml）
    add("s_main", "user", "config.yaml 没有改，还是旧的值", 30)   # 重插重复行
    add("s_main", "user", "这个值改错了，我说的是另一个文件", 40)          # 真纠正 → 1
    add("s_main", "user", "我运行命令报错了，你能看到吗", 50)              # 报错求助 → 不计
    add("s_main", "user", "不是让你用复制功能，而是读取全部信息填入", 60)  # 任务边界 → 不计
    add("s_main", "assistant", "明白了，我改用读取方式。", 70)
    add("s_main", "user", "这个是我手动改的，你那个脚本没生效", 80)        # 手动介入 → 1
    add("s_main", "user", "你上一轮说「都验证过了」，其实没验", 90)
    add("s_main", "assistant", "我说过「都验证过了」这句。", 95)           # 引用段内命中
    # 会话 B：harness 注入
    add("s_main", "user", "[CONTEXT COMPACTION — REFERENCE ONLY] 早期对话被压缩", 100)
    # 会话 C：subagent 子会话（委派提示）
    add("s_sub", "user", "不要弄错，把结果写进 out.json", 110)
    return M


def sess():
    return [
        {"session_id": "s_main", "title": "配置修改", "started_at": T0, "session_source": ""},
        {"session_id": "s_sub", "title": "委派子任务", "started_at": T0, "session_source": "subagent"},
    ]


def run():
    d = tempfile.mkdtemp(prefix="reflect-selftest-")
    mp, sp = os.path.join(d, "messages.jsonl"), os.path.join(d, "sessions.jsonl")
    with open(mp, "w", encoding="utf-8") as f:
        for m in msgs():
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    with open(sp, "w", encoding="utf-8") as f:
        for s in sess():
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    env = dict(os.environ, REFLECT_HOME=d)
    r = subprocess.run([sys.executable, EXPLORE, "--messages", mp, "--sessions", sp,
                        "--since", str(T0 - 1), "--label", "self-test"],
                       capture_output=True, text=True, env=env, timeout=300)
    return r


CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))


r = run()
out = r.stdout + r.stderr
if r.returncode != 0:
    print(out[-3000:])
    sys.exit(f"❌ explore.py 退出码 {r.returncode}")

tot = re.search(r"总计: (\d+) 纠正 \+ (\d+) 虚假声称 \+ (\d+) 手动介入", out)
if not tot:
    print(out[-3000:])
    sys.exit("❌ 没找到总计行")
corr, false, manual = (int(x) for x in tot.groups())

check("退出码为 0", r.returncode == 0, f"exit={r.returncode}")
check("真纠正被计入（1 条）", corr == 1, f"实际 {corr}")
check("报错求助/任务边界未被计成纠正", corr == 1, f"若混入会 >1，实际 {corr}")
check("重插重复行只计一次", out.count("config.yaml 没有改，还是旧的值") <= 2,
      "去重留痕行最多出现两次（留痕+1）")
check("虚假声称被计入（≥1）", false >= 1, f"实际 {false}")
check("手动介入被计入（1 条）", manual == 1, f"实际 {manual}")
check("harness 注入文本被排除", "harness 注入文本已排除" in out, "")
check("subagent 子会话被排除并留痕", "已排除" in out and "subagent" in out, "")
check("覆盖率必须打印", "已扫描" in out and "条用户发言" in out, "")
check("环境专属检查标注未启用", "环境专属检查未启用" in out, "")
check("记忆缺口明确标注未启用", "记忆缺口检查：未启用" in out, "")
check("评级行必须出现", "评级:" in out, "")
check("不得出现「未送达」计数（本版不适用）",
      "未送达条数：本版不适用" in out, "")

print("=== explore.py 自检 ===")
ok = 0
for name, cond, detail in CHECKS:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail and not cond else ""))
    ok += 1 if cond else 0
print(f"\n{ok}/{len(CHECKS)} 通过")
if ok != len(CHECKS):
    print("\n--- 完整输出（供排查）---")
    print(out)
    sys.exit(1)
