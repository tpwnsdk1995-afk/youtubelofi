"""새로 생성된 영상의 공개 확인 요청을 텔레그램으로 보낸다.
인라인 버튼(승인/거부)의 callback_data에 video_id를 실어 보내서,
poll_telegram_response.py가 나중에 어떤 영상에 대한 응답인지 식별할 수 있게 한다.
"""

import argparse
import json
import os
import sys
import urllib.request


def send_message(bot_token, chat_id, text, video_id):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ 승인 (공개)", "callback_data": f"approve:{video_id}"},
                {"text": "❌ 거부 (비공개 유지)", "callback_data": f"reject:{video_id}"},
            ]]
        },
    }
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
    return result


def main():
    parser = argparse.ArgumentParser(description="공개 확인 요청을 텔레그램으로 보낸다")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 없습니다", file=sys.stderr)
        sys.exit(1)

    text = (
        f"오늘 영상 확인해주세요\n\n"
        f"제목: {args.title}\n"
        f"비공개 링크: https://youtu.be/{args.video_id}\n\n"
        f"응답 없으면 19:14 KST에 자동으로 공개됩니다."
    )
    send_message(bot_token, chat_id, text, args.video_id)
    print(f"텔레그램 확인 요청 전송 완료: {args.video_id}")


if __name__ == "__main__":
    main()
