# CLAUDE.md — 노아뮤직 유튜브 로파이 자동화

유튜브 채널 "노아뮤직"의 매일 로파이 플레이리스트 영상 자동 생성·업로드 파이프라인.
이 문서는 세션 컨텍스트가 초기화돼도 운영 규칙과 인프라 지식이 유실되지 않도록 하는
단일 기준 문서다. **여기 적힌 사용자 지시는 사용자가 직접 번복하기 전까지 유효하다.**

## 절대 규칙 (사용자가 명시적으로 지시함)

1. **실패는 먼저 보고한다.** 워크플로우가 실패하면 사용자가 묻기 전에 원인을 확인해서
   먼저 알린다. 조용히 넘어가는 것 금지. 성공도 트리거해놓고 결과 확인 없이 방치 금지
   (`send_later`로 후속 확인 예약).
2. **짧은 간격으로 재시도 금지.** 실패했다고 연달아 재트리거하면 GitHub 러너 할당이
   통째로 막힌다 (2026-08-06에 run #18~#21 연속 실패로 실증: `runner_id: 0`,
   billable 0ms 상태로 15분+ 큐잉 후 자동 취소). 실패하면 원인부터 파악해 보고하고,
   재시도는 충분한 간격을 두고 1회만.
3. **한도초과로 밀린 날은 건너뛴다.** Claude 사용 한도 초과 등으로 Routine이 못 돌면
   그날은 포기. 밀린 알림이 나중에 몰려와도 따라잡기 금지, **하루 1편 초과 생성 절대
   금지** (유튜브 반복 콘텐츠 정책 + 사용자 지시).
4. **자동 공개는 19:14 KST.** 정각(19:00)은 업로드 트래픽이 몰려서 사용자가 19:14로
   지정함. 시각 변경 시 Routine cron + `auto-publish.yml` 이슈 댓글 문구 +
   `send_telegram_confirmation.py` 안내 문구 3곳을 같이 바꿔야 한다.

## 매일 사이클 (KST)

| 시각 | 담당 | 동작 |
|---|---|---|
| 17:15 | Routine `trig_01P2h6jdukbjN9byV49DXUGG` (cron `15 8 * * *` UTC) | 오늘 실행 기록 없으면 `publish-video.yml` 1회 dispatch |
| ~17:55 | publish-video.yml | 생성 완료 → 비공개 업로드 → pending-confirmation 이슈 + 텔레그램 승인/거부 버튼 발송 |
| (버튼 누르면) | n8n 웹훅 → `handle-telegram-decision.yml` | 즉시(~15초) 공개 전환 or 삭제, 이슈 닫기 |
| 19:14 | Routine `trig_01646XuLcE2nw4LuXL2ddVEG` (cron `14 10 * * *` UTC) | `auto-publish.yml` dispatch — 무응답 이슈가 있으면 자동 공개 |

- 트리거는 전적으로 Claude Code Remote Routine (세션 `session_01X5WRFHPYNZxAWS9qqi9WyD`에 바인딩).
  GitHub Actions 자체 `schedule:` cron은 스킵/수시간 지연이 반복돼 **제거했고 다시 쓰지 않는다.**
- Routine 프롬프트 안에 위 절대 규칙(지각 도착 시 스킵, 재시도 금지)이 이미 내장되어 있다.
- 정상 소요시간: 생성 전체 35~45분. 1시간 넘게 in_progress면 이상 신호.

## 텔레그램 실시간 웹훅 (n8n)

