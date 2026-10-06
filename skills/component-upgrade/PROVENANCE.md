# Provenance

This skill is a **derived publication**, not a standalone original. This file records where each
published file came from and what was changed, so that a reader — and the author — can tell a
deliberate transformation from a content loss.

Scope note: registering the relation is the whole point of this file. **Nothing is merged back and
no private file is overwritten**; the public copy is a snapshot of what was true when it was
published, and the private tree keeps evolving independently.

## 1. Published files and their sources

| Published path | Private source | Transformation |
|---|---|---|
| `SKILL.md` | `component-upgrade-assessment/SKILL.md` | translated to English + de-proprietarised (harness-specific paths, commands and vendor names replaced with the generic equivalent); section numbering normalised |
| `references/case-study-clickhouse-upgrade.md` | `component-upgrade-assessment/references/clickhouse-upgrade-case.md` **and** `infrastructure-upgrade/references/clickhouse-26.5-to-26.7-upgrade-plan.md` | **genericised (host artifacts only) + expanded — a two-source synthesis** (see §3) |
| `references/platform-self-upgrade-mechanics.md` | `component-upgrade-assessment/references/host-update-execution-mechanics.md` | renamed at publication (the private name is host-specific); de-proprietarised |
| `scripts/probe-config-migration-survival.py` | `component-upgrade-assessment/scripts/probe-config-migration-preset-survival.py` | renamed at publication |
| `scripts/recon-upgrade-surface.sh` | `component-upgrade-assessment/scripts/probe-host-upgrade-surface.sh` | renamed at publication |

Private-file names are given without their private category prefix, and the file names
below are relative to the skill that holds them.

The private tree holds **three** records of the same upgrade (the assessment case file, the plan
document, and the skill body). Only the assessment case file has a published counterpart; the plan
document appears only as the expansion source of the case study.

## 2. Deliberately not published

Three private files have no published counterpart. They are operational notes about one specific
harness and its host, not upgrade methodology — and the published copy therefore contains **no
pointer to them** (verified: zero mentions of either name anywhere under this directory).

| Private file | Why it is held back |
|---|---|
| `references/hermes-agent-release-research.md` | release research for one specific agent harness |
| `references/honcho-deepseek-structured-output.md` | vendor-specific structured-output workaround |
| `scripts/probe-honcho-model-slots.py` | probes one deployment's model-slot layout |

## 2b. The four placeholders in the case study

Declared here so a fidelity comparison can read them: the published case study will report
private-side values as "not present" — that is what these masks are for.

| Placeholder | Masks | Why it is not published |
|---|---|---|
| `APP_DIR` | the absolute path of this Compose stack | it carries the operator's home directory |
| `CONTAINER_NAME` | `langfuse-clickhouse-1` (Compose-derived container name) | host artifact; a reader substitutes their own |
| `VOLUME_PATH` | `langfuse_clickhouse_data` (Compose-derived volume name) | same |
| `APP_HOST` / `APP_PORT` | the local address the app is reached at | site-specific |

## 3. What the case study masks, and why the fidelity check is tricky here

The products are **named**: ClickHouse is the component and Langfuse is the application that depends
on it — both are public, and a case study whose subject cannot be named teaches nothing. What is
masked is **this deployment's own shape**: the compose stack directory (`APP_DIR`), the ClickHouse
container (`CONTAINER_NAME`) and its data volume (`VOLUME_PATH`). The file was then **expanded** with
mechanism-level detail taken from the plan document, so it is a synthesis of two private files, not a
copy of one.

This matters when checking fidelity: **the transformation target is the identifier itself**, so a
provenance check that matches on *identifiers* produces false negatives here (measured: only 4 of 13
identifiers survived, which reads as "no single source" when the source is actually known). Match on
**values that must survive instead** — version numbers, ports, issue ids, table names, capacity
figures. The evidence that both files describe the same run:

- same event: single-node ClickHouse with an embedded Keeper, `26.5.1 → 26.7.1`;
- same numbers: ~31 MB compressed total; 12 tables; `observations` 27 MB / `traces` 3 MB;
- same configuration decisions: a 14-day TTL driven by `clickhouse-system-ttl.xml`; Keeper on `9181`;
- same ruled-out hypothesis: upstream issue `#103398`;
- the private source states its own provenance ("two rounds of independent verification plus an
  adversarial re-check"), and the published copy repeats that claim — both describe the same
  verified run, not two similar ones;
- the expansion is traceable: `mergeTreeAnalyzeIndexes`-level detail and the 79 / 366 / 117 figures
  exist in the plan document, not in the case file.

One factual correction landed in the same pass: the health endpoint is `/api/public/health`
(Langfuse's public API path, per the private source) — an earlier published revision had it as
`/api/health`, which does not match the source.

## 4. Maintenance rule

Treat the published copy as a snapshot with a recorded derivation, not a mirror:

1. Before publishing an update, re-derive from the private source rather than hand-editing the
   public copy — a hand-edited public file immediately loses its provenance.
2. When a private file is renamed, update §1 in the same change; a stale mapping is what makes a
   later reader believe a file was lost.
3. Keep the "deliberately not published" list honest. A file that is merely *pending* publication
   belongs in that list only with that status named, never as a deliberate exclusion.
