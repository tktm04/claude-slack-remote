#!/bin/zsh
# Claude Code Slack Approval Hook
# PermissionRequest時にSlackへ通知→リアクションで承認/拒否
#
# 環境変数(~/.claude-slack-env):
#   SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, MACHINE_NAME

# 環境変数読み込み
[ -f ~/.claude-slack-env ] && source ~/.claude-slack-env

SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:?SLACK_BOT_TOKEN is not set}"
SLACK_CHANNEL_ID="${SLACK_CHANNEL_ID:?SLACK_CHANNEL_ID is not set}"
MACHINE_NAME="${MACHINE_NAME:-unknown}"
TIMEOUT_SECONDS="${APPROVAL_TIMEOUT:-300}"

# stdin からJSON読み取り
INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // "unknown"')
TOOL_INPUT=$(echo "$INPUT" | jq -r '.tool_input // {} | tostring' | head -c 500)

# Slack通知
MESSAGE=$(cat <<EOF
{
  "channel": "${SLACK_CHANNEL_ID}",
  "text": ":bell: *[${MACHINE_NAME}]* Permission request\n\nTool: \`${TOOL_NAME}\`\n\`\`\`${TOOL_INPUT}\`\`\`\n:white_check_mark: to allow / :x: to deny (${TIMEOUT_SECONDS}s timeout)"
}
EOF
)

RESPONSE=$(curl -s -X POST "https://slack.com/api/chat.postMessage" \
  -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "$MESSAGE")

TS=$(echo "$RESPONSE" | jq -r '.ts')
OK=$(echo "$RESPONSE" | jq -r '.ok')

if [ "$OK" != "true" ]; then
  echo '{"decision": "ask"}'
  exit 0
fi

# リアクション監視
ELAPSED=0
POLL_INTERVAL=2

while [ $ELAPSED -lt $TIMEOUT_SECONDS ]; do
  REACTIONS=$(curl -s "https://slack.com/api/reactions.get" \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
    -G --data-urlencode "channel=${SLACK_CHANNEL_ID}" \
    --data-urlencode "timestamp=${TS}" | jq -r '.message.reactions[]?.name' 2>/dev/null || true)

  if echo "$REACTIONS" | grep -qE "white_check_mark|heavy_check_mark"; then
    curl -s -X POST "https://slack.com/api/chat.postMessage" \
      -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
      -H "Content-Type: application/json; charset=utf-8" \
      -d "{\"channel\":\"${SLACK_CHANNEL_ID}\",\"thread_ts\":\"${TS}\",\"text\":\"Approved\"}" > /dev/null
    echo '{"decision": "allow"}'
    exit 0
  fi

  if echo "$REACTIONS" | grep -q "^x$"; then
    curl -s -X POST "https://slack.com/api/chat.postMessage" \
      -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
      -H "Content-Type: application/json; charset=utf-8" \
      -d "{\"channel\":\"${SLACK_CHANNEL_ID}\",\"thread_ts\":\"${TS}\",\"text\":\"Denied\"}" > /dev/null
    echo '{"decision": "deny", "message": "Denied via Slack"}'
    exit 0
  fi

  sleep $POLL_INTERVAL
  ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

# Timeout
curl -s -X POST "https://slack.com/api/chat.postMessage" \
  -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "{\"channel\":\"${SLACK_CHANNEL_ID}\",\"thread_ts\":\"${TS}\",\"text\":\"Timeout - denied\"}" > /dev/null

echo '{"decision": "deny", "message": "Approval timeout"}'
exit 0
