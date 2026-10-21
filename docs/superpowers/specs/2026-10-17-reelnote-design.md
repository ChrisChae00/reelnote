# reelnote: design spec

Date: 2026-10-17
Status: draft, awaiting review

## 1. Goal

Instagram Reels and Shorts often carry useful information: app lists, recipes, tips. Creators also send extra links by DM to people who comment. Saving a Reel inside Instagram makes it hard to find again, and after many saves the user forgets what was kept.

reelnote lets the user send a Reel link, plus an optional DM link, from the phone in one step. The Mac then reads the video and writes a searchable Obsidian note. Similar notes are grouped so that reading one reminds the user of the related ones.

### Success criteria

1. Any saved video can be found again by searching what it said or showed.
2. Duplicate or similar notes are grouped into one topic hub.
3. The DM link sent with a video ends up on the same note as the video.
4. Nothing sent from the phone is lost, even if the Mac is off for days.
5. No new recurring cost.

### Constraints

- Personal use only. Runs locally; nothing is deployed.
- AI runs only through CLIs the user is already subscribed to. No pay-per-use API.
- Code is public. All personal data lives in a separate private repo.

### Out of scope

- Bulk import of past Instagram saves (the "Download your information" export).
- Matching a DM link sent on its own to an earlier video. A DM link is accepted only together with its video link.
- A "DM expected" toggle and a sender-account field.
- Gemini, Grok, the xAI API, and Cursor as AI providers.
- Browser automation of the Instagram "Saved" list. It violates the ToS and risks an account ban.

## 2. Architecture

```
iPhone                           GitHub (private)                 Mac (when on)
──────                           ────────────────                 ─────────────
Instagram ─Share─▶ Scriptable ──▶ reelnote-data/inbox/*.json ──▶ launchd ─▶ reelnote run
                   share UI   ──▶ reelnote-data/vault/categories.md        │
                                                                           ▼
Obsidian (Obsidian Git) ◀────── reelnote-data/vault/  ◀──────── notes, hubs, review.md
```

- **Two repos.** `reelnote` (public) holds code and docs. `reelnote-data` (private) holds `inbox/` and `vault/`.
- **The queue is the private repo.** The phone creates one file per item through the GitHub Contents API. Items wait there with no time limit while the Mac is off. iCloud is not used, so a full iCloud plan cannot stop the queue.
- **The vault lives in the same private repo.** The phone reads it in Obsidian through the Obsidian Git plugin.
- **The phone talks only to GitHub, never to the Mac.**

## 3. Phone: share UI

### 3.1 Technology

- The UI is HTML/JS inside a Scriptable `WebView`, presented from the iOS share sheet.
- The prototype at `docs/prototype/share-sheet.html` is the starting code for the real UI.
- Each action (save, create, rename, delete) is sent to Scriptable as a navigation to `reelnote://m?<json>`, which Scriptable intercepts with `shouldAllowRequest` and answers by calling `bridgeReply` in the page. Scriptable makes all GitHub API calls itself, so the WebView never sees the token. The UI waits for each reply, so "저장됨" means the commit landed.
- `evaluateJavaScript(js, true)` is not used: on iOS it left the page blank (spike, 2026-10-20).
- After a save in the share sheet, Scriptable calls `Script.complete()` to close it.
- The GitHub token is a fine-grained PAT scoped to `reelnote-data` with Contents read/write only. It is stored in Scriptable's `Keychain` and never in the script file.
- **Fallback:** if the spike in §8 shows the WebView cannot run inside the share extension, use Scriptable's native `UITable` and `Alert`. The flow stays the same, and only the visual layer changes.
- **Later:** a SwiftUI app with a Share Extension (Apple Developer Program, $99/year) can replace Scriptable. The inbox format stays the same.

### 3.2 Screens and flow

1. **Category screen (first screen).**
   - "자동 분류" (auto) is pinned on top.
   - Below it, the user's categories appear as a stack ordered by most recently created or used. The list scrolls.
   - Each row has an `edit` button.
   - The "새 카테고리" (new category) button sits at the top right.
   - Tapping a category goes straight to the save screen.
2. **Edit sheet.**
   - Rises from the bottom. Contains a rename field, "이름 저장" (save name), and "카테고리 삭제" (delete category).
   - Delete asks for confirmation inline and states how many notes will be reclassified.
3. **New category sheet.**
   - Contains a name field and "만들고 선택" (create and select).
4. **Save screen.** Video link (prefilled), DM link and memo (optional), and optional tags. Tags in use are shown as chips with their note counts, followed by the category names; the chosen category is not offered. A new tag can be typed in. Tags follow Obsidian's rule: spaces become `_`, only letters, digits, `_`, `-`, `/`, and not all digits.
   - On create, the category goes to the top of the stack and the save screen opens with it selected.
4. **Save screen.**
   - Shows the selected category as a chip.
   - Video link: required. Prefilled from the shared URL.
   - DM link: optional.
   - Memo: optional.
   - Tapping "저장" (save) changes the button to "저장됨" (saved), then returns to the list. The used category moves to the top of the stack, and the share sheet closes.

