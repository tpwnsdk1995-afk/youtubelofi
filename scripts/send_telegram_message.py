"""버튼 없이 텍스트만 있는 상태 알림을 텔레그램으로 보낸다 (예: 무응답으로 자동 공개됨)."""

import argparse
import json
import os
import sys
import urllib.request


def main():
    parser = argparse.ArgumentParser(description="텔레그램으로 텍스트 알림을 보낸다")
    parser.add_argument("--text", required=True)
    args = parser.parse_args()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 없습니다", file=sys.stderr)
        sys.exit(1)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = {"chat_id": chat_id, "text": args.text}
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
    print("텔레그램 알림 전송 완료")


if __name__ == "__main__":
    main()
