#!/bin/zsh
# Claude Code Slack Notification Hook
# PC操作中にSlackにも通知を送る（承認はPC側）

[ -f ~/.claude-slack-env ] && source ~/.claude-slack-env

SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:?SLACK_BOT_TOKEN is not set}"
SLACK_CHANNEL_ID="${SLACK_CHANNEL_ID:?SLACK_CHANNEL_ID is not set}"
MACHINE_NAME="${MACHINE_NAME:-unknown}"

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // "unknown"')
TOOL_INPUT=$(echo "$INPUT" | jq -r '.tool_input // {} | tostring' | head -c 300)

curl -s -X POST "https://slack.com/api/chat.postMessage" \
  -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "{
    \"channel\": \"${SLACK_CHANNEL_ID}\",
    \"text\": \":bell: [${MACHINE_NAME}] \`${TOOL_NAME}\`\n\`\`\`${TOOL_INPUT}\`\`\`\"
  }" > /dev/null 2>&1

# 通知のみ。承認はPC側で行う
echo '{"decision": "ask"}'
exit 0
