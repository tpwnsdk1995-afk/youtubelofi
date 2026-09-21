"""Analytics 스코프 토큰 재발급을 최대한 대신해 준다.

노출수·CTR은 YouTube Analytics API 소관인데, 이 API는 device flow를 지원하지 않아
최초 설정에 쓴 모바일 절차를 재사용할 수 없다. 웹 애플리케이션 OAuth로 다시 받아야
하고, 그 과정에서 브라우저 구글 로그인이 꼭 필요하다 — **그 로그인만은 사람이 해야
하고 에이전트가 대신할 수 없다.** 나머지(주소 조립, 토큰 교환)는 전부 여기서 한다.

두 가지 모드가 있다.

- `--mode url`    : 로그인 주소를 만들어 텔레그램으로 보낸다. 폰에서 누르기만 하면 된다.
- `--mode exchange --code <코드>` : 로그인 후 받은 코드를 refresh token으로 바꿔
                    텔레그램으로 보낸다.

**토큰은 로그에 절대 찍지 않는다.** 워크플로우 로그는 저장소 접근 권한이 있으면
누구나 보므로, 발급된 토큰은 사장님 텔레그램으로만 간다.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

import send_telegram_message as stm

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# 업로드(youtube)와 분석(yt-analytics.readonly)을 **함께** 요청해야 refresh token
# 하나로 둘 다 된다. 따로 받으면 업로드가 멈춘다.
SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

# 웹 애플리케이션 OAuth 클라이언트에 등록해야 하는 리디렉션 주소.
# 실제로 연결되진 않고(오류 페이지가 뜬다) 주소창의 코드만 쓰면 된다.
REDIRECT_URI = "http://localhost"


def build_auth_url(client_id):
    """동의 화면 주소를 만든다.

    `prompt=consent`와 `access_type=offline`이 둘 다 있어야 refresh token이 나온다.
    하나라도 빠지면 access token만 받고 끝나 매번 다시 로그인해야 한다.
    """
    if not client_id:
        raise ValueError("YOUTUBE_CLIENT_ID가 비어 있습니다")
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "scope": " ".join(SCOPES),
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def exchange_code(client_id, client_secret, code):
    """인증 코드를 토큰으로 바꾼다. refresh_token이 없으면 실패로 본다.

    코드를 한 번 쓰면 재사용할 수 없으므로, refresh_token 없이 성공 처리하면
    사장님이 처음부터 다시 로그인해야 한다는 걸 나중에야 알게 된다.
    """
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_ENDPOINT, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        payload = json.loads(resp.read())
    if not payload.get("refresh_token"):
        raise RuntimeError(
            "응답에 refresh_token이 없습니다. 동의 화면 주소에 prompt=consent가 있었는지, "
            "그리고 코드를 처음 쓰는 것인지 확인해 주세요 (코드는 1회용입니다)."
        )
    return payload


def granted_scopes(payload):
    return (payload.get("scope") or "").split()


def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가 없습니다")
    chunks = stm.split_message(text)
    for i, chunk in enumerate(chunks, 1):
        suffix = f"\n\n({i}/{len(chunks)})" if len(chunks) > 1 else ""
        stm.send(token, chat_id, chunk + suffix)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Analytics 스코프 토큰 재발급 도우미")
    parser.add_argument("--mode", choices=["url", "exchange"], required=True)
    parser.add_argument("--code", default="", help="exchange 모드에서 쓸 인증 코드")
    args = parser.parse_args(argv)

    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()

    if args.mode == "url":
        url = build_auth_url(client_id)
        send(
            "🔑 Analytics 권한 받기 — 1단계\n\n"
            "아래 주소를 눌러 채널 계정으로 로그인하고 '허용'을 눌러 주세요.\n\n"
            f"{url}\n\n"
            "누르면 '사이트에 연결할 수 없음' 오류 페이지가 뜹니다. 정상입니다.\n"
            "그 페이지 주소창에서 code= 뒤부터 & 앞까지를 복사해 주세요.\n"
            "(주소가 길면 code= 부분이 화면 밖에 있을 수 있으니 끝까지 밀어서 보세요)\n\n"
            "복사한 코드를 Claude에게 주시면 나머지는 제가 합니다."
        )
        # 주소 자체는 비밀이 아니라 로그에 남겨도 된다 (client_id는 공개 식별자).
        print("동의 화면 주소를 텔레그램으로 보냈습니다.")
        print(url)
        return 0

    code = args.code.strip()
    if not code:
        print("ERROR: --code 가 비어 있습니다", file=sys.stderr)
        return 2
    if not client_secret:
        print("ERROR: YOUTUBE_CLIENT_SECRET이 비어 있습니다", file=sys.stderr)
        return 2

    payload = exchange_code(client_id, client_secret, code)
    scopes = granted_scopes(payload)
    has_analytics = any("yt-analytics" in s for s in scopes)
    has_youtube = any(s.endswith("/auth/youtube") for s in scopes)

    send(
        "🔑 Analytics 권한 받기 — 2단계 완료\n\n"
        "아래 값을 GitHub Secrets의 YOUTUBE_REFRESH_TOKEN 에 붙여넣어 주세요.\n"
        "(Settings → Secrets and variables → Actions → YOUTUBE_REFRESH_TOKEN → Update)\n\n"
        f"{payload['refresh_token']}\n\n"
        f"받은 권한: 업로드 {'O' if has_youtube else 'X'} / 분석 {'O' if has_analytics else 'X'}\n"
        + ("" if has_analytics else
           "\n⚠️ 분석 권한이 없습니다. 동의 화면에서 두 항목을 모두 허용했는지 확인해 주세요.")
    )
    # 토큰은 절대 로그로 내보내지 않는다.
    print("refresh token을 텔레그램으로 보냈습니다 (로그에는 남기지 않음).")
    print(f"받은 권한: youtube={has_youtube} / analytics={has_analytics}")
    return 0 if (has_analytics and has_youtube) else 1


if __name__ == "__main__":
    sys.exit(main())
