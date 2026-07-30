"""Google Drive의 음악 라이브러리 폴더에서 mp3 파일을 로컬로 내려받는다.
저장소에는 더 이상 음원 파일을 커밋하지 않고, 실행 시점마다 Drive에서 동기화한다.
Drive 폴더는 서비스 계정(Service Account) 이메일에 '뷰어'로 공유되어 있어야 한다.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
AUDIO_QUERY = "mimeType='audio/mpeg'"

REQUIRED_ENV_VARS = ["GDRIVE_SERVICE_ACCOUNT_JSON", "GDRIVE_MUSIC_FOLDER_ID"]


def get_drive_service(service_account_json):
    info = json.loads(service_account_json)
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=credentials)


def list_library_files(service, folder_id):
    """폴더 안의 mp3 파일 목록을 (페이지네이션 포함) 전부 가져온다."""
    files = []
    page_token = None
    query = f"'{folder_id}' in parents and {AUDIO_QUERY} and trashed=false"
    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name)",
            pageToken=page_token,
            pageSize=1000,
        ).execute()
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def download_file(service, file_id, dest_path):
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def sync_library(service, folder_id, local_dir):
    """Drive 폴더의 모든 mp3를 local_dir로 내려받는다. 실행 중 재시도 등으로
    같은 이름의 파일이 이미 존재하면 다시 받지 않는다."""
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    files = list_library_files(service, folder_id)
    downloaded = []
    for f in files:
        dest = local_dir / f["name"]
        if dest.exists():
            continue
        download_file(service, f["id"], dest)
        downloaded.append(f["name"])
    return files, downloaded


def main():
    parser = argparse.ArgumentParser(description="Google Drive 음악 라이브러리를 로컬로 동기화한다")
    parser.add_argument("--dest", default="music_library")
    args = parser.parse_args()

    env = os.environ
    missing = [name for name in REQUIRED_ENV_VARS if not env.get(name)]
    if missing:
        print(f"ERROR: 다음 환경변수가 설정되어 있지 않습니다: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    service = get_drive_service(env["GDRIVE_SERVICE_ACCOUNT_JSON"])
    files, downloaded = sync_library(service, env["GDRIVE_MUSIC_FOLDER_ID"], args.dest)
    print(f"동기화 완료: Drive 폴더 내 {len(files)}개 중 {len(downloaded)}개 새로 다운로드 -> {args.dest}")


if __name__ == "__main__":
    main()
