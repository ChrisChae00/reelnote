# reelnote Mac pipeline: execution record

Plan: `docs/superpowers/plans/2026-10-17-reelnote-mac-pipeline.md`
Method: subagent-driven development, as chosen by the project owner.
Branch: `main`, with direct commits as the owner asked. Each batch is pushed after its review passes.

## How work is split

A **batch** is one implementer subagent plus one task reviewer (spec compliance and code quality). Review findings go back to the same implementer for up to 3 fix rounds. If findings are still open after that, a fresh implementer on a stronger model takes rounds 4 and 5. After all batches, one reviewer on the most capable model reviews the whole branch.

Tasks share a batch when they are the same kind of work and a reviewer can judge them together. A task gets its own batch when it carries a distinct risk that deserves an undivided review.

| Batch | Tasks | Runs in | Why it is grouped or split this way |
|---|---|---|---|
| Spike | 0 | Controller (inline) | Produces findings, not product code. It needs this Mac's real logins (Claude, Codex, Grok), network access, and judgment about what the output means. A subagent would start cold and re-derive all of that. Its findings go into later briefs. |
| A | 1–4 (scaffold, config, shortcode, inbox, categories, store) | One subagent, cheap model | Four small, pure modules of the same shape (a module plus its unit tests). No external tools. The plan contains the complete code, so the work is transcription plus testing. Splitting them would cost four cold starts and four reviews for about 300 lines of foundation code. |
| B | 5 (yt-dlp fetch, transcript, OCR, DM page text) | Own subagent | The boundary to external binaries and the network. Its risk is wrong error mapping (login wall vs. deleted video vs. crash), which decides whether a user's item retries, becomes a stub, or fails. It depends on the spike's yt-dlp findings. |
| C | 6 (AI provider chain) | Own subagent | Security-relevant: it launches agentic CLIs (`claude`, `codex`, `grok`) with the user's subscriptions, and each must run in an empty directory. It also owns the usage-limit and fallback logic that decides whether work is lost or retried. It depends on the spike's output-envelope findings. |
| D | 7–8 (embeddings and grouping, vault writing) | One subagent | Both shape what the user sees in Obsidian: grouping decides which hub a note joins, and the vault writes notes and hubs. Task 7 is small (two short files). The main risk is in Task 8, overwriting the user's hand edits, and the same reviewer can judge both together. |
| E | 9 (data repo git sync) | Own subagent | The only code that pushes to the private data repo, and the main data-loss risk. It handles rebase conflicts with the phone's concurrent commits. It deserves a reviewer focused only on git semantics. |
| F | 10–11 (pipeline, CLI, launchd, setup docs) | One subagent, standard model | The integration layer that wires every earlier module together. The CLI is a thin wrapper over the pipeline, and launchd and the docs only invoke the CLI. Splitting them would make the second reviewer re-learn the whole pipeline. |
| Final | whole branch | Most capable model | Cross-module review: interfaces between batches and the deferred minor findings. |

Batch C runs before Batch B because B's `yt-dlp` check waits on a real Reel URL from the owner, and C does not depend on B.

Batches run one after another, never in parallel. They all commit to `main`, and the later ones import the earlier modules.

## Decisions made during execution

The rulings below were made where the plan or spec left a gap. Each entry gives the decision, the reason, and the cost if the decision is wrong.

- **Frontmatter `url` uses `/p/<code>/`** instead of the spec example's `/reel/<code>/`. `/p/` opens both reels and photo posts. Cost if wrong: a cosmetic URL difference.
- **OCR text is deduplicated across frames**, instead of dropping near-duplicate frames as the spec describes. It serves the same goal more simply. Cost if wrong: about one OCR call per second of video, so runs are slower.
- **A provider that hits its usage limit waits a fixed `cooldown_minutes`.** The spec asks for the provider's reset time, but the CLIs don't print one in a parseable form. Cost if wrong: a provider can sit idle up to an hour after its limit resets.
- **Grok removed (owner decision).** Its `strict` and `read-only` sandboxes refuse to start on macOS when `/var/run/docker.sock` is a symlink. The looser `workspace` sandbox would let injected Reel text make it read local files.
- **Pinned hubs are protected from `regroup`** (their name and membership). New notes can still join them through the per-note match. Cost if wrong: pinned hubs gain new members.

## Batch results

