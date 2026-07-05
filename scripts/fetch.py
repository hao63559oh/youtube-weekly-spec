#!/usr/bin/env python3
"""週次データ収集メインスクリプト（Phase 1: allowlist 系統）。

allowlist.json の信頼チャンネル群から直近 lookback_days 日の新着を uploads playlist 経由で集め、
videos.list で詳細統合・機械フィルタ（尺/埋め込み可否）したうえで週JSON・index.json を生成する。

discovery 系統（search + LLM 採点）は Phase 2 で実装する。本ファイルには空のプレースホルダを置く。

ローカル検証:
    python scripts/fetch.py --dry-run            # tests/fixtures の保存済み応答で API/キー無し生成
    python scripts/fetch.py                       # 実 API（要 YOUTUBE_API_KEY）

セキュリティ(10.2): APIキーは環境変数からのみ。URL/キーをログに出さない。
冪等性(3.1): 同じ週は同名 JSON を上書き。index.json は過去週を消さず追記/更新する。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import score  # 同一 scripts/ の LLM 採点モジュール。

logger = logging.getLogger("fetch")

# YouTube Data API v3 エンドポイント。
YT_BASE = "https://www.googleapis.com/youtube/v3"
# search.list は part に依らず 1 呼び出し 100 ユニット固定（spec 3.4）。
SEARCH_UNIT_COST = 100
# discovery search の概算ユニット上限（日次1万に対し allowlist/videos.list/再実行分の余裕を残す）。
# 既定で keywords 9 × pages 1 × 100 = 900 ユニットなので通常は超えない。config で上書き可。
SEARCH_UNIT_BUDGET = 5000
# videoId / channelId の形式検証（10.1）。
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
# ISO 8601 期間（PnDTnHnMnS）パーサ用。動画尺は通常 PT#H#M#S。
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


# --------------------------------------------------------------------------
# 時刻・週・尺ユーティリティ（純関数）
# --------------------------------------------------------------------------
def parse_dt(value: str) -> datetime:
    """RFC3339/ISO8601 文字列（末尾 Z 可）を aware datetime(UTC) にする。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_z(dt: datetime) -> str:
    """datetime を末尾 Z 付き RFC3339（秒精度）に整形する。"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_week_label(dt: datetime) -> str:
    """ISO 8601 週番号ラベル YYYY-Www を返す（3.1）。"""
    cal = dt.isocalendar()
    return f"{cal[0]}-W{cal[1]:02d}"


def parse_iso8601_duration(value: str) -> int:
    """ISO8601 期間（例 PT2M30S）を秒に変換する。解析不能は 0。"""
    if not value:
        return 0
    m = _DURATION_RE.match(value)
    if not m:
        return 0
    days = int(m.group("days") or 0)
    hours = int(m.group("h") or 0)
    minutes = int(m.group("m") or 0)
    seconds = int(m.group("s") or 0)
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def within_period(published_at: str, after: datetime, before: datetime) -> bool:
    """published_at が [after, before] の期間内か（境界含む）。"""
    try:
        dt = parse_dt(published_at)
    except (ValueError, AttributeError):
        return False
    return after <= dt <= before


# --------------------------------------------------------------------------
# 動画オブジェクト生成（純関数）
# --------------------------------------------------------------------------
def _opt_int(stats: dict, key: str) -> Optional[int]:
    """statistics の数値文字列を int に。欠損/非数は None（0 で代用しない。3.9/4.1）。"""
    val = stats.get(key)
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def build_video_object(item: dict, source: str, genre: Optional[str]) -> dict:
    """videos.list の 1 アイテムから週JSON 用の動画オブジェクトを作る（3.9）。"""
    video_id = item.get("id", "")
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    content = item.get("contentDetails") or {}
    duration_iso = content.get("duration", "")
    return {
        "videoId": video_id,
        "title": snippet.get("title", ""),
        "channelId": snippet.get("channelId", ""),
        "channelTitle": snippet.get("channelTitle", ""),
        "publishedAt": snippet.get("publishedAt", ""),
        "viewCount": _opt_int(stats, "viewCount"),
        "likeCount": _opt_int(stats, "likeCount"),
        "duration": duration_iso,
        "durationSeconds": parse_iso8601_duration(duration_iso),
        # サムネは i.ytimg.com 固定で構築（CSP img-src と統一。10.5）。
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "source": source,
        "genre": genre or None,
        "score": None,
        "label": None,
        "reason": None,
    }


# --------------------------------------------------------------------------
# マージ・ソート（純関数。3.6）
# --------------------------------------------------------------------------
def merge_and_dedupe(allowlist_videos: list, discovery_videos: list) -> list:
    """videoId で重複排除（allowlist 優先）。"""
    merged: dict = {}
    for v in allowlist_videos:
        merged.setdefault(v["videoId"], v)
    for v in discovery_videos:
        merged.setdefault(v["videoId"], v)
    return list(merged.values())


def _sort_key(v: dict, sort_by: str):
    pub = v.get("publishedAt") or ""
    if sort_by == "viewCount":
        vc = v.get("viewCount")
        return (vc is not None, vc if vc is not None else 0, pub)
    if sort_by == "score":
        sc = v.get("score")
        # score=null は常に末尾、null/同点は publishedAt 降順でタイブレーク（3.6）。
        return (sc is not None, sc if sc is not None else 0, pub)
    # 既定 publishedAt（新着順）。
    return (pub,)


def sort_videos(videos: list, sort_by: str) -> list:
    """config.sort_by に従い降順ソート（publishedAt/viewCount/score）。"""
    return sorted(videos, key=lambda v: _sort_key(v, sort_by), reverse=True)


# --------------------------------------------------------------------------
# uploads playlist 解決（3.3）
# --------------------------------------------------------------------------
def resolve_uploads_map(channels: list, channels_uploads_fn) -> dict:
    """各チャンネルの uploadsPlaylistId を決定する（保存済み → channels.list → UU 置換）。

    channels_uploads_fn(ids) は {channelId: uploadsPlaylistId} を返す callable。
    返り値: {channelId: uploadsPlaylistId}
    """
    result: dict = {}
    unresolved: list = []
    for ch in channels:
        cid = ch.get("channelId")
        if not cid:
            continue
        saved = ch.get("uploadsPlaylistId")
        if saved:
            result[cid] = saved
        else:
            unresolved.append(cid)

    if unresolved:
        resolved = channels_uploads_fn(unresolved) or {}
        for cid in unresolved:
            up = resolved.get(cid)
            if up:
                result[cid] = up
            elif CHANNEL_ID_RE.match(cid):
                # 最後の手段: UC → UU 置換（公式保証なし。要警告）。
                fallback = "UU" + cid[2:]
                logger.warning("uploadsPlaylistId 未解決のため UU 置換にフォールバック: %s", cid)
                result[cid] = fallback
            else:
                logger.warning("uploadsPlaylistId を解決できずスキップ: %s", cid)
    return result


# --------------------------------------------------------------------------
# allowlist 収集パイプライン
# --------------------------------------------------------------------------
def collect_allowlist_video_ids(client, uploads_id: str, after: datetime, before: datetime) -> list:
    """uploads playlist をページングし、期間内の videoId を収集する（3.3）。

    uploads は新しい順。published_after より古い項目に達するか、nextPageToken が尽きたら停止。
    """
    collected: list = []
    page_token = None
    while True:
        page = client.playlist_items(uploads_id, page_token)
        reached_old = False
        for it in page.get("items", []):
            content = it.get("contentDetails") or {}
            vpub = content.get("videoPublishedAt")
            vid = content.get("videoId")
            if not vid or not vpub:
                continue
            try:
                dt = parse_dt(vpub)
            except (ValueError, AttributeError):
                continue
            if dt < after:
                # 以降はさらに古い（新しい順のため打ち切り）。
                reached_old = True
                break
            if dt > before:
                # 未来日（予約公開など）は対象外だが、後続に対象がある可能性があり継続。
                continue
            collected.append(vid)
        page_token = page.get("nextPageToken")
        if reached_old or not page_token:
            break
    return collected


def _chunk(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def collect_allowlist_videos(client, channels: list, config: dict, after: datetime, before: datetime) -> list:
    """allowlist 全チャンネルから期間内動画を集め、詳細統合・フィルタして動画オブジェクト化する。"""
    uploads_map = resolve_uploads_map(channels, client.channels_uploads)
    genre_by_channel = {c["channelId"]: c.get("genre", "") for c in channels if c.get("channelId")}

    # videoId -> 収集元 channelId（genre 付与に使用）。
    id_to_channel: dict = {}
    for ch in channels:
        cid = ch.get("channelId")
        uploads_id = uploads_map.get(cid)
        if not uploads_id:
            continue
        ids = collect_allowlist_video_ids(client, uploads_id, after, before)
        for vid in ids:
            id_to_channel.setdefault(vid, cid)
        logger.info("allowlist 収集: channel=%s 件数=%d", cid, len(ids))

    all_ids = list(id_to_channel.keys())
    if not all_ids:
        return []

    # videos.list で詳細取得（50件/ユニット）。
    items: list = []
    for batch in _chunk(all_ids, 50):
        resp = client.videos_list(batch)
        items.extend(resp.get("items", []))

    min_dur = (config.get("allowlist") or {}).get("min_duration_sec", 15)
    videos: list = []
    for item in items:
        vid = item.get("id", "")
        # フィルタ: 埋め込み可否（4.2）。
        status = item.get("status") or {}
        if status.get("embeddable") is not True:
            continue
        # フィルタ: 尺下限。
        content = item.get("contentDetails") or {}
        dur = parse_iso8601_duration(content.get("duration", ""))
        if dur < min_dur:
            continue
        genre = genre_by_channel.get(id_to_channel.get(vid, ""), "")
        videos.append(build_video_object(item, source="allowlist", genre=genre))
    return videos


def collect_discovery_videos(client, config: dict, channels: list, after: datetime,
                             before: datetime, score_client=None, feedback=None) -> list:
    """discovery 系統（spec 3.4）：search → 前段フィルタ → videos.list → 後段フィルタ → LLM採点 → 閾値。

    フロー順序厳守：categoryId/尺は videos.list 後でしか判定できないため後段で判定する。
    YouTube API エラーは送出（週生成を中止）。LLM採点失敗(ScoreError)は discovery を空にして継続（3.10）。
    """
    disc = config.get("discovery") or {}
    if not disc.get("enabled", True):
        return []
    keywords = disc.get("keywords") or []
    if not keywords:
        return []

    search_pages = disc.get("search_pages", 1)
    order = disc.get("order", "relevance")
    region = config.get("region_code", "JP")
    lang = config.get("relevance_language", "ja")
    published_after = iso_z(after)

    # クォータ概算ガード（spec 3.4）: 超過しそうなら明示エラーで止める。
    budget = disc.get("search_unit_budget", SEARCH_UNIT_BUDGET)
    estimate = len(keywords) * search_pages * SEARCH_UNIT_COST
    if estimate > budget:
        raise RuntimeError(
            f"discovery search の概算 {estimate} ユニットが上限 {budget} を超過。"
            "keywords / search_pages を減らしてください"
        )
    logger.info("discovery search 概算ユニット=%d (keywords=%d × pages=%d × %d)",
                estimate, len(keywords), search_pages, SEARCH_UNIT_COST)
    # ※ categories は videoCategoryId 絞り込み用だが、キーワード×カテゴリの総当たりは
    #   呼び出し数を膨らませる（概算式にも含めない）ため既定の検索ループでは適用しない（spec 3.4）。

    allow_channel_ids = {c.get("channelId") for c in channels if c.get("channelId")}

    # 1+2: search（part=snippet）→ 前段フィルタ（videoId 重複排除・allowlist チャンネル除外）。
    candidate_ids: list = []
    seen: set = set()
    for kw in keywords:
        page_token = None
        for _ in range(search_pages):
            page = client.search(kw, published_after, order, region, lang, page_token)
            for it in page.get("items", []):
                vid = (it.get("id") or {}).get("videoId")
                channel_id = (it.get("snippet") or {}).get("channelId")
                if not vid or vid in seen:
                    continue
                if channel_id in allow_channel_ids:
                    continue  # allowlist 分は収集済み。
                seen.add(vid)
                candidate_ids.append(vid)
            page_token = page.get("nextPageToken")
            if not page_token:
                break
    logger.info("discovery 前段: 候補 videoId=%d", len(candidate_ids))
    if not candidate_ids:
        return []

    # 3: videos.list で詳細取得（50件/ユニット）。
    items: list = []
    for batch in _chunk(candidate_ids, 50):
        items.extend(client.videos_list(batch).get("items", []))

    # 4: 後段フィルタ（embeddable・exclude_categories・尺レンジ・max_candidates 上限）。
    exclude_cats = set(disc.get("exclude_categories", ["22"]))
    min_dur = disc.get("min_duration_sec", 30)
    max_dur = disc.get("max_duration_sec", 1800)
    max_candidates = disc.get("max_candidates", 60)

    filtered: list = []  # (videos.list item, durationSeconds)
    for item in items:
        status = item.get("status") or {}
        if status.get("embeddable") is not True:
            continue
        snippet = item.get("snippet") or {}
        if snippet.get("categoryId") in exclude_cats:
            continue
        content = item.get("contentDetails") or {}
        dur = parse_iso8601_duration(content.get("duration", ""))
        if not (min_dur <= dur <= max_dur):
            continue
        filtered.append((item, dur))
        if len(filtered) >= max_candidates:
            break
    logger.info("discovery 後段: 採点候補=%d", len(filtered))
    if not filtered:
        return []

    # 5: LLM 採点。失敗時は discovery を空にして継続（spec 3.10）。
    candidates = [score.make_candidate(item, dur) for item, dur in filtered]
    try:
        scores = score.score_candidates(candidates, config, client=score_client, feedback=feedback)
    except score.ScoreError as e:
        logger.warning("LLM採点に失敗。discovery を空にして継続: %s", e)
        return []

    # 6: 閾値フィルタ → 動画オブジェクト化（source=discovery, genre=LLM分類, score/label/reason 付与）。
    threshold = (disc.get("llm") or {}).get("score_threshold", 65)
    item_by_id = {item.get("id"): item for item, _ in filtered}
    videos: list = []
    for vid, result in scores.items():
        if result["score"] < threshold:
            continue
        item = item_by_id.get(vid)
        if not item:
            continue
        # genre は LLM 分類（mv/shortfilm/cm/brand/animation）。未分類は None（タグ無し）。
        v = build_video_object(item, source="discovery", genre=result.get("genre"))
        v["score"] = result["score"]
        v["label"] = result["label"]
        v["reason"] = result["reason"]
        videos.append(v)
    logger.info("discovery 採用: %d (閾値 score>=%d)", len(videos), threshold)
    return videos


# --------------------------------------------------------------------------
# 出力（検証 → 原子的書き込み）
# --------------------------------------------------------------------------
def build_week_payload(week: str, after: datetime, before: datetime, videos: list) -> dict:
    return {
        "week": week,
        "period": {"from": iso_z(after), "to": iso_z(before)},
        "generatedAt": iso_z(before),
        "count": len(videos),
        "videos": videos,
    }


def validate_week_payload(payload: dict) -> None:
    """書き込み前バリデーション（3.10: 失敗時は既存を壊さず非ゼロ終了させる）。"""
    if not isinstance(payload, dict):
        raise ValueError("payload が dict ではありません")
    for key in ("week", "period", "generatedAt", "count", "videos"):
        if key not in payload:
            raise ValueError(f"payload に必須キー {key} がありません")
    if not isinstance(payload["videos"], list):
        raise ValueError("videos が配列ではありません")
    if payload["count"] != len(payload["videos"]):
        raise ValueError("count と videos の件数が一致しません")
    for v in payload["videos"]:
        vid = v.get("videoId")
        if not (isinstance(vid, str) and VIDEO_ID_RE.match(vid)):
            raise ValueError(f"videoId の形式が不正です: {vid!r}")
        for f in ("title", "channelId", "channelTitle", "publishedAt",
                  "duration", "durationSeconds", "thumbnail", "url", "source"):
            if f not in v:
                raise ValueError(f"動画 {vid} に必須フィールド {f} がありません")
        if not isinstance(v["durationSeconds"], int):
            raise ValueError(f"動画 {vid} の durationSeconds が int ではありません")
        if v["source"] not in ("allowlist", "discovery"):
            raise ValueError(f"動画 {vid} の source が不正です: {v['source']!r}")
        for nf in ("viewCount", "likeCount", "score"):
            if v.get(nf) is not None and not isinstance(v[nf], int):
                raise ValueError(f"動画 {vid} の {nf} が int/None ではありません")


def _atomic_write_json(path: Path, data: dict) -> None:
    """同一ディレクトリの一時ファイルへ書いてから os.replace で原子的に差し替える。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_week_json(data_dir: Path, payload: dict) -> Path:
    """週JSON を docs/data/weeks/<week>.json に冪等上書きする（検証後に書き込み）。"""
    validate_week_payload(payload)
    path = data_dir / "weeks" / f"{payload['week']}.json"
    _atomic_write_json(path, payload)
    return path


