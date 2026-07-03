# HANDOFF — 新セッション引き継ぎ

> このファイルは「新しいセッションが最初に読む1枚」。詳細仕様は [youtube-mv-weekly-spec.md](youtube-mv-weekly-spec.md) が唯一の権威。

## 1. これは何か
毎週1回、直近7日に日本のYouTubeで公開された質の高い映像作品（MV/短編/CM/ブランド/アニメ）を自動ピックアップし、
GitHub Pages の静的サイトに公開・蓄積する個人ツール。用途は**カラグレ/演出のリファレンス収集**。

## 2. 権威ある資料
- **`youtube-mv-weekly-spec.md`（実装ブリーフ・これがソースオブトゥルース）**
  - 現在 **v1.1**：技術精査によるブラッシュアップ＋Codexレビュー（機能/安全）を反映済み。
  - 各章の「判断根拠」は設計意図の固定。**勝手に変えない**（変更時はユーザー確認）。

## 3. 現状（どこまで終わったか）
- **全5フェーズ実装完了**（Phase 1〜5）。作成済み：`config.json` / `allowlist.json` /
  `scripts/{fetch,score,resolve}.py` / `docs/`（index.html・assets/{style.css,app.js}・data/index.json） /
  `tests/`（unittest・fixtures・app.test.js） / `.github/workflows/{weekly,ci}.yml` / `README.md` / `requirements.txt`。
- **フィードバック機能実装済み（spec 12 章）**：カードの good/bad（インラインSVGアイコン）→ localStorage →
  「エクスポート」で `feedback.json` 出力 → コミット → `fetch.py`/`score.py` が読み、discovery 採点プロンプトに
  `<preferences>` として傾向を同梱（fail-open）。fixtures に `feedback.json` あり。
- **UI改修・絞り込み実装済み（spec 13 章）**：ビジュアル刷新（タイトル「YouTube PickUp」/ワインレッド配色/横長ジグザグカード/sticky ヘッダー）、
  **全期間横断ビュー**（週セレクタ「すべて」→ `docs/data/all.json`・各カードに週バッジ）、
  **統一タグ**（ジャンル/ソース/評価を `type:value` で統一・よく使う順バー上位6・🔍全タグパネル・`localStorage(tagUsage:v1)`・good/bad は `feedbackStore` 連携）。
  データ層は `fetch.py` の `rebuild_all_json()` が週JSON群から `all.json` を冪等再構築（main に組込済み）。
- **テスト全通過**：`python3 -m unittest discover -s tests`（71件）/ `node --test tests/app.test.js`（34件）。
  `python3 scripts/fetch.py --dry-run`（fixtures）で API 未使用の週JSON・index.json 生成と冪等上書きを検証済み。
  ※環境に `python` は無く `python3` を使用。
- 外部 action は **SHA ピン留め済み**（checkout v4.2.2 / setup-python v5.3.0 / setup-node v4.1.0）。
- **git 未初期化**（リポジトリ未作成）。**現在 `docs/data/` には UI 確認用のプレビューデータ**（実在 MV の実 URL）が入っている
  （`index.json`・`all.json`・`weeks/2026-W25.json`・`2026-W26.json`）。**本番前に空へ戻す**（実データは weekly 実行で生成。UI-PROGRESS 8章）。

## 4. 次の一歩（実装は完了。残りはデプロイ運用）
- **git 初期化 → GitHub リポジトリ作成 → push**（public 推奨。データ設計上 public で問題ない＝0章/6章）。
- **Secrets 登録**：`YOUTUBE_API_KEY` / `ANTHROPIC_API_KEY`（Settings→Secrets and variables→Actions）。
- **Actions 権限**：General→Workflow permissions を「Read and write」。
- **Pages**：Settings→Pages→Deploy from a branch→`main` / `/docs`。
- **キーのハードニング（必須）**：YouTube は API制限（v3のみ）、Anthropic は支出上限を設定（6章/10.5）。
- **初回実行**：Actions→「weekly」→ Run workflow（workflow_dispatch）→ `https://<user>.github.io/<repo>/` で確認。
- 手順詳細は **README.md** に記載済み。（任意）独立レビューは git 初期化後に `/code-review ultra`。

## 5. 確定事項（蒸し返さない）
- discovery 検索順は `order=relevance`（config で `date`/`viewCount` も可）。
- LLM採点は alias `claude-haiku-4-5`（上位 `claude-sonnet-4-6`）。Haiku 4.5 は `effort`/adaptive thinking 非対応。
- LLM採点は**構造化出力**（`output_config.format`）。値域/文字長/videoId一致は**コード側で検証＝fail-closed**。
- discovery フロー順序：`search(part=snippet)`→前段フィルタ→`videos.list(...,status)`→後段フィルタ→LLM採点（categoryId/尺は videos.list 後でしか判定不可）。
- 埋め込み可否：search に `videoEmbeddable=true`、`videos.list` で `status.embeddable` 確認、不可は「YouTubeで開く」フォールバック。
- uploads playlist は `relatedPlaylists.uploads`（`allowlist.json` の `uploadsPlaylistId`）優先。`nextPageToken` でページング。`UU`置換は最終フォールバック。
- 埋め込みは `youtube-nocookie.com` ＋ `playsinline=1`、CSP は YouTube(nocookie)/i.ytimg.com に限定。
- 公開前提：Pages は公開サイト（機微情報ゼロ）。キーは Secrets でコミットしない。private リポジトリの Pages は有料プラン要。
- UI/絞り込み（spec 13 章）：タグは `type:value`（同一タイプ内 OR・タイプ間 AND）。`all.json` は週JSONからの派生（唯一のソースは週JSON）。週バッジは横断ビューのみ。`rebuild_all_json` は冪等。

## 6. ローカル検証の入口
- `fetch.py --dry-run`（fixtureモード）：`tests/fixtures/` の保存済み APIレスポンスで、**APIキー無し**で週JSON/index.json 生成まで検証。
- スクリプト：`python -m unittest discover` ／ フロント純関数：`node --test`。
- 実キー利用時の環境変数：`YOUTUBE_API_KEY` / `ANTHROPIC_API_KEY`（未設定は明示エラー）。

## 7. 留意・未決
- セキュリティ：外部action は **SHA固定済み**。Python依存は **バージョン固定済み**（ハッシュ固定 lock は任意・生成手順は README 記載済み）。
- セットアップ必須（**運用側で要対応**）：YouTubeキーの API制限（v3のみ）、Anthropic の支出上限（10.5/6章/受け入れ基準）。
- CI（`ci.yml`、secret無し・push/PR）実装**済み**。`weekly.yml` は `schedule`+`workflow_dispatch` のみ。
- フロントの **実ブラウザ目視**（クリック再生/チップ操作）は環境制約で未実施。純関数の node --test と配信/セキュリティの静的確認まで完了（`cd docs && python3 -m http.server` で確認可）。
- discovery の判断（spec準拠の設計メモ）：YouTube API エラーは週中止／LLM障害は discovery 空で継続。`config.discovery.categories` は検索ループ未適用（コスト概算式と整合）。score の運用既定（config未記載・`llm`配下で上書き可）：`max_tokens=8000`/`batch_size=30`/`max_reason_chars=60`、`search_unit_budget=5000`。

## 8. 進め方の約束（グローバルルール／CLAUDE.md は自動ロード）
- 応答・コメントは日本語。複数ステップは1ステップずつ確認（「p」で次へ）。
- コードを作成・変更したら必ずテストを実行。
- 指示のない既存要素・構造は変更しない。
