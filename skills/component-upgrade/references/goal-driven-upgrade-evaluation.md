# Evaluating "will upgrading deliver objective X?"

## When it applies

The user is not asking "can this be upgraded" but "will the upgrade bring a **quantified benefit**"
(cut cost, go faster, save space) or "will it fix a specific problem". "Will upgrading to the latest
version cut cost?" is the canonical shape — what is wanted is a **verdict**, not a plan.

## Shape of the answer (verdict first)

Give "yes / partly / no" in the first sentence and name the real lever; only then present the evidence table.
**Do not retell the changelog as a sales pitch** — that makes the user think you are recommending the upgrade.

## Steps

1. **Pin the local version, read it from code**: `git describe --tags`, the `version` in `pyproject.toml`.
   Do not trust version lines in docs or dashboards.
2. **Read the upstream changelog over a raw URL, not the git protocol**:
   `https://raw.githubusercontent.com/<owner>/<repo>/main/CHANGELOG.md`
   (`git fetch` hits link flakiness and stale locks; raw depends only on HTTPS, which makes it the most
   reliable basis for a "where is upstream now" verdict.)
3. **Build a "version → line number" map, then attribute entries by line number** — this step decides
   whether the verdict is right:

   ```bash
   grep -n '^## \[' CHANGELOG.md            # version headings → line numbers (confirm oldest→newest or newest→oldest first)
   sed -n 'A,Bp' CHANGELOG.md               # read your local version's own section
   grep -n -iE '<target keywords>' CHANGELOG.md    # which line range the candidate entry falls in ⇒ which version owns it
   ```

   ⚠️ An entry counts only if its line number falls **between two version headings**; grepping keywords
   alone mis-attributes it to the wrong version.
   This family of changelogs is ordered **newest → oldest** (smaller line number = newer version).
   **The most valuable verdict is often "that optimisation is already in your local version"** — it
   prevents a wrong recommendation.
4. **Classify each entry against the objective**: saves / neutral / **costs more**. The third bucket is
   the easiest to miss (e.g. raising an output cap for some class of response *adds* tokens; that is not a
   cost reduction). New features unrelated to the objective (new backends, new integrations, new
   protocols) never count.
5. **Cross-check against the measured share of the objective's own metric**: if the **bulk consumer** of
   that metric has no corresponding upstream change at all, the expected benefit of upgrading is zero —
   say so, instead of hedging with "it might help a little".
6. **Give the cost of the upgrade itself as its own paragraph** (none of it is free):
   - the `requires-python` floor (check per tag / per version — it can jump between minor releases)
   - DB migrations: index size multiple, whether they run `CONCURRENTLY`, and the failure shape after a
     timeout (do they leave invalid objects behind?)
   - **breaking config renames**: old keys are ignored silently ⇒ the env must be migrated; grep locally
     for the old key first
   - the files in the local uncommitted-changes list (`git status --short`) that will conflict
7. **Offer the levers that reach the objective without changing the version** (with numbers) — that is
   the path that actually gets there.
8. **Honest boundary**: state that what you read are **version-level** changelog entries, not a
   commit-by-commit diff, and name the path to go deeper (the source directories/files worth diffing),
   to be taken once the user agrees.

## Traps

| trap | how it shows up | prevention |
|---|---|---|
| Treating an upstream "optimisation" as something you lack | a wrong "upgrading will cut cost" verdict; the user follows it and sees no change | attribute entries to versions per step 3 before comparing against your local version |
| Grepping keywords without attributing by range | a new version's entry counted against an old version | bracket with the version-heading line numbers; confirm newest→oldest vs oldest→newest first |
| Using the changelog as the only evidence | entry text that does not match the real code behaviour | for a hard verdict read the **target version's** source (raw URL, or `git show <tag>:<path>`), or diff the target path |
| **`fetch` not erroring ≠ the ref updated** | after a real `git fetch`, `origin/main` is still **a month old**, so "N commits behind" is badly understated (upstream may already have new tags) | cross three sources for "where is upstream now": the date in `git log -1 --format=%ci origin/main`, the GitHub API `/commits?per_page=1` and `/tags?per_page=5`, and `git ls-remote`. If the dates disagree, trust the API (it does not go over the git protocol) |
| Sliding from "can it deliver the objective" to "how to upgrade" | the user wanted a verdict and got a whole upgrade plan | verdict first; produce a plan only when the user explicitly says to execute |

## Reporting shape (what the user wants)

- First sentence: the verdict + where the real lever is.
- Evidence table: one row per change × its effect on the objective metric (including the "costs more" bucket).
- Cost paragraph: the Python floor / migrations / config renames / local-change conflicts.
- Closing: the quantified "reach the objective without upgrading" items + the honest boundary (say so if
  going deeper needs a source diff, and wait for the user's go-ahead).
