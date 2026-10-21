# reelnote

A personal organizer for the information buried in Instagram Reels and Shorts.

Saving a Reel in Instagram is easy. Finding it again later is not, and after a few hundred saves you forget what you kept. reelnote takes the video link (and the extra-info link the creator sent by DM) from your phone in one step. Your Mac then reads the video itself and files it as an Obsidian note, grouped with similar notes.

> **Status:** Mac pipeline works. Phone share UI built; first on-device run pending.

## How it works

1. **Phone (Scriptable share sheet):** Tap Share in Instagram and pick a category, or leave it on Auto to let the AI choose. Then confirm the video link and add the DM link, a memo, and tags if you have them.
2. **Queue (private GitHub repo):** Each save becomes one JSON file in the private data repo's `inbox/`. Items wait there while the Mac is off.
3. **Mac (pipeline):** When the Mac is on, `launchd` checks GitHub every minute and, when something new arrived, pulls the inbox and processes each item:
   - Dedupes by Instagram shortcode, so the same post is never processed twice.
   - Downloads the video and caption with `yt-dlp`.
   - Extracts speech with `whisper.cpp` and on-screen text with macOS Vision OCR.
   - Extracts the page text behind the DM link, if one was sent.
   - Has an AI write a note with a summary, key facts, and a category.
   - Groups similar notes into topic hubs using local embeddings.
4. **Obsidian vault:** Notes and hubs are stored in the private data repo. On the phone, read them with the Obsidian Git plugin.

## Repositories

| Repo | Visibility | Contents |
|---|---|---|
| `reelnote` (this repo) | public | Code, example config, docs |
| `reelnote-data` | **private** | `inbox/` (links you sent), `vault/` (notes, hubs, `categories.md`) |

Personal data (links, notes, videos, transcripts) and secrets (GitHub token, browser cookies) are never committed here. `.gitignore` blocks those paths.

## Stack

- **Mac:** Python (`uv`), `yt-dlp`, `ffmpeg`, `whisper.cpp`, macOS Vision OCR, Ollama `bge-m3`, SQLite.
- **AI:** Subscription-login CLIs only, with no pay-per-use API. The pipeline starts with `claude -p`. When that hits its usage limit, it falls back to `codex exec`.
- **Phone:** [Scriptable](https://scriptable.app) (iOS, free). It can move to a SwiftUI app later if needed.

## Cost

The current design adds no cost. Every tool is free, and the AI calls go through subscriptions you already have.

## Setup (Mac)

```bash
brew install yt-dlp whisper-cpp ffmpeg
ollama pull bge-m3
mkdir -p ~/.cache/reelnote && curl -L -o ~/.cache/reelnote/ggml-large-v3-turbo.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
git clone https://github.com/<you>/reelnote-data ~/reelnote-data
mkdir -p ~/.config/reelnote && cp config.example.toml ~/.config/reelnote/config.toml
uv sync
uv run reelnote run          # first run by hand; check ~/reelnote-data/vault
env -i HOME=$HOME /bin/zsh -lc 'command -v uv claude codex yt-dlp ffmpeg whisper-cli ollama git'
sed -e "s#__REPO__#$PWD#" -e "s#__HOME__#$HOME#" launchd/com.reelnote.run.plist > ~/Library/LaunchAgents/com.reelnote.run.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.reelnote.run.plist
```

Every tool must print a path; launchd runs with this PATH. Log in to `claude` and `codex` once in a terminal before enabling launchd. `uv run reelnote status` shows item states and which AI providers are cooling down. `uv run reelnote regroup` groups unassigned notes into new hubs.

Ollama must be running whenever the pipeline runs, including right after login: open the Ollama app or run `brew services start ollama`. If Ollama, `yt-dlp`, `ffmpeg`, `whisper-cli` or both AI CLIs are missing, the run stops early without counting a failed attempt and `vault/review.md` names the missing tool.

To stop the scheduled runs:

```bash
launchctl bootout gui/$(id -u)/com.reelnote.run
```

## Setup (iPhone)

1. Create a fine-grained GitHub token: **Only select repositories → `reelnote-data`**, **Contents: Read and write**, nothing else.
2. Install [Scriptable](https://scriptable.app), add a script named `reelnote`, and paste in `phone/dist/reelnote.js`.
3. In the script's settings, turn on **Share Sheet Inputs → URLs** and **Text**.
4. Run it once in the app and paste the token. It is stored in the iOS Keychain only.
5. In Instagram: Share → **Run Script** → `reelnote`.

To change the UI, edit `phone/ui.html` (open it in a browser to try it with demo data) or `phone/reelnote.js`, then run `node phone/test.mjs && node phone/build.mjs`.

## Retrying a failed item

An item that failed 3 times moves to `inbox/failed/<id>.json` (invalid items also get `<id>.error.txt`). To retry it, move the JSON back:

```bash
cd ~/reelnote-data
git mv inbox/failed/<id>.json inbox/ && git commit -m "retry <id>" && git push
```

## Recovering from a sync conflict

reelnote resolves conflicts in `vault/categories.md` by itself (the phone's version wins). A conflict in any other file stops the run, prints `reelnote: sync failed: pull conflict in [...]`, and shows a macOS notification. Every later run fails the same way until you fix it. reelnote aborts its own rebase before it stops, so start the pull again by hand and resolve it there:

```bash
launchctl bootout gui/$(id -u)/com.reelnote.run   # stop scheduled runs, otherwise a run aborts your rebase
cd ~/reelnote-data
git pull --rebase                  # stops at the conflict
git status                         # lists the conflicted files
# edit each file to resolve it, or pick one side:
git checkout --theirs <file>       # keep the Mac's version (in a rebase "theirs" is the local commit)
git checkout --ours <file>         # keep the remote (phone) version
git add <file>
git rebase --continue              # repeat checkout/add/continue if more commits conflict
git push
# or, to back out and leave everything as it was:
git rebase --abort
# last step: resume scheduled runs
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.reelnote.run.plist
```
