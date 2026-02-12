#!/usr/bin/env python3
"""
Claude Code Slack Daemon
Slackからリモートでclaude codeを操作するためのデーモン。

使い方:
  source ~/.claude-slack-env
  python3 claude_slack_daemon.py
"""

import os, sys, json, time, subprocess, signal, logging
import urllib.request, urllib.parse

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.expanduser("~/.claude/slack-daemon.log")),
    ]
)
log = logging.getLogger(__name__)

# --- Config ---
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
MACHINE_NAME = os.environ.get("MACHINE_NAME", "unknown")
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "600"))
SHELL_TIMEOUT = int(os.environ.get("SHELL_TIMEOUT", "30"))
DEFAULT_CWD = os.environ.get("CLAUDE_WORK_DIR", os.path.expanduser("~"))

if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
    print("ERROR: SLACK_BOT_TOKEN and SLACK_CHANNEL_ID must be set")
    print("  source ~/.claude-slack-env")
    sys.exit(1)

# Find claude binary (alias is not available in subprocess)
import shutil
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser("~/.claude/local/claude")
if not os.path.isfile(CLAUDE_BIN):
    print(f"ERROR: claude not found (tried: {CLAUDE_BIN})")
    sys.exit(1)
log.info(f"Claude binary: {CLAUDE_BIN}")

# Blocked shell commands
BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf $HOME",
    "mkfs",
    "dd if=",
    ":(){",
    "> /dev/sd",
    "chmod -R 777 /",
    ":(){ :|:",
]


# === Slack API ===

def slack_api(method, params=None, post_data=None):
    url = f"https://slack.com/api/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    if post_data:
        headers["Content-Type"] = "application/json"
        data = json.dumps(post_data).encode()
        req = urllib.request.Request(url, data=data, headers=headers)
    else:
        req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def send(text, thread_ts=None):
    payload = {"channel": SLACK_CHANNEL_ID, "text": text, "mrkdwn": True}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    resp = slack_api("chat.postMessage", post_data=payload)
    return resp.get("ts")


# === State ===

sessions = {}         # thread_ts -> session_id
thread_cwd = {}       # thread_ts -> working directory
active_threads = {}   # thread_ts -> last_seen_ts


def get_cwd(thread_ts):
    return thread_cwd.get(thread_ts, DEFAULT_CWD)


# === Claude Code ===

def run_claude(prompt, cwd, session_id=None, resume_last=False):
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json"]
    if session_id:
        cmd.extend(["--resume", session_id])
    elif resume_last:
        cmd.append("--continue")
    log.info(f"Running claude in {cwd}: {prompt[:80]}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT, cwd=cwd
        )
        stdout = result.stdout.strip()
        log.debug(f"stdout: {stdout[:300]}")
        if result.stderr.strip():
            log.debug(f"stderr: {result.stderr[:300]}")
        if stdout:
            try:
                data = json.loads(stdout)
                return data.get("result", stdout), data.get("session_id")
            except json.JSONDecodeError:
                return stdout, None
        return result.stderr.strip() or "(no output)", None
    except subprocess.TimeoutExpired:
        return f"Timeout ({CLAUDE_TIMEOUT}s)", None
    except Exception as e:
        log.error(f"run_claude error: {e}", exc_info=True)
        return f"Error: {e}", None


# === Shell command ===

def run_shell(cmd_str, cwd):
    for pat in BLOCKED_PATTERNS:
        if pat in cmd_str:
            return f"Blocked: {cmd_str}"
    try:
        result = subprocess.run(
            cmd_str, shell=True, capture_output=True, text=True,
            timeout=SHELL_TIMEOUT, cwd=cwd
        )
        output = (result.stdout + result.stderr).strip()
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Timeout ({SHELL_TIMEOUT}s)"
    except Exception as e:
        return f"Error: {e}"


# === Message Handler ===

