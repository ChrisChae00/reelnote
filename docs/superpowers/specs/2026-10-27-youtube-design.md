# reelnote: YouTube support and Chrome extension

Date: 2026-10-27
Status: draft, awaiting review
Extends: `2026-10-17-reelnote-design.md`. Everything not stated here stays as that spec says.
Decision record: `docs/decisions/0001-youtube-via-chrome-extension.md`

## 1. Goal

Save YouTube videos (usually up to 30–40 minutes, Shorts included) into the same vault as Instagram Reels. The notes share categories, tags, hubs, and search.

Two ways to save:

- **Chrome extension** on any computer: click the toolbar icon while a YouTube video is open.
- **Phone:** share from the YouTube app to the existing Scriptable script.

### Success criteria

1. A 40-minute YouTube video becomes one note that covers the whole video, not only its start.
2. When the video has human-made captions, they are the transcript and whisper does not run.
3. The extension and the phone write the same inbox format. The Mac stays the only processor.
4. Instagram processing is unchanged: same settings, same notes.
5. No new recurring cost.

### Out of scope

- Playlists and channels. One video per save.
- Live streams that are still running.
- Members-only videos, unless the Mac's `cookies_browser` can see them.
- Publishing the extension on the Chrome Web Store. It is loaded unpacked.
- Scraping captions from the YouTube page (decision 0001, option B).

## 2. Architecture

```
Chrome (any computer)                  GitHub (private)                 Mac (when on)
─────────────────────                  ────────────────                 ─────────────
YouTube tab ─icon─▶ extension popup ──▶ reelnote-data/inbox/*.json ──▶ launchd ─▶ reelnote run
iPhone YouTube app ─Share─▶ Scriptable ─▶        (same format)                   │
                                                                                 ▼
                                                                  Instagram path │ YouTube path
```

- The extension talks only to GitHub, never to the Mac.
- The Mac picks the path from the URL host.

## 3. Item identity

| Source | Accepted URLs | Item code |
|---|---|---|
| Instagram | as today (`/reel/`, `/reels/`, `/p/`, `/tv/`) | shortcode, unchanged |
| YouTube | `youtube.com/watch?v=<id>`, `m.youtube.com/watch?v=<id>`, `youtube.com/shorts/<id>`, `youtu.be/<id>`, `music.youtube.com/watch?v=<id>` | `yt-<id>` |

- A YouTube id is 11 characters of `[A-Za-z0-9_-]`. Anything else is rejected.
- Query parameters other than `v` (`t`, `list`, `si`, `feature`) are dropped. The canonical URL is `https://www.youtube.com/watch?v=<id>`.
- `shortcode.py` becomes `source.py`. It exposes one function that returns `(source, code)` with source `"instagram"` or `"youtube"`, and `canonical_url(code)` for both. Existing Instagram codes stay valid, so no migration is needed.
- Saving a known YouTube video again appends the memo to its note, the same as Instagram.

## 4. Inbox format

Same as main spec §4. `video_url` may now be a YouTube URL.

- `dm_url` must be `null` for YouTube. The Mac rejects a YouTube item that has one.
- The UI hides the DM link field for YouTube links.

## 5. Mac: YouTube path

### 5.1 Settings per source

| Setting | Instagram (unchanged) | YouTube |
|---|---|---|
| Download timeout | 600 s | 1800 s |
| OCR frame rate | 2 fps | 1 fps |
| OCR frame cap | 600 (5 min) | 3600 (60 min) |
| Transcript | whisper | captions first, see §5.2 |
| Source text for the AI | 12000 chars | 120000 chars |
| Over the limit | truncated | split into parts, see §5.4 |

The values live in one place per source, in code, not in the user config. Nobody asked for them to be configurable.

### 5.2 Transcript order

1. **Human-made captions** in the video's language (`language` in yt-dlp's info JSON). If the language is unknown, take the first human-made track. Whisper does not run.
2. **Whisper** (large-v3-turbo) on the audio when there are no human-made captions.
3. **Auto-generated captions** in the video's original language (yt-dlp names them `<lang>-orig`, or `<lang>`) only when whisper fails.
4. Nothing worked: the item fails with the whisper error, as today.

Captions are downloaded as VTT. Before use:

- Timestamps, cue numbers, and inline tags (`<c>`, `<00:00:01.000>`) are removed.
- Repeated lines are dropped. Auto-generated VTT repeats each line across consecutive cues.
- The transcript record says which source it came from (`captions`, `whisper`, `auto-captions`). The note's Source section shows it.

### 5.3 Download

- The first yt-dlp call writes the video and the info JSON, as today.
- A second call with `--skip-download --write-subs --write-auto-subs --sub-format vtt --sub-langs <chosen>` fetches only the chosen track.
- A login or bot-check error retries once with `cookies_browser`, as today for Instagram.

