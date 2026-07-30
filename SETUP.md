# 설정 가이드 (사용자가 직접 해야 하는 1회성 작업)

이 파이프라인은 에이전트가 대신할 수 없는, 사용자 본인 계정으로만 가능한 작업들이 있습니다. 아래 순서대로 진행하세요.

## 1. Google Cloud / YouTube API 자격 증명

1. [Google Cloud Console](https://console.cloud.google.com/)에서 새 프로젝트를 만듭니다.
2. "API 및 서비스 → 라이브러리"에서 **YouTube Data API v3**를 검색해 사용 설정합니다.
3. "API 및 서비스 → OAuth 동의 화면"에서 User Type을 **외부(External)**로 설정하고, "테스트 사용자"에 본인 구글 계정을 추가합니다 (앱을 게시 상태로 전환하지 않으면 Google의 앱 검토 없이 계속 사용 가능합니다 — 1인 사용 목적이므로 이걸로 충분합니다).
4. "API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID"에서 애플리케이션 유형을 **"TV 및 제한된 입력 기기(TVs and Limited Input devices)"**로 선택해 생성합니다. `client_id`, `client_secret`을 기록해 둡니다.

   > 이 유형을 쓰는 이유: 일반적인 "데스크톱 앱" 유형은 로컬 컴퓨터에 임시 웹 서버를 띄워 브라우저 리다이렉트를 받는 방식(`run_local_server`)이라 PC가 필요합니다. "TV 및 제한된 입력 기기" 유형은 **Device Authorization Grant** 플로우를 쓰는데, 로컬 서버가 전혀 필요 없고 사용자는 그냥 아무 브라우저(폰 포함)에서 URL을 열어 코드만 입력하면 됩니다. 모바일에서 진행하기에 이 방식이 적합합니다.

5. **refresh_token 발급 (Device Flow, 모바일 가능)**: 아래 두 개의 HTTP 요청만으로 발급받을 수 있습니다 (curl 또는 이 저장소를 다루고 있는 에이전트 세션에서 대신 실행해도 됩니다).

   1) 디바이스 코드 요청:
   ```bash
   curl -s -X POST https://oauth2.googleapis.com/device/code \
     -d client_id=YOUR_CLIENT_ID \
     -d scope="https://www.googleapis.com/auth/youtube.upload"
   ```
   응답에 `verification_url`(또는 `verification_uri`)과 `user_code`, `device_code`가 들어 있습니다.

   2) 폰 브라우저로 `verification_url`을 열어 `user_code`를 입력하고, 본인 유튜브 채널 계정으로 로그인/동의합니다.

   3) 동의 후 아래 요청으로 토큰을 받습니다 (동의 전에 호출하면 `authorization_pending` 에러가 나며, 몇 초 후 재시도하면 됩니다):
   ```bash
   curl -s -X POST https://oauth2.googleapis.com/token \
     -d client_id=YOUR_CLIENT_ID \
     -d client_secret=YOUR_CLIENT_SECRET \
     -d device_code=DEVICE_CODE_FROM_STEP_1 \
     -d grant_type=urn:ietf:params:oauth:grant-type:device_code
   ```
   응답의 `refresh_token` 값을 저장합니다. `client_id`/`client_secret`과 함께 이 값이 GitHub Secrets에 들어갈 세 값입니다.

6. Cloud Console → "API 및 서비스 → 할당량"에서 `youtube.googleapis.com` 의 `videos.insert` 쿼터 비용을 확인하세요. (최근 정책 변경 가능성이 있으므로, 하루에 몇 개까지 업로드해도 안전한지 이 값으로 직접 확인한 뒤 워크플로우의 cron 주기를 정하는 것을 권장합니다.)

## 2. Gemini API 키 (이미지 생성용)

이미지 생성은 Gemini의 이미지 생성 모델(gemini-2.5-flash-image, 일명 "나노바나나")을 사용합니다. **무료 등급으로 하루 500장까지 가능하고 카드 등록도 필요 없습니다** (기존에 결제해둔 Gemini 앱 구독과는 별개의, 개발자용 API입니다).

