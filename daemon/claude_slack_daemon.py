#!/usr/bin/env python3
"""
Claude Code Slack Daemon
Slackからリモートでclaude codeを操作するためのデーモン。

使い方:
  source ~/.claude-slack-env
  python3 claude_slack_daemon.py
"""

import os, sys, json, time, subprocess, signal, logging, shutil, re, threading, shlex
import urllib.request, urllib.parse, urllib.error

# --- Ensure log directory exists ---
CLAUDE_DIR = os.path.expanduser("~/.claude")
os.makedirs(CLAUDE_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(CLAUDE_DIR, "slack-daemon.log")),
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
DEFAULT_ALLOWED_TOOLS = os.environ.get("CLAUDE_ALLOWED_TOOLS", "")
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "")
PROGRESS_INTERVAL = int(os.environ.get("PROGRESS_INTERVAL", "30"))
STATE_FILE = os.path.join(CLAUDE_DIR, "slack-daemon-state.json")
API_TIMEOUT = int(os.environ.get("SLACK_API_TIMEOUT", "30"))

# --- Security: Allowed users (comma-separated Slack user IDs) ---
# If empty, all users in the channel are allowed (less secure)
ALLOWED_USERS = [u.strip() for u in os.environ.get("ALLOWED_USERS", "").split(",") if u.strip()]

# --- Security: Allowed shell command prefixes ---
# Only these commands are allowed with ! prefix
ALLOWED_SHELL_PREFIXES = [
    "ls", "pwd", "cat", "head", "tail", "grep", "find", "wc",
    "git status", "git log", "git diff", "git branch", "git show",
    "claude conversation", "claude config",
    "echo", "date", "whoami", "hostname", "uname",
    "df", "du", "free", "ps", "top -l 1", "uptime",
    "python --version", "python3 --version", "node --version", "npm --version",
    "which", "type", "file", "stat",
]

if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
    print("ERROR: SLACK_BOT_TOKEN and SLACK_CHANNEL_ID must be set")
    print("  source ~/.claude-slack-env")
    sys.exit(1)

# Find claude binary
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser("~/.claude/local/claude")
if not os.path.isfile(CLAUDE_BIN):
    print(f"ERROR: claude not found (tried: {CLAUDE_BIN})")
    sys.exit(1)
log.info(f"Claude binary: {CLAUDE_BIN}")
log.info(f"Allowed users: {ALLOWED_USERS if ALLOWED_USERS else 'ALL (no restriction)'}")

# --- Execution Modes ---
MODES = {
    "plan": {
        "description": "Plan only (no execution)",
        "permission_mode": "plan",
        "allowed_tools": None,
        "emoji": ":memo:",
    },
    "readonly": {
        "description": "Read-only analysis",
        "permission_mode": None,
        "allowed_tools": "Read,Glob,Grep,WebSearch,WebFetch",
        "emoji": ":eyes:",
    },
    "auto": {
        "description": "Auto-approve all (use with caution)",
        "permission_mode": "bypassPermissions",
        "allowed_tools": None,
        "emoji": ":robot_face:",
    },
    "yolo": {
        "description": "Skip all permissions (dangerous!)",
        "permission_mode": None,
        "dangerously_skip": True,
        "allowed_tools": None,
        "emoji": ":fire:",
    },
}

MODELS = ["sonnet", "opus", "haiku"]


# === Slack API ===

class SlackAPIError(Exception):
    """Slack API error."""
    pass


