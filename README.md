# 週次 日本映像作品ピックアップサイト

毎週1回、直近7日に日本の YouTube で公開された質の高い映像作品（MV／ショートフィルム／CM・広告／
ブランド・ファッションフィルム／アニメーション）を自動ピックアップし、GitHub Pages の静的サイトに
公開・蓄積する個人ツールです。用途は **カラーグレーディング/演出のリファレンス収集**。

- 質の担保は二系統：**allowlist**（信頼チャンネルの新着＝出どころで保証）＋ **discovery**（検索候補を
  Claude がメタデータ採点して一次フィルタ）。
- 詳細仕様は [youtube-mv-weekly-spec.md](youtube-mv-weekly-spec.md) が唯一の権威です。

## ディレクトリ構成

```
.
├── .github/workflows/
│   ├── weekly.yml   # 週次 cron + 手動実行（データ生成→自動コミット）
│   └── ci.yml       # secret 不要のテスト専用 CI（push / PR）
├── scripts/
│   ├── fetch.py     # メイン: allowlist + discovery 収集 → 週JSON生成
│   ├── score.py     # discovery 候補の LLM 採点（Anthropic）
│   └── resolve.py   # @handle / URL → channelId・uploadsPlaylistId 解決
├── config.json      # チューニング用パラメータ
├── allowlist.json   # 信頼チャンネル一覧（主軸ソース）
├── feedback.json    # good/bad フィードバック（任意・サイトからエクスポートしてコミット）
├── docs/            # GitHub Pages 配信ルート（HTML/CSS/JS + data/）
├── tests/           # unittest（Python）/ node --test（フロント純関数）+ fixtures
└── requirements.txt # requests（バージョン固定）
```

## セットアップ手順

### 1. YouTube Data API キー
1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクト作成。
2. 「YouTube Data API v3」を有効化。
3. 「APIとサービス → 認証情報」でAPIキーを発行。
4. **発行後、キーの「APIの制限」を「YouTube Data API v3」のみに設定（必須）。** 公開リポジトリ＋Actions
   前提では、漏洩時の被害上限を絞るために必須です。

