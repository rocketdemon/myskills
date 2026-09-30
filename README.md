# myskills

Field-tested skills for AI agents, packaged in the open [Agent Skills](https://agentskills.io/specification) format
(a directory containing a `SKILL.md` with `name` + `description` frontmatter).

Every skill here was distilled from a real failure and the fix that actually worked — not from documentation.
Each one documents *why* a symptom misleads you, and what evidence distinguishes the real cause from the
plausible-looking wrong one.

## Skills

| Skill | What it does |
|:--|:--|
| [`mcp-diagnostics`](skills/mcp-diagnostics/SKILL.md) | Diagnoses MCP servers that run but expose no tools, config changes that never take effect, and tool calls that blow up the context window |
| [`component-upgrade`](skills/component-upgrade/SKILL.md) | Plans and executes version upgrades of self-hosted components — Docker containers/databases and git-deployed services — with a restore-verified backup, an execution-driven config-migration check, and a tested rollback path |
| [`deep-research`](skills/deep-research/SKILL.md) | A research and diagnosis protocol: route the request to the right scenario, drill at least three layers deep, then summarize and verify the conclusion — including the ten traps that make a half-finished investigation look complete |
| [`component-lifecycle`](skills/component-lifecycle/SKILL.md) | Covers the gap after "the component is installed": container and systemd lifecycle, layered fault isolation, multi-point verification, state snapshots, and the integration checklist — plus the cron `no_agent` script-path resolution pitfall and a bottom-up MCP connection diagnostic flow |
| [`integration-verification`](skills/integration-verification/SKILL.md) | Proves that an integration **actually took effect** instead of looking configured: the three-variable env trap that fails silently, a `preflight` check that passes while the real path is broken, and how a credential display mask written into a config file produces an authentication failure with zero log lines |
| [`clickhouse-operations`](skills/clickhouse-operations/SKILL.md) | Operating a self-hosted ClickHouse that backs another service: system-log TTL governance where XML config and the engine definition disagree, repairing values in a key column (`INSERT` + `DELETE` + `FINAL`), corrupted-parts triage, backup/restore that is verified rather than assumed, and diagnosing trace timestamps that drift by whole hours |

## Install

### Hermes Agent

Copy the skill directory into your skills root (the layout measured on a real install is
`~/.hermes/skills/<any-group>/<skill-name>/`):

```bash
cp -r skills/mcp-diagnostics ~/.hermes/skills/mcp/mcp-diagnostics
```

> Whether `hermes skills install <git-url>` can install straight from a repository is **not verified** —
> the known form `hermes skills install official/<category>/<skill>` only covers the official
> optional-skills catalog. Do not assume it works here.

### Claude Code

This repository ships `.claude-plugin/marketplace.json`, so it can be added as a plugin marketplace:

```
/plugin marketplace add rocketdemon/myskills
```

## Layout

```
.claude-plugin/marketplace.json   # Claude Code plugin marketplace manifest
skills/<name>/SKILL.md            # the skill itself (frontmatter: name + description)
skills/<name>/references/*.md     # detail loaded on demand (progressive disclosure)
```

## Scope

Skill bodies are written in **English**. The diagnostic paths assume Hermes Agent
(`~/.hermes/...` paths, `hermes mcp ...` commands), but the mechanism-level judgements are
harness-independent:

- reading PPID / cgroup to spot an orphaned stdio child
- reading log-header signatures to tell whether the child reached `main()`
- comparing cold-start duration against `connect_timeout` headroom
- minimizing tool returns **at the parameter level** instead of trimming them afterwards

Concrete numbers in the text (timeout seconds, byte sizes, durations) are **measured historical
values** — read yours from your own config rather than copying them as constants.

## License

MIT — see [LICENSE](LICENSE).
