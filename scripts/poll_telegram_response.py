"""텔레그램에서 승인/거부 버튼 응답이 왔는지 확인한다.
매칭되는 게 있으면 콜백을 확인 처리(answerCallbackQuery)하고 버튼을 제거한 뒤
"approve" 또는 "reject"를 stdout에 출력한다. 없으면 "none"을 출력한다.
"""

import argparse
import json
import os
import sys
import urllib.request


def call(bot_token, method, body=None):
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def call_best_effort(bot_token, method, body=None):
    """토스트 표시/버튼 제거 등 부수적인 호출. 콜백 쿼리 만료 등으로 실패해도
    이미 확정된 승인/거부 판정 자체는 절대 막지 않아야 하므로 예외를 삼킨다."""
    try:
        call(bot_token, method, body)
    except Exception as e:
        print(f"WARNING: {method} 실패 (무시하고 계속 진행): {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="텔레그램 승인/거부 응답을 확인한다")
    parser.add_argument("--video-id", required=True)
    args = parser.parse_args()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("ERROR: TELEGRAM_BOT_TOKEN 환경변수가 없습니다", file=sys.stderr)
        sys.exit(1)

    updates = call(bot_token, "getUpdates", {"timeout": 0})
    if not updates.get("ok"):
        print(f"ERROR: getUpdates 실패: {updates}", file=sys.stderr)
        sys.exit(1)

    results = updates["result"]
    decision = "none"
    matched_query = None

    for update in results:
        cq = update.get("callback_query")
        if not cq:
            continue
        data = cq.get("data", "")
        if data == f"approve:{args.video_id}":
            decision = "approve"
            matched_query = cq
        elif data == f"reject:{args.video_id}":
            decision = "reject"
            matched_query = cq

    if matched_query:
        call_best_effort(bot_token, "answerCallbackQuery", {
            "callback_query_id": matched_query["id"],
            "text": "승인 처리됨" if decision == "approve" else "거부 처리됨",
        })
        message = matched_query.get("message", {})
        if message.get("message_id") is not None and message.get("chat", {}).get("id") is not None:
            label = "✅ 승인됨 (공개 전환)" if decision == "approve" else "❌ 거부됨 (비공개 유지)"
            call_best_effort(bot_token, "editMessageText", {
                "chat_id": message["chat"]["id"],
                "message_id": message["message_id"],
                "text": f"{message.get('text', '')}\n\n— {label}",
            })

    # 가져온 업데이트는 전부 offset을 넘겨 소비 처리(다음 폴링에서 중복 조회 방지).
    # 이것도 실패하면 다음 폴링 때 같은 업데이트를 다시 보게 될 뿐 치명적이지 않으므로
    # best-effort로 처리해 이미 확정된 decision 출력을 막지 않는다.
    if results:
        max_update_id = max(u["update_id"] for u in results)
        call_best_effort(bot_token, "getUpdates", {"offset": max_update_id + 1, "timeout": 0})

    print(decision)


if __name__ == "__main__":
    main()
