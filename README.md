# instagramInfo-organizer

인스타그램 릴스·쇼츠에서 얻은 정보를 다시 찾기 쉽게 정리해 주는 개인용 organizer.

릴스를 "저장"만 해두면 무엇을 저장했는지 잊고, 다시 찾기도 어렵습니다. 이 프로젝트는 폰에서 영상 링크(와 DM으로 받은 추가 정보 링크)를 한 번에 보내면, 맥이 영상을 직접 읽어서 Obsidian 노트로 정리하고 비슷한 내용끼리 묶어 줍니다.

> **상태:** 설계 단계. 아직 실행 가능한 코드는 없습니다.

## 동작 흐름

1. **폰 (Scriptable 공유 화면):** 인스타 앱에서 공유를 누르고 카테고리를 고릅니다. 카테고리를 정하지 않으면 AI가 자동으로 분류합니다. 그다음 영상 링크, DM 추가 정보 링크(선택), 메모(선택)를 입력해서 저장합니다.
2. **큐 (private GitHub repo):** 저장한 항목은 private data repo의 `inbox/`에 JSON 파일로 쌓입니다. 맥이 꺼져 있어도 사라지지 않습니다.
3. **맥 (처리 파이프라인):** 맥이 켜지면 `launchd`가 inbox를 가져와서 처리합니다.
   - 같은 영상인지 shortcode로 판단해서 중복을 제거합니다.
   - `yt-dlp`로 영상과 캡션을 받습니다.
   - `whisper.cpp`로 음성을, macOS Vision OCR로 화면 텍스트를 추출합니다.
   - DM 링크가 있으면 그 페이지의 본문도 추출합니다.
   - AI가 요약, 핵심 정보, 카테고리를 담은 노트를 씁니다.
   - 로컬 임베딩으로 비슷한 노트끼리 주제 hub로 묶습니다.
4. **Obsidian vault:** 노트와 hub는 private data repo에 저장됩니다. 폰에서는 Obsidian Git 플러그인으로 봅니다.

## Repo 구성

| Repo | 공개 | 내용 |
|---|---|---|
| 이 repo | public | 코드, 설정 예시, 문서 |
| data repo | **private** | `inbox/`(보낸 링크), `vault/`(노트, hub, `categories.md`) |

개인 데이터(링크, 노트, 영상, 받아쓰기)와 비밀 정보(GitHub 토큰, 브라우저 쿠키)는 이 repo에 절대 커밋하지 않습니다. `.gitignore`가 해당 경로를 막고 있습니다.

## 기술 구성

- **맥:** Python (`uv`), `yt-dlp`, `ffmpeg`, `whisper.cpp`, macOS Vision OCR, Ollama `bge-m3`, SQLite
- **AI:** 구독 로그인 CLI만 사용하고 API 과금은 쓰지 않습니다. `claude -p`로 시작하고, 한도에 걸리면 `codex exec`, 그다음 `grok -p` 순서로 넘어갑니다.
- **폰:** [Scriptable](https://scriptable.app) (iOS, 무료). 필요해지면 나중에 SwiftUI 앱으로 옮길 수 있습니다.

## 비용

현재 설계 기준 추가 비용은 0원입니다. 사용하는 도구는 모두 무료이고, AI는 이미 구독 중인 서비스의 로그인 CLI로 호출합니다.
