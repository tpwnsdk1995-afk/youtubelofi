# YouTube 로파이 채널 자동화 파이프라인

학습/작업용 로파이(lofi) 음악 영상을 자동으로 만들어 유튜브에 업로드하는 파이프라인입니다.

## 동작 방식

1. **음악 조립** (`scripts/assemble_music.py`) — `music_library/`에 사용자가 미리 채워 넣은 Suno 음원(무편집 mp3)을 셔플백 방식으로 골라 목표 길이(기본 2시간)만큼 크로스페이드로 이어붙입니다. API 호출 없음, 비용 $0.
2. **이미지 생성** (`scripts/generate_image.py`) — Stability AI Stable Image Core API로 씬 이미지를 1장 생성합니다 (영상당 약 $0.03).
3. **영상 조립** (`scripts/build_video.py`) — 정적 이미지를 애니메이션 없이 오디오 길이만큼 반복하는 mp4를 ffmpeg로 만듭니다.
4. **메타데이터 생성** (`scripts/generate_metadata.py`) — 제목/설명/태그를 여러 문구 풀의 조합으로 생성합니다.
5. **업로드** (`scripts/upload_youtube.py`) — YouTube Data API v3로 무인 업로드합니다.

`scripts/pipeline.py`가 이 다섯 단계를 순서대로 실행하며, `.github/workflows/publish-video.yml`이 매일 자동으로 이 파이프라인을 실행합니다.

## 시작하기

처음 설정하려면 **[SETUP.md](./SETUP.md)** 를 따라 API 키/OAuth 자격 증명을 준비하고, `music_library/`에 음원을 채워 넣으세요.

## 로컬에서 파이프라인 검증하기

```bash
pip install -r requirements.txt

# 업로드 없이 파이프라인 전체를 검증 (STABILITY_IMAGE_API_KEY는 여전히 필요)
STABILITY_IMAGE_API_KEY=... python3 scripts/pipeline.py --dry-run

# 유닛 테스트
python3 -m unittest discover -s tests
```

## 설정 변경

- `config/settings.yml` — 영상 길이(`video.target_duration_seconds`), 해상도, fps, privacy_status 등
- `config/scenes.yml` — 비주얼 씬 프롬프트 풀
- `config/title_templates.yml` — 제목/설명/태그 문구 풀

## 상태 관리

`state/state.json`은 각 로테이션 풀(곡/씬/제목문구 등)의 진행 상황을 기록합니다. 워크플로우가 성공적으로 끝난 실행에 대해서만 갱신되어 커밋됩니다.