def handle(msg, bot_user_id):
    text = msg.get("text", "").strip()
    ts = msg.get("ts")
    thread_ts = msg.get("thread_ts", ts)

    log.info(f"HANDLE: {text[:100]} (thread={thread_ts})")
    low = text.lower()

    # --- Built-in commands ---

    if low == "stop":
        send(":red_circle: Daemon stopped", thread_ts)
        sys.exit(0)

    if low == "status":
        cwd = get_cwd(thread_ts)
        lines = [f"*{MACHINE_NAME} Status*", f"cwd: `{cwd}`", ""]
        if sessions:
            lines.append("*Sessions:*")
            for t, s in sessions.items():
                lines.append(f"  `{s}`")
        else:
            lines.append("_No active sessions_")
        send("\n".join(lines), thread_ts)
        return

    if low == "new":
        sessions.pop(thread_ts, None)
        send(f"New session started\ncwd: `{get_cwd(thread_ts)}`", thread_ts)
        return

    if low.startswith("resume"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            sid = parts[1].strip()
            sessions[thread_ts] = sid
            send(f"Resuming session `{sid}`\ncwd: `{get_cwd(thread_ts)}`", thread_ts)
        else:
            sessions[thread_ts] = "__continue__"
            send(f"Resuming last session\ncwd: `{get_cwd(thread_ts)}`", thread_ts)
        return

    # --- Shell commands (! prefix) ---

    if text.startswith("!"):
        cmd_str = text[1:].strip()
        if not cmd_str:
            return
        cwd = get_cwd(thread_ts)

        # Special: cd changes thread working directory
        if cmd_str.startswith("cd "):
            path = os.path.expanduser(cmd_str[3:].strip())
            if os.path.isdir(path):
                thread_cwd[thread_ts] = path
                send(f"`{path}`", thread_ts)
            else:
                send(f"Not found: `{path}`", thread_ts)
            return

        log.info(f"Shell: {cmd_str} in {cwd}")
        output = run_shell(cmd_str, cwd)
        send(f"```\n{output[:3000]}\n```", thread_ts)
        return

    # --- Run Claude Code ---

    cwd = get_cwd(thread_ts)
    send(f":hourglass_flowing_sand: `{cwd}`", thread_ts)
    active_threads[thread_ts] = ts

    sid = sessions.get(thread_ts)
    resume_last = sid == "__continue__"
    if resume_last:
        sid = None

    response, new_sid = run_claude(text, cwd=cwd, session_id=sid, resume_last=resume_last)
    if new_sid:
        sessions[thread_ts] = new_sid

    # Split long responses
    for i in range(0, len(response), 3000):
        send(response[i:i+3000], thread_ts)


# === Startup message ===

STARTUP_MSG = """:large_green_circle: *{name}* is online

*How to use:*
Send a message → Claude Code executes it.
Reply in thread → continues the same session.
`!command` → runs shell command directly.

*Commands:*
```
!<command>   Run shell command (e.g. !ls, !git status)
!cd <path>   Change working directory
new          New Claude Code session
resume       Continue last PC session
resume <id>  Continue specific session
status       Show daemon status
stop         Stop daemon
```

*Session list:*
`!claude conversation list` to see all sessions.
Use `resume <id>` to continue a specific session.

Default directory: `{cwd}`

*Troubleshooting:*
On the machine: `tmux attach -t claude-daemon`
Log: `!tail ~/.claude/slack-daemon.log`"""


# === Main loop ===

def main():
    bot_user_id = slack_api("auth.test").get("user_id", "")
    log.info(f"Bot: {bot_user_id}, Channel: {SLACK_CHANNEL_ID}, Machine: {MACHINE_NAME}")

    startup_ts = send(STARTUP_MSG.format(name=MACHINE_NAME, cwd=DEFAULT_CWD))
    last_ts = startup_ts or str(int(time.time()))
    log.info(f"Watching from ts: {last_ts}")

    def shutdown(sig, frame):
        send(f":red_circle: *{MACHINE_NAME}* is offline")
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        try:
            # 1. Top-level messages
            data = slack_api("conversations.history", params={
                "channel": SLACK_CHANNEL_ID, "oldest": last_ts, "limit": "10"
            })
            msgs = data.get("messages", [])
            msgs.sort(key=lambda m: float(m.get("ts", "0")))
            for m in msgs:
                user = m.get("user", "")
                bot_id = m.get("bot_id")
                if bot_id or user == bot_user_id or not m.get("text", "").strip():
                    log.debug(f"Skip: bot_id={bot_id} user={user}")
                else:
                    handle(m, bot_user_id)
                last_ts = m["ts"]

            # 2. Thread replies
            for thread_ts in list(active_threads.keys()):
                thread_last = active_threads[thread_ts]
                rdata = slack_api("conversations.replies", params={
                    "channel": SLACK_CHANNEL_ID,
                    "ts": thread_ts,
                    "oldest": thread_last,
                    "limit": "10"
                })
                replies = [r for r in rdata.get("messages", [])
                           if float(r["ts"]) > float(thread_last)]
                for r in replies:
                    user = r.get("user", "")
                    bot_id = r.get("bot_id")
                    if not bot_id and user != bot_user_id and r.get("text", "").strip():
                        handle(r, bot_user_id)
                    active_threads[thread_ts] = r["ts"]

        except SystemExit:
            raise
        except Exception as e:
            log.error(f"Loop error: {e}", exc_info=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