| Batch | Commits | Review |
|---|---|---|
| A (Tasks 1–4) | `ff97aad..4b5403f` | Spec ✅. 4 Important findings, all in plan-given code: 3 fixed in 1 round (unknown-code `KeyError`, atomic `fail_attempt`, `providers` validation), 1 parked (`note_path` cannot be cleared; no caller needs it). 38 tests. |
| C (Task 6) | `978519e..9182cb8` | Spec ✅. Review: the login check misread notes that mention "login" (fixed). Background security review: prompt injection could reach CLI tools, since Reel text and DM pages are untrusted. Fixed: Codex `--sandbox read-only`; Grok `--sandbox workspace`, shell/edit/web tools removed, `--max-turns 1`; Claude already had `--tools ""`; the prompt marks sources as untrusted. Grok's `strict` profile cannot start on this Mac (Docker socket symlink), so it uses `workspace`. CLI timeouts now move to the next provider. 2 fix rounds, 54 tests. |
| C follow-up | `1d9d138` | Owner decision after the background security review flagged the weaker Grok sandbox: **Grok removed entirely**. Providers are now `claude` → `codex`. README and spec updated. Re-review clean. |
| D (Tasks 7–8) | `598d4a8..66ad8c6` | Spec ✅. 5 Important, fixed in 1 round: frontmatter is now kept as text, so the user's `[[links]]` and comments survive; line-anchored section headings; broken hand-edited YAML no longer stops the run; atomic writes; hub markers checked; HDBSCAN warning and zero-vector crash fixed. 76 tests. |
| E (Task 9) | `fa48fa2..c683d6c` | Spec ✅ except one gap: nothing may be lost. 1 Critical, reproduced: `--autostash` could commit conflict markers into notes. Fixed: no autostash, a dirty tree is committed first, an unfinished rebase is recovered. Also fixed: push errors are surfaced, the rebase loop is capped at 50 steps, path parsing uses `-z`. 2 rounds; the second strengthened two tests that did not prove their claims. Incident: the unreviewed `fa48fa2` was pushed early, most likely by the reviewer's experiment; the private data repo was untouched. Every later subagent prompt forbids `git push`. 85 tests. |
| B (Task 5) | `cd12139..fae4560` | Spec ✅. Background security review: SSRF, since DM links from strangers could reach localhost or the LAN. Fixed: http(s) only, private, loopback and link-local addresses blocked on every redirect hop, 10 MB cap. DNS rebinding accepted as a residual risk. Review: 7 Important, fixed in 1 round: per-attempt workdir cleanup, `yt-dlp` errors classified on the `ERROR:` line only (a generic error no longer becomes a permanent stub), clear `ffmpeg`/`whisper` errors, silent Reels get an empty transcript, `--` before the URL. A login wall after the cookie retry stays a retried failure, not a stub. 111 tests. The real-video check still waits on the owner's Reel URL. |
| B security follow-up | `1d11295..d592eba` | The background security review flagged SSRF 3 more times. Fixed in rounds 2–4: connect only to the IP that was validated (no DNS rebinding), ignore proxy settings, allow only global addresses (CGNAT `100.64/10` and others used to slip through), unwrap NAT64/6to4/IPv4-mapped addresses, allow IPv6 only in `2000::/3`, remove the `file`/`ftp`/`data` handlers. Round 4 went to a fresh, stronger implementer (Opus). Live check: public HTTPS fetched; localhost, CGNAT, NAT64 loopback and `file://` blocked. 123 tests. The rebinding "accepted risk" ruling was withdrawn. |
| F (Tasks 10–11) | `3e6189d..7b6fffb` | Spec ✅ (`run_once` had no test in the plan). 6 Important, fixed in 1 round: per-item isolation, so one bad item never blocks the run or the push; no duplicate memos on merge; a written note can't be flipped to `failed` by hub errors; inbox processed in timestamp order with renames interleaved; a missing `categories.md` no longer wipes categories; explicit PATH in launchd; the CLI reports sync errors cleanly. 2nd push incident: the pre-fix commits reached `main` during review, source unknown. 145 tests. The smoke test against a real Reel waits on the owner's URL. |
| Final review | `4bb81d5..047f0fe` | Opus whole-branch review, verdict *with fixes*. 1 Critical: a phone rename was treated as a delete and wiped manual categories. 6 Important: memos landed in the wrong note after a hand rename or delete; an outage failed the whole inbox; a non-categories conflict stalled silently; path traversal via `created_at`; data exfiltration through markdown images; invalid items missing from review. |
| Final fix wave | `899691c..9323a1e` | One fix dispatch (Opus) for the full list. 174 tests. Tests also stopped showing real macOS notifications, after the owner saw one. Re-review: everything fixed except one C1 case, a save stalled across runs when a rename lands. That case is parked and passed to the owner, because the process allows only one fix wave. |
| Smoke test | — | First real Reel run end to end against a local copy of the data repo: 1 note written in about 60 s, category auto-picked, OCR corrected whisper's mishearings. `yt-dlp` field names verified, so Task 0 and Task 11 Step 5 are done. |
| Residual fix (owner approved) | `abc7c96..5361d2c` | Remaining C1 case fixed: a category rename also rewrites matching saves still waiting in the inbox, so a manual category survives a stall across runs. Phone-written files (inbox JSON, `categories.md`) are rewritten atomically as UTF-8. README says to stop launchd before resolving a conflict by hand. 2 fix rounds. 178 tests, and the new tests fail on the old code. |
