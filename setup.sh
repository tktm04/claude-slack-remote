#!/bin/bash
# Claude Code Slack Remote - Setup
#
# Usage:
#   bash setup.sh                              # 対話モード
#   bash setup.sh -t xoxb-... -c C0XX -n Mac   # ワンライナー

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HOME/.claude-slack-env"

# --- Parse args ---
ARG_TOKEN=""
ARG_CHANNEL=""
ARG_NAME=""
ARG_WORKDIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--token)   ARG_TOKEN="$2"; shift 2 ;;
        -c|--channel) ARG_CHANNEL="$2"; shift 2 ;;
        -n|--name)    ARG_NAME="$2"; shift 2 ;;
        -d|--dir)     ARG_WORKDIR="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bash setup.sh [-t TOKEN] [-c CHANNEL_ID] [-n MACHINE_NAME] [-d WORK_DIR]"
            echo ""
            echo "Options:"
            echo "  -t, --token    Slack Bot Token (xoxb-...)"
            echo "  -c, --channel  Slack Channel ID (C0...)"
            echo "  -n, --name     Machine display name"
            echo "  -d, --dir      Default working directory"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "=== Claude Code Slack Remote Setup ==="
echo ""

# --- 1. Copy files ---
mkdir -p "$HOME/.claude/hooks" "$HOME/.claude/daemon"

cp "$SCRIPT_DIR/hooks/approve.sh" "$HOME/.claude/hooks/approve.sh"
cp "$SCRIPT_DIR/hooks/notify.sh" "$HOME/.claude/hooks/notify.sh"
chmod +x "$HOME/.claude/hooks/approve.sh" "$HOME/.claude/hooks/notify.sh"

cp "$SCRIPT_DIR/daemon/claude_slack_daemon.py" "$HOME/.claude/daemon/claude_slack_daemon.py"
cp "$SCRIPT_DIR/daemon/start_daemon.sh" "$HOME/.claude/daemon/start_daemon.sh"
cp "$SCRIPT_DIR/daemon/stop_daemon.sh" "$HOME/.claude/daemon/stop_daemon.sh"
chmod +x "$HOME/.claude/daemon/start_daemon.sh" "$HOME/.claude/daemon/stop_daemon.sh"

echo "Copied files to ~/.claude/"

# --- 2. Environment variables ---
echo ""

# Load existing values if present
EXISTING_TOKEN=""
EXISTING_CHANNEL=""
EXISTING_NAME=""
EXISTING_WORKDIR=""
if [ -f "$ENV_FILE" ]; then
    EXISTING_TOKEN=$(grep '^export SLACK_BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | sed 's/^export SLACK_BOT_TOKEN="//' | sed 's/"$//' || true)
    EXISTING_CHANNEL=$(grep '^export SLACK_CHANNEL_ID=' "$ENV_FILE" 2>/dev/null | sed 's/^export SLACK_CHANNEL_ID="//' | sed 's/"$//' || true)
    EXISTING_NAME=$(grep '^export MACHINE_NAME=' "$ENV_FILE" 2>/dev/null | sed 's/^export MACHINE_NAME="//' | sed 's/"$//' || true)
    EXISTING_WORKDIR=$(grep '^export CLAUDE_WORK_DIR=' "$ENV_FILE" 2>/dev/null | sed 's/^export CLAUDE_WORK_DIR="//' | sed 's/"$//' || true)
fi

# Determine values: arg > existing > prompt
if [ -n "$ARG_TOKEN" ]; then
    TOKEN="$ARG_TOKEN"
elif [ -n "$EXISTING_TOKEN" ] && [ "$EXISTING_TOKEN" != "xoxb-YOUR-TOKEN-HERE" ]; then
    TOKEN="$EXISTING_TOKEN"
    echo "Using existing SLACK_BOT_TOKEN"
else
    echo -n "Slack Bot Token (xoxb-...): "
    read -r TOKEN
fi

if [ -n "$ARG_CHANNEL" ]; then
    CHANNEL="$ARG_CHANNEL"
elif [ -n "$EXISTING_CHANNEL" ] && [ "$EXISTING_CHANNEL" != "C0XXXXXXXXX" ]; then
    CHANNEL="$EXISTING_CHANNEL"
    echo "Using existing SLACK_CHANNEL_ID: $CHANNEL"
else
    echo -n "Slack Channel ID (C0...): "
    read -r CHANNEL
fi

if [ -n "$ARG_NAME" ]; then
    NAME="$ARG_NAME"
elif [ -n "$EXISTING_NAME" ]; then
    NAME="$EXISTING_NAME"
    echo "Using existing MACHINE_NAME: $NAME"
else
    echo -n "Machine name (e.g. Mac, Ubuntu, miyabi, ABCI): "
    read -r NAME
fi

WORKDIR="${ARG_WORKDIR:-${EXISTING_WORKDIR:-$HOME}}"

# Write env file
cat > "$ENV_FILE" << EOF
# Claude Code Slack Remote
export SLACK_BOT_TOKEN="$TOKEN"
export SLACK_CHANNEL_ID="$CHANNEL"
export MACHINE_NAME="$NAME"
export CLAUDE_WORK_DIR="$WORKDIR"
# export APPROVAL_TIMEOUT=300
# export CLAUDE_TIMEOUT=600
EOF

chmod 600 "$ENV_FILE"
echo ""
echo "Wrote $ENV_FILE (permissions: 600)"

# --- 3. Shell config ---
for RC in "$HOME/.bashrc" "$HOME/.zshrc"; do
    if [ -f "$RC" ] && ! grep -q "claude-slack-env" "$RC" 2>/dev/null; then
        echo "" >> "$RC"
        echo "# Claude Code Slack Remote" >> "$RC"
        echo '[ -f ~/.claude-slack-env ] && source ~/.claude-slack-env' >> "$RC"
        echo "Added env loading to $RC"
    fi
done

# --- 4. Dependencies ---
echo ""
echo "Dependencies:"
for cmd in curl jq tmux claude; do
    if command -v $cmd &>/dev/null; then
        echo "  $cmd: OK"
    else
        echo "  $cmd: MISSING"
    fi
done

PYTHON=""
for p in /usr/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
    if [ -n "$p" ] && [ -x "$p" ]; then
        PYTHON="$p"
        break
    fi
done
[ -n "$PYTHON" ] && echo "  python3: OK ($PYTHON)" || echo "  python3: MISSING"

# --- Done ---
echo ""
echo "=== Done ==="
echo ""
echo "Start daemon:"
echo "  source ~/.bashrc && ~/.claude/daemon/start_daemon.sh"
