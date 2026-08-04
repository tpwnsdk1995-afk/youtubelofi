<?php
/**
 * 텔레그램 승인/거부 버튼 클릭을 실시간 웹훅으로 받아서 즉시 GitHub 워크플로우를 트리거한다.
 *
 * 이 파일은 git에는 아래처럼 ${PLACEHOLDER} 형태의 자리표시자만 커밋되어 있고, 실제 시크릿 값은
 * deploy-telegram-webhook.yml이 배포할 때 envsubst로 채워넣은 뒤 카페24 서버에 업로드한다.
 * 즉, 실제 비밀값이 들어간 버전은 절대 git 이력에 남지 않는다.
 *
 * 실제 승인 처리(공개 전환)/거부 처리(영상 삭제)는 여기서 직접 하지 않고
 * handle-telegram-decision.yml 워크플로우에 위임한다 — 유튜브 API 자격증명 등 민감한
 * 값을 이 카페24 서버에는 전혀 두지 않기 위함이다.
 */

$TELEGRAM_BOT_TOKEN = '${TELEGRAM_BOT_TOKEN}';
$WEBHOOK_SECRET = '${TELEGRAM_WEBHOOK_SECRET}';
$DISPATCH_PAT = '${DISPATCH_PAT}';
$GH_OWNER = '${GH_OWNER}';
$GH_REPO = '${GH_REPO}';
$GH_REF = '${GH_REF}';
$WORKFLOW_FILE = 'handle-telegram-decision.yml';

// 1. 텔레그램이 보낸 요청이 맞는지 확인 (setWebhook 등록 시 지정한 secret_token 헤더 검증).
//    이게 없으면 누구든 이 URL로 POST를 쏴서 임의 영상을 승인/삭제시킬 수 있으므로 필수.
$incoming_secret = $_SERVER['HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN'] ?? '';
if (!hash_equals($WEBHOOK_SECRET, $incoming_secret)) {
    http_response_code(403);
    exit('forbidden');
}

$body = file_get_contents('php://input');
$update = json_decode($body, true);

$cq = $update['callback_query'] ?? null;
if (!$cq) {
    // callback_query가 아닌 업데이트(일반 메시지 등)는 무시하고 200으로 응답해
    // 텔레그램이 재전송을 반복하지 않게 한다.
    http_response_code(200);
    exit('ignored');
}

$data = $cq['data'] ?? '';
if (!preg_match('/^(approve|reject):(.+)$/', $data, $m)) {
    http_response_code(200);
    exit('unrecognized');
}
$decision = $m[1];
$video_id = $m[2];

function tg_call($token, $method, $body) {
    $ch = curl_init("https://api.telegram.org/bot{$token}/{$method}");
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, ['Content-Type: application/json']);
    curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 8);
    $result = curl_exec($ch);
    curl_close($ch);
    return $result;
}

// 2. 콜백 확인 처리(버튼 로딩 스피너 제거) + 메시지에 결과 라벨 붙이기.
//    실패해도(콜백 만료 등) 아래 3번 GitHub 트리거는 반드시 진행되어야 하므로 결과를 검사하지 않는다.
$label = $decision === 'approve' ? '✅ 승인됨 (공개 전환)' : '❌ 거부됨 (영상 삭제됨)';
tg_call($TELEGRAM_BOT_TOKEN, 'answerCallbackQuery', [
    'callback_query_id' => $cq['id'],
    'text' => $decision === 'approve' ? '승인 처리됨' : '거부 처리됨 (영상 삭제)',
]);
$message = $cq['message'] ?? null;
if ($message && isset($message['message_id'], $message['chat']['id'])) {
    tg_call($TELEGRAM_BOT_TOKEN, 'editMessageText', [
        'chat_id' => $message['chat']['id'],
        'message_id' => $message['message_id'],
        'text' => ($message['text'] ?? '') . "\n\n— {$label}",
    ]);
}

// 3. GitHub workflow_dispatch 호출 — 실제 승인/거부 반영은 handle-telegram-decision.yml이 담당.
$ch = curl_init("https://api.github.com/repos/{$GH_OWNER}/{$GH_REPO}/actions/workflows/{$WORKFLOW_FILE}/dispatches");
curl_setopt($ch, CURLOPT_POST, true);
curl_setopt($ch, CURLOPT_HTTPHEADER, [
    'Content-Type: application/json',
    'Accept: application/vnd.github+json',
    "Authorization: Bearer {$DISPATCH_PAT}",
    'X-GitHub-Api-Version: 2022-11-28',
    'User-Agent: noamusic-telegram-webhook',
]);
curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode([
    'ref' => $GH_REF,
    'inputs' => [
        'video_id' => $video_id,
        'decision' => $decision,
    ],
]));
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 8);
curl_exec($ch);
curl_close($ch);

http_response_code(200);
echo 'ok';