- **경로**: 텔레그램 버튼 → `https://n8n.issuejupjup.com/webhook/noamusic-telegram`
  (사용자 소유 GCP VM `34.121.25.236:5678`의 n8n, Caddy가 Let's Encrypt HTTPS 제공)
  → n8n 워크플로우 `noamusic-telegram-webhook` (Code 노드가 시크릿 헤더 검증,
  answerCallbackQuery/editMessageText 호출, `handle-telegram-decision.yml` dispatch).
- n8n 워크플로우는 `.github/workflows/setup-n8n-telegram-webhook.yml`로 배포/재생성
  가능 (base64로 파이썬 스크립트 내장 — heredoc 금지, 아래 함정 참고).
- **같은 n8n 인스턴스에 사용자의 다른 사업(워드프레스 자동 발행) 워크플로우가 있다.
  절대 건드리지 말 것.** 새 워크플로우 추가만 허용.
- **Cafe24 (`issuejupjup.com` 웹호스팅)는 SSL이 깨져 있어 웹훅 호스트로 쓸 수 없다**
  (유료 SSL 상품 결제 없이는 복구 불가). PHP 웹훅 방식은 2026-08-07에 완전 폐기했다.
  폴링(check-telegram-response) 방식도 같이 폐기 — 부활시키지 말 것.
- 실시간 웹훅 정상 검증 완료: 실제 버튼 → 공개 전환까지 13~22초 (8/7, 8/9, 8/10, 8/11 실증).

## 저장소 구성

- 브랜치: `claude/youtube-lofi-playlist-automation-959lx5` (유일한 작업 브랜치)
- 워크플로우:
  - `publish-video.yml` — 메인 파이프라인 (Drive 동기화 → 곡 조립 → Gemini 이미지 →
    ffmpeg → 업로드 → 이슈/텔레그램)
  - `handle-telegram-decision.yml` — n8n이 dispatch하는 승인/거부 처리
  - `auto-publish.yml` — 19:14 무응답 자동 공개 (자체 텔레그램 알림 포함 → Routine이
    중복 알림 보내면 안 됨)
  - `finalize-publish.yml`, `check-video-status.yml`, `list-channel-videos.yml` — 수동 도구
  - `setup-n8n-telegram-webhook.yml` — n8n 웹훅 재배포용
- 음원: 저장소가 아니라 **Google Drive** (`sync_music_library.py`가 실행 시 다운로드).
  사용자가 Suno에서 직접 뽑아 Drive 폴더에 채운다. 현재 90곡+, 매 영상 40~50곡 셔플백.
- GitHub Secrets (이름만): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
  `TELEGRAM_WEBHOOK_SECRET`, `DISPATCH_PAT`, `N8N_API_KEY`, `N8N_BASE_URL`,
  `GEMINI_API_KEY`, `GDRIVE_SERVICE_ACCOUNT_JSON`, `GDRIVE_MUSIC_FOLDER_ID`,
  `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`
- 시크릿 생성/수정 도구는 세션에 없음 → 사용자에게 GitHub 웹 UI 등록을 요청해야 한다.

## 작업 시 함정 (전부 실제로 겪은 것)

- **GitHub Actions `run: |` 블록 안에서 heredoc(`<< 'EOF'`) 금지.** YAML 들여쓰기
  규칙 때문에 종결자를 들여쓰면 bash가 인식 못 해 파일 끝까지 통째로 먹는다.
  긴 스크립트는 base64로 인코딩해 env로 넘기고 `base64 -d`로 복원할 것
  (푸시 전 로컬에서 디코드 → `py_compile` 검증).
- **`mcp__github__actions_list` 출력이 토큰 한도를 초과한다** (`per_page:1`이어도).
  에러 메시지에 적힌 저장 파일을 `python3 json.load`로 파싱해 필요한 필드만 뽑을 것.
- **이 세션의 네트워크는 임의 IP:포트로 직접 못 나간다** (n8n raw IP 접근 불가).
  외부 API 호출이 필요하면 GitHub Actions 러너를 경유할 것.
- 커밋 후 푸시는 `git push -u origin claude/youtube-lofi-playlist-automation-959lx5`.
  로컬 클론은 세션 스크래치패드에 있고 컨테이너 재생성 시 사라진다 — 푸시 안 한
  작업은 유실된다.

## 현재 상태 & 진행 중인 논의 (2026-08-11 기준)

- 8/9~8/11 3일 연속 정상 (생성 → 승인 → 공개). 8/7~8/8은 사용 한도 초과로 누락(건너뜀).
- **조회수가 사실상 0 → 채널 정체성 리브랜딩 논의 중.** 방향(사용자와 합의 중, 미확정):
  - **컨셉: "조선 세계관 + 상황 어그로 제목"** (사무라이 로파이의 한국판 빈자리 +
    때껄룩식 상황 제목. 예: "과거시험 D-1", "암행어사 출두 직전에 듣는 노래")
  - 김정은 등 실존 정치인 소재는 광고 제한 위험으로 제외하되, "웃긴 상황 제목" 공식
    자체는 세계관 안에서 유지
  - **썸네일 통일**: 고정 선비 캐릭터 + 붓글씨체 큰 한글 텍스트 + 낙관 로고 + 먹/한지/촛불
    색감. 변주는 계절/시간대/문구만
  - **발견된 결함**: `upload_youtube.py`의 `set_thumbnail()`이 존재하지만
    `pipeline.py`에서 호출되지 않음 (죽은 코드) — 썸네일이 아예 설정 안 되고 있다.
    리브랜딩과 무관하게 고쳐야 함
  - 음원: 사용자가 Suno에서 국악 질감 로파이(대금/가야금 멜로디 + lofi 비트) 배치를
    새로 뽑아 Drive에 채우기로 함
  - 기존 영상 11개는 삭제하지 않고 유지, 채널도 유지 (합의됨)
- 확정되면 할 일: `scenes.yml` 전면 교체, `title_templates.yml` 상황 어그로형 재작성,
  썸네일 파이프라인(텍스트 오버레이 + `set_thumbnail` 연결) 구축.
