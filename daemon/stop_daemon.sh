#!/bin/bash
# Claude Code Slack Daemon - Stop
tmux kill-session -t claude-daemon 2>/dev/null && echo "Daemon stopped" || echo "Daemon not running"
