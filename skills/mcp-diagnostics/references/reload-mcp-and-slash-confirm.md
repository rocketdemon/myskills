# MCP reload mechanics and the slash-command confirmation gate

When "the tool did not come back" after an MCP config change, it almost always lands on this page: the gateway does not auto-reload, and the reload command sits behind a confirmation gate.

## Mechanism (source level)

- The gateway **has no config-change watcher** ⇒ after editing config you must explicitly reload (`/reload-mcp`) or restart the gateway.
- `/reload-mcp` is registered in `gateway/run_busy.py::_IDLE_COMMANDS` (dispatched only on the **idle** path).
- The handler is `gateway/slash_commands.py::_handle_reload_mcp_command`:
  - `approvals.mcp_reload_confirm` is false → calls `_execute_mcp_reload()` directly, no confirmation.
  - otherwise `_request_slash_confirm(...)`: **sends a prompt + stores a resume handler in `tools/slash_confirm`**, and only executes on reply.
- The confirmation state is a **module-level dict inside the gateway process** (`tools/slash_confirm._pending`): not persisted to disk, not persisted to `state.db`.
- **`DEFAULT_TIMEOUT_SECONDS = 300`**; when `resolve()` misses on a stale entry it simply `return None`, and **the caller does not reply to None**
  ⇒ after expiry, a reply is completely silent (no execution, no error, no log entry).
- Text-only platforms (no buttons) resolve it by replying **`/approve`** (approve once), **`/always`** (approve and persist
  `approvals.mcp_reload_confirm: false`), or **`/cancel`**.
- While a confirmation is pending, new inbound messages are intercepted as confirmation-path input (they do not start a new turn).

## Three usable paths

| Path | Steps | Cost |
|---|---|---|
| Minimal change | Send `/reload-mcp`, reply `/always` **within 5 minutes** | A human must be there in time; the confirmation gate is permanently disabled |
| One-shot | Set `approvals.mcp_reload_confirm: false`, then send `/reload-mcp` | You lose the "a reload invalidates the prompt cache and may cost money" reminder; one command reverts it |
| Restart | Run `hermes gateway restart` **in an external shell** (outside the gateway) | Interrupts running cron/turns; an agent cannot do this from inside the gateway (the platform guard blocks it) |

Changing config is a "config change" class operation: check MEMORY for lessons → check for an existing script → **get user confirmation before acting**.

## Diagnostic recipe: determine what a user actually sent

The gateway **does not record slash-command text**. The available evidence, in order:

1. **Not being in `state.db.messages`** ≠ not received. Slash commands go down the command-handling path and **are not written to the message table** (that table holds real user/agent turns).
   Absent from the table + an inbound line in the log = **the command arrived and was intercepted as a command**.
2. **`delivery_obligations` is only a persisted-delivery record** (with failure retries); it does not guarantee every outbound message is in it ⇒ treating its absence as "never sent" is wrong.
3. `gateway.log` / `agent.log` only record `[platform] inbound ...` and `Sending response (N chars)`, **no body, no command name**.
4. **The same fixed character count recurring = a static locale string**: measure the `len()` of candidate templates in
   `locales/<lang>.yaml`; **an exact length match is sufficient to identify it**. Measured `gateway.reload_mcp.confirm_prompt`:
   zh=**225**, en=**463**. Three `Sending response (463 chars)` lines in the log ⇒ that is the one (the user sent `/reload-mcp` three times that day).
5. **How the locale is decided**: `display.locale` unset + process env `LANG=C.UTF-8`
   (`tr '\0' '\n' < /proc/<gateway_pid>/environ | grep -E '^(LANG|LC_ALL|LANGUAGE|HERMES_LOCALE)='`)
   ⇒ falls back to `en`. Do not assume the UI language equals the locale.
6. **Was the response synchronous?**: `Sending response` appearing **0.3 seconds** after `inbound` ⇒ it cannot be an agent turn (a turn takes minutes),
   so it must be the command-handling path ⇒ that reply can only be the command's fixed acknowledgement.
7. **The silently swallowed shape**: `inbound` followed by **no** `Sending response` line at all (neither a turn nor a command acknowledgement).

Combining 4/5/6 lets you infer the command name even though the gateway records no command text — but **label the conclusion as an inference**:
a length match is strong evidence, not the original text.

## Pitfalls

- Always read `state.db` as `sqlite3 -readonly state.db ...`; **never write** (it has raced with the gateway opening the DB concurrently in the past).
- Do not treat "restart the gateway" as a universal reload method: it interrupts running cron/turns; look for an idle window first.
- Do not treat "X is not in the tool list in my context" as "the gateway does not have X" — that is a snapshot from context build time.
