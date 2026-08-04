<?php
/**
 * 텔레그램 승인/거부 버튼 클릭을 실시간 웹훅으로 받아서 즉시 GitHub 워크플로우를 트리거한다.
 *
 * 이 파일은 git에는 아래처럼 ${PLACEHOLDER} 형태의 자리표시자만 커밋되어 있고, 실제 시크릿 값은
 * deploy-telegram-webhook.yml이 배포할 때 envsubst로 채워넣은 뒤 카페24 서버에 업로드한다.
 * 즉, 실제 비밀값이 들어간 버전은 절대 git 이력에 남지 않는다.
 *
 * 주의: envsubst는 ${VAR}뿐 아니라 $VAR(중괄호 없는 형태)도 치환 대상으로 삼는다. 그래서
 * 아래 PHP 변수 이름은 치환 화이트리스트 이름(TELEGRAM_BOT_TOKEN 등)과 절대 겹치지 않도록
 * cfgXxx 형태로 지었다 — 겹치면 "$TELEGRAM_BOT_TOKEN = '...'"의 좌변 변수명 자체가 시크릿
 * 값으로 치환되어버려 문법 오류가 난다 (실제로 겪은 버그).
 *
 * 실제 승인 처리(공개 전환)/거부 처리(영상 삭제)는 여기서 직접 하지 않고
 * handle-telegram-decision.yml 워크플로우에 위임한다 — 유튜브 API 자격증명 등 민감한
 * 값을 이 카페24 서버에는 전혀 두지 않기 위함이다.
 */

$cfgBotToken = '${TELEGRAM_BOT_TOKEN}';
$cfgWebhookSecret = '${TELEGRAM_WEBHOOK_SECRET}';
$cfgDispatchPat = '${DISPATCH_PAT}';
$cfgGhOwner = '${GH_OWNER}';
$cfgGhRepo = '${GH_REPO}';
$cfgGhRef = '${GH_REF}';
$cfgWorkflowFile = 'handle-telegram-decision.yml';

// 1. 텔레그램이 보낸 요청이 맞는지 확인 (setWebhook 등록 시 지정한 secret_token 헤더 검증).
//    이게 없으면 누구든 이 URL로 POST를 쏴서 임의 영상을 승인/삭제시킬 수 있으므로 필수.
$incomingSecret = $_SERVER['HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN'] ?? '';
if (!hash_equals($cfgWebhookSecret, $incomingSecret)) {
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
$videoId = $m[2];

function tgCall($token, $method, $body) {
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
tgCall($cfgBotToken, 'answerCallbackQuery', [
    'callback_query_id' => $cq['id'],
    'text' => $decision === 'approve' ? '승인 처리됨' : '거부 처리됨 (영상 삭제)',
]);
$message = $cq['message'] ?? null;
if ($message && isset($message['message_id'], $message['chat']['id'])) {
    tgCall($cfgBotToken, 'editMessageText', [
        'chat_id' => $message['chat']['id'],
        'message_id' => $message['message_id'],
        'text' => ($message['text'] ?? '') . "\n\n— {$label}",
    ]);
}

// 3. GitHub workflow_dispatch 호출 — 실제 승인/거부 반영은 handle-telegram-decision.yml이 담당.
$ch = curl_init("https://api.github.com/repos/{$cfgGhOwner}/{$cfgGhRepo}/actions/workflows/{$cfgWorkflowFile}/dispatches");
curl_setopt($ch, CURLOPT_POST, true);
curl_setopt($ch, CURLOPT_HTTPHEADER, [
    'Content-Type: application/json',
    'Accept: application/vnd.github+json',
    "Authorization: Bearer {$cfgDispatchPat}",
    'X-GitHub-Api-Version: 2022-11-28',
    'User-Agent: noamusic-telegram-webhook',
]);
curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode([
    'ref' => $cfgGhRef,
    'inputs' => [
        'video_id' => $videoId,
        'decision' => $decision,
    ],
]));
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 8);
curl_exec($ch);
curl_close($ch);

http_response_code(200);
echo 'ok';