def slack_api(method, params=None, post_data=None, retries=3):
    """Call Slack API with timeout, error checking, and retry logic."""
    url = f"https://slack.com/api/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}

    for attempt in range(retries):
        try:
            if post_data:
                headers["Content-Type"] = "application/json"
                data = json.dumps(post_data).encode()
                req = urllib.request.Request(url, data=data, headers=headers)
            else:
                req = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                result = json.loads(resp.read().decode())

            # Check Slack API response
            if not result.get("ok"):
                error = result.get("error", "unknown_error")
                # Rate limit - wait and retry
                if error == "ratelimited":
                    retry_after = int(result.get("headers", {}).get("Retry-After", 5))
                    log.warning(f"Rate limited, waiting {retry_after}s...")
                    time.sleep(retry_after)
                    continue
                log.error(f"Slack API error: {method} -> {error}")
                raise SlackAPIError(f"Slack API error: {error}")

            return result

        except urllib.error.URLError as e:
            log.error(f"Slack API network error (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                raise SlackAPIError(f"Network error: {e}")
        except urllib.error.HTTPError as e:
            log.error(f"Slack API HTTP error: {e.code} {e.reason}")
            if e.code == 429:  # Rate limit
                retry_after = int(e.headers.get("Retry-After", 5))
                log.warning(f"Rate limited (HTTP 429), waiting {retry_after}s...")
                time.sleep(retry_after)
                continue
            raise SlackAPIError(f"HTTP error: {e.code}")

    raise SlackAPIError("Max retries exceeded")


def send(text, thread_ts=None):
    """Send a message to Slack."""
    payload = {"channel": SLACK_CHANNEL_ID, "text": text, "mrkdwn": True}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        resp = slack_api("chat.postMessage", post_data=payload)
        return resp.get("ts")
    except SlackAPIError as e:
        log.error(f"Failed to send message: {e}")
        return None


def update_message(ts, text, thread_ts=None):
    """Update an existing message."""
    payload = {"channel": SLACK_CHANNEL_ID, "ts": ts, "text": text, "mrkdwn": True}
    try:
        slack_api("chat.update", post_data=payload)
    except SlackAPIError as e:
        log.error(f"Failed to update message: {e}")


# === State Management ===

sessions = {}
thread_cwd = {}
thread_mode = {}
thread_model = {}
active_threads = {}


def save_state():
    """Save persistent state to file."""
    state = {
        "sessions": sessions,
        "thread_cwd": thread_cwd,
        "thread_mode": thread_mode,
        "thread_model": thread_model,
        "active_threads": active_threads,
    }
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
        log.debug("State saved")
    except Exception as e:
        log.error(f"Failed to save state: {e}")


def load_state():
    """Load persistent state from file."""
    global sessions, thread_cwd, thread_mode, thread_model, active_threads
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
            sessions = state.get("sessions", {})
            thread_cwd = state.get("thread_cwd", {})
            thread_mode = state.get("thread_mode", {})
            thread_model = state.get("thread_model", {})
            active_threads = state.get("active_threads", {})
            log.info(f"State loaded: {len(sessions)} sessions, {len(active_threads)} active threads")
    except Exception as e:
        log.error(f"Failed to load state: {e}")


def get_cwd(thread_ts):
    return thread_cwd.get(thread_ts, DEFAULT_CWD)


# === Security ===

def is_user_allowed(user_id):
    """Check if user is allowed to use the daemon."""
    if not ALLOWED_USERS:
        return True  # No restriction
    return user_id in ALLOWED_USERS


def is_shell_command_allowed(cmd_str):
    """Check if shell command is in the allowed list."""
    cmd_lower = cmd_str.lower().strip()
    for prefix in ALLOWED_SHELL_PREFIXES:
        if cmd_lower.startswith(prefix.lower()):
            return True
    # Allow cd (handled specially)
    if cmd_lower.startswith("cd "):
        return True
    return False


# === Claude Code ===

def run_claude(prompt, cwd, session_id=None, resume_last=False, mode=None, model=None, thread_ts=None):
    """Run claude with optional mode/model settings and progress updates."""
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json"]

    if session_id:
        cmd.extend(["--resume", session_id])
    elif resume_last:
        cmd.append("--continue")

    mode_config = MODES.get(mode, {})

    if mode_config.get("dangerously_skip"):
        cmd.append("--dangerously-skip-permissions")
    elif mode_config.get("permission_mode"):
        cmd.extend(["--permission-mode", mode_config["permission_mode"]])

    allowed_tools = mode_config.get("allowed_tools") or DEFAULT_ALLOWED_TOOLS
    if allowed_tools:
        cmd.extend(["--allowedTools", allowed_tools])

    effective_model = model or DEFAULT_MODEL
    if effective_model and effective_model in MODELS:
        cmd.extend(["--model", effective_model])

    log.info(f"Running claude in {cwd} (mode={mode}, model={effective_model}): {prompt[:80]}")
    log.debug(f"Command: {' '.join(cmd[:12])}...")

    result_container = {"stdout": "", "stderr": "", "error": None, "done": False}
    start_time = time.time()

    def run_subprocess():
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=CLAUDE_TIMEOUT, cwd=cwd
            )
            result_container["stdout"] = result.stdout.strip()
            result_container["stderr"] = result.stderr.strip()
        except subprocess.TimeoutExpired:
            result_container["error"] = f"Timeout ({CLAUDE_TIMEOUT}s)"
        except Exception as e:
            result_container["error"] = str(e)
        finally:
            result_container["done"] = True

    thread = threading.Thread(target=run_subprocess)
    thread.start()

    progress_ts = None
    last_update = start_time

    while not result_container["done"]:
        thread.join(timeout=2)
        elapsed = int(time.time() - start_time)

        if thread_ts and elapsed > 0 and (time.time() - last_update) >= PROGRESS_INTERVAL:
            progress_msg = f":hourglass: Working... ({elapsed}s)"
            if progress_ts:
                update_message(progress_ts, progress_msg, thread_ts)
            else:
                progress_ts = send(progress_msg, thread_ts)
            last_update = time.time()

    if progress_ts:
        elapsed = int(time.time() - start_time)
        update_message(progress_ts, f":white_check_mark: Completed in {elapsed}s", thread_ts)

    if result_container["error"]:
        log.error(f"run_claude error: {result_container['error']}")
        return f"Error: {result_container['error']}", None

    stdout = result_container["stdout"]
    stderr = result_container["stderr"]

    log.debug(f"stdout: {stdout[:300] if stdout else '(empty)'}")
    if stderr:
        log.debug(f"stderr: {stderr[:300]}")

    if stdout:
        try:
            data = json.loads(stdout)
            return data.get("result", stdout), data.get("session_id")
        except json.JSONDecodeError:
            return stdout, None
    return stderr or "(no output)", None


