# MultiCLI — Personal Council v0.2

自分のCodex CLIとClaude Code CLIを使う、ローカル研究Council。GitHubから同じアプリを配布し、各利用者が自分のPC・自分の契約アカウントで利用します。白基調の専用Web UIを同梱しています。

Python 3.10以降のみで起動できます。pipでの依存パッケージ導入、共有APIキー、運営側のサーバーは不要です。

## Windowsで使う

1. [Python](https://www.python.org/downloads/)を導入します。インストーラーの「Add python.exe to PATH」を有効にしてください。
2. [Codex CLI](https://developers.openai.com/codex/cli)と[Claude Code](https://code.claude.com/docs/en/setup)を公式手順で導入します。
3. PowerShellで各自のアカウントにログインします。

```powershell
codex login
claude auth login
```

4. [このリポジトリ](https://github.com/hanadamushin/MultiCLI)の「Code → Download ZIP」でダウンロードして展開するか、次のコマンドで取得します。

```powershell
git clone https://github.com/hanadamushin/MultiCLI.git
cd MultiCLI
```
5. 展開先の `start.cmd` をダブルクリックします。ブラウザーが自動で開きます。

PowerShellからも起動できます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

画面にCodex / Claudeのログイン状態が表示されます。未導入・未ログインのときは案内に従って導入／ログイン後、設定画面を開いて状態を更新してください。CLIの実行ファイルは設定画面で指定できます。終了は起動したターミナルで `Ctrl+C`。

PythonがPATHにない場合は場所を指定できます。

```powershell
.\start.ps1 -PythonPath 'C:\path\to\python.exe'
```

ポート競合時は次のように変更します。

```powershell
.\start.ps1 -Port 8766
```

macOS / Linuxでは `python3 app.py --open` で起動できます。Windowsの動作を主に想定しており、CIはWindows / LinuxでPython 3.10と3.12を確認します。

## Councilの動作

| モード | 処理 | CLI呼び出し数 |
| --- | --- | --- |
| Quick | 独立回答 → 統合 | 3 |
| Council | 独立回答 → 相互批評 → 改訂 → 統合 | 7 |
| Deep Debate | 独立回答 → 相互批評 → 改訂 → 追加レビュー → 統合 | 9 |

独立回答・相互批評は両CLIに並行して依頼します。改訂では各モデルに相手の批評を渡し、選択した研究コンテキストはすべての段階に渡します。最終統合はCodexが担当します。モードごとに処理回数が増えるため、時間と各契約の利用枠を消費します。

回答、相互批評、改訂、追加レビュー、最終回答、実行ログをUIで確認できます。エラー時は取得済み回答を画面に残し、失敗箇所を表示します。一度に実行できるCouncilは1件です。回答はサーバーのメモリーに保持され、アプリ終了時に消えます。CLIログインはそのPCのCLI自身が管理します。

## 利用者別の設定・研究コンテキスト

Windowsの個人データ保存先は `%LOCALAPPDATA%\PersonalCouncil`。macOS / Linuxは `~/.local/share/personal-council`。アプリを更新しても個人設定は上書きされません。

```text
PersonalCouncil/
  settings.json       # CLIパス、タイムアウト
  profiles/
    sleep_research.json
    ai_economics.json
    my_research.json
```

初回起動時に同梱サンプルを個人フォルダーへコピーします。UIの「新規作成」「プロファイルを編集」でJSONプロファイルを追加・編集できます。JSONを直接編集する場合は次の形式です。

```json
{
  "name": "私の研究",
  "description": "研究対象の短い説明",
  "context": "研究の前提、対象集団、既知の事実など",
  "instructions": "根拠の強さ、因果識別など回答時に重視する方針"
}
```

保存先IDを変えて保存すると別プロファイルになります。JSONの `context` にMarkdown文章を書くこともできます。個別のMarkdownファイル読み込みは未対応です。

独立した保存先で使う場合は起動前に指定できます。

```powershell
$env:PERSONAL_COUNCIL_HOME = Join-Path $PWD 'local-data'
.\start.ps1
```

以前のv0でアプリ直下に設定／プロファイルを保存した場合は、必要なJSONを新しい個人フォルダーへ手動でコピーしてください。

## CLI連携とローカル接続

- アプリのバックエンドが引数配列を使い、`shell=False` でCLIを呼びます。両CLIに標準入力で質問を渡し、長い質問もコマンドラインへ連結しません。
- Codexは `exec --skip-git-repo-check --ephemeral --sandbox read-only -` を使用します。Claudeは `-p --output-format text --tools "" --strict-mcp-config --no-session-persistence` を使用します。
- CLIは実行ごとに空の一時ディレクトリで動作します。Claudeの組み込みツールは無効にし、Codexは読み取り専用サンドボックスを指定します。これはOS全体を完全に隔離する仕組みではありません。CLI自身の管理設定・認証・許可済みフック等はCLIに依存します。
- 子プロセスにAPIキー用の環境変数を渡さず、実行前に契約アカウントのログイン状態を確認します。CLI内部の設定・契約条件をアプリが変更することはありません。
- サーバーは `127.0.0.1` のみで待ち受けます。Host / Origin検証と起動ごとのトークンで、別サイトからの実行・設定変更を防ぎます。
- 質問、研究コンテキスト、各段階の回答は両プロバイダーへ送信されます。アプリから運営者へ送るテレメトリーはありません。
- 対象プラン、利用上限、契約条件は各プロバイダーに従います。サブスクリプションでの利用可否は利用者が公式案内を確認してください。

公式の非対話実行仕様: [Codex](https://developers.openai.com/codex/noninteractive)、[Claude Code](https://code.claude.com/docs/en/headless)。CLIのオプションや認証形式が変わる場合があります。古いCLIでエラーになる場合は公式手順で更新してください。

## GitHubで配布する

ソースコードと同梱サンプルのみを公開します。個人設定や認証情報をリポジトリに追加しないでください。`.gitignore` にローカルデータ、環境変数ファイル、ログを除外する設定があります。GitHub Pagesへのデプロイは不要です。

このリポジトリはアプリ配布用です。更新するときは、変更内容を確認してからコミット・pushします。

```powershell
git status
git add app.py static profiles start.ps1 start.cmd README.md LICENSE .gitignore .gitattributes .github tests docs
git commit -m 'Update Personal Council'
git push origin main
```

Releaseに配布ZIPを添付できます。

```powershell
gh release create v0.2.0 .\PersonalCouncil-v0.2.zip --title 'Personal Council v0.2' --notes '利用者ごとのCLIログインで利用するローカルアプリ'
```

GitHub ActionsはCLIを実行せず、模擬CLIによるモード分岐・批評の受け渡し・標準入力・個人データ・HTTP保護を検証します。GitHub側へ利用者の認証情報を登録する必要はありません。

## 開発

```powershell
python -m unittest discover -s tests -v
python app.py --open
```

外部依存はありません。`app.py` がローカルサーバーとCouncil制御、`static/` がUI、`profiles/` が公開サンプルです。MIT Licenseで配布できます。OpenAI / Anthropicの公式製品ではありません。

既知の制約: インストーラー／実行ファイル化、回答履歴保存、実行キャンセル、検索による出典の裏取りは未実装。CodexとClaudeの両方にログインしていないとCouncilは実行できません。

設計の詳細は [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) を参照してください。