def update_index_json(data_dir: Path, payload: dict, generated_at: str) -> Path:
    """index.json を読み込み、当該週を追記/更新（過去週は保持）。新しい週が先頭（3.9）。"""
    index_path = data_dir / "index.json"
    index: dict = {"updatedAt": generated_at, "weeks": []}
    if index_path.exists():
        with index_path.open(encoding="utf-8") as f:
            index = json.load(f)
    weeks = [w for w in index.get("weeks", []) if w.get("week") != payload["week"]]
    weeks.append({
        "week": payload["week"],
        "count": payload["count"],
        "generatedAt": payload["generatedAt"],
    })
    # 新しい週が先頭（週ラベル降順）。
    weeks.sort(key=lambda w: w.get("week", ""), reverse=True)
    index["weeks"] = weeks
    index["updatedAt"] = generated_at
    _atomic_write_json(index_path, index)
    return index_path


def rebuild_all_json(data_dir: Path, updated_at: Optional[str] = None) -> Path:
    """週JSON 群を唯一のソースに all.json（全期間横断ビュー用）を再構築する。

    index.json の週（降順）順に各週JSON の videos を集約し、各 video に week を付与する。
    冪等: 同じ入力からは同じ出力（updatedAt は呼び出し側が固定値を渡せばバイト一致）。
    index/週JSON が無ければ空の all.json（count=0）を書く。
    """
    index_path = data_dir / "index.json"
    weeks_dir = data_dir / "weeks"
    index_updated = updated_at
    videos: list = []
    if index_path.exists():
        with index_path.open(encoding="utf-8") as f:
            index = json.load(f)
        if index_updated is None:
            index_updated = index.get("updatedAt")
        # index は既に週降順だが、唯一のソースとして確実に降順へ並べ直す。
        week_labels = sorted(
            (w.get("week") for w in index.get("weeks", []) if w.get("week")),
            reverse=True,
        )
        for wk in week_labels:
            wp = weeks_dir / f"{wk}.json"
            if not wp.exists():
                logger.warning("all.json 再構築: 週JSON が見つかりません（スキップ）: %s", wk)
                continue
            with wp.open(encoding="utf-8") as f:
                week_payload = json.load(f)
            label = week_payload.get("week", wk)
            for v in week_payload.get("videos", []):
                merged = dict(v)
                merged["week"] = label   # 横断表示で各カードに週バッジを出すため付与。
                videos.append(merged)
    payload = {
        "updatedAt": index_updated or "",
        "count": len(videos),
        "videos": videos,
    }
    all_path = data_dir / "all.json"
    _atomic_write_json(all_path, payload)
    return all_path