### 2. Anthropic API キー
1. [console.anthropic.com](https://console.anthropic.com/) でAPIキーを発行。
2. **コンソールで支出上限（spend limit）を設定（必須）。**

### 3. GitHub Secrets
リポジトリの **Settings → Secrets and variables → Actions** に登録：
- `YOUTUBE_API_KEY`
- `ANTHROPIC_API_KEY`

キーはコード・設定にハードコードしません（環境変数からのみ読み込み）。

### 4. Actions の書き込み権限
**Settings → Actions → General → Workflow permissions → 「Read and write permissions」** を選択
（週次ジョブが `docs/data` をコミットするため）。

### 5. GitHub Pages
**Settings → Pages → Source「Deploy from a branch」→ Branch `main` / Folder `/docs`**。
- 扱うのは公開済み YouTube 動画のサムネ/タイトル/URL のみで機微情報はゼロのため、通常は **public リポジトリ**
  で運用します。private にしたい場合、Free プランでは private リポジトリの Pages は使えません（Pro 以上が必要）。

### 6. allowlist の初期登録
信頼チャンネルを数件登録します（空でも discovery のみで動作します）。手入力は事故りやすいので
`resolve.py` 経由を推奨：

```bash
export YOUTUBE_API_KEY=...
python scripts/resolve.py @SomeOfficialChannel --genre mv
python scripts/resolve.py https://www.youtube.com/channel/UCxxxxxxxx --genre shortfilm
```

`channelId` と `uploadsPlaylistId`、チャンネル名を解決して `allowlist.json` に追記します。
`genre` は `mv` / `shortfilm` / `cm` / `brand` / `animation` のいずれか。

サイト上の各カードには「チャンネルIDをコピー」ボタンがあり、discovery で見つけた良質チャンネルを
allowlist に昇格させる導線になります（コピーした channelId を `resolve.py` に渡せば最速・確実に登録できます）。

### 7. 初回実行
**Actions → 「weekly」→ Run workflow**（`workflow_dispatch`）で生成し、
`https://<user>.github.io/<repo>/` で確認します。以降は毎週月曜 09:00 JST（cron `0 0 * * 1`）に自動実行されます。

## ローカル開発・テスト

### 環境変数
実 API を使う場合のみ必要（未設定は明示エラー）：
```bash
export YOUTUBE_API_KEY=...
export ANTHROPIC_API_KEY=...
```

### データ生成
```bash
pip install -r requirements.txt

# 実 API で生成（docs/data に出力）
python scripts/fetch.py

# ドライラン（fixtures モード・API/キー不要。.dryrun/data に出力し実データを汚さない）
python scripts/fetch.py --dry-run
```

### フロントのローカル確認
`fetch` 系は `file://` では動かないため HTTP で配信します：
```bash
cd docs && python3 -m http.server 8000
# → http://localhost:8000/
```
（初期状態は `docs/data/index.json` が空で「まだデータがありません」と表示されます。
データを見たい場合は `python scripts/fetch.py --dry-run --data-dir docs/data` で生成できます。
※ 検証後は `docs/data` を空に戻すか、実データのみコミットしてください。）

### テスト
```bash
python -m unittest discover -s tests   # スクリプト（週算出/フィルタ/マージ/LLM検証/dry-run）
node --test tests/app.test.js          # フロント純関数（videoId検証/エスケープ/フィルタ/並び替え）
```

## 依存とハッシュ固定（任意・推奨）

`requirements.txt` はバージョンを完全固定しています。サプライチェーン堅牢化のため、ハッシュ固定 lock を
生成して `pip install --require-hashes` で導入できます：

```bash
pip install pip-tools
pip-compile --generate-hashes -o requirements.lock requirements.txt
```

採用する場合は `weekly.yml` / `ci.yml` のインストール手順を
`pip install --require-hashes -r requirements.lock` に置き換えてください。

## 設定（config.json）

主なパラメータ（詳細は spec 3.7）：
- `lookback_days`（既定7）/ `max_videos`（既定40）/ `sort_by`（`publishedAt`/`viewCount`/`score`）
- `allowlist.min_duration_sec`（既定15）
- `discovery`：`keywords` / `order`（既定 `relevance`）/ `search_pages` / `exclude_categories`（既定 `["22"]`）/
  `min_duration_sec`〜`max_duration_sec`（既定30〜1800）/ `max_candidates`（既定60）/
  `llm.model`（`claude-haiku-4-5`）/ `llm.score_threshold`（既定65）

## フィードバック（good/bad で選定を調整）

各カードの 👍 / 👎 アイコンで「特に求めている／不要」を記録し、その傾向を **discovery の LLM 採点**に
反映できます（詳細は spec 12 章）。静的サイトのため、フィードバックは次の流れで pipeline に届けます：

1. サイト上で気になった作品に **good / bad** を付ける（ブラウザの localStorage に即保存）。
2. コントロール右の **「フィードバックをエクスポート」** で `feedback.json` をダウンロード。
3. ダウンロードした `feedback.json` を**リポジトリ直下に置いてコミット**する。
4. 次回 `weekly` 実行時に `fetch.py` が読み込み、good/bad の傾向を採点プロンプトに同梱して
   「求めているものを高く・不要なものを低く」採点に反映します。

- `feedback.json` は公開済み動画のメタデータと good/bad のみで機微情報ゼロのため、**コミットして構いません**
  （`.gitignore` しません）。無い／壊れていても週次は止まりません（fail-open）。
- 反映の上限は code 既定（good/bad 各 30 件・タイトル 80 字）。必要なら `config.discovery.llm.feedback`
  （`max_examples` / `max_title_chars`）で上書きできます。
- ローカル確認では `python scripts/fetch.py --dry-run --feedback tests/fixtures/feedback.json` で
  採点に `<preferences>` が同梱される経路を API 無しで検証できます。

## セキュリティ要点

- APIキーは環境変数からのみ。URL/キーはログ出力しません。`.env` は `.gitignore` 済み。
- 外部 action はすべてコミット SHA でピン留め。`weekly.yml` は `schedule` + `workflow_dispatch` のみ・
  `permissions: contents: write` のみ。
- フロントは外部由来文字列を `textContent`/DOM で描画（`innerHTML` 不使用）、`videoId` を正規表現検証後にのみ
  iframe 化。CSP の `frame-src` は `youtube-nocookie.com`、`img-src` は `i.ytimg.com` に限定。
- LLM 採点は構造化出力＋コード側で値域/文字長/videoId を検証（fail-closed）。LLM 障害時は discovery を
  空にして allowlist 分のみで継続します。
