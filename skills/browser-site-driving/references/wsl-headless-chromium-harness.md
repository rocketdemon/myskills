# Getting browser_exec running on local WSL — a field log with two-sided evidence (2026-09-22)

On the first call, `browser_exec` failed outright; this document is the full diagnostic chain and verification evidence. **The conclusion is already in SKILL.md §1**,
and the raw physical evidence is kept here so we don't have to grope around again next time or misdiagnose it as "the browser tool is broken".

## 1. Failure symptoms and the raw log

```
browser_exec → {"success": false, "exit_code": 1,
  "stderr": "browser-harness: daemon default didn't come up -- check ~/.config/browser-harness/tmp/bu-default.log"}
```

The full log (1 line, 84 bytes):

```
fatal: chrome-not-running: no supported Chromium-family browser is running -- start Chrome, then retry
```

⇒ Diagnosis: **the harness does not ship its own browser**; it requires a Chromium-family browser already running on the host machine (it takes it over via CDP).
It is neither "the tool is broken" nor a missing Python dependency (the harness's own `Installed 104 packages in 8.68s` is normal).

Relevant paths (inspect in this order when diagnosing):

| Path | Purpose |
|---|---|
| `~/.config/browser-harness/tmp/bu-default.log` | why the daemon failed to start (the entry point for the diagnosis) |
| `~/.config/browser-harness/runtime/bu-default.sock` | the daemon's unix socket (a leftover means it started before) |
| `~/.config/browser-harness/version-cache.json` | harness version cache |
| `~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome` | a Chromium usable on this machine (Playwright cache) |

Source-code clues on the harness side (`browser_harness/admin.py`): it supports `BH_CHROME_PATH` / `CHROME_PATH` to point at a browser,
and when it detects `chrome-not-running` it will try to launch one itself; on this machine the least troublesome route, as measured, was "start it manually + let it probe 9222 on its own".

## 2. The launch command and measured behavior

```bash
# find the path first (the version number changes, don't hard-code it)
find ~/.cache/ms-playwright -name "chrome" -type f
# → ~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome
```

Launch it in the background (`background=true`, not nohup / `&`):

```bash
~/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome \
  --headless=new --remote-debugging-port=9222 \
  --user-data-dir=~/.config/google-chrome \
  --no-sandbox --disable-gpu --no-first-run about:blank
```

The process's actual argv (`tr '\0' ' ' < /proc/<pid>/cmdline`) — note that the **extra** arguments at the end are appended by the harness:

```
… --no-first-run --noerrdialogs --ozone-platform=headless \
  --ozone-override-screen-size=800,600 --use-angle=swiftshader-webgl about:blank
```

Among these, `--ozone-override-screen-size=800,600` is directly tied to "headless being judged as a phone" (see section 4).

Success criterion (the process's stdout):

```
DevTools listening on ws://127.0.0.1:9222/devtools/browser/39198b4d-84ff-4d0c-bc92-b5991edf8a47
[471422:471422:…:ERROR:dbus/object_proxy.cc:572] …UPower… (harmless, ignore)
```

## 3. A timing pitfall that misleads you

Checking the port immediately after launch gives you the **wrong conclusion**:

```bash
sleep 3; ss -ltn | grep 9222 || echo "9222 not listening"     # ← as measured, at this point it reports "not listening"
```
But checking again later in the same session (`ss -ltn`) already shows `LISTEN 0 10 127.0.0.1:9222`,
and `process(action='log')` had long since printed `DevTools listening on ws://127.0.0.1:9222/…`.

⇒ Priority of criteria: **`DevTools listening on …` on the process's stdout > `ss`**.
`ss` can only be a snapshot of "whether it's there right now"; it is not evidence that "startup failed".

## 4. headless judged as a phone: the UA interception page

Without a UA override, the target site (`https://www.<target-site>`) returns a **mobile interception page**:

```json
{"FINAL_URL": "https://www.<target-site>/", "TITLE": "🐴 <target-site>",
 "TEXT": "<target-site>\n桌游创作平台\n移动端页面正在开发中\n\n当前请使用电脑端访问<target-site>，以获得更完整、稳定的功能体验。",
 "输入框": [], "按钮": [], "链接": [], "VISIBLE_ELEMS": 30}
```

⇒ If you wrote a conclusion from that, you would get "this site has no login feature" — **completely wrong**.

After the following two CDP overrides, navigating again turns the same URL into the real login page at once:

```python
cdp("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
cdp("Emulation.setUserAgentOverride",
    userAgent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
goto_url("https://www.<target-site>/auth?redirect=/apps")
```

The result (as measured):

```
TEXT: BETA / 登录 / 还没有注册账号?立刻注册 / 手机号 / 邮箱 / 发送验证码
INPUTS: [{"type":"tel","ph":"请输入手机号","cls":"el-input__inner"}]
BUTTONS: ["手机号","邮箱","发送验证码"]
UA: Mozilla/5.0 (Windows NT 10.0; Win64; x64) … Chrome/148.0.0.0 Safari/537.36
```

**Criterion**: the total element count and the number of interactive controls are the fastest way to tell "interception page or real page";
the UA override itself must be applied **before** navigating (applying it after navigation is only stable with a reload).

## 5. Screenshot timeouts (stop relying on them)

In the same headless session, `capture_screenshot()` throws:

```
browser_harness.helpers._IPCResponseTimeout: Page.captureScreenshot timed out after 60s waiting for the daemon
RuntimeError: Page.captureScreenshot timed out after 60s
```

⇒ For evidence-gathering, switch to `js(...)` to pull DOM/text (the four-part sequence in SKILL.md §3), or `cdp("Page.captureScreenshot")` with its own short timeout.
Note: a screenshot failure inside one exec makes **the whole code block exit=1**, though already-printed content is still returned — so put the `js()` evidence-gathering before the screenshot.

## 6. Other harness behaviors (known, no action needed)

- The first call prints `Installed 104 packages in 8.68s` (uv resolving dependencies; normal).
- Occasionally stderr shows `browser-harness: Chrome is asking "Allow remote debugging?" — click Allow to continue.`
  — it can be ignored in headless scenarios (when the tool still returns `success: true`, there's no need to chase it).
- `browser_exec`'s `code` is full Python: beyond `import time`, `cdp(...)`, `js(...)` and `process`, the regular stdlib is available;
  variables do not persist across calls, so if you need state across calls, write it to a workspace file (`$BH_AGENT_WORKSPACE`).
