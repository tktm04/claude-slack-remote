# Claude Code Slack Remote

Slack経由で複数マシンのClaude Codeをリモート操作するデーモン。
スマホのSlackアプリからClaude Codeに指示を出したり、PCセッションを引き継いだりできる。

## 特徴

- スマホからClaude Codeを操作
- 複数マシン対応（マシンごとにSlackチャンネル）
- スレッド = セッション。返信で会話を継続
- PCセッションの引き継ぎ
- `!` でシェルコマンドも直接実行
- python3標準ライブラリのみ（pip不要）

## 構成

```
daemon/
  claude_slack_daemon.py   メインデーモン
  start_daemon.sh          tmuxで起動
  stop_daemon.sh           停止
hooks/
  notify.sh                Slack通知フック（idle通知は除外）
setup.sh                   セットアップ（対話 or ワンライナー）
env.template               環境変数テンプレート
settings.json.example      Claude Code hooks設定テンプレート
```

## セットアップ

### 1. Slack App作成

1. https://api.slack.com/apps → Create New App → From scratch
2. OAuth & Permissions → Bot Token Scopes:
   - `chat:write`
   - `reactions:read`
   - `channels:history`
   - `channels:read`
3. Install to Workspace → Bot User OAuth Token (`xoxb-...`) をコピー
4. マシンごとにチャンネル作成 (例: `#mac-claude`, `#ubuntu-claude`)
5. 各チャンネルでBotを招待: `/invite @BotName`

### 2. 各マシンでセットアップ

```bash
git clone git@github.com:YOUR_USER/claude-slack-remote.git
cd claude-slack-remote

# 対話モード
bash setup.sh

# またはワンライナー
bash setup.sh -t xoxb-YOUR-TOKEN -c C0CHANNEL_ID -n Mac
```

### 3. デーモン起動

```bash
source ~/.bashrc  # or ~/.zshrc
~/.claude/daemon/start_daemon.sh
```

## 使い方

### 基本操作

チャンネルにメッセージを送信するとClaude Codeが実行し、スレッドに結果を返す。
スレッド返信で同じセッションを継続。

### コマンド

| コマンド | 説明 |
|---------|------|
| `!<command>` | シェルコマンド実行 (例: `!ls -la`, `!git status`) |
| `!cd ~/project` | 作業ディレクトリ変更 |
| `new` | 新規Claude Codeセッション |
| `resume` | PCの最新セッション引き継ぎ |
| `resume <id>` | 指定セッション引き継ぎ |
| `status` | デーモン状態 |
| `stop` | デーモン停止 |

### PCセッション引き継ぎ

```
# 1. ディレクトリを合わせる
!cd ~/my-project

# 2. 直近のセッションを継続
resume
```

## 環境変数

`~/.claude-slack-env` で設定（`chmod 600` で保護される）:

| 変数 | 説明 | デフォルト |
|------|------|-----------|
| `SLACK_BOT_TOKEN` | Bot Token (全マシン共通) | 必須 |
| `SLACK_CHANNEL_ID` | チャンネルID (マシンごと) | 必須 |
| `MACHINE_NAME` | 表示名 | `unknown` |
| `CLAUDE_WORK_DIR` | デフォルト作業ディレクトリ | `$HOME` |
| `CLAUDE_TIMEOUT` | Claude Code タイムアウト(秒) | `600` |
| `SHELL_TIMEOUT` | シェルコマンド タイムアウト(秒) | `30` |

## 注意

- `~/.claude-slack-env` は `chmod 600` で保護（共有サーバーでは特に重要）
- デーモンはtmuxで動作。マシン再起動時は `start_daemon.sh` を再実行
- Slack無料プランは90日以上前のメッセージが非表示（ログは `~/.claude/slack-daemon.log`）
