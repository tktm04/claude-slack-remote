#!/bin/bash
# Claude Code Slack Daemon - Start
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HOME/.claude-slack-env"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found. Run setup.sh first."
    exit 1
fi

source "$ENV_FILE"

# Find python3
PYTHON=""
for p in /usr/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)"; do
    if [ -n "$p" ] && [ -x "$p" ]; then
        PYTHON="$p"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "ERROR: python3 not found"
    exit 1
fi

# Kill existing daemon
tmux kill-session -t claude-daemon 2>/dev/null || true

# Start in tmux
tmux new-session -d -s claude-daemon "$PYTHON $SCRIPT_DIR/claude_slack_daemon.py"
echo "Daemon started"
echo "  View:  tmux attach -t claude-daemon"
echo "  Stop:  tmux kill-session -t claude-daemon"
