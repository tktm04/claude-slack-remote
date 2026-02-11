#!/bin/zsh
# Claude Code Slack Notification Hook
# Claude Codeの通知をSlackに転送する（idle通知は除外）

[ -f ~/.claude-slack-env ] && source ~/.claude-slack-env

[ -z "$SLACK_BOT_TOKEN" ] && exit 0
[ -z "$SLACK_CHANNEL_ID" ] && exit 0

INPUT=$(cat)
MESSAGE=$(echo "$INPUT" | jq -r '.message // "unknown"' 2>/dev/null || echo "unknown")

# Skip idle notifications
echo "$MESSAGE" | grep -qi "waiting for your input" && exit 0

PAYLOAD=$(jq -n --arg ch "$SLACK_CHANNEL_ID" --arg text ":bell: [${MACHINE_NAME:-unknown}] $MESSAGE" '{channel: $ch, text: $text}')

curl -s -X POST "https://slack.com/api/chat.postMessage" \
  -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "$PAYLOAD" > /dev/null 2>&1

exit 0
