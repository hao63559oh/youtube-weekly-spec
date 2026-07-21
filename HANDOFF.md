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
- **テスト全通過**：`python3 -m unittest discover -s tests`（**85件**）/ `node --test tests/app.test.js`（**39件**）。
  ※環境に `python` は無く `python3` を使用。
- 外部 action は **SHA ピン留め済み**（checkout v4.2.2 / setup-python v5.3.0 / setup-node v4.1.0）。

### 3.1 デプロイ済み・本番稼働中（2026-07 更新）
- **公開URL**：https://hao63559oh.github.io/youtube-weekly-spec/ （GitHub Pages・`main`/`docs`）
- **リポジトリ**：`hao63559oh/youtube-weekly-spec`（**public**）。Secrets 登録済み（`YOUTUBE_API_KEY`/`ANTHROPIC_API_KEY`）、Actions 権限は **read のまま**（weekly.yml が `contents: write` を自己宣言するため昇格不要）。
- **weekly は毎週月曜 09:00 JST（`cron '0 0 * * 1'`）に自動実行**され `docs/data` を更新（手動実行で実データ生成済み）。
- git コミット identity：`hao63559oh <295605048+hao63559oh@users.noreply.github.com>`（noreply）。
- **プレビューデータは撤去済み**（本番は weekly が生成）。

### 3.2 追加実装（spec md 未反映の拡張・2026-07）
- **ジャンル自動タグ**：LLM採点が `genre`（mv/shortfilm/cm/brand/animation/other）も分類。`score.py` の出力スキーマ/検証に genre 追加（fail-open で None）、`fetch.py` が discovery に付与、UIはカードバッジ＋タグ絞り込みで表示。
- **再生モーダル**：カードクリックで全画面モーダル再生（背景暗転／×・背景・Escで閉じる／iframe破棄で停止／native全画面可）。in-card 再生は廃止（`activePlayer` 廃止）。
- **高解像度サムネ**：`maxresdefault` 優先＋無い動画は `hqdefault` にフォールバック（フロント `buildThumb` のみ）。
- **選定ロジック（`fetch.py`）**：候補収集を `_round_robin` で均等化＋ `select_by_ratio()`＝同一チャンネル上限＋ジャンル比率選定。
- **再選出防止 `recent_exclude`（2026-07）**：過去週JSONを参照し、①同一動画は `video_days`（既定3650＝実質恒久）、②**discovery 由来**チャンネルは `channel_days`（既定30日）だけ再選出を除外（`config.discovery.recent_exclude`）。allowlist チャンネルは対象外。今回の週ラベルは自己除外回避でスキップ（同週再実行の冪等性維持）。`fetch.py` の `load_recent_picks()` が算出、discovery 前段で除外（videos.list/採点前でコスト節約）。
- **`all.json` 週跨ぎ重複排除（2026-07）**：`rebuild_all_json` が videoId 初出のみ採用（最新週の版を残す）。横断ビューの二重表示を解消（従前は週を単純連結し重複が出ていた）。フロント `dedupeById` でも保険。
- **UI（2026-07）**：初期表示を週別→**全件（すべて）**に変更。タイトル「YouTube PickUp」クリック/Enter で**初期状態にリセット**（週=すべて・タグ解除・並び新着）。一覧を横長ジグザグ→**タイル整列グリッド**（`auto-fill/minmax(300px)`・16:9）に。**サムネ上の情報オーバーレイ（タイトル/タグ等）を非表示**。カード左上に**「表示から削除」ボタン**（`hidden:v1` localStorage・上部に「非表示 N件／すべて表示に戻す」復元バー）を追加し、**削除時に bad も自動付与**（採点の `<preferences>` に反映＝連動）。
- ※ これらは `youtube-mv-weekly-spec.md`（spec）には未反映。仕様を正典に戻す場合は spec 側の追記が必要。

## 4. 次の一歩（デプロイ完了・稼働中。以降は運用/チューニング）
- **基本は放置で毎週自動更新**（月曜 09:00 JST）。手動即時実行は `gh workflow run weekly.yml --repo hao63559oh/youtube-weekly-spec`。
- **精度チューニングの入口**：
  - 好みの微調整 → サイトで👍/👎 →「エクスポート」→ `feedback.json` をリポジトリ**ルート**に置いて commit（`<preferences>` で採点反映）。現在 good/bad 登録済み。
  - 条件（`config.json`）→ `discovery.keywords`（現在 CM/MV/その他 各4語）、`genre_ratio`（cm:mv:other=0.4/0.4/0.2）、`max_per_channel`(1＝1チャンネル1件。尺表記だけ違う同名タイトルは事前に長尺版へ集約)、`min/max_duration_sec`(15/600)、`score_threshold`(65)、`max_candidates`(80)、`recent_exclude`（`video_days`=3650・`channel_days`=30・`enabled`）。
  - 採点方針（`score.py` の `SYSTEM_PROMPT`）→ プロ制作MUST・CM本編優先・短編は対象外・個人制作抑制。
- **キーのハードニング（運用側で要確認）**：YouTube は API制限（v3のみ）、Anthropic は支出上限（6章/10.5）。
- 独立レビューは `/code-review ultra`（任意）。手順詳細は **README.md**。

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
- 採点方針v2（2026-07）：**プロの制作会社/代理店/企業公式/レーベル制作をMUST**、CM**本編**を優先（報道/取材まとめ媒体は減点）、短編映画/ショートフィルム/個人自主制作は**対象外**（非優先）。
- 収集/選定v2（2026-07）：keywords は CM/MV/その他 各4語、候補収集は**ラウンドロビン均等化**、最終選定は `select_by_ratio`（**同一チャンネル最大2件**＋**ジャンル比率 CM:MV:その他≒4:4:2**、供給不足枠はスコア順で再配分）。尺は**15秒〜10分**。genre は LLM 分類（fail-open で None）。
- 実測（直近W27・40件）：CM48%/MV32%/その他20%。MV供給が枠に3件不足しCMへ再配分＝比率は「程度」（MVを増やすなら MVキーワード追加）。

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