# --------------------------------------------------------------------------
# API クライアント
# --------------------------------------------------------------------------
class YouTubeClient:
    """実 YouTube Data API クライアント。APIキーは環境変数からのみ（10.2）。"""

    def __init__(self):
        api_key = os.environ.get("YOUTUBE_API_KEY")
        if not api_key:
            raise RuntimeError("環境変数 YOUTUBE_API_KEY が未設定です")
        self._api_key = api_key
        import requests  # 遅延 import。
        self._requests = requests
        self._session = requests.Session()

    def _get(self, resource: str, params: dict) -> dict:
        query = dict(params)
        query["key"] = self._api_key
        try:
            resp = self._session.get(f"{YT_BASE}/{resource}", params=query, timeout=30)
        except self._requests.RequestException:
            # 例外メッセージに URL（=キー）が含まれうるため内容は出さない。
            raise RuntimeError(f"{resource} リクエストに失敗しました（ネットワークエラー）")
        if resp.status_code != 200:
            message = ""
            try:
                message = (resp.json().get("error") or {}).get("message", "")
            except ValueError:
                message = ""
            raise RuntimeError(f"{resource} が HTTP {resp.status_code} を返しました: {message}")
        return resp.json()

    def channels_uploads(self, channel_ids: list) -> dict:
        result: dict = {}
        for batch in _chunk(list(channel_ids), 50):
            resp = self._get("channels", {"part": "contentDetails", "id": ",".join(batch)})
            for item in resp.get("items", []):
                up = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
                if up:
                    result[item.get("id")] = up
        return result

    def playlist_items(self, playlist_id: str, page_token: Optional[str]) -> dict:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token
        return self._get("playlistItems", params)

    def videos_list(self, video_ids: list) -> dict:
        params = {
            "part": "snippet,statistics,contentDetails,status",
            "id": ",".join(video_ids),
        }
        return self._get("videos", params)

    def search(self, keyword: str, published_after: str, order: str, region: str,
               lang: str, page_token: Optional[str]) -> dict:
        # part=snippet で channelId を取得（前段の allowlist 除外に使う）。1呼び出し100ユニット。
        params = {
            "part": "snippet",
            "type": "video",
            "q": keyword,
            "videoEmbeddable": "true",
            "regionCode": region,
            "relevanceLanguage": lang,
            "publishedAfter": published_after,
            "order": order,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token
        return self._get("search", params)


class FixtureClient:
    """tests/fixtures の保存済み応答を返すクライアント（--dry-run 用。ネット/キー不使用）。

    期待するファイル（いずれも任意）:
      channels.json      : {channelId: <channel resource(contentDetails)>}
      playlist_items.json: {playlistId: [<page0>, <page1>, ...]}  ※ページは順に返す
      videos.json        : {videoId: <video resource>}
    """

    def __init__(self, fixtures_dir: Path):
        self._channels = self._load(fixtures_dir / "channels.json")
        self._playlist_items = self._load(fixtures_dir / "playlist_items.json")
        self._videos = self._load(fixtures_dir / "videos.json")
        self._search = self._load(fixtures_dir / "search.json")
        self._pi_cursor: dict = {}
        self._search_cursor: dict = {}

    @staticmethod
    def _load(path: Path) -> dict:
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def channels_uploads(self, channel_ids: list) -> dict:
        result: dict = {}
        for cid in channel_ids:
            item = self._channels.get(cid)
            if not item:
                continue
            up = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
            if up:
                result[cid] = up
        return result

    def playlist_items(self, playlist_id: str, page_token: Optional[str]) -> dict:
        pages = self._playlist_items.get(playlist_id, [{"items": []}])
        idx = self._pi_cursor.get(playlist_id, 0)
        if idx >= len(pages):
            return {"items": []}
        self._pi_cursor[playlist_id] = idx + 1
        return pages[idx]

    def videos_list(self, video_ids: list) -> dict:
        items = [self._videos[v] for v in video_ids if v in self._videos]
        return {"items": items}

    def search(self, keyword: str, published_after: str, order: str, region: str,
               lang: str, page_token: Optional[str]) -> dict:
        pages = self._search.get(keyword, [{"items": []}])
        idx = self._search_cursor.get(keyword, 0)
        if idx >= len(pages):
            return {"items": []}
        self._search_cursor[keyword] = idx + 1
        return pages[idx]


class FixtureScoreClient:
    """--dry-run 用の LLM 採点クライアント。tests/fixtures/llm.json の採点をそのまま返す。

    llm.json 形式: {videoId: {"score": int, "label": str, "reason": str}}
    score.score_candidates の検証/閾値パスを実 API 無しで end-to-end に通すため、
    score.py が組んだ user メッセージから候補 videoId を読み取って応答を組み立てる。
    """

    def __init__(self, fixtures_dir: Path):
        path = fixtures_dir / "llm.json"
        self._scores = {}
        if path.exists():
            with path.open(encoding="utf-8") as f:
                self._scores = json.load(f)

    def create_message(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        import re
        match = re.search(r"<candidates>\n(.*)\n</candidates>", user, re.S)
        candidates = json.loads(match.group(1)) if match else []
        results = []
        for c in candidates:
            vid = c.get("videoId")
            if vid in self._scores:
                s = self._scores[vid]
                results.append({"videoId": vid, "score": s["score"],
                                "label": s["label"], "reason": s["reason"]})
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps({"results": results})}]}