Verified on 2026-10-27 with yt-dlp 2026.08.19: `--list-subs` lists human and auto captions for a public video without cookies.

### 5.4 Long sources

- **Up to 120000 characters** (caption, transcript, screen text, memos together): one LLM call, same schema as today.
- **Over the limit:**
  1. Split the transcript into parts of about 30 minutes, by caption timestamps or by whisper segments.
  2. Summarize each part with a short prompt into key facts and a few sentences.
  3. Run the normal note prompt once on the joined part summaries, plus the caption and screen text.
- A 30–40 minute video never reaches this path. It exists so a long video does not lose its end.

### 5.5 Prompt

- The note prompt says "a video (Instagram Reel or YouTube video)" instead of "an Instagram Reel".
- The YouTube description is passed in the caption slot. Long descriptions are cut to 4000 characters before they count toward the limit, since they are mostly links and sponsor text.
- Chapters from the info JSON, when present, are passed as a short list. The model may use them as `###` groups.

### 5.6 Note

Same format as main spec §6. Additions:

- Frontmatter `source: youtube` or `source: instagram`. Existing notes without it are Instagram.
- The Source section links the canonical YouTube URL and names the transcript source.

## 6. Chrome extension

### 6.1 Layout

```
extension/
  manifest.json      Manifest V3
  popup.html         built from phone/ui.html
  popup.js           bridge: page requests -> core functions
  (core.js)          built from phone/core.js
phone/
  core.js            GitHub Contents API, categories.md edits, tag rules, inbox writes. Shared by both clients.
  reelnote.js        Scriptable shell: Keychain, Alert, WebView bridge
  ui.html            the UI, shared by both clients
  build.mjs          builds phone/dist/reelnote.js and extension/
```

- The GitHub and category logic moves out of `phone/reelnote.js` into `phone/core.js` unchanged. The phone keeps working the same way.
- `core.js` gets its HTTP client and secret store from the shell: Scriptable `Request` and `Keychain` on the phone, `fetch` and `chrome.storage.local` in the extension.

### 6.2 Manifest

- `permissions`: `activeTab`, `storage`.
- `host_permissions`: `https://api.github.com/*`.
- No content scripts, no background service worker.

### 6.3 Flow

1. Click the icon. The popup reads the active tab URL (`chrome.tabs.query`, allowed by `activeTab`).
2. Not a YouTube or Instagram video URL: the popup shows "이 탭은 저장할 수 있는 영상이 아니에요." and nothing else.
3. Otherwise it shows the same screens as the phone: category list, tags, memo. The DM field shows for Instagram links only.
4. Save writes the inbox file. The popup shows the sent state and closes after a moment.

### 6.4 Token

- The first run asks for a fine-grained PAT: `reelnote-data` only, Contents read/write. It is a separate token from the phone's.
- It is stored in `chrome.storage.local`. A 401 deletes it and asks again, as on the phone.

### 6.5 Install

- `node phone/build.mjs` writes `extension/`.
- Chrome → `chrome://extensions` → Developer mode → Load unpacked → `extension/`.
- Built files are gitignored, as `phone/dist` is.

## 7. Phone

- The URL check in the save request accepts YouTube URLs with the same rules as §3.
- Sharing from the YouTube app gives a URL in `args.urls` or text in `args.plainTexts`. `firstUrl` already handles both. This is not yet verified on the device.

## 8. Error handling

Same as main spec §7, plus:

- **Bot check** ("Sign in to confirm you're not a bot"): treated as login required. Retry with cookies; if it still fails, the item stays in the inbox and the attempt counts toward `max_attempts`.
- **Live stream or premiere not started yet:** treated as a temporary failure, retried on later runs.
- **Video longer than 60 minutes:** processed. OCR covers the first 60 minutes. The transcript covers the whole video.

## 9. Testing

- `source.py`: every accepted URL form, ids with `-` and `_`, rejected hosts and malformed ids, `t`/`list`/`si` dropped.
- VTT cleaning: timestamps, inline tags, and auto-caption line repeats removed.
- Transcript order: human captions skip whisper; whisper used when none; auto captions only after a whisper failure.
- Long sources: under the limit one call; over the limit parts are summarized and joined.
- Inbox: a YouTube item with `dm_url` is rejected.
- Instagram regression: existing pipeline tests pass unchanged.
- `phone/core.js`: node tests as in `phone/test.mjs`, now shared by both shells.
- Manual: one real 30-minute video with human captions, one with only auto captions, and a Short. Run each through the extension and through the phone.

## 10. Cost

- No new service. The LLM still runs on the existing subscriptions.
- A 40-minute video sends about 3–4 times more text than a Reel, so it uses more of the subscription quota per save.
- Captions skip whisper, so most YouTube saves are faster than whisper on the same length.