1. [aistudio.google.com](https://aistudio.google.com/)에 본인 구글 계정으로 로그인합니다.
2. "Get API key" (API 키 받기) 메뉴로 이동해 새 API 키를 발급받습니다.
3. 발급된 키를 복사해 둡니다.

## 3. GitHub 저장소 Secrets 등록

저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서 아래 값들을 등록합니다.

| Secret 이름 | 값 |
|---|---|
| `GEMINI_API_KEY` | 위에서 발급받은 Gemini API 키 |
| `YOUTUBE_CLIENT_ID` | OAuth 클라이언트 ID |
| `YOUTUBE_CLIENT_SECRET` | OAuth 클라이언트 시크릿 |
| `YOUTUBE_REFRESH_TOKEN` | 위에서 발급받은 refresh token |

## 4. 채널 설정 확인

- 채널 수익화(YouTube Partner Program) 자격은 이 API 설정과 별개의 조건(구독자/시청 시간 등)입니다. 별도로 확인하세요.
- 초기 테스트 업로드는 `config/settings.yml`의 `youtube.privacy_status`가 `private`로 설정되어 있습니다. 결과가 만족스러우면 `public`으로 바꾸세요.

## 5. `music_library/`에 Suno 음원 채워 넣기 (반복 작업)

이 파이프라인은 음악을 API로 자동 생성하지 않습니다 (비용 절감을 위해). 대신:

1. [Suno](https://suno.com)에서 직접 3분 내외의 무편집 인스트루멘탈 lofi 트랙을 여러 개 다운로드합니다. **한 번에 40~80개 정도** 받아두는 것을 권장합니다 (라이브러리 전체 분량이 곧 영상 길이의 기준이 됩니다 — 약 40개면 대략 2시간 안팎이 나옵니다. `config/settings.yml`의 `audio.reuse_ratio`로 일부를 재사용해 목표 길이를 살짝 웃돌게 조절합니다).
2. 다운로드한 mp3 파일들을 `music_library/` 폴더에 추가합니다. 방법은 두 가지입니다:
   - **git 사용**: 로컬에 클론 후 `music_library/`에 파일을 넣고 `git add music_library && git commit -m "add tracks" && git push`
   - **GitHub 웹 UI**: 저장소 페이지에서 `music_library` 폴더로 이동 → "Add file → Upload files" → 파일들을 드래그 앤 드롭 → Commit. git 명령어 없이도 가능합니다.
3. 워크플로우가 실행될 때 라이브러리가 비어있거나 파일을 하나도 읽을 수 없으면 자동으로 실패하고 저장소에 안내 Issue가 생성됩니다. 그 알림을 보면 이 작업을 반복하면 됩니다. 라이브러리 곡 수가 곧 영상 길이에 직결되니, 원하는 길이에 맞춰 넉넉히 채워두면 여러 번의 실행을 커버할 수 있습니다.
4. 같은 곡이라도 매 실행마다 다른 순서/조합으로 섞이므로, 라이브러리를 자주 갈아엎지 않아도 결과물은 계속 달라집니다.

## 6. 첫 실행

1. 저장소의 **Actions** 탭 → "Publish lofi video" 워크플로우 → **Run workflow**를 클릭해 수동으로 1회 실행합니다. 처음에는 `dry_run: true`로 실행해 업로드 없이 파이프라인이 끝까지 도는지 확인하는 것을 권장합니다.
2. 문제가 없으면 `dry_run: false`로 실제 업로드까지 테스트합니다 (`privacy_status: private`이므로 본인만 볼 수 있습니다).
3. 결과가 만족스러우면 `config/settings.yml`의 `privacy_status`를 `public`으로 바꾸고, 필요하면 `.github/workflows/publish-video.yml`의 cron 주기를 조정합니다.
