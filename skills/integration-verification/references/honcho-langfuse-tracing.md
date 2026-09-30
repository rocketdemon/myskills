# Honcho Langfuse Tracing — Configuration and Verification

## Background

Honcho ships a built-in Langfuse integration (`conditional_observe` in `src/telemetry/logging.py`) that traces LLM calls through the `@observe` decorator. Enabling condition: `LANGFUSE_PUBLIC_KEY` in `.env` is non-empty.

## Configuration steps

1. Create an API key in Langfuse (or insert it directly into PostgreSQL)
2. **Confirm the `AppSettings` class declares all three variables** (`extra="ignore"` drops undeclared fields)
3. Write the three environment variables into Honcho's `.env`
4. Restart the Honcho API
5. **Verify authentication with curl** (this step cannot be skipped)

## The three required environment variables (none may be missing)

```bash
LANGFUSE_HOST=http://localhost:PORT
LANGFUSE_PUBLIC_KEY=pk-lf-...1234
LANGFUSE_SECRET_KEY=sk-lf-...1234
```

**Key constraint**: the `AppSettings` class in `src/config.py` must declare `LANGFUSE_SECRET_KEY: str | None = None`.
The class sets `model_config = SettingsConfigDict(extra="ignore")`, so undeclared fields are dropped by `DotEnvSettingsSource`,
leaving the Langfuse SDK unable to read SECRET_KEY from the OS environment → authentication fails silently → zero traces and no error.

## Pitfall: truncated key (silent authentication failure)

### Symptoms
- The `.env` config looks complete (grep shows `LANGFUSE_SECRET_KEY=sk-lf-...a1b2`)
- Honcho runs normally and its API responds normally
- Langfuse holds no Honcho trace at all
- The Honcho logs contain no "langfuse" string whatsoever (silent failure)

### Root cause
The SECRET_KEY value in `.env` is the display mask `sk-lf-...a1b2`, not the full-length key.

The full API key had been inserted directly into Langfuse's PostgreSQL (bcrypt+HMAC); when it was extracted, the visually truncated display version was copied out of the terminal output.

### How it surfaced
1. The user asks "did Honcho Langfuse tracing succeed?"
2. Search the honcho-api logs with journalctl → zero langfuse mentions
3. Check `.env` → grep output shows `sk-lf-...a1b2` (truncated by the terminal display)
4. session_search finds the full-length key in that session
5. curl verification: the full key authenticates ✅, the truncated version returns "Invalid credentials"

### Fix
1. Update `LANGFUSE_SECRET_KEY` in `.env` to the full value
2. `systemctl --user restart honcho-api`
3. After the next Honcho LLM call, check for traces in Langfuse

## Pitfall: `extra="ignore"` discarding a field

### Symptoms
- All three variables in `.env` are complete and correct (curl proves the key itself can authenticate against the Langfuse API)
- Honcho runs normally and its API responds normally
- Searching Langfuse for traces of `pk-lf-...1234` → **0 rows**
- The Honcho logs contain no "langfuse" string whatsoever (silent failure)

### Root cause
`AppSettings` in `src/config.py` declares only `LANGFUSE_HOST` and `LANGFUSE_PUBLIC_KEY`,
not `LANGFUSE_SECRET_KEY`. The class sets `extra="ignore"`, so `LANGFUSE_SECRET_KEY` from `.env` is
filtered out by `DotEnvSettingsSource`. The Langfuse SDK client reads `LANGFUSE_SECRET_KEY` from the OS environment,
but the process environment does not carry that variable → authentication fails silently.

### How it surfaced
1. curl confirms Honcho's API key authenticates against Langfuse ✅
2. Query Langfuse traces → all belong to Hermes (`pk-lf-...1234`), 0 belong to Honcho
3. Search the `AppSettings` class → only `LANGFUSE_HOST` and `LANGFUSE_PUBLIC_KEY` are declared
4. Search `model_config` → `extra="ignore"`
5. **Root cause confirmed**: SECRET_KEY is present in .env but dropped by pydantic

### Fix
1. Add `LANGFUSE_SECRET_KEY: str | None = None` to the `AppSettings` class (L1276)
2. `systemctl --user restart honcho-api`
3. Verify the trace on the next Honcho LLM call

## Verification: the uvicorn routing problem is resolved

### Symptom
- `honcho_llm_call` invoked through a standalone Python script → traces arrive in Langfuse normally ✅
- The same function invoked through `POST /v3/.../chat` (uvicorn → Dialectic → `honcho_llm_call`) → zero traces ❌

### Verification result: the problem resolved itself

Once the truncated-key pitfall and the `extra="ignore"` pitfall were fixed and a `.env` containing all three variables was created,
the next Honcho LLM call was all it took. A full check of the Langfuse dashboard:

| Trace Name | Count | Status |
|------------|-------|--------|
| Minimal Deriver | 1,390 | ✅ |
| Create Short Summary | 108 | ✅ |
| Create Long Summary | 33 | ✅ |
| **Dialectic Agent** | **30** | ✅ **the one previously suspected of producing no traces** |
| Hermes turn | 19 | ✅ |
| **Total** | **1.58K** | |

**Conclusion**: The root cause of "uvicorn routes produce no traces" was exactly the truncated-key pitfall plus the `extra="ignore"` pitfall. Framework black-box speculations such as "event loop context isolation" or "OTEL exporter initialization ordering" were wrong — with the three environment variables in place, both Deriver and Dialectic produce traces normally.

**Takeaway**: Do not attribute a failure to a framework black box too early. First check whether the config file was written correctly, whether pydantic dropped an environment variable, and whether the process actually loaded the variables. Until those three checks are done, do not jump to a "framework problem" conclusion.

## Verification commands

```bash
# Confirm the key in .env is complete (a full value, not the truncated version)
grep LANGFUSE_SECRET_KEY ~/honcho/.env

# Call the Langfuse API directly to verify authentication
PUBLIC_KEY=pk-lf-...1234
SECRET_KEY=<full key>
curl -s "http://localhost:PORT/api/public/projects" -u "$PUBLIC_KEY:$SECRET_KEY"
# expected: {"data":[{"id":"my-project",...}]}

# Check whether Honcho is using Langfuse
journalctl --user -u honcho-api --no-pager -n 50 | grep -i langfuse
```

## Langfuse API key database structure

```sql
SELECT id, public_key, 
       LEFT(hashed_secret_key, 30) as bcrypt_hash,
       LEFT(fast_hashed_secret_key, 30) as hmac_hash
FROM api_keys WHERE public_key LIKE '%honcho%';
```

- `hashed_secret_key`: bcrypt(rounds=5) — used by Langfuse for internal verification
- `fast_hashed_secret_key`: HMAC-SHA256(secret, salt) — used for fast verification
- The plaintext secret is not in the database; save the full value immediately after creation
