"""버튼 없이 텍스트만 있는 상태 알림을 텔레그램으로 보낸다 (예: 무응답 자동 공개, 리포트).

텔레그램 sendMessage는 한 통당 4096자 제한이 있다. 주간/월간 리포트가 이 길이를
넘기면 API가 통째로 거부해 워크플로우가 실패하므로, 긴 본문은 줄 경계에서 잘라
여러 통으로 나눠 보낸다 (표·목록이 문장 중간에서 끊기지 않게 줄 단위로 자른다).
"""

import argparse
import json
import os
import sys
import urllib.request

TELEGRAM_LIMIT = 4096
# 분할 표시("(1/3)" 등)를 덧붙일 여유를 남긴다
CHUNK_SIZE = TELEGRAM_LIMIT - 64


def split_message(text, chunk_size=CHUNK_SIZE):
    """줄 경계를 지키며 chunk_size 이하 조각들로 나눈다.
    한 줄 자체가 너무 길면 그 줄만 강제로 자른다."""
    lines = text.split("\n")
    chunks = []
    current = []
    length = 0

    def flush():
        if current:
            chunks.append("\n".join(current))

    for line in lines:
        while len(line) > chunk_size:
            flush()
            current.clear()
            length = 0
            chunks.append(line[:chunk_size])
            line = line[chunk_size:]
        added = len(line) + (1 if current else 0)
        if length + added > chunk_size:
            flush()
            current = [line]
            length = len(line)
        else:
            current.append(line)
            length += added
    flush()
    return chunks or [""]


def send(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = {"chat_id": chat_id, "text": text}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {result}")


def main():
    parser = argparse.ArgumentParser(description="텔레그램으로 텍스트 알림을 보낸다")
    parser.add_argument("--text", required=True)
    args = parser.parse_args()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 없습니다", file=sys.stderr)
        sys.exit(1)

    chunks = split_message(args.text)
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        suffix = f"\n\n({i}/{total})" if total > 1 else ""
        send(bot_token, chat_id, chunk + suffix)
    print(f"텔레그램 알림 전송 완료 ({total}통)")


if __name__ == "__main__":
    main()