### 3.3 Interaction rules (Apple fluid-interface principles)

| Rule | Applied as |
|---|---|
| Respond on press | Rows and buttons give feedback on pointer-down. Pointer-up commits. |
| Springs, not fixed curves | One spring takes Apple's damping ratio and response. Default damping is 1.0 (no bounce). Damping 0.8 is used only after a flick (sheet release). Push/pop uses response 0.38 and sheets use 0.3–0.36. |
| Interruptible | Every animation starts from the live on-screen value and velocity. A closing sheet or popping screen can be grabbed mid-flight. |
| Direct manipulation | Sheet drag from the grabber and edge-swipe back on the save screen track the finger 1:1 with pointer capture. |
| Velocity handoff + projection | On release, the spring inherits the finger's velocity. The target is chosen from the projected end point (`v/1000 · d/(1−d)`, d = 0.998), not from the release point. |
| Rubber-banding | Dragging a sheet up, or the save screen past its open position, meets increasing resistance. |
| Spatial consistency | The save screen enters and leaves from the right. Sheets enter and leave from the bottom. |
| Depth | An open sheet dims the background and scales it to 0.94 (a modal task). The nav and bottom bars are translucent materials that appear only when content scrolls under them. |
| Typography | System font. Large title at 34pt with −0.024em tracking. Body text uses near-zero tracking. |
| Feedback | Validation runs inline while typing: video URL format, DM URL format, duplicate category names. A status banner confirms each save, rename, and delete. |
| Accessibility | `prefers-reduced-motion` replaces slides and springs with a 200 ms fade. `prefers-reduced-transparency` makes the bars solid. Keyboard focus is visible. Esc closes a sheet, and focus returns to its trigger. |

### 3.4 Category storage

- `vault/categories.md` holds one category per line. Line order is the stack order, and lines starting with `#` are comments.
- **Use or create:** the phone moves the line to the top (or inserts it) and commits the file through the API.
- **Delete:** the phone removes the line. On every run, the Mac diffs the file against the last-seen list in SQLite and reclassifies notes whose category disappeared. This works the same when the user edits the file in Obsidian.
- **Rename:** the phone edits the line and also writes `inbox/<id>.json` with `{"op":"rename_category","from":"A","to":"B"}`. The Mac then rewrites every note's frontmatter. A rename done by hand in Obsidian looks like a delete plus an add, so that case is only supported from the phone. This limit is documented in `categories.md`'s header comment.
- **Note counts in the UI:** the Mac writes `vault/.reelnote/category-counts.json` after each run, and the phone reads it.

## 4. Inbox format

One file per save: `inbox/<YYYYMMDD-HHMMSS>-<4 random hex>.json`.

```json
{
  "op": "save",
  "video_url": "https://www.instagram.com/reel/C8xYz2Qp/?igsh=...",
  "dm_url": "https://example.notion.site/...",
  "memo": "for setting up the new Mac",
  "category": "생산성",
  "created_at": "2026-10-17T18:30:12+09:00"
}
```

- `dm_url` and `memo` may be `null`.
- `category` is a name from `categories.md`, or `"auto"`.
- The Mac deletes an inbox file in the same commit that writes its note. If processing fails, the file stays in the inbox.

## 5. Mac: pipeline

### 5.1 Layout

```
reelnote/
  phone/              Scriptable script + share UI HTML
  src/reelnote/
    inbox.py          pull data repo, read inbox, commit/push results. The only module that knows the queue is git.
    shortcode.py      canonical URL -> shortcode (/reel/, /reels/, /p/ are equal; tracking params dropped)
    fetch.py          yt-dlp: video + caption; optional --cookies-from-browser
    extract.py        whisper.cpp transcript; ffmpeg frames (2 fps, up to 600 = 5 min, near-duplicates dropped) -> Vision OCR; DM page text (HTML main text, PDF text)
    llm.py            provider chain: claude -p -> codex exec; JSON-schema validation; limit detection
    index.py          SQLite: item state, embeddings (Ollama bge-m3), similarity queries
    organize.py       hub assignment; `regroup` re-clusters unassigned notes (HDBSCAN); pinned hubs never change
    categories.py     parse categories.md, diff against last-seen, apply rename ops
    vault.py          write notes and hubs; preserve user edits
    cli.py            reelnote run | regroup | status
  launchd/com.reelnote.run.plist
```

- **Runtime:** Python managed with `uv`.
- **External tools:** `yt-dlp`, `ffmpeg`, `whisper.cpp` (large-v3-turbo, Metal), Ollama (`bge-m3`). macOS Vision is reached through PyObjC.

### 5.2 Per-item flow

1. Canonicalize the URL to a shortcode.
   - **Already known:** append the memo and DM link to the existing note and stop.
   - **New:** continue.
2. Download the video and caption.
   - **Unavailable** (deleted or private): write a stub note with `status: unavailable` and stop.
