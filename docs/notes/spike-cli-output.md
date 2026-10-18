# Spike: yt-dlp and AI CLI output (Task 0)

Date: 2026-10-17. Mac: M4, macOS 26.6. Throwaway findings that later tasks rely on.

## Tools installed

- `yt-dlp` 2026.08.19 and `whisper-cli` via Homebrew.
- Ollama `bge-m3`, plus `~/.cache/reelnote/ggml-large-v3-turbo.bin` (1.5 GB).

## AI CLIs

| CLI | Where the JSON is | Notes |
|---|---|---|
| `claude -p --output-format json --json-schema S` | `structured_output` (the object), also `result` (as a string) | A default call carries ~35k tokens of Claude Code system prompt, tools, and user hooks, which burns Claude Pro usage fast. Adding `--model sonnet --tools "" --setting-sources "" --strict-mcp-config --disable-slash-commands --no-session-persistence` cuts it to ~3.4k. `--bare` does not work with a subscription login: it returns `is_error: true`, `"result": "Not logged in · Please run /login"`. |
| `codex exec --skip-git-repo-check --output-schema F -o OUT` | the `OUT` file holds the object | A default call runs the user's Codex hooks. `--ephemeral --ignore-user-config` skips them and still authenticates (~18k tokens per call). |
| `grok -p --json-schema S --disable-web-search` | not verified | In a non-interactive shell it prints `{"type":"error","message":"Not signed in. To authenticate without a browser, run:\n  grok login --device-code ..."}`. The user must run `grok login` once. |

Consequences for Task 6:
- Add the light flags above to the `claude` and `codex` commands.
- Detect missing login with `not signed in|not logged in|please run /login|grok login`. Treat it like a missing CLI: skip the provider without retrying and without setting a cooldown.
- No CLI printed a parseable usage-limit reset time, so the fixed `cooldown_minutes` stays.

## yt-dlp

- `yt-dlp` lists Instagram's `instagram:user` extractor as broken (`Unable to extract data`). Profile listing does not work, which reelnote doesn't need anyway.
- Single-Reel download: pending a real public Reel URL from the owner.
