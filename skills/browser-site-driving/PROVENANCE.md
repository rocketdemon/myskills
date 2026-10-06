# Provenance

This skill is a **derived publication**: a translated and genericised copy of a private skill, with
one of that skill's files deliberately withheld. This file records the relation so that a reader —
and the author — can tell a deliberate transformation from a content loss.

Scope note: nothing is merged back and no private file is overwritten. The public copy is a snapshot
of what was true when it was published; the private skill keeps evolving on its own.

## 1. Published files and their sources

All nine published files come from the private copy of this same skill, at the same relative path.

| Published path | Transformation |
|---|---|
| `SKILL.md` | translated to English; frontmatter reduced to this repository's majority shape (`name` / `description` / `trigger`); the private name's directory caveat dropped because the public name is ASCII; host-absolute paths → `~` |
| `references/wsl-headless-chromium-harness.md` | translated; host-absolute paths → `~` |
| `references/clone-remote-object-via-api.md` | translated; host paths → `~`; target site name → placeholder |
| `references/readonly-api-extraction.md` | translated; host paths → `~`; target site name → placeholder |
| `scripts/probe-cookie-persistence.py` | comments and messages translated; code byte-identical |
| `scripts/analyze-spa-bundle.py` | comments and messages translated; code byte-identical |
| `scripts/fetch-spa-chunks.sh` | comments and messages translated; code byte-identical |
| `scripts/verify-project-clone.js` | comments and messages translated; code byte-identical; target site name → placeholder |
| `scripts/clone-project-to-target.js` | comments and messages translated; code byte-identical; target site name → placeholder |

Neither side keeps the other in sync automatically: the private copy stays in its original language
and is what the author actually uses.

## 2. Deliberately not published

| Private file | Why it is held back | Status |
|---|---|---|
| `references/<target-site>.md` (the private file is named after the site, hence the placeholder) | a dossier of **one third-party commercial platform**: its page map, endpoint map, route table, per-account quotas, and a login walkthrough with an account identifier. High exposure (someone else's interface surface plus an account record), low general value (site-specific, not method) | **deliberately not published** — not "pending" |

The private skill also has an on-disk research note for that site (kept outside the skill directory,
alongside the author's other working notes). It is not published either, for the same reason.

## 3. Placeholders — what each one masks, and why

Declared here so that a fidelity comparison against the private source can read its own output: the
private-side values behind these masks will be reported as "not present in the public copy". That is
what they are for.

| Placeholder | Masks | Why it is masked |
|---|---|---|
| `<target-site>` | the real hostname of the site the private copy was exercised against | publishing a *named* third-party platform together with its endpoint and quota specifics is exactly the exposure this skill avoids; the transferable part is the method, not the site |
| `<account-identifier>` | a phone-number-style account identifier appearing in the private copy's login walkthrough | it is a personal identifier, not method |
| `<project-name>` | the name of the object that was cloned during the private run | it is the operator's own content; the method does not need it |

**Not masked, on purpose:** product names that are public (Hermes, Chrome, Playwright, CDP,
Vue/Vite, Element Plus), the harness's own paths (`~/.hermes/...`), the standard DevTools endpoint
(`127.0.0.1:9222`), and the browser User-Agent string including its version digits. A version string
inside a User-Agent is not an IP address, and a project name is not a person's name; both are
occasional false positives of a shape-based scanner and are kept verbatim so the examples remain
copy-pasteable.

## 4. Maintenance rule

Treat the published copy as a snapshot with a recorded derivation, not a mirror:

1. To publish an update, re-derive from the private source instead of hand-editing the public copy —
   a hand-edited public file immediately loses its provenance.
2. If a private file is renamed, update §1 in the same change; a stale mapping is what makes a later
   reader believe a file was lost.
3. Keep §2 honest about status: a file that is merely *pending* publication must say so, and must
   never be written up as a deliberate exclusion.
4. Each public release of a translated copy should re-run the repository's language and fidelity
   gates rather than trusting this table: the table explains *why* a difference exists, it does not
   prove that no unintended difference was introduced.
