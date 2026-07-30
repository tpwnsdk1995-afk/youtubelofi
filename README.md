# YouTube 로파이 채널 자동화 파이프라인

학습/작업용 로파이(lofi) 음악 영상을 자동으로 만들어 유튜브에 업로드하는 파이프라인입니다.

## 동작 방식

0. **음악 라이브러리 동기화** (`scripts/sync_music_library.py`) — 사용자가 Google Drive 폴더에 미리 채워 넣은 Suno 음원(무편집 mp3)을 매 실행마다 로컬로 내려받습니다. 계속 늘어나는 대용량 파일을 git에 쌓지 않기 위해 저장소가 아닌 Drive에 보관합니다. 서비스 계정 인증(`GDRIVE_SERVICE_ACCOUNT_JSON`, `GDRIVE_MUSIC_FOLDER_ID`)이 설정되어 있을 때만 동작하고, 없으면 저장소의 `music_library_dir` 설정을 그대로 사용합니다(로컬 개발용).
1. **음악 조립** (`scripts/assemble_music.py`) — 동기화된 음원 전체를 매번 셔플된 순서로 한 번씩 이어붙이고, 그중 일부(`audio.reuse_ratio`, 기본 25%)를 무작위로 한 번 더 섞어 넣습니다. 목표 길이를 강제하지 않아 어떤 곡이 재사용되느냐에 따라 매번 총 길이가 자연스럽게 달라집니다 (예: 라이브러리가 약 1시간 50분 분량이면 결과물은 대략 2시간 10분~2시간 30분 사이를 오갑니다). API 호출 없음, 비용 $0.
2. **이미지 생성** (`scripts/generate_image.py`) — Gemini API(gemini-2.5-flash-image)로 씬 이미지를 1장 생성합니다 (무료 등급 하루 500장, 비용 $0).
3. **영상 조립** (`scripts/build_video.py`) — 정적 이미지를 애니메이션 없이 오디오 길이만큼 반복하는 mp4를 ffmpeg로 만듭니다.
4. **메타데이터 생성** (`scripts/generate_metadata.py`) — 제목/설명/태그를 여러 문구 풀의 조합으로 생성합니다.
5. **업로드** (`scripts/upload_youtube.py`) — YouTube Data API v3로 무인 업로드합니다.

`scripts/pipeline.py`가 이 다섯 단계를 순서대로 실행하며, `.github/workflows/publish-video.yml`이 매일 자동으로 이 파이프라인을 실행합니다.

## 시작하기

처음 설정하려면 **[SETUP.md](./SETUP.md)** 를 따라 API 키/OAuth 자격 증명을 준비하고, Google Drive 폴더에 음원을 채워 넣으세요.

## 로컬에서 파이프라인 검증하기

```bash
pip install -r requirements.txt

# 업로드 없이 파이프라인 전체를 검증 (GEMINI_API_KEY는 여전히 필요)
GEMINI_API_KEY=... python3 scripts/pipeline.py --dry-run

# 유닛 테스트
python3 -m unittest discover -s tests
```

## 설정 변경

- `config/settings.yml` — 곡 재사용 비율(`audio.reuse_ratio`), 해상도, fps, privacy_status 등
- `config/scenes.yml` — 비주얼 씬 프롬프트 풀
- `config/title_templates.yml` — 제목/설명/태그 문구 풀

## 상태 관리

`state/state.json`은 각 로테이션 풀(곡/씬/제목문구 등)의 진행 상황을 기록합니다. 워크플로우가 성공적으로 끝난 실행에 대해서만 갱신되어 커밋됩니다.
