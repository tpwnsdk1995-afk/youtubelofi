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
     -d scope="https://www.googleapis.com/auth/youtube"
   ```
   > `youtube.upload`가 아니라 전체 관리 스코프(`youtube`)를 요청합니다. 업로드 후 제목/설명 수정, 썸네일 교체, 재생목록 추가까지 하려면 업로드 전용 스코프로는 403이 나기 때문입니다.
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
- `config/settings.yml`의 `youtube.privacy_status`는 현재 `public`으로 설정되어 있습니다. 테스트 목적으로 비공개 업로드를 원하면 `private` 또는 `unlisted`로 바꾸세요.

## 5. Google Drive에 음악 라이브러리 채워 넣기 (반복 작업)

이 파이프라인은 음악을 API로 자동 생성하지 않습니다 (비용 절감을 위해). 대신 사용자가 [Suno](https://suno.com)에서 직접 다운로드한 mp3를 채워 넣습니다. **다만 이 음원 파일들은 더 이상 GitHub 저장소(git)에 저장하지 않고 Google Drive 폴더에 보관합니다** — git은 파일이 계속 쌓이면(매일 몇백 MB씩 영구 추가) 저장소가 무한정 커지는 데다, 공개 저장소라 누구나 다운로드할 수 있다는 문제도 있기 때문입니다. 파이프라인은 매 실행마다 이 Drive 폴더에서 필요한 파일을 자동으로 내려받습니다.

### 5-1. Drive 폴더 준비 (최초 1회)

1. Google Drive 앱(또는 웹)에서 새 폴더를 만듭니다. 이름은 자유(예: `noa music library`).
2. 다운로드해 둔 Suno mp3 파일들을 이 폴더에 업로드합니다. **한 번에 40~80개 정도** 받아두는 것을 권장합니다 (라이브러리 전체 분량이 곧 영상 길이의 기준입니다 — 약 40개면 대략 2시간 안팎. `config/settings.yml`의 `audio.reuse_ratio`로 일부를 재사용해 길이를 살짝 늘립니다).
3. 이 폴더를 열었을 때 주소창 마지막의 `.../folders/` 뒤에 오는 문자열이 **폴더 ID**입니다. 기록해 둡니다.

### 5-2. 서비스 계정 준비 (최초 1회, Google Cloud Console)

Gemini API 키를 발급받은 것과 같은 프로젝트를 그대로 사용하면 됩니다.

1. "API 및 서비스 → 라이브러리"에서 **Google Drive API**를 검색해 사용 설정합니다.
2. "IAM 및 관리자 → 서비스 계정 → + 서비스 계정 만들기"로 이동해 이름을 아무거나 입력하고(예: `music-library-reader`) 만듭니다. 프로젝트 역할(role) 부여 단계는 건너뛰어도 됩니다 — 권한은 다음 단계에서 Drive 폴더 공유로 직접 부여합니다.
3. 만들어진 서비스 계정을 클릭 → **"키" 탭 → 키 추가 → 새 키 만들기 → JSON**을 선택하면 JSON 파일이 다운로드됩니다. 이 **파일 내용 전체**가 나중에 GitHub Secret에 들어갈 값입니다.
4. 서비스 계정 상세 화면에 표시되는 이메일 주소(`...iam.gserviceaccount.com` 형태)를 기록해 둡니다.

### 5-3. 폴더를 서비스 계정과 공유

Drive에서 5-1의 폴더를 길게 눌러(또는 우클릭) **공유** → 위에서 기록한 서비스 계정 이메일을 입력 → 권한은 **"뷰어(Viewer)"** 로 설정하고 공유합니다. (사람에게 공유하는 것과 완전히 같은 방식입니다.)

### 5-4. GitHub Secrets에 2개 추가

| Secret 이름 | 값 |
|---|---|
| `GDRIVE_SERVICE_ACCOUNT_JSON` | 5-2에서 다운로드한 JSON 파일의 내용 **전체**를 그대로 복사해서 붙여넣기 |
| `GDRIVE_MUSIC_FOLDER_ID` | 5-1에서 기록한 폴더 ID |

### 5-5. 이후 반복 작업

음원을 보충할 때는 **Drive 앱에서 그 폴더에 파일을 업로드하기만 하면 끝**입니다. git이나 GitHub 웹 업로드는 더 이상 필요 없습니다. 워크플로우가 실행될 때 라이브러리가 비어있거나 파일을 하나도 읽을 수 없으면 자동으로 실패하고 저장소에 안내 Issue가 생성되니, 그 알림을 보면 이 작업을 반복하면 됩니다. 같은 곡이라도 매 실행마다 다른 순서/조합으로 섞이므로, 라이브러리를 자주 갈아엎지 않아도 결과물은 계속 달라집니다.

### 참고: Drive 저장 공간

Google 계정의 무료 저장 공간(Gmail/Photos/Drive 합산 15GB)을 초과하면 Google One 유료 요금제가 필요해질 수 있습니다. mp3 위주라면 상당히 오래 버틸 수 있지만(수천 곡 단위), 계속 쌓이므로 언젠가 여유 공간을 확인해볼 필요는 있습니다. 다만 git 방식(무한정 증식, 삭제해도 히스토리에 영구 잔존)보다는 훨씬 관리하기 쉽고, 필요 없어진 곡은 Drive에서 그냥 지우면 실제로 공간이 회수됩니다.

## 6. 첫 실행

1. 저장소의 **Actions** 탭 → "Publish lofi video" 워크플로우 → **Run workflow**를 클릭해 수동으로 1회 실행합니다. 처음에는 `dry_run: true`로 실행해 업로드 없이 파이프라인이 끝까지 도는지 확인하는 것을 권장합니다.
2. 문제가 없으면 `dry_run: false`로 실제 업로드까지 테스트합니다.
3. 필요하면 `.github/workflows/publish-video.yml`의 cron 주기를 조정합니다.

## 7. 공개 전 확인 플로우

`config/settings.yml`의 `youtube.privacy_status`는 `private`입니다 — 즉 매번 자동으로 영상을 만들어 **비공개로만** 업로드하고, 바로 공개하지 않습니다. 업로드가 끝나면 저장소에 `pending-confirmation` 라벨의 Issue가 자동 생성되고(제목/설명/비공개 링크 포함), 채팅 세션이 이를 감지해 알림을 보냅니다. 사진과 제목/설명을 확인한 뒤 채팅에서 확인해주면, 그때 `.github/workflows/finalize-publish.yml` 워크플로우가 트리거되어 실제로 공개 전환됩니다 (필요하면 이때 제목/설명도 함께 수정). 별도로 사용자가 직접 할 일은 없고, 채팅으로 "확인" 또는 수정 요청을 답하기만 하면 됩니다.

## 8. (선택) 노출수·클릭률 분석 켜기 — YouTube Analytics 스코프 추가

주간 리포트의 **원인 진단**(노출 단계 / 썸네일 / 음악 중 어디가 병목인지)은 YouTube
Analytics API가 필요합니다. 이걸 켜지 않아도 리포트는 정상 발송되며, 해당 섹션만
빠집니다. 켜면 아래 지표가 추가됩니다.

- `videoThumbnailImpressions` — 노출수 (알고리즘이 우리 영상을 몇 번 보여줬나)
- `videoThumbnailImpressionsClickRate` — 노출 클릭률 (썸네일이 클릭을 유도하나)
- `averageViewDuration` — 평균 시청 시간 (음악이 실제로 붙잡나)
- 유입 경로 (검색 / 추천 / 탐색)

### 왜 1번의 device flow를 재사용할 수 없나

**YouTube Analytics API는 device flow(TV 및 제한된 입력 기기)를 지원하지 않습니다.**
1번에서 쓴 방식으로는 `yt-analytics.readonly` 스코프를 받을 수 없어, 아래의 별도
절차가 필요합니다. (출처: developers.google.com/youtube/reporting/guides/authorization)

### 절차 (폰만으로 가능, PC 불필요)

> ⚠️ 이 절차는 `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN`
> **3개를 모두 교체**합니다. 잘못되면 매일 업로드가 멈추므로, **기존 3개 값을 먼저
> 어딘가에 복사해 두세요.** 문제가 생기면 되돌릴 수 있어야 합니다.

1. Google Cloud Console → "API 및 서비스 → 라이브러리"에서 **YouTube Analytics API**를
   검색해 사용 설정합니다. (Data API와 별개의 API입니다)

2. "사용자 인증 정보 만들기 → OAuth 클라이언트 ID"에서 유형을 **"웹 애플리케이션"**으로
   선택하고, **승인된 리디렉션 URI**에 `http://localhost` 를 추가해 생성합니다.
   새 `client_id`, `client_secret`을 기록합니다.

