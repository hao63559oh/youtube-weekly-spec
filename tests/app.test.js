// docs/assets/app.js の純関数を node 標準テストランナーで検証（11.1 / 10.1）。
// 実行: node --test tests/app.test.js
const { test } = require("node:test");
const assert = require("node:assert");
const app = require("../docs/assets/app.js");

test("isValidVideoId: 11文字の許可文字のみ true", () => {
  assert.strictEqual(app.isValidVideoId("dQw4w9WgXcQ"), true);
  assert.strictEqual(app.isValidVideoId("vidaaaa0001"), true);
  assert.strictEqual(app.isValidVideoId("abc-_ABC123"), true);
});

test("isValidVideoId: 不正は false（長さ違い・記号・非文字列）", () => {
  assert.strictEqual(app.isValidVideoId("short"), false);
  assert.strictEqual(app.isValidVideoId("toolongvideoid1"), false);
  assert.strictEqual(app.isValidVideoId("bad/id<scr>"), false);
  assert.strictEqual(app.isValidVideoId('"><img src=x>'), false);
  assert.strictEqual(app.isValidVideoId(null), false);
  assert.strictEqual(app.isValidVideoId(12345678901), false);
});

test("escapeHtml: HTML特殊文字をエスケープ", () => {
  assert.strictEqual(app.escapeHtml('<script>"&\'</script>'),
    "&lt;script&gt;&quot;&amp;&#39;&lt;/script&gt;");
  assert.strictEqual(app.escapeHtml(null), "");
});

test("formatCount: 桁区切り / 欠損は null", () => {
  assert.strictEqual(app.formatCount(1234567), "1,234,567");
  assert.strictEqual(app.formatCount(0), "0");
  assert.strictEqual(app.formatCount(999), "999");
  assert.strictEqual(app.formatCount(null), null);
  assert.strictEqual(app.formatCount(undefined), null);
  assert.strictEqual(app.formatCount(NaN), null);
  assert.strictEqual(app.formatCount("123"), null);
});

test("formatDuration: m:ss / h:mm:ss / 異常は null", () => {
  assert.strictEqual(app.formatDuration(150), "2:30");
  assert.strictEqual(app.formatDuration(45), "0:45");
  assert.strictEqual(app.formatDuration(3661), "1:01:01");
  assert.strictEqual(app.formatDuration(0), "0:00");
  assert.strictEqual(app.formatDuration(null), null);
  assert.strictEqual(app.formatDuration(-5), null);
});

test("embedUrl: youtube-nocookie + autoplay + playsinline", () => {
  const url = app.embedUrl("dQw4w9WgXcQ");
  assert.ok(url.startsWith("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"));
  assert.ok(url.includes("autoplay=1"));
  assert.ok(url.includes("playsinline=1"));
  assert.ok(url.includes("rel=0"));
});

