# Claude Code Slack Remote

Slack経由で複数マシンのClaude Codeをリモート操作するデーモン。
スマホのSlackアプリからClaude Codeに指示を出したり、PCセッションを引き継いだりできる。

## 特徴

- スマホからClaude Codeを操作
- 複数マシン対応（マシンごとにSlackチャンネル）
- スレッド = セッション。返信で会話を継続
- PCセッションの引き継ぎ
- `!` でシェルコマンドも直接実行
- **実行モード**: plan / readonly / auto / yolo で権限制御
- python3標準ライブラリのみ（pip不要）

## 構成

```
daemon/
  claude_slack_daemon.py   メインデーモン
  start_daemon.sh          tmuxで起動
  stop_daemon.sh           停止
setup.sh                   セットアップ（対話 or ワンライナー）
env.template               環境変数テンプレート
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
| `mode <name>` | スレッドの実行モードを設定 |
| `model <name>` | スレッドのモデルを設定（sonnet/opus/haiku） |
| `new` | 新規Claude Codeセッション |
| `resume` | PCの最新セッション引き継ぎ |
| `resume <id>` | 指定セッション引き継ぎ |
| `status` | デーモン状態 |
| `help` | 詳細ヘルプ |
| `stop` | デーモン停止 |

### 実行モード

モードを使うと、Claude Codeの権限を制御できる。予約投稿でコーディングを進めたい場合などに便利。

| モード | 説明 | ユースケース |
|--------|------|-------------|
| `plan` | 計画のみ、実行しない | 方針を確認してから実行したい時 |
| `readonly` | 読み取り専用 | コードベースの分析・説明 |
| `auto` | 全て自動承認 | 定型作業の自動化 |
| `yolo` | 全権限スキップ(危険!) | CI/テスト環境での完全自動化 |

**使い方 (3種類):**

```
# 1. プレフィックス（その場限り → スレッド継続）
plan: このAPIにキャッシュを追加する方法を考えて

# 2. コマンド（スレッド内で永続）
mode auto

# 3. 環境変数（デフォルト設定）
export CLAUDE_ALLOWED_TOOLS="Read,Glob,Grep,Edit,Write,Bash"
```

### モデル指定

| モデル | 説明 |
|--------|------|
| `sonnet` | バランス型（デフォルト相当） |
| `opus` | 最高性能 |
| `haiku` | 高速・低コスト |

```
# プレフィックス
opus: この複雑なバグを修正して

# コマンド
model haiku

# モードと組み合わせ
auto: opus: このプロジェクトをリファクタリングして
```

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
| `CLAUDE_ALLOWED_TOOLS` | 許可するツール（カンマ区切り） | 全許可 |
| `CLAUDE_MODEL` | デフォルトモデル（sonnet/opus/haiku） | 自動 |
| `PROGRESS_INTERVAL` | 進捗更新間隔（秒） | `30` |

## アップデート

コードを更新した場合は以下を実行:

```bash
cd claude-slack-remote
git pull && bash setup.sh && ~/.claude/daemon/start_daemon.sh
```

- `setup.sh` はデーモンファイルを `~/.claude/daemon/` にコピーする（`git pull` だけでは反映されない）
- `~/.claude-slack-env` の既存設定は自動で引き継がれるので再入力不要
- `start_daemon.sh` は既存デーモンを自動停止してから起動するので、事前に `stop` する必要はない

## 追加機能

### 進捗表示
30秒以上かかるタスクは `:hourglass: Working... (30s)` のように進捗を表示。完了時に経過時間も表示。`PROGRESS_INTERVAL` で間隔を変更可能。

### セッション永続化
デーモン再起動後も以下が復元される：
- 各スレッドの作業ディレクトリ
- 設定したモード・モデル
- アクティブなスレッド

状態は `~/.claude/slack-daemon-state.json` に保存。

## 注意

- `~/.claude-slack-env` は `chmod 600` で保護（共有サーバーでは特に重要）
- デーモンはtmuxで動作。マシン再起動時は `start_daemon.sh` を再実行
- Slack無料プランは90日以上前のメッセージが非表示（ログは `~/.claude/slack-daemon.log`）
