#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Statically analyze an SPA's front-end artifacts: baseURL / route table / relative endpoints / business copy / keyword hits (read-only).

Usage:
  analyze-spa-bundle.py <directory or a single .js> [extra keywords...]

Example:
  analyze-spa-bundle.py /tmp/example_chunks        # extra keywords are matched verbatim against the bundle:
                                                   # pass the site's own UI words (any language), not English ones

Why it's needed (pre-login reconnaissance): an SPA's route table and API paths both live in the JS artifacts,
so you can answer "what pages does the platform have / what APIs / is there feature X" without logging in.
Note: the main bundle usually only handles routing and the framework; business copy and form fields live in the page chunks
⇒ you must first download the lazy-loaded chunks too (use fetch-spa-chunks.sh).
"""
import glob
import os
import re
import sys
from collections import Counter

DEFAULT_KW = ["复制", "克隆", "另存", "复刻", "duplicate", "clone", "导出", "导入", "上传", "批量"]

# Common UI words (filtered out when analyzing business copy, otherwise the noise drowns the signal)
NOISE = set("""确定 取消 保存 删除 编辑 添加 加载 成功 失败 返回 提交 关闭 打开 输入 请输入
搜索 重置 全部 更多 详情 确认 提示 警告 错误 信息 暂无 数据 加载中 请选择 选择 上一页 下一页
完成 开始 结束 新建 修改 更新 查看 复制 粘贴 上传 下载 导出 导入 刷新 排序 筛选 取消 是 否
""".split())


def load(paths):
    for p in paths:
        if os.path.isdir(p):
            yield from ((f, open(f, encoding="utf-8", errors="ignore").read())
                        for f in sorted(glob.glob(os.path.join(p, "*.js"))))
        else:
            yield p, open(p, encoding="utf-8", errors="ignore").read()


def main():
    args = [a for a in sys.argv[1:] if a]
    if not args:
        print(__doc__)
        return 1
    target, extra = args[0], args[1:]
    kw = DEFAULT_KW + [k for k in extra if k not in DEFAULT_KW]
    files = list(load([target]))
    print("Scanned %d files\n" % len(files))

    all_src = []
    base_urls, routes = set(), []
    endpoints, paths = set(), set()
    for name, src in files:
        all_src.append(src)
        base_urls |= set(re.findall(r"""baseURL\s*:\s*["'`]([^"'`]{1,60})["'`]""", src))
        routes += re.findall(r"""path\s*:\s*["'`](/[^"'`]{0,60})["'`]""", src)
        endpoints |= set(re.findall(
            r"""["'`](/(?:api/[A-Za-z0-9_/{}.:$-]+|[a-z][a-z0-9_-]{1,20}/[A-Za-z0-9_/{}.:$-]{2,}?))["'`]""",
            src))
        paths |= set(re.findall(r"""["'`](/[a-z][A-Za-z0-9_/{}$.-]{2,60})["'`]""", src))

    print("## baseURL: %s" % (", ".join(sorted(base_urls)) or "(not found → endpoints may be absolute paths)"))
    print("\n## route path (%d)" % len(routes))
    for r in sorted(set(routes)):
        print("   " + r)
    print("\n## suspected endpoints/paths (%d, including forms relative to baseURL; .js/.css/.png and other static assets already filtered out)"
          % len([p for p in paths if not re.search(r"\.(js|css|png|jpe?g|svg|woff2?|ico|map)$", p)]))
    for p in sorted(paths):
        if re.search(r"\.(js|css|png|jpe?g|svg|woff2?|ico|map)$", p):
            continue
        print("   " + p[:110])

    words, seen = [], set()
    for src in all_src:
        for w in re.findall(r"[\u4e00-\u9fa5]{2,12}", src):
            if w in NOISE or w in seen:
                continue
            seen.add(w)
            words.append(w)
    print("\n## business copy (deduped %d, first 60 listed)" % len(words))
    for w in words[:60]:
        print("   " + w)

    blob = "\n".join(all_src)
    print("\n## keyword hits")
    for k in kw:
        n = blob.count(k)
        flag = "★zero hits" if n == 0 else ""
        print("   %-12s %5d %s" % (k, n, flag))
        if 0 < n <= 6:
            for c in re.findall(r".{40}%s.{40}" % re.escape(k), blob)[:2]:
                print("        ..." + c.replace("\n", " ") + "...")
    print("\nReading: zero hits on a feature keyword + no matching API ⇒ \"the platform does not provide this feature\"; switch to an alternative route.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
