/* 週次 日本映像作品ピックアップ — フロント（依存ライブラリなし）
 *
 * セキュリティ(10.1): 外部由来文字列（title/channelTitle 等）は textContent / DOM 生成のみで描画し、
 * innerHTML に未エスケープのまま入れない。iframe 組み立て前に videoId を正規表現で検証する。
 * 再生は youtube-nocookie + playsinline（4.2 / 10.5 の CSP と統一）。
 *
 * 純関数（isValidVideoId / escapeHtml / formatCount / formatDuration / embedUrl / watchUrl）は
 * DOM 非依存で分離し、node --test で検証する（11.1）。
 */
(function () {
  "use strict";

  // ---- 純関数（テスト対象） ---------------------------------------------
  var VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;

  function isValidVideoId(id) {
    return typeof id === "string" && VIDEO_ID_RE.test(id);
  }

  // 防御的ユーティリティ（描画は基本 textContent/DOM だが、文字列組み立て用に用意）。
  function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // 数値の桁区切り。null/undefined/非数は null（「0」「NaN」を出さない。3.9/4.1）。
  function formatCount(n) {
    if (n == null || typeof n !== "number" || !isFinite(n)) return null;
    return Math.trunc(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  // 秒 → m:ss / h:mm:ss。
  function formatDuration(seconds) {
    if (seconds == null || typeof seconds !== "number" || !isFinite(seconds) || seconds < 0) {
      return null;
    }
    var s = Math.trunc(seconds);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    var pad = function (x) { return x < 10 ? "0" + x : "" + x; };
    if (h > 0) return h + ":" + pad(m) + ":" + pad(sec);
    return m + ":" + pad(sec);
  }

  // 埋め込み URL（youtube-nocookie + autoplay + playsinline）。呼び出し側で videoId 検証済み前提。
  function embedUrl(id) {
    return "https://www.youtube-nocookie.com/embed/" + id +
      "?autoplay=1&rel=0&playsinline=1";
  }

  function watchUrl(id) {
    return "https://www.youtube.com/watch?v=" + id;
  }

  // 並び替えキー（fetch.py の _sort_key と同等の順序を再現）。
  function sortKey(v, sortBy) {
    var pub = v.publishedAt || "";
    if (sortBy === "viewCount") {
      return [v.viewCount != null, v.viewCount != null ? v.viewCount : 0, pub];
    }
    if (sortBy === "score") {
      return [v.score != null, v.score != null ? v.score : 0, pub];
    }
    return [pub]; // 既定: publishedAt（新着順）
  }

  function cmpKey(ka, kb) {
    var n = Math.min(ka.length, kb.length);
    for (var i = 0; i < n; i++) {
      var x = ka[i], y = kb[i];
      if (typeof x === "boolean") x = x ? 1 : 0;
      if (typeof y === "boolean") y = y ? 1 : 0;
      if (x < y) return -1;
      if (x > y) return 1;
    }
    return 0;
  }

  // 降順ソート。null は末尾、null/同点は publishedAt 降順でタイブレーク（3.6 と一致）。
  function sortVideos(videos, sortBy) {
    return videos.slice().sort(function (a, b) {
      return cmpKey(sortKey(b, sortBy), sortKey(a, sortBy));
    });
  }

  // ジャンル/source で絞り込み（空＝全件。値の一致判定）。
  function filterVideos(videos, genres, sources) {
    return videos.filter(function (v) {
      var gOk = !genres || genres.length === 0 || genres.indexOf(v.genre) !== -1;
      var sOk = !sources || sources.length === 0 || sources.indexOf(v.source) !== -1;
      return gOk && sOk;
    });
  }

  // ---- 統一タグ（ジャンル/ソース/評価）純関数 ----------------------------
  // タグは "type:value" 形式（type ∈ {genre, source, rating}）。
  // 「よく使う順バー」「全タグパネル」「good/bad 絞り込み」を支える。順序＝パネルの既定並び。
  var TAG_DEFS = [
    { key: "genre:mv",          type: "genre",  value: "mv",         label: "MV" },
    { key: "genre:shortfilm",   type: "genre",  value: "shortfilm",  label: "短編" },
    { key: "genre:cm",          type: "genre",  value: "cm",         label: "CM" },
    { key: "genre:brand",       type: "genre",  value: "brand",      label: "ブランド" },
    { key: "genre:animation",   type: "genre",  value: "animation",  label: "アニメ" },
    { key: "source:allowlist",  type: "source", value: "allowlist",  label: "allowlist" },
    { key: "source:discovery",  type: "source", value: "discovery",  label: "discovery" },
    { key: "rating:good",       type: "rating", value: "good",       label: "good" },
    { key: "rating:bad",        type: "rating", value: "bad",        label: "bad" }
  ];
  var TAG_KEYS = TAG_DEFS.map(function (t) { return t.key; }); // 既定順（安定ソートの基準）。

  function parseTagKey(key) {
    var i = typeof key === "string" ? key.indexOf(":") : -1;
    if (i === -1) return null;
    return { type: key.slice(0, i), value: key.slice(i + 1) };
  }

  // 選択タグで絞り込む。タイプ内 OR・タイプ間 AND（空＝全件）。
  // ratingFn(videoId) -> "good"|"bad"|null を渡すと評価タグが feedbackStore と連携する。
  function filterByTags(videos, selectedKeys, ratingFn) {
    if (!selectedKeys || selectedKeys.length === 0) return videos.slice();
    var byType = { genre: [], source: [], rating: [] };
    selectedKeys.forEach(function (key) {
      var p = parseTagKey(key);
      if (p && byType[p.type]) byType[p.type].push(p.value);
    });
    return videos.filter(function (v) {
      var gOk = byType.genre.length === 0 || byType.genre.indexOf(v.genre) !== -1;
      var sOk = byType.source.length === 0 || byType.source.indexOf(v.source) !== -1;
      var rOk = true;
      if (byType.rating.length > 0) {
        var r = ratingFn ? ratingFn(v.videoId) : null;
        rOk = byType.rating.indexOf(r) !== -1;
      }
      return gOk && sOk && rOk;
    });
  }

  // タグ群を使用回数降順 → 既定順（TAG_DEFS の並び）で安定ソート。
  function sortTagsByUsage(tagKeys, usage) {
    var order = {};
    tagKeys.forEach(function (k, i) { order[k] = i; });
    return tagKeys.slice().sort(function (a, b) {
      var ua = (usage && usage[a]) || 0;
      var ub = (usage && usage[b]) || 0;
      if (ub !== ua) return ub - ua;     // 使用回数 降順。
      return order[a] - order[b];         // 同数は既定順で安定。
    });
  }

  // よく使う順の上位 n 件（既定 6）。
  function topTags(tagKeys, usage, n) {
    return sortTagsByUsage(tagKeys, usage).slice(0, n == null ? 6 : n);
  }

  // 使用回数を 1 加算（破壊的）。usage を返す。
  function bumpTagUsage(usage, key) {
    usage = usage || {};
    if (key) usage[key] = (usage[key] || 0) + 1;
    return usage;
  }

  // ---- フィードバック純関数（テスト対象。localStorage/DOM 非依存。spec 12.3/12.4） ----
  var FEEDBACK_RATINGS = { good: true, bad: true };

  function getRating(store, videoId) {
    var e = store && store[videoId];
    return e && e.rating ? e.rating : null;
  }

  // good/bad のトグル：同じ印の再押下は解除、異なる印は置換（相互排他）。store を返す（破壊的）。
  function applyRating(store, video, rating, nowIso) {
    store = store || {};
    if (!video || !video.videoId || !FEEDBACK_RATINGS[rating]) return store;
    var vid = video.videoId;
    if (store[vid] && store[vid].rating === rating) {
      delete store[vid];            // 同じ印の再押下 → 解除
      return store;
    }
    store[vid] = {
      rating: rating,
      title: video.title || "",
      channelId: video.channelId || "",
      channelTitle: video.channelTitle || "",
      genre: video.genre != null ? video.genre : null,
      source: video.source || "",
      week: video.week || "",
      ratedAt: nowIso || ""
    };
    return store;
  }

  // store マップ → items 配列（ratedAt 降順 → videoId 昇順で安定ソート）。
  function buildFeedbackItems(store) {
    var items = [];
    Object.keys(store || {}).forEach(function (vid) {
      var e = store[vid];
      if (!e || !FEEDBACK_RATINGS[e.rating]) return;
      items.push({
        videoId: vid,
        rating: e.rating,
        title: e.title || "",
        channelId: e.channelId || "",
        channelTitle: e.channelTitle || "",
        genre: e.genre != null ? e.genre : null,
        source: e.source || "",
        week: e.week || "",
        ratedAt: e.ratedAt || ""
      });
    });
    items.sort(function (a, b) {
      if (a.ratedAt < b.ratedAt) return 1;
      if (a.ratedAt > b.ratedAt) return -1;
      return a.videoId < b.videoId ? -1 : (a.videoId > b.videoId ? 1 : 0);
    });
    return items;
  }

  function countRatings(store) {
    var good = 0, bad = 0;
    Object.keys(store || {}).forEach(function (vid) {
      var r = store[vid] && store[vid].rating;
      if (r === "good") good++;
      else if (r === "bad") bad++;
    });
    return { good: good, bad: bad };
  }

  function serializeFeedback(store, nowIso) {
    return { version: 1, updatedAt: nowIso || "", items: buildFeedbackItems(store) };
  }

  // ---- DOM 描画（ブラウザ実行時のみ） ------------------------------------
  var currentVideos = [];    // 現在表示中の全動画（フィルタ/並び替え前）。
  var selectedTags = [];     // 選択中タグ key（空＝全件。ジャンル/ソース/評価の統一）。
  var tagUsage = {};         // タグ key -> 使用回数（localStorage 永続。よく使う順バー用）。
  var sortBy = "publishedAt"; // 並び替え（既定: 新着）。
  var feedbackStore = {};    // videoId -> {rating,...}（localStorage 永続。spec 12.3）。
  var currentWeek = "";      // 現在表示中の週ラベル（feedback に保存）。
  var allMode = false;       // true=全期間横断ビュー（all.json・週バッジ表示）。

  // ---- タグ使用回数 localStorage（よく使う順バー） ------------------------
  var TAG_USAGE_KEY = "tagUsage:v1";

  function loadTagUsage() {
    try {
      var raw = window.localStorage.getItem(TAG_USAGE_KEY);
      var parsed = raw ? JSON.parse(raw) : {};
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) { return {}; }
  }

  function saveTagUsage(usage) {
    try { window.localStorage.setItem(TAG_USAGE_KEY, JSON.stringify(usage)); }
    catch (e) { /* localStorage 不可時は黙ってスキップ */ }
  }

  // ---- フィードバック localStorage（spec 12.3） ----------------------------
  var FEEDBACK_KEY = "feedback:v1";

  function loadFeedbackStore() {
    try {
      var raw = window.localStorage.getItem(FEEDBACK_KEY);
      var parsed = raw ? JSON.parse(raw) : {};
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) { return {}; }
  }

  function saveFeedbackStore(store) {
    try { window.localStorage.setItem(FEEDBACK_KEY, JSON.stringify(store)); }
    catch (e) { /* localStorage 不可時は黙ってスキップ */ }
  }

  // ---- good/bad アイコン（インライン SVG。絵文字不使用。spec 12.2） ----------
  var SVG_NS = "http://www.w3.org/2000/svg";
  var ICON_PATHS = {
    good: "M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z",
    bad: "M15 3H6c-.83 0-1.54.5-1.84 1.22l-3.02 7.05c-.09.23-.14.47-.14.73v2c0 1.1.9 2 2 2h6.31l-.95 4.57-.03.32c0 .41.17.79.44 1.06L9.83 23l6.59-6.59c.36-.36.58-.86.58-1.41V5c0-1.1-.9-2-2-2zm4 0v12h4V3h-4z"
  };

  function makeIcon(kind) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "fb-icon");
    svg.setAttribute("aria-hidden", "true");
    var path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", ICON_PATHS[kind]);
    svg.appendChild(path);
    return svg;
  }

  function syncFeedbackButtons(videoId, buttons) {
    var r = getRating(feedbackStore, videoId);
    buttons.good.classList.toggle("active", r === "good");
    buttons.good.setAttribute("aria-pressed", r === "good" ? "true" : "false");
    buttons.bad.classList.toggle("active", r === "bad");
    buttons.bad.setAttribute("aria-pressed", r === "bad" ? "true" : "false");
  }

  function makeFeedbackButton(kind, video, buttons) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fb-btn fb-" + kind;
    btn.title = kind === "good" ? "特に求めている（good）" : "不要（bad）";
    btn.setAttribute("aria-label", btn.title);
    btn.appendChild(makeIcon(kind));
    btn.addEventListener("click", function () {
      applyRating(feedbackStore, video, kind, new Date().toISOString());
      saveFeedbackStore(feedbackStore);
      syncFeedbackButtons(video.videoId, buttons);
      updateFeedbackSummary();
      // 評価タグで絞り込み中は、評価変更で一致集合が変わるため再描画する。
      if (hasRatingTagSelected()) applyAndRender();
    });
    return btn;
  }

  function buildFeedbackControls(video) {
    var wrap = document.createElement("div");
    wrap.className = "card-feedback";
    var buttons = {};
    buttons.good = makeFeedbackButton("good", video, buttons);
    buttons.bad = makeFeedbackButton("bad", video, buttons);
    wrap.appendChild(buttons.good);
    wrap.appendChild(buttons.bad);
    syncFeedbackButtons(video.videoId, buttons);
    return wrap;
  }

  function updateFeedbackSummary() {
    var el = document.getElementById("feedback-summary");
    if (!el) return;
    var c = countRatings(feedbackStore);
    el.textContent = "good " + c.good + " / bad " + c.bad;
  }

  // エクスポート: feedback.json をダウンロード（spec 12.2/12.4）。CSP は変更しない。
  // ダウンロードが阻まれた場合はクリップボードコピーにフォールバック（4.3 と同じ手段）。
  function exportFeedback() {
    var json = JSON.stringify(serializeFeedback(feedbackStore, new Date().toISOString()), null, 2);
    try {
      var blob = new Blob([json], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "feedback.json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(url); }, 0);
    } catch (e) {
      copyText(json).then(function () {
        setStatus("ダウンロード不可のため feedback.json の内容をクリップボードにコピーしました。");
      }).catch(function () {
        setStatus("エクスポートに失敗しました。");
      });
    }
  }

  function buildThumb(button) {
    // dataset から img + 再生バッジを組み立てる（再生→停止の復元にも使う）。
    button.textContent = "";
    var img = document.createElement("img");
    img.src = button.dataset.thumb;
    img.alt = button.dataset.title || "";
    img.loading = "lazy";
    button.appendChild(img);
    var badge = document.createElement("span");
    badge.className = "play-badge";
    button.appendChild(badge);
    button.classList.remove("playing");
  }

  // 再生モーダルを開く（クリックしたカードの動画を中央に大きく再生。背景暗転で周囲を隠す）。
  function openPlayer(videoId, title) {
    // iframe 生成前に videoId を検証（不正なら再生しない＝URL注入防止。10.1）。
    if (!isValidVideoId(videoId)) return;
    var modal = document.getElementById("player-modal");
    var embed = document.getElementById("player-embed");
    if (!modal || !embed) return;
    embed.textContent = "";
    var iframe = document.createElement("iframe");
    iframe.src = embedUrl(videoId);
    iframe.title = title || "";
    iframe.setAttribute("allow", "autoplay; encrypted-media; fullscreen");
    iframe.setAttribute("allowfullscreen", "");
    embed.appendChild(iframe);
    modal.hidden = false;
    document.body.classList.add("modal-open");
  }

  // モーダルを閉じる（iframe を破棄して再生停止）。
  function closePlayer() {
    var modal = document.getElementById("player-modal");
    var embed = document.getElementById("player-embed");
    if (!modal || !embed) return;
    embed.textContent = ""; // iframe 破棄で再生停止
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    // 非セキュアコンテキスト等のフォールバック。
    return new Promise(function (resolve, reject) {
      try {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.className = "visually-hidden";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        resolve();
      } catch (e) { reject(e); }
    });
  }

  // ジャンル値 → 表示ラベル（タグバーの表記 MV/短編/CM/ブランド/アニメ と統一）。
  var GENRE_LABELS = { mv: "MV", shortfilm: "短編", cm: "CM", brand: "ブランド", animation: "アニメ" };
  function genreLabel(value) {
    return GENRE_LABELS[value] || value;
  }

  function createBadge(text, extraClass) {
    var span = document.createElement("span");
    span.className = "badge" + (extraClass ? " " + extraClass : "");
    span.textContent = text;
    return span;
  }

  function createCard(video) {
    var card = document.createElement("article");
    card.className = "card";

    var validId = isValidVideoId(video.videoId);

    // メディア（サムネ＋再生バッジ）。クリックでカード内 iframe 差し替え。
    var media = document.createElement("button");
    media.className = "card-media";
    media.type = "button";
    media.dataset.videoId = validId ? video.videoId : "";
    media.dataset.thumb = video.thumbnail || "";
    media.dataset.title = video.title || "";
    buildThumb(media);
    if (validId) {
      media.addEventListener("click", function () {
        openPlayer(media.dataset.videoId, media.dataset.title);
      });
    } else {
      media.disabled = true;
    }
    card.appendChild(media);

    // good/bad フィードバック（カード右上。spec 12.2）。media の後に置き、
    // 再生中は CSS の隣接兄弟（.card-media.playing ~ .card-feedback）で非表示。
    card.appendChild(buildFeedbackControls(video));

    // 下部情報オーバーレイ（テロップ背後に半透明シェイプ）。
    var overlay = document.createElement("div");
    overlay.className = "card-overlay";

    var title = document.createElement("h2");
    title.className = "card-title";
    title.textContent = video.title || "(無題)"; // textContent で XSS 防止。
    overlay.appendChild(title);

    var channel = document.createElement("p");
    channel.className = "card-channel";
    channel.textContent = video.channelTitle || "";
    overlay.appendChild(channel);

    var meta = document.createElement("div");
    meta.className = "card-meta";
    // 横断ビューでは各カードに週バッジ（どの週の作品か一目で分かるように）。
    if (allMode && video.week) meta.appendChild(createBadge(video.week, "week-badge"));
    if (video.source) meta.appendChild(createBadge(video.source));
    if (video.genre) meta.appendChild(createBadge(genreLabel(video.genre), "genre-badge"));
    if (video.source === "discovery" && typeof video.score === "number") {
      meta.appendChild(createBadge("score " + video.score, "badge-score"));
    }
    var dur = formatDuration(video.durationSeconds);
    if (dur) {
      var durSpan = document.createElement("span");
      durSpan.textContent = dur;
      meta.appendChild(durSpan);
    }
    var views = formatCount(video.viewCount);
    if (views) {
      var viewSpan = document.createElement("span");
      viewSpan.textContent = "▶ " + views;
      meta.appendChild(viewSpan);
    }
    overlay.appendChild(meta);

    var actions = document.createElement("div");
    actions.className = "card-actions";
    // フォールバック導線（埋め込み再生不可/失敗時に YouTube で開く。4.2）。
    if (validId) {
      var link = document.createElement("a");
      link.className = "fallback-link";
      link.href = watchUrl(video.videoId);
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "YouTubeで開く";
      actions.appendChild(link);
    }
    // allowlist 昇格支援: channelId コピー（4.3）。
    if (video.channelId) {
      var copyBtn = document.createElement("button");
      copyBtn.className = "copy-btn";
      copyBtn.type = "button";
      copyBtn.textContent = "チャンネルIDをコピー";
      copyBtn.addEventListener("click", function () {
        copyText(video.channelId).then(function () {
          copyBtn.textContent = "コピーしました";
          setTimeout(function () { copyBtn.textContent = "チャンネルIDをコピー"; }, 1500);
        }).catch(function () {
          copyBtn.textContent = "コピー失敗";
          setTimeout(function () { copyBtn.textContent = "チャンネルIDをコピー"; }, 1500);
        });
      });
      actions.appendChild(copyBtn);
    }
    card.appendChild(overlay);
    // 操作系（YouTubeで開く / channelId コピー）はカード右下にホバー表示。
    card.appendChild(actions);
    return card;
  }

  function setStatus(message) {
    var status = document.getElementById("status");
    if (!status) return;
    if (message) {
      status.textContent = message;
      status.classList.remove("hidden");
    } else {
      status.classList.add("hidden");
    }
  }

  function renderGrid(videos) {
    var grid = document.getElementById("grid");
    grid.textContent = "";
    closePlayer(); // 週/絞り込み切替などの再描画時はモーダルを閉じる
    if (!videos || videos.length === 0) {
      setStatus("条件に一致する映像がありません。");
      return;
    }
    setStatus("");
    var frag = document.createDocumentFragment();
    videos.forEach(function (v) { frag.appendChild(createCard(v)); });
    grid.appendChild(frag);
  }

  // 週セレクタの「すべて」（全期間横断ビュー）を表す内部値。週ラベルと衝突しない。
  var ALL_VALUE = "__all__";

  // 評価タグの絞り込みは feedbackStore を参照（filterByTags の ratingFn）。
  function ratingAccessor(videoId) {
    return getRating(feedbackStore, videoId);
  }

  function hasRatingTagSelected() {
    return selectedTags.some(function (k) { return k.indexOf("rating:") === 0; });
  }

  function applyAndRender() {
    var filtered = filterByTags(currentVideos, selectedTags, ratingAccessor);
    renderGrid(sortVideos(filtered, sortBy));
  }

  // ---- タグ UI（よく使う順バー / 全タグパネル / 🔍トグル） ----------------
  var tagDefByKey = {};
  TAG_DEFS.forEach(function (t) { tagDefByKey[t.key] = t; });

  var TAG_GROUPS = [
    { type: "genre", title: "ジャンル" },
    { type: "source", title: "ソース" },
    { type: "rating", title: "評価" }
  ];

  // タグ 1 個分のボタン（.chip）。評価タグは thumb アイコン付き。選択中は active。
  function makeTagChip(key) {
    var def = tagDefByKey[key];
    if (!def) return null;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.dataset.tagKey = key;
    if (def.type === "rating") btn.appendChild(makeIcon(def.value)); // good/bad の thumb SVG（既存）。
    btn.appendChild(document.createTextNode(def.label));             // ラベルは textContent 経由で安全。
    var on = selectedTags.indexOf(key) !== -1;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.addEventListener("click", function () { onTagClick(key); });
    return btn;
  }

  function onTagClick(key) {
    var idx = selectedTags.indexOf(key);
    if (idx === -1) {
      selectedTags.push(key);
      bumpTagUsage(tagUsage, key);   // 選択した時だけ使用回数を加算（解除では加算しない）。
      saveTagUsage(tagUsage);
    } else {
      selectedTags.splice(idx, 1);
    }
    syncTagActiveStates();
    applyAndRender();
  }

  // バー/パネル両方の .chip[data-tag-key] の active 表示を selectedTags に同期。
  function syncTagActiveStates() {
    document.querySelectorAll(".chip[data-tag-key]").forEach(function (chip) {
      var on = selectedTags.indexOf(chip.dataset.tagKey) !== -1;
      chip.classList.toggle("active", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  // よく使う順バー（上位6個）。週/モード切替時の並べ替え反映のため都度生成。
  function renderTagBar() {
    var bar = document.getElementById("tag-bar");
    if (!bar) return;
    bar.textContent = "";
    var frag = document.createDocumentFragment();
    topTags(TAG_KEYS, tagUsage, 6).forEach(function (key) {
      var chip = makeTagChip(key);
      if (chip) frag.appendChild(chip);
    });
    bar.appendChild(frag);
  }

  // 全タグパネル（ジャンル/ソース/評価のグループ）。初回のみ生成すれば十分。
  function renderTagPanel() {
    var panel = document.getElementById("tag-panel");
    if (!panel) return;
    panel.textContent = "";
    var frag = document.createDocumentFragment();
    TAG_GROUPS.forEach(function (g) {
      var group = document.createElement("div");
      group.className = "tag-group";
      var title = document.createElement("span");
      title.className = "tag-group-title";
      title.textContent = g.title;
      group.appendChild(title);
      var chips = document.createElement("div");
      chips.className = "tag-group-chips";
      TAG_DEFS.forEach(function (t) {
        if (t.type !== g.type) return;
        var chip = makeTagChip(t.key);
        if (chip) chips.appendChild(chip);
      });
      group.appendChild(chips);
      frag.appendChild(group);
    });
    panel.appendChild(frag);
  }

  function setupTagToggle() {
    var toggle = document.getElementById("tag-toggle");
    var panel = document.getElementById("tag-panel");
    if (!toggle || !panel) return;
    toggle.addEventListener("click", function () {
      var willOpen = panel.hasAttribute("hidden");
      if (willOpen) panel.removeAttribute("hidden");
      else panel.setAttribute("hidden", "");
      toggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
    });
  }

  function loadWeek(weekLabel) {
    allMode = false;
    setStatus("読み込み中…");
    fetch("data/weeks/" + encodeURIComponent(weekLabel) + ".json")
      .then(function (r) {
        if (!r.ok) throw new Error("week fetch failed");
        return r.json();
      })
      .then(function (data) {
        currentWeek = data.week || "";
        // feedback 保存時に週ラベルを残すため各動画に week を付与（spec 12.3）。
        currentVideos = (data.videos || []).map(function (v) { v.week = currentWeek; return v; });
        renderTagBar();    // 週切替時によく使う順を反映。
        applyAndRender();
      })
      .catch(function () { setStatus("週データの読み込みに失敗しました。"); });
  }

  function loadAll() {
    allMode = true;
    setStatus("読み込み中…");
    fetch("data/all.json")
      .then(function (r) {
        if (!r.ok) throw new Error("all fetch failed");
        return r.json();
      })
      .then(function (data) {
        currentWeek = "";  // 横断ビューは単一週ではない（各 video.week は all.json 側で付与済み）。
        currentVideos = data.videos || [];
        renderTagBar();    // モード切替時によく使う順を反映。
        applyAndRender();
      })
      .catch(function () { setStatus("全期間データの読み込みに失敗しました。"); });
  }

  function formatUpdated(iso) {
    // ISO 文字列をそのまま日付+時刻(UTC)として表示（タイムゾーン計算はしない）。
    if (typeof iso !== "string" || iso.length < 16) return "";
    return "更新: " + iso.slice(0, 10) + " " + iso.slice(11, 16) + " UTC";
  }

  function init() {
    var select = document.getElementById("week-select");
    var sortSelect = document.getElementById("sort-select");
    if (sortSelect) {
      sortSelect.value = sortBy;
      sortSelect.addEventListener("change", function () {
        sortBy = sortSelect.value;
        applyAndRender();
      });
    }
    // タグ: 使用回数を復元し、よく使う順バー / 全タグパネル / 🔍トグルを配線。
    tagUsage = loadTagUsage();
    renderTagBar();
    renderTagPanel();
    setupTagToggle();

    // フィードバック: localStorage 復元・エクスポート配線・件数表示（spec 12.2）。
    feedbackStore = loadFeedbackStore();
    var exportBtn = document.getElementById("export-btn");
    if (exportBtn) exportBtn.addEventListener("click", exportFeedback);
    updateFeedbackSummary();

    // 再生モーダル: ×ボタン / 背景クリック / Escape で閉じる。
    var playerClose = document.getElementById("player-close");
    var playerBackdrop = document.getElementById("player-backdrop");
    if (playerClose) playerClose.addEventListener("click", closePlayer);
    if (playerBackdrop) playerBackdrop.addEventListener("click", closePlayer);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closePlayer();
    });

    fetch("data/index.json")
      .then(function (r) {
        if (!r.ok) throw new Error("index fetch failed");
        return r.json();
      })
      .then(function (index) {
        var updated = document.getElementById("updated");
        if (updated) updated.textContent = formatUpdated(index.updatedAt);
        var weeks = index.weeks || [];
        if (weeks.length === 0) {
          setStatus("まだデータがありません。");
          return;
        }
        // 先頭に「すべて」（全期間横断ビュー = all.json）。
        var allOpt = document.createElement("option");
        allOpt.value = ALL_VALUE;
        allOpt.textContent = "すべて";
        select.appendChild(allOpt);
        weeks.forEach(function (w) {
          var opt = document.createElement("option");
          opt.value = w.week;
          opt.textContent = w.week + "（" + (w.count != null ? w.count : 0) + "件）";
          select.appendChild(opt);
        });
        select.value = weeks[0].week; // 最新週をデフォルト表示。
        select.addEventListener("change", function () {
          if (select.value === ALL_VALUE) loadAll();
          else loadWeek(select.value);
        });
        loadWeek(weeks[0].week);
      })
      .catch(function () { setStatus("インデックスの読み込みに失敗しました。"); });
  }

  // ---- エクスポート / 起動 -----------------------------------------------
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      isValidVideoId: isValidVideoId,
      escapeHtml: escapeHtml,
      formatCount: formatCount,
      formatDuration: formatDuration,
      embedUrl: embedUrl,
      watchUrl: watchUrl,
      filterVideos: filterVideos,
      sortVideos: sortVideos,
      TAG_DEFS: TAG_DEFS,
      TAG_KEYS: TAG_KEYS,
      parseTagKey: parseTagKey,
      filterByTags: filterByTags,
      sortTagsByUsage: sortTagsByUsage,
      topTags: topTags,
      bumpTagUsage: bumpTagUsage,
      getRating: getRating,
      applyRating: applyRating,
      buildFeedbackItems: buildFeedbackItems,
      countRatings: countRatings,
      serializeFeedback: serializeFeedback,
    };
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", init);
  }
})();
