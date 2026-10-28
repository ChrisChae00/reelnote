# 0001: YouTube videos are saved through a Chrome extension that only writes to the inbox

Date: 2026-10-27
Status: accepted

## Context

reelnote handles only Instagram today. The phone share script rejects any other URL, and the Mac pipeline rejects inbox items without an Instagram shortcode.

YouTube videos are longer (often 30 minutes or more), and most of them have captions. They need their own processing path:

- OCR at 1 frame per second.
- A larger transcript and source budget for the AI.
- Captions as the transcript source.

The user wants to save YouTube videos from Chrome on a computer. The extension popup is the panel that opens from the extension's icon in the Chrome toolbar.

## Options

**A. The extension is only a save button.** The popup shows category, tags, and memo, then writes one file to `reelnote-data/inbox/` through the GitHub Contents API, as the phone does. The Mac downloads the video and the captions with yt-dlp (`--write-subs --write-auto-subs`) and does all processing.

**B. The extension also scrapes the captions from the YouTube page** and puts the caption text into the inbox file.

**C. The extension sends the link straight to the Mac** through a local server, skipping GitHub.

## Decision

A.

## Why

- **One queue.** The phone and the extension write the same inbox format. The Mac pipeline stays the only place that processes videos. Items still wait safely while the Mac is off. This keeps success criterion 4: nothing sent is lost.
- **Works from any computer.** Only GitHub must be reachable. C works only in Chrome on the Mac itself, and only while the Mac is on.
- **Robust to YouTube changes.** yt-dlp is maintained against YouTube changes, and it gets both manual and auto-generated captions. B depends on YouTube's page markup and its internal caption endpoints. Those change often and would break the extension without warning.
- **Small extension.** A popup and one API call are easy to review and keep up to date. No scraping code and no local server.
- **No large data in the inbox.** With B, a full caption text would go into every inbox file and into the private repo's history.

## Costs of this decision

- **Captions that need a login or membership are out of reach.** yt-dlp sees only what the Mac can see. Members-only or age-restricted videos may need browser cookies (`cookies_browser` in the config) or may fail.
- **YouTube bot checks.** YouTube sometimes asks yt-dlp to sign in. The Mac may need cookies from a browser where the user is signed in to YouTube.
- **Not instant.** Processing starts on the Mac's next check (within about a minute while the Mac is on), the same as phone saves.
- **The extension holds a GitHub token.** It needs its own fine-grained PAT for `reelnote-data` only, with Contents read/write, stored in `chrome.storage.local`. Anyone who can use that Chrome profile can use the token.

## Revisit when

- yt-dlp can no longer get YouTube captions reliably. B then becomes the fallback.
- A source the Mac cannot reach becomes important (for example, members-only videos).