# --------------------------------------------------------------------------
# オーケストレーション
# --------------------------------------------------------------------------
def run(config: dict, allowlist: dict, client, now: datetime, score_client=None,
        feedback=None) -> Optional[dict]:
    """allowlist + discovery を収集し週 payload を返す。0件なら None。"""
    lookback_days = config.get("lookback_days", 7)
    after = now - timedelta(days=lookback_days)
    before = now
    channels = allowlist.get("channels", [])

    allowlist_videos = collect_allowlist_videos(client, channels, config, after, before)
    discovery_videos = collect_discovery_videos(client, config, channels, after, before,
                                                score_client=score_client, feedback=feedback)

    merged = merge_and_dedupe(allowlist_videos, discovery_videos)
    ordered = sort_videos(merged, config.get("sort_by", "publishedAt"))
    final = ordered[: config.get("max_videos", 40)]

    logger.info(
        "収集結果: allowlist=%d discovery=%d 統合=%d 採用=%d",
        len(allowlist_videos), len(discovery_videos), len(merged), len(final),
    )
    if not final:
        return None

    week = iso_week_label(now)
    return build_week_payload(week, after, before, final)


def load_json(path: Path) -> dict:
    """JSON を読み込む。欠損/破損は文脈つき ValueError にして呼び出し側で明示エラー化する（3.10）。"""
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ValueError(f"ファイルが見つかりません: {path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析エラー ({path}): {e}")


