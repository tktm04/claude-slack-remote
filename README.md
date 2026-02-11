# Claude Code Slack Remote

Slack経由で複数マシンのClaude Codeをリモート操作するためのツール。

## 機能

- **リモート操作**: スマホのSlackアプリからClaude Codeに指示を出せる
- **複数マシン対応**: マシンごとにSlackチャンネルを分けて管理
- **セッション管理**: スレッド = セッション。返信で会話を継続
- **PCセッション引き継ぎ**: PCで作業中のセッションをSlackから継続
- **承認Hook**: PCのClaude Code実行許可をSlackから承認/拒否
- **ディレクトリ切り替え**: スレッドごとに作業ディレクトリを設定

## 構成

```
hooks/
  approve.sh          Slack承認Hook (PermissionRequest)
  notify.sh           Slack通知Hook (通知のみ、承認はPC側)
daemon/
  claude_slack_daemon.py  メインデーモン
  start_daemon.sh         tmuxで起動
  stop_daemon.sh          停止
setup.sh              セットアップスクリプト
env.template          環境変数テンプレート
settings.json.example Claude Code設定例
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
bash setup.sh
```

### 3. 環境変数を設定

```bash
nano ~/.claude-slack-env
```

| 変数 | 説明 | 例 |
|------|------|-----|
| SLACK_BOT_TOKEN | Bot Token (全マシン共通) | `xoxb-...` |
| SLACK_CHANNEL_ID | チャンネルID (マシンごと) | `C0XXXXXXXXX` |
| MACHINE_NAME | 表示名 | `Mac` |

### 4. デーモン起動

```bash
source ~/.bashrc  # or ~/.zshrc
~/.claude/daemon/start_daemon.sh
```

### 5. (任意) 承認Hookを有効化

`~/.claude/settings.json` に追加:

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "zsh ~/.claude/hooks/approve.sh"
          }
        ]
      }
    ]
  }
}
```

## 使い方

### Slackから操作

チャンネルにメッセージを送信 → Claude Codeが実行 → 結果がスレッドに返信

```
# 新しいメッセージ = 新しいセッション
このプロジェクトのREADMEを書いて

# スレッド返信 = 同じセッション継続
テストも追加して
```

### コマンド

| コマンド | 説明 |
|---------|------|
| `!cd ~/project` | 作業ディレクトリ変更 |
| `!ls` | ファイル一覧 |
| `!pwd` | 現在のディレクトリ |
| `new` | 新規セッション |
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

# 3. または特定のセッションIDを指定
resume abc123-def456-...
```

## マシンごとの設定例

| マシン | MACHINE_NAME | チャンネル |
|--------|-------------|-----------|
| Mac | `Mac` | `#mac-claude` |
| Ubuntu | `Ubuntu` | `#ubuntu-claude` |
| miyabi | `miyabi` | `#miyabi-claude` |
| ABCI | `ABCI` | `#abci-claude` |

## 注意

- `~/.claude-slack-env` は `chmod 600` で保護（共有サーバーでは特に重要）
- デーモンはtmuxで動作。マシン再起動時は `start_daemon.sh` を再実行
- Slack無料プランは90日以上前のメッセージが非表示（ログは `~/.claude/slack-daemon.log`）
- python3の標準ライブラリのみ使用（pip不要）