3. Extract the transcript, the on-screen text, and the DM page text (if `dm_url` is set).
4. Generate the note JSON through the LLM chain: title, summary, `content` (the information reorganized in Markdown: `###` groups, bullets, tables, a Mermaid flowchart for a process), key facts (one line each, used by hubs), keywords, 0–3 tags picked only from tags already in use or category names, and a category when the item says `"auto"`. The on-screen text is given to the model but not kept in the note.
5. Embed the summary and compare it with existing notes.
   - **Very high similarity:** mark it as a duplicate candidate in `review.md` and link it to the original. Nothing is merged automatically.
   - **Medium:** attach it to the nearest hub.
   - **Low:** leave it unassigned.
6. Write the note, update the hub, delete the inbox file, commit, and push.

Thresholds for step 5 are tuned on real data and stored in config.

### 5.3 AI provider chain

- **Order:** `claude -p` (Claude Pro), then `codex exec` (ChatGPT Plus). Grok was removed: its strict sandbox cannot start on macOS, and untrusted Reel text must not reach a CLI that can read local files.
- **Common contract:** every provider gets the same prompt and must return JSON matching one schema. Claude enforces it with `--json-schema`, Codex with `--output-schema`; results are validated after the call.
- **Usage limit:** the provider is skipped until its reset time, which is stored in SQLite. Once that time passes, the chain returns to Claude first.
- **Invalid JSON:** one retry on the same provider, then the next provider.
- **All providers limited:** the item stays at `extracted` and is retried on the next run.

### 5.4 Scheduling

- `launchd` runs `reelnote run` at login and every minute. Each run first asks GitHub for the remote HEAD (`git ls-remote`). It starts the full run only when HEAD changed or 15 minutes have passed since the last full run, which retries items left in the inbox. When GitHub cannot be reached (no Wi-Fi yet after wake), the run exits quietly and the next minute tries again; no notification is shown.
- The phone writes to GitHub, not to the Mac, so a local folder watch is not possible. The one-minute `ls-remote` check is the trigger.
- A lock file prevents overlapping runs.

## 6. Vault format

```
vault/
  categories.md
  notes/(<category>) <title>.md     (미분류) when there is no category; renamed when the category changes
  hubs/<topic>.md
  review.md                       duplicate candidates, failures, unavailable videos
  .reelnote/category-counts.json
  .reelnote/tag-counts.json         tags in use, read by the phone
```

Note frontmatter:

```yaml
shortcode: C8xYz2Qp
url: https://www.instagram.com/reel/C8xYz2Qp/
category: 생산성
category_source: manual   # manual | auto
tags: [AI, 이력서]          # the user's tags plus tags the model reused; never the main category
hub: "[[Mac productivity apps]]"
status: done              # done | review | unavailable
dm_links: [https://example.notion.site/...]
saved_at: 2026-10-17
```

Note body sections: Summary, Details (the model's `content`), DM info, My memo, Source (caption, transcript).

Rules that protect hand edits:
- A note is written in full only once.
- After that, the Mac changes only frontmatter keys it owns (`category` when `category_source: auto`, `tags` (only adds), `hub`, `status`, `dm_links`) and the `(<category>)` filename prefix and appends to the "DM info" and "My memo" sections.
- Hub notes keep generated content between `<!-- reelnote:start -->` and `<!-- reelnote:end -->`. Everything outside the markers belongs to the user.
- `category_source: manual` is never overwritten. The one exception is when the category is deleted; the note then becomes `auto` and is reclassified.

## 7. Error handling

- Each item has a stored state: `queued`, `fetched`, `extracted`, `summarized`, `written`, or `failed`. A run resumes from the last finished state.
- An item is retried 3 times across runs. After that it is marked `failed` and listed in `review.md` with the error. No item disappears silently.
- If `yt-dlp` hits a login wall, it retries with `--cookies-from-browser`.
  - Cookies never leave the Mac and are covered by `.gitignore`.
  - Downloads are spaced several seconds apart to keep the account safe.
- If a push is rejected because the phone committed meanwhile: pull with rebase, then retry. Phone and Mac never edit the same inbox file. `categories.md` is the only shared file, and on a conflict the phone's version wins.
- A DM page that needs a login or fails to load: the note still gets written, the DM link is kept, and the item goes to `review.md`.

## 8. Testing

- **Unit (pytest):**
  - shortcode canonicalization
  - `categories.md` parse, diff, and rename
  - inbox parsing and validation
  - hub marker merge (hand edits preserved)
  - provider chain fallback, using fake CLIs on `PATH`
- **Integration:** one local sample video through the full pipeline. Network, git push, and AI are replaced by fakes.
- **Spikes before implementation:**
  1. Scriptable presents the share UI WebView from the share sheet, and the JS→Scriptable result handoff works.
  2. `yt-dlp` downloads a Reel without cookies, and with `--cookies-from-browser`.

## 9. Cost

| Item | Cost |
|---|---|
| GitHub private repo, Scriptable, Obsidian, Obsidian Git | free |
| yt-dlp, ffmpeg, whisper.cpp, Ollama + bge-m3, macOS Vision | free |
| AI (Claude Pro, ChatGPT Plus) | existing subscriptions |
| Optional later: Apple Developer Program for a native app | $99/year |
| Optional later: Obsidian Sync instead of Obsidian Git | about $4–5/month |
