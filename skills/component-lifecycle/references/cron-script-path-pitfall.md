# The cron `no_agent` script path resolution pitfall

## Symptom

A cron job reports `Script not found`, but the script file does exist.

```
Cron job 'a scheduled inventory job' failed: Script not found
```

## Root cause

For a job with `no_agent=true`, the cron scheduler **always resolves relative paths against `~/.hermes/scripts/`**. `workdir` only affects the behavior of the terminal/file/search tools in agent mode; it does not affect script path resolution.

### Example of a wrong configuration

```yaml
script: skills/your-business-skill/scripts/your-script.py
workdir: USER_HOME/.hermes
```

Expected lookup: `USER_HOME/.hermes/skills/your-business-skill/scripts/your-script.py`  
Actual lookup: `~/.hermes/scripts/skills/your-business-skill/scripts/your-script.py` ← does not exist

## Solution: a wrapper script

Create a wrapper under `~/.hermes/scripts/` that `exec`s the real script:

```bash
# ~/.hermes/scripts/your-script.sh
#!/bin/bash
exec python3 USER_HOME/.hermes/skills/your-business-skill/scripts/your-script.py "$@"
```

```bash
chmod +x ~/.hermes/scripts/your-script.sh
```

Then change the cron job to:
```yaml
script: your-script.sh
```

## ⚠️ Do not use a symbolic link

The cron system detects a symlink as an "escape" and rejects it:

```
error: Script path escapes the scripts directory via traversal: 'your-script.py'
```

**Symlink resolution happens at the security-check stage**: the scheduler follows the symlink to its real target and rejects it as soon as the target is outside `~/.hermes/scripts/`.

## Takeaways

1. A `no_agent` cron script's relative path always starts from `~/.hermes/scripts/`
2. `workdir` does not override script path resolution
3. Bridge an external script with a wrapper script (not with a symlink)
4. After configuring a cron job, test it immediately with `cronjob action='run'`