def load_feedback(path: Path) -> dict:
    """feedback.json を読み込む（spec 12.6）。

    feedback は採点の補助シグナルであり、欠落で全体を失敗させない（fail-open）。
    無い → 空 dict。壊れている/形式不正 → 警告ログのうえ空 dict。
    """
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("feedback.json を読めません（無視して継続）: %s", e)
        return {}
    if not isinstance(data, dict):
        logger.warning("feedback.json の形式が不正です（無視して継続）")
        return {}
    return data


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="週次 収集（allowlist + discovery/LLM採点）")
    repo_root = Path(__file__).resolve().parent.parent
    parser.add_argument("--config", default=str(repo_root / "config.json"))
    parser.add_argument("--allowlist", default=None,
                        help="allowlist.json（既定: 通常は直下、--dry-run 時は fixtures/allowlist.json）")
    parser.add_argument("--data-dir", default=None,
                        help="出力先（既定: 通常 docs/data、--dry-run 時は .dryrun/data）")
    parser.add_argument("--feedback", default=None,
                        help="feedback.json（既定: 通常は直下、--dry-run 時は fixtures/feedback.json）")
    parser.add_argument("--dry-run", action="store_true",
                        help="fixtures モード（API/キー不使用）")
    parser.add_argument("--fixtures", default=str(repo_root / "tests" / "fixtures"),
                        help="--dry-run 時の fixtures ディレクトリ")
    parser.add_argument("--now", help="基準時刻 ISO8601（テスト用。既定: 現在UTC）")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")

    # dry-run 時は実データを汚さないよう fixtures の allowlist / .dryrun 出力を既定にする。
    fixtures_dir = Path(args.fixtures)
    if args.allowlist is not None:
        allowlist_path = Path(args.allowlist)
    elif args.dry_run:
        allowlist_path = fixtures_dir / "allowlist.json"
    else:
        allowlist_path = repo_root / "allowlist.json"
    if args.data_dir is not None:
        data_dir = Path(args.data_dir)
    elif args.dry_run:
        data_dir = repo_root / ".dryrun" / "data"
    else:
        data_dir = repo_root / "docs" / "data"
    # feedback.json（spec 12.4/12.6）。dry-run は fixtures を見る。
    if args.feedback is not None:
        feedback_path = Path(args.feedback)
    elif args.dry_run:
        feedback_path = fixtures_dir / "feedback.json"
    else:
        feedback_path = repo_root / "feedback.json"

    # 設定読み込み・基準時刻・クライアント初期化は失敗時に明示エラーで非ゼロ終了（3.10）。
    try:
        config = load_json(Path(args.config))
        allowlist = load_json(allowlist_path)
    except (OSError, ValueError) as e:
        logger.error("設定の読み込みに失敗しました: %s", e)
        return 1

    try:
        now = parse_dt(args.now) if args.now else datetime.now(timezone.utc)
    except (ValueError, TypeError) as e:
        logger.error("--now の解析に失敗しました: %s", e)
        return 1

    # discovery の LLM 採点が有効なら ANTHROPIC_API_KEY 未設定は明示エラー（3.10）。
    # ※実行時の Anthropic API 障害は別扱いで discovery を空にして継続する（collect_discovery_videos）。
    disc = config.get("discovery") or {}
    discovery_active = disc.get("enabled", True) and (disc.get("llm") or {}).get("enabled", True)
    if not args.dry_run and discovery_active and not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY が未設定です（discovery の LLM 採点が有効）")
        return 1

    try:
        client = FixtureClient(fixtures_dir) if args.dry_run else YouTubeClient()
        score_client = FixtureScoreClient(fixtures_dir) if args.dry_run else None
    except (OSError, ValueError, RuntimeError) as e:
        # 例: YOUTUBE_API_KEY 未設定、fixtures 破損。
        logger.error("初期化に失敗しました: %s", e)
        return 1

    # feedback は fail-open（無い/壊れていても週次は継続。spec 12.6）。
    feedback = load_feedback(feedback_path)

    try:
        payload = run(config, allowlist, client, now, score_client=score_client, feedback=feedback)
    except RuntimeError as e:
        # YouTube API エラー等: 既存JSONを壊さず非ゼロ終了（3.10）。
        logger.error("収集に失敗しました: %s", e)
        return 1

    if payload is None:
        logger.warning("取得0件: 週JSON・index は変更しません（%s）", iso_week_label(now))
        return 0

    try:
        week_path = write_week_json(data_dir, payload)
        index_path = update_index_json(data_dir, payload, payload["generatedAt"])
        # 横断ビュー用 all.json を週JSON 群から再構築（updatedAt は index と揃える）。
        all_path = rebuild_all_json(data_dir, payload["generatedAt"])
    except (ValueError, OSError) as e:
        logger.error("書き込みに失敗しました（既存JSONは保持）: %s", e)
        return 1

    logger.info("生成: %s (%d件) / index 更新: %s / all 更新: %s",
                week_path, payload["count"], index_path, all_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