# === Shell command ===

def run_shell(cmd_str, cwd):
    """Run shell command safely using shlex parsing."""
    if not is_shell_command_allowed(cmd_str):
        return f":no_entry: Command not allowed. Allowed: {', '.join(ALLOWED_SHELL_PREFIXES[:5])}..."

    try:
        # Use shlex.split for safer parsing (no shell=True)
        args = shlex.split(cmd_str)
        result = subprocess.run(
            args, capture_output=True, text=True,
            timeout=SHELL_TIMEOUT, cwd=cwd
        )
        output = (result.stdout + result.stderr).strip()
        return output or "(no output)"
    except ValueError as e:
        return f"Invalid command syntax: {e}"
    except FileNotFoundError:
        return f"Command not found: {cmd_str.split()[0]}"
    except subprocess.TimeoutExpired:
        return f"Timeout ({SHELL_TIMEOUT}s)"
    except Exception as e:
        log.error(f"Shell command error: {e}")
        return f"Error: {e}"


# === Message Handler ===

def parse_prefix(text):
    """Parse mode and model prefix from message."""
    mode = None
    model = None
    remaining = text

    mode_match = re.match(r'^(plan|readonly|auto|yolo):\s*(.+)$', remaining, re.IGNORECASE | re.DOTALL)
    if mode_match:
        mode = mode_match.group(1).lower()
        remaining = mode_match.group(2).strip()

    model_match = re.match(r'^(sonnet|opus|haiku):\s*(.+)$', remaining, re.IGNORECASE | re.DOTALL)
    if model_match:
        model = model_match.group(1).lower()
        remaining = model_match.group(2).strip()

    return mode, model, remaining