test("watchUrl: watch?v=<id>", () => {
  assert.strictEqual(app.watchUrl("dQw4w9WgXcQ"),
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ");
});

const SAMPLE = [
  { videoId: "a", genre: "mv", source: "allowlist", viewCount: 10, score: null, publishedAt: "2026-01-01T00:00:00Z" },
  { videoId: "b", genre: "shortfilm", source: "allowlist", viewCount: 50, score: null, publishedAt: "2026-02-01T00:00:00Z" },
  { videoId: "c", genre: null, source: "discovery", viewCount: null, score: 90, publishedAt: "2026-03-01T00:00:00Z" },
  { videoId: "d", genre: null, source: "discovery", viewCount: 5, score: 70, publishedAt: "2026-01-15T00:00:00Z" },
];

test("filterVideos: 空指定は全件", () => {
  assert.strictEqual(app.filterVideos(SAMPLE, [], []).length, 4);
  assert.strictEqual(app.filterVideos(SAMPLE, null, null).length, 4);
});

test("filterVideos: ジャンル絞り込み", () => {
  const ids = app.filterVideos(SAMPLE, ["mv"], []).map((v) => v.videoId);
  assert.deepStrictEqual(ids, ["a"]);
});

test("filterVideos: source 絞り込み", () => {
  const ids = app.filterVideos(SAMPLE, [], ["discovery"]).map((v) => v.videoId);
  assert.deepStrictEqual(ids.sort(), ["c", "d"]);
});

test("filterVideos: ジャンル AND source", () => {
  // shortfilm かつ discovery → 該当なし（c/d は genre=null）
  assert.strictEqual(app.filterVideos(SAMPLE, ["shortfilm"], ["discovery"]).length, 0);
  // mv かつ allowlist → a
  const ids = app.filterVideos(SAMPLE, ["mv"], ["allowlist"]).map((v) => v.videoId);
  assert.deepStrictEqual(ids, ["a"]);
});

test("dedupeById: 同一 videoId は初出のみ残す（順序保持）", () => {
  const dupes = [
    { videoId: "x", week: "W28" },
    { videoId: "y", week: "W28" },
    { videoId: "x", week: "W27" },
    { videoId: "z", week: "W27" },
  ];
  const out = app.dedupeById(dupes);
  assert.deepStrictEqual(out.map((v) => v.videoId), ["x", "y", "z"]);
  // 初出（先頭 = より新しい週）の版を残す。
  assert.strictEqual(out.find((v) => v.videoId === "x").week, "W28");
});

test("dedupeById: videoId 欠落は残す・空入力は空", () => {
  assert.deepStrictEqual(app.dedupeById([]), []);
  const noId = [{ title: "a" }, { title: "b" }];
  assert.strictEqual(app.dedupeById(noId).length, 2);
});

test("applyHide / isHidden: 非表示の登録と判定（冪等）", () => {
  const store = {};
  app.applyHide(store, { videoId: "a" }, "2026-07-14T00:00:00Z");
  assert.strictEqual(app.isHidden(store, "a"), true);
  assert.strictEqual(app.isHidden(store, "b"), false);
  app.applyHide(store, { videoId: "a" }, "2026-07-14T00:00:00Z"); // 再押下でも1件のまま。
  assert.strictEqual(app.countHidden(store), 1);
  // videoId 欠落は無視。
  app.applyHide(store, {}, "");
  assert.strictEqual(app.countHidden(store), 1);
});

test("applyUnhide: 非表示解除", () => {
  const store = {};
  app.applyHide(store, { videoId: "a" }, "");
  app.applyUnhide(store, "a");
  assert.strictEqual(app.isHidden(store, "a"), false);
  assert.strictEqual(app.countHidden(store), 0);
});

test("filterHidden: 非表示 videoId を除外（空ストアは全件）", () => {
  const ids = (arr) => arr.map((v) => v.videoId);
  const store = {};
  app.applyHide(store, { videoId: "b" }, "");
  assert.deepStrictEqual(ids(app.filterHidden(SAMPLE, store)), ["a", "c", "d"]);
  assert.deepStrictEqual(ids(app.filterHidden(SAMPLE, {})), ["a", "b", "c", "d"]);
  assert.deepStrictEqual(ids(app.filterHidden(SAMPLE, null)), ["a", "b", "c", "d"]);
});

test("sortVideos: 新着（publishedAt 降順）", () => {
  const ids = app.sortVideos(SAMPLE, "publishedAt").map((v) => v.videoId);
  assert.deepStrictEqual(ids, ["c", "b", "d", "a"]);
});

test("sortVideos: 再生数降順・null 末尾", () => {
  const ids = app.sortVideos(SAMPLE, "viewCount").map((v) => v.videoId);
  // 50(b) > 10(a) > 5(d) > null(c)
  assert.deepStrictEqual(ids, ["b", "a", "d", "c"]);
});

test("sortVideos: スコア降順・null 末尾・publishedAt タイブレーク", () => {
  const ids = app.sortVideos(SAMPLE, "score").map((v) => v.videoId);
  // 90(c) > 70(d) > null同士は publishedAt 降順で b(2月) > a(1月)
  assert.deepStrictEqual(ids, ["c", "d", "b", "a"]);
});

test("sortVideos: 元配列を破壊しない", () => {
  const before = SAMPLE.map((v) => v.videoId);
  app.sortVideos(SAMPLE, "viewCount");
  assert.deepStrictEqual(SAMPLE.map((v) => v.videoId), before);
});

// ---- 統一タグ純関数（ジャンル/ソース/評価） ----
const TAG_SAMPLE = [
  { videoId: "a", genre: "mv", source: "allowlist" },
  { videoId: "b", genre: "shortfilm", source: "allowlist" },
  { videoId: "c", genre: null, source: "discovery" },
  { videoId: "d", genre: null, source: "discovery" },
];
// d=good, b=bad の評価を持つ想定の rating アクセサ。
const ratingOf = (id) => ({ d: "good", b: "bad" }[id] || null);

test("filterByTags: 空指定は全件（コピーを返す）", () => {
  assert.strictEqual(app.filterByTags(TAG_SAMPLE, []).length, 4);
  assert.strictEqual(app.filterByTags(TAG_SAMPLE, null).length, 4);
});

test("filterByTags: 同一タイプ内は OR（genre mv または shortfilm）", () => {
  const ids = app.filterByTags(TAG_SAMPLE, ["genre:mv", "genre:shortfilm"]).map((v) => v.videoId);
  assert.deepStrictEqual(ids.sort(), ["a", "b"]);
});

test("filterByTags: タイプ間は AND（discovery かつ shortfilm → 0件）", () => {
  assert.strictEqual(app.filterByTags(TAG_SAMPLE, ["source:discovery", "genre:shortfilm"]).length, 0);
});

test("filterByTags: 評価タグは ratingFn と連携（good → d）", () => {
  const ids = app.filterByTags(TAG_SAMPLE, ["rating:good"], ratingOf).map((v) => v.videoId);
  assert.deepStrictEqual(ids, ["d"]);
});

test("filterByTags: 評価 OR（good または bad → b,d）", () => {
  const ids = app.filterByTags(TAG_SAMPLE, ["rating:good", "rating:bad"], ratingOf).map((v) => v.videoId);
  assert.deepStrictEqual(ids.sort(), ["b", "d"]);
});

test("filterByTags: 評価 AND ソース（discovery かつ good → d）", () => {
  const ids = app.filterByTags(TAG_SAMPLE, ["source:discovery", "rating:good"], ratingOf).map((v) => v.videoId);
  assert.deepStrictEqual(ids, ["d"]);
});

test("parseTagKey: type/value 分解・不正は null", () => {
  assert.deepStrictEqual(app.parseTagKey("genre:mv"), { type: "genre", value: "mv" });
  assert.strictEqual(app.parseTagKey("nocolon"), null);
  assert.strictEqual(app.parseTagKey(null), null);
});

test("sortTagsByUsage: 使用回数降順 → 既定順で安定", () => {
  const usage = { "genre:cm": 3, "genre:mv": 1, "source:discovery": 1 };
  const sorted = app.sortTagsByUsage(app.TAG_KEYS, usage);
  // 最多 cm が先頭。次は同数(1) の mv と discovery が既定順（mv が先）。
  assert.strictEqual(sorted[0], "genre:cm");
  assert.ok(sorted.indexOf("genre:mv") < sorted.indexOf("source:discovery"));
  // 未使用タグも全件含む（欠落しない）。
  assert.strictEqual(sorted.length, app.TAG_KEYS.length);
});

test("sortTagsByUsage: usage 無しなら既定順を維持", () => {
  assert.deepStrictEqual(app.sortTagsByUsage(app.TAG_KEYS, {}), app.TAG_KEYS);
  assert.deepStrictEqual(app.sortTagsByUsage(app.TAG_KEYS, null), app.TAG_KEYS);
});

test("topTags: 上位6件（既定）", () => {
  const top = app.topTags(app.TAG_KEYS, { "rating:bad": 9 });
  assert.strictEqual(top.length, 6);
  assert.strictEqual(top[0], "rating:bad"); // 最多が先頭に繰り上がる
});

test("bumpTagUsage: 加算（破壊的・新規キーは1）", () => {
  const usage = {};
  app.bumpTagUsage(usage, "genre:mv");
  app.bumpTagUsage(usage, "genre:mv");
  assert.strictEqual(usage["genre:mv"], 2);
  app.bumpTagUsage(usage, null); // 不正キーは無視
  assert.strictEqual(Object.keys(usage).length, 1);
});

// ---- フィードバック純関数（spec 12.3/12.4 / 12.7） ----
const VID = {
  videoId: "dQw4w9WgXcQ", title: "作品01", channelId: "UCxxxxxxxxxxxxxxxxxxxxx1",
  channelTitle: "A Studio", genre: "mv", source: "discovery", week: "2026-W26",
};

test("applyRating: good を付与（メタデータと ratedAt を保存）", () => {
  const store = {};
  app.applyRating(store, VID, "good", "2026-06-23T00:00:00Z");
  assert.strictEqual(app.getRating(store, "dQw4w9WgXcQ"), "good");
  assert.strictEqual(store["dQw4w9WgXcQ"].title, "作品01");
  assert.strictEqual(store["dQw4w9WgXcQ"].week, "2026-W26");
  assert.strictEqual(store["dQw4w9WgXcQ"].ratedAt, "2026-06-23T00:00:00Z");
});

test("applyRating: 同じ印の再押下で解除（トグル）", () => {
  const store = {};
  app.applyRating(store, VID, "good", "t1");
  app.applyRating(store, VID, "good", "t2");
  assert.strictEqual(app.getRating(store, "dQw4w9WgXcQ"), null);
  assert.ok(!("dQw4w9WgXcQ" in store));
});

test("applyRating: good→bad は置換（相互排他）", () => {
  const store = {};
  app.applyRating(store, VID, "good", "t1");
  app.applyRating(store, VID, "bad", "t2");
  assert.strictEqual(app.getRating(store, "dQw4w9WgXcQ"), "bad");
});

test("applyRating: 不正な rating / videoId 欠落は無視", () => {
  const store = {};
  app.applyRating(store, VID, "meh", "t1");
  app.applyRating(store, { title: "no id" }, "good", "t1");
  assert.deepStrictEqual(store, {});
});

test("getRating: 未登録は null", () => {
  assert.strictEqual(app.getRating({}, "zzz"), null);
  assert.strictEqual(app.getRating(null, "zzz"), null);
});

test("countRatings: good/bad 件数", () => {
  const store = {};
  app.applyRating(store, { videoId: "a" }, "good", "t1");
  app.applyRating(store, { videoId: "b" }, "good", "t2");
  app.applyRating(store, { videoId: "c" }, "bad", "t3");
  assert.deepStrictEqual(app.countRatings(store), { good: 2, bad: 1 });
});

test("buildFeedbackItems: ratedAt 降順 → videoId 昇順で安定", () => {
  const store = {};
  app.applyRating(store, { videoId: "a" }, "good", "2026-06-23T00:00:00Z");
  app.applyRating(store, { videoId: "b" }, "bad", "2026-06-24T00:00:00Z");
  app.applyRating(store, { videoId: "c" }, "good", "2026-06-23T00:00:00Z");
  const ids = app.buildFeedbackItems(store).map((i) => i.videoId);
  assert.deepStrictEqual(ids, ["b", "a", "c"]); // b(24日) → a,c(23日)は videoId 昇順
});

test("serializeFeedback: version/updatedAt/items を持つ", () => {
  const store = {};
  app.applyRating(store, VID, "good", "2026-06-23T00:00:00Z");
  const out = app.serializeFeedback(store, "2026-06-25T12:00:00Z");
  assert.strictEqual(out.version, 1);
  assert.strictEqual(out.updatedAt, "2026-06-25T12:00:00Z");
  assert.strictEqual(out.items.length, 1);
  assert.strictEqual(out.items[0].rating, "good");
  assert.strictEqual(out.items[0].channelTitle, "A Studio");
});
