# tests/fixtures — ドライラン/結合テスト用の保存済み APIレスポンス

`fetch.py --dry-run`（`FixtureClient`）と `tests/test_fetch.py` が読み込む。ネットワーク・APIキー不要で
週JSON / index.json 生成までを検証するためのデータ（spec 11.2）。

## ファイル
- `allowlist.json` — ドライラン用の信頼チャンネル（A: uploadsPlaylistId 保存済み / B: 未保存）
- `channels.json` — `{channelId: channel resource}`。B の uploads を `channels.list` で解決する経路の再現
- `playlist_items.json` — `{playlistId: [page0, page1, ...]}`。ページは順に返す（ページング検証）
- `videos.json` — `{videoId: video resource}`（allowlist の vid*／discovery の discov* 両方）
- `search.json` — `{keyword: [page0, ...]}`。discovery の search.list 応答（前段フィルタ検証）
- `llm.json` — `{videoId: {score,label,reason}}`。`FixtureScoreClient` が返す採点（LLM採点の代替）

## このデータが網羅する分岐（基準時刻 now=2026-06-19T08:00:00Z, lookback=7 → after=2026-06-12T08:00:00Z）
- A: uploadsPlaylistId 保存済み経路 / B: 未保存→channels.list 解決経路
- ページング: A は 2 ページにまたがり、2ページ目の古い項目(vidaaaa0005, 6/10)で打ち切り
- 期間外の打ち切り: B の vidbbbb0002(6/05)で停止
- 埋め込み不可除外: vidaaaa0002(embeddable=false)
- 短尺除外: vidaaaa0003(PT8S < 15s)
- 再生数欠損→null: vidaaaa0004(statistics に viewCount なし)
- 採用される 3 件: vidaaaa0001(mv) / vidaaaa0004(mv) / vidbbbb0001(shortfilm)
  - 既定 publishedAt 降順: vidaaaa0001(6/18) > vidaaaa0004(6/15) > vidbbbb0001(6/14)

## discovery 系統（キーワード "テスト検索KW" / score_threshold=65）
- 前段フィルタ: discov00001 は重複出現→重複排除、allowdsc001(allowlist チャンネル A)→除外
- 後段フィルタ: discov00002(categoryId=22)→除外 / discov00003(PT20S<30)→除外 / discov00004(embeddable=false)→除外
- LLM採点: discov00001=90(cinematic)→採用 / discov00005=40(casual)→閾値未満で除外
- 採用される discovery 1 件: discov00001（source=discovery, genre=null, score=90）