def handle(msg, bot_user_id):
    text = msg.get("text", "").strip()
    ts = msg.get("ts")
    thread_ts = msg.get("thread_ts", ts)
    user_id = msg.get("user", "")

    log.info(f"HANDLE: {text[:100]} (thread={thread_ts}, user={user_id})")

    # --- Security: Check user permission ---
    if not is_user_allowed(user_id):
        log.warning(f"Unauthorized user: {user_id}")
        send(f":no_entry: You are not authorized to use this daemon.", thread_ts)
        return

    low = text.lower()

    # --- Built-in commands ---

    if low == "stop":
        save_state()
        send(":red_circle: Daemon stopped", thread_ts)
        sys.exit(0)

    if low == "help":
        send(HELP_MSG, thread_ts)
        return

    if low == "status":
        cwd = get_cwd(thread_ts)
        mode = thread_mode.get(thread_ts, "default")
        model = thread_model.get(thread_ts, DEFAULT_MODEL or "default")
        lines = [
            f"*{MACHINE_NAME} Status*",
            f"cwd: `{cwd}`",
            f"mode: `{mode}`",
            f"model: `{model}`",
            ""
        ]
        if sessions:
            lines.append("*Sessions:*")
            for _, s in sessions.items():
                lines.append(f"  `{s}`")
        else:
            lines.append("_No active sessions_")
        send("\n".join(lines), thread_ts)
        return

    if low == "new":
        sessions.pop(thread_ts, None)
        thread_mode.pop(thread_ts, None)
        thread_model.pop(thread_ts, None)
        save_state()
        send(f"New session started\ncwd: `{get_cwd(thread_ts)}`", thread_ts)
        return

    if low.startswith("mode"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            mode_name = parts[1].strip().lower()
            if mode_name in MODES:
                thread_mode[thread_ts] = mode_name
                save_state()
                mode_info = MODES[mode_name]
                send(f"{mode_info['emoji']} Mode set to `{mode_name}`\n_{mode_info['description']}_", thread_ts)
            elif mode_name == "default":
                thread_mode.pop(thread_ts, None)
                save_state()
                send(":arrows_counterclockwise: Mode reset to default", thread_ts)
            else:
                modes_list = ", ".join([f"`{m}`" for m in MODES.keys()])
                send(f"Unknown mode. Available: {modes_list}, `default`", thread_ts)
        else:
            current = thread_mode.get(thread_ts, "default")
            send(f"Current mode: `{current}`", thread_ts)
        return

    if low.startswith("model"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            model_name = parts[1].strip().lower()
            if model_name in MODELS:
                thread_model[thread_ts] = model_name
                save_state()
                send(f":brain: Model set to `{model_name}`", thread_ts)
            elif model_name == "default":
                thread_model.pop(thread_ts, None)
                save_state()
                send(f":arrows_counterclockwise: Model reset to default ({DEFAULT_MODEL or 'auto'})", thread_ts)
            else:
                models_list = ", ".join([f"`{m}`" for m in MODELS])
                send(f"Unknown model. Available: {models_list}, `default`", thread_ts)
        else:
            current = thread_model.get(thread_ts, DEFAULT_MODEL or "default")
            send(f"Current model: `{current}`", thread_ts)
        return

    if low.startswith("resume"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            sid = parts[1].strip()
            sessions[thread_ts] = sid
            save_state()
            send(f"Resuming session `{sid}`\ncwd: `{get_cwd(thread_ts)}`", thread_ts)
        else:
            sessions[thread_ts] = "__continue__"
            save_state()
            send(f"Resuming last session\ncwd: `{get_cwd(thread_ts)}`", thread_ts)
        return

    # --- Shell commands (! prefix) ---

    if text.startswith("!"):
        cmd_str = text[1:].strip()
        if not cmd_str:
            return
        cwd = get_cwd(thread_ts)
        active_threads[thread_ts] = ts

        # Special: cd changes thread working directory
        if cmd_str.lower().startswith("cd "):
            path = os.path.expanduser(cmd_str[3:].strip())
            # Security: prevent directory traversal attacks
            try:
                real_path = os.path.realpath(path)
                if os.path.isdir(real_path):
                    thread_cwd[thread_ts] = real_path
                    save_state()
                    send(f"`{real_path}`", thread_ts)
                else:
                    send(f"Not found: `{path}`", thread_ts)
            except Exception as e:
                send(f"Invalid path: {e}", thread_ts)
            return

        log.info(f"Shell: {cmd_str} in {cwd}")
        output = run_shell(cmd_str, cwd)
        send(f"```\n{output[:3000]}\n```", thread_ts)
        return

    # --- Parse mode/model prefix ---
    prefix_mode, prefix_model, prompt = parse_prefix(text)

    mode = prefix_mode or thread_mode.get(thread_ts)
    model = prefix_model or thread_model.get(thread_ts)

    if prefix_mode:
        thread_mode[thread_ts] = prefix_mode
    if prefix_model:
        thread_model[thread_ts] = prefix_model
    if prefix_mode or prefix_model:
        save_state()

    # --- Run Claude Code ---

    cwd = get_cwd(thread_ts)
    mode_emoji = MODES.get(mode, {}).get("emoji", ":hourglass_flowing_sand:")
    model_str = model or DEFAULT_MODEL or "default"
    send(f"{mode_emoji} `{cwd}` (mode: {mode or 'default'}, model: {model_str})", thread_ts)
    active_threads[thread_ts] = ts
    save_state()

    sid = sessions.get(thread_ts)
    resume_last = sid == "__continue__"
    if resume_last:
        sid = None

    response, new_sid = run_claude(
        prompt, cwd=cwd, session_id=sid, resume_last=resume_last,
        mode=mode, model=model, thread_ts=thread_ts
    )
    if new_sid:
        sessions[thread_ts] = new_sid
        save_state()

    for i in range(0, len(response), 3000):
        send(response[i:i+3000], thread_ts)


# === Startup message ===

STARTUP_MSG = """:large_green_circle: *{name}* is online

*How to use:*
Send a message → Claude Code executes it.
Reply in thread → continues the same session.
`!command` → runs shell command (limited).

*Modes:* `plan:` | `readonly:` | `auto:` | `yolo:`
*Models:* `sonnet:` | `opus:` | `haiku:`

*Commands:*
`!<cmd>` `mode <name>` `model <name>` `new` `resume` `status` `help` `stop`

Default: `{cwd}`"""


HELP_MSG = """*Claude Code Slack Remote - Help*

*Modes:*
• `plan:` - Plan only, no execution
• `readonly:` - Read-only (Read, Glob, Grep, WebSearch)
• `auto:` - Auto-approve all tool uses
• `yolo:` - Skip ALL permission checks (dangerous!)

*Models:*
• `sonnet:` | `opus:` | `haiku:`

*Prefixes can be combined:*
`auto: opus: このバグを修正して`

*Commands:*
• `mode/model <name>` - Set for thread
• `new` - New session
• `resume [id]` - Resume session
• `!cmd` - Run shell command (limited to safe commands)
• `status` / `help` / `stop`

*Shell commands allowed:*
`ls`, `cat`, `git status/log/diff`, `claude conversation list`, etc.

*Session list:*
`!claude conversation list`"""


# === Main loop ===

def fetch_messages(oldest_ts, limit=50):
    """Fetch messages with pagination support."""
    all_messages = []
    cursor = None

    while True:
        params = {
            "channel": SLACK_CHANNEL_ID,
            "oldest": oldest_ts,
            "limit": str(min(limit, 100)),
        }
        if cursor:
            params["cursor"] = cursor

        try:
            data = slack_api("conversations.history", params=params)
        except SlackAPIError as e:
            log.error(f"Failed to fetch messages: {e}")
            break

        messages = data.get("messages", [])
        all_messages.extend(messages)

        # Check for more messages
        if data.get("has_more") and len(all_messages) < limit:
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        else:
            break

    return all_messages


def fetch_replies(thread_ts, oldest_ts, limit=50):
    """Fetch thread replies with pagination support."""
    all_replies = []
    cursor = None

    while True:
        params = {
            "channel": SLACK_CHANNEL_ID,
            "ts": thread_ts,
            "oldest": oldest_ts,
            "limit": str(min(limit, 100)),
        }
        if cursor:
            params["cursor"] = cursor

        try:
            data = slack_api("conversations.replies", params=params)
        except SlackAPIError as e:
            log.error(f"Failed to fetch replies: {e}")
            break

        replies = data.get("messages", [])
        all_replies.extend(replies)

        if data.get("has_more") and len(all_replies) < limit:
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        else:
            break

    return all_replies


def main():
    load_state()

    try:
        bot_user_id = slack_api("auth.test").get("user_id", "")
    except SlackAPIError as e:
        print(f"ERROR: Failed to authenticate with Slack: {e}")
        sys.exit(1)

    log.info(f"Bot: {bot_user_id}, Channel: {SLACK_CHANNEL_ID}, Machine: {MACHINE_NAME}")

    startup_ts = send(STARTUP_MSG.format(name=MACHINE_NAME, cwd=DEFAULT_CWD))
    if not startup_ts:
        print("ERROR: Failed to send startup message")
        sys.exit(1)

    last_ts = startup_ts
    log.info(f"Watching from ts: {last_ts}")

    def shutdown(_sig, _frame):
        save_state()
        send(f":red_circle: *{MACHINE_NAME}* is offline")
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        try:
            # 1. Top-level messages (with pagination)
            msgs = fetch_messages(last_ts)
            msgs.sort(key=lambda m: float(m.get("ts", "0")))

            for m in msgs:
                user = m.get("user", "")
                bot_id = m.get("bot_id")
                if bot_id or user == bot_user_id or not m.get("text", "").strip():
                    log.debug(f"Skip: bot_id={bot_id} user={user}")
                else:
                    handle(m, bot_user_id)
                last_ts = m["ts"]

            # 2. Thread replies (with pagination)
            for thread_ts in list(active_threads.keys()):
                thread_last = active_threads[thread_ts]
                replies = fetch_replies(thread_ts, thread_last)
                replies = [r for r in replies if float(r["ts"]) > float(thread_last)]

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