3. 폰 브라우저에서 아래 주소를 엽니다 (`YOUR_CLIENT_ID`만 바꿔서 한 줄로):

   ```
   https://accounts.google.com/o/oauth2/v2/auth?client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost&response_type=code&access_type=offline&prompt=consent&scope=https://www.googleapis.com/auth/youtube%20https://www.googleapis.com/auth/yt-analytics.readonly
   ```

   > 두 스코프를 **함께** 요청하는 게 핵심입니다. 그래야 refresh token 하나로
   > 업로드와 분석이 모두 됩니다. `prompt=consent`가 있어야 refresh token이 나옵니다.

4. 채널 계정으로 로그인/동의하면 `http://localhost/?code=4/0A...&scope=...` 로
   이동하며 **"사이트에 연결할 수 없음" 오류 페이지가 뜹니다. 정상입니다.**
   주소창의 `code=` 뒤부터 `&` 앞까지의 값을 복사합니다.

5. 그 코드를 토큰으로 교환합니다 (이 저장소 세션의 에이전트에게 맡겨도 됩니다):

   ```bash
   curl -s -X POST https://oauth2.googleapis.com/token \
     -d client_id=YOUR_CLIENT_ID \
     -d client_secret=YOUR_CLIENT_SECRET \
     -d code=CODE_FROM_STEP_4 \
     -d redirect_uri=http://localhost \
     -d grant_type=authorization_code
   ```

   응답의 `refresh_token` 값을 기록합니다.

6. GitHub Secrets에서 `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`,
   `YOUTUBE_REFRESH_TOKEN` 3개를 새 값으로 교체합니다.

7. **검증**: `weekly-report.yml`을 수동 실행해 리포트에 `[유입 지표]` 섹션이 나오는지
   확인하고, 이어서 `publish-video.yml`을 `dry_run: true`로 실행해 업로드 경로도
   여전히 정상인지 확인합니다. 둘 다 통과하면 완료입니다.
