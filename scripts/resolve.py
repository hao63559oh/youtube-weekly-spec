#!/usr/bin/env python3
"""YouTube ハンドル/チャンネルURL → channelId・uploadsPlaylistId 解決ユーティリティ。

spec 3.8: 手で正確なIDを入れるのは事故りやすいため、allowlist 登録は必ずこの経路を推奨する。
`channels.list`（part=snippet,contentDetails）で channelId と
contentDetails.relatedPlaylists.uploads（=uploadsPlaylistId）の両方を解決し、allowlist.json に保存する。

使い方:
    YOUTUBE_API_KEY=... python scripts/resolve.py @handle [URL ...] --genre mv [--note "..."]
    YOUTUBE_API_KEY=... python scripts/resolve.py UCxxxx --no-save   # 解決結果の表示のみ

セキュリティ(10.2): APIキーは環境変数からのみ読む。URL/キーはログに出さない。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

# allowlist の genre 値（spec 3.8）。
ALLOWED_GENRES = {"mv", "shortfilm", "cm", "brand", "animation"}

# channels.list エンドポイント。
CHANNELS_ENDPOINT = "https://www.googleapis.com/youtube/v3/channels"

# channelId の形式: UC + 22文字（spec 10.1 系の素朴な検証）。
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

# 型エイリアス: API 応答を返す関数（テストで差し替え可能にする）。
Fetcher = Callable[[dict], dict]


def parse_channel_input(text: str) -> dict:
    """入力文字列を channels.list 用のクエリ種別に正規化する（純関数・ネット不使用）。

    返り値: {"param": "id"|"forHandle"|"forUsername", "value": <str>}
    解決できない形式（/c/カスタムURL など）は ValueError を送出する。
    """
    if text is None:
        raise ValueError("入力が空です")
    s = text.strip()
    if not s:
        raise ValueError("入力が空です")

    # URL 形式。
    if s.lower().startswith(("http://", "https://")):
        parsed = urlparse(s)
        # 空要素を除いたパス断片。
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            raise ValueError(f"チャンネルを特定できないURLです: {s}")
        first = parts[0]
        if first.startswith("@"):
            return {"param": "forHandle", "value": first[1:]}
        if first == "channel" and len(parts) >= 2:
            cid = parts[1]
            if not _CHANNEL_ID_RE.match(cid):
                raise ValueError(f"channelId の形式が不正です: {cid}")
            return {"param": "id", "value": cid}
        if first == "user" and len(parts) >= 2:
            return {"param": "forUsername", "value": parts[1]}
        if first == "c" and len(parts) >= 2:
            # /c/カスタムURL は channels.list で直接解決できない。
            raise ValueError(
                "カスタムURL(/c/...)は直接解決できません。@handle または /channel/UC... を指定してください"
            )
        raise ValueError(f"チャンネルを特定できないURLです: {s}")

    # @handle 形式。
    if s.startswith("@"):
        return {"param": "forHandle", "value": s[1:]}

    # 生の channelId。
    if _CHANNEL_ID_RE.match(s):
        return {"param": "id", "value": s}

    # それ以外はハンドルとして扱う（@ 無しのハンドル入力を許容）。
    return {"param": "forHandle", "value": s}


def extract_entry(api_response: dict) -> dict:
    """channels.list 応答から allowlist エントリの中核を抽出する（純関数）。

    返り値: {"channelId", "uploadsPlaylistId", "name"}
    items が空（チャンネル未発見）や必須欠損は ValueError。
    """
    items = api_response.get("items") or []
    if not items:
        raise ValueError("チャンネルが見つかりませんでした")
    item = items[0]
    channel_id = item.get("id")
    snippet = item.get("snippet") or {}
    content = item.get("contentDetails") or {}
    uploads = (content.get("relatedPlaylists") or {}).get("uploads")
    name = snippet.get("title", "")
    if not channel_id:
        raise ValueError("応答に channelId がありません")
    if not uploads:
        raise ValueError("応答に uploadsPlaylistId がありません")
    return {"channelId": channel_id, "uploadsPlaylistId": uploads, "name": name}


def _default_fetcher(params: dict) -> dict:
    """requests を用いた既定の channels.list 呼び出し。

    APIキーは環境変数 YOUTUBE_API_KEY からのみ取得し、URL/キーはログに出さない(10.2)。
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        raise RuntimeError("環境変数 YOUTUBE_API_KEY が未設定です")

    import requests  # 依存最小化のため遅延 import。

    query = dict(params)
    query["key"] = api_key
    try:
        resp = requests.get(CHANNELS_ENDPOINT, params=query, timeout=30)
    except requests.RequestException:
        # 例外メッセージにURL（=キー）が含まれうるため内容は出さない。
        raise RuntimeError("channels.list リクエストに失敗しました（ネットワークエラー）")
    if resp.status_code != 200:
        # エラー本文の message のみ取り出す（URL/キーは出さない）。
        message = ""
        try:
            message = (resp.json().get("error") or {}).get("message", "")
        except ValueError:
            message = ""
        raise RuntimeError(f"channels.list が HTTP {resp.status_code} を返しました: {message}")
    return resp.json()


def resolve_channel(text: str, fetcher: Optional[Fetcher] = None) -> dict:
    """入力 → channels.list → allowlist エントリ中核（channelId/uploadsPlaylistId/name）。"""
    fetcher = fetcher or _default_fetcher
    query = parse_channel_input(text)
    params = {
        "part": "snippet,contentDetails",
        query["param"]: query["value"],
    }
    response = fetcher(params)
    return extract_entry(response)


def load_allowlist(path: Path) -> dict:
    """allowlist.json を読み込む。無ければ空の構造を返す。"""
    if not path.exists():
        return {"channels": []}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if "channels" not in data or not isinstance(data["channels"], list):
        raise ValueError("allowlist.json の形式が不正です（channels 配列が必要）")
    return data


def upsert_channel(
    allowlist: dict,
    entry: dict,
    genre: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """channelId で重複排除しつつ allowlist にエントリを追加/更新する（純関数）。

    既存があれば name/uploadsPlaylistId を更新。genre/note は引数指定時のみ上書きし、
    無指定なら既存値（無ければ空）を維持する。返り値は (allowlist, 'added'|'updated') ではなく
    更新後の allowlist を返し、追加/更新の別は呼び出し側でログする。
    """
    channels = allowlist.setdefault("channels", [])
    existing = next((c for c in channels if c.get("channelId") == entry["channelId"]), None)
    if existing is None:
        new_entry = {
            "channelId": entry["channelId"],
            "uploadsPlaylistId": entry["uploadsPlaylistId"],
            "name": entry.get("name", ""),
            "genre": genre or "",
            "note": note or "",
        }
        channels.append(new_entry)
    else:
        existing["uploadsPlaylistId"] = entry["uploadsPlaylistId"]
        existing["name"] = entry.get("name", existing.get("name", ""))
        if genre is not None:
            existing["genre"] = genre
        if note is not None:
            existing["note"] = note
    return allowlist


def save_allowlist(path: Path, allowlist: dict) -> None:
    """allowlist.json を整形して書き出す（末尾改行つき・日本語そのまま）。"""
    with path.open("w", encoding="utf-8") as f:
        json.dump(allowlist, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="YouTube ハンドル/URL を channelId + uploadsPlaylistId に解決し allowlist.json に登録する"
    )
    parser.add_argument("inputs", nargs="+", help="@handle / チャンネルURL / channelId（複数可）")
    parser.add_argument("--genre", help=f"ジャンル {sorted(ALLOWED_GENRES)}（このコマンドの全入力に適用）")
    parser.add_argument("--note", help="メモ（任意）")
    parser.add_argument(
        "--allowlist",
        default=str(Path(__file__).resolve().parent.parent / "allowlist.json"),
        help="allowlist.json のパス（既定: リポジトリ直下）",
    )
    parser.add_argument("--no-save", action="store_true", help="解決結果を表示するのみで保存しない")
    args = parser.parse_args(argv)

    if args.genre is not None and args.genre not in ALLOWED_GENRES:
        print(f"エラー: genre は {sorted(ALLOWED_GENRES)} のいずれかです（指定値: {args.genre}）", file=sys.stderr)
        return 2

    allowlist_path = Path(args.allowlist)
    allowlist = load_allowlist(allowlist_path) if not args.no_save else None

    exit_code = 0
    for text in args.inputs:
        try:
            entry = resolve_channel(text)
        except (ValueError, RuntimeError) as e:
            print(f"解決失敗: {text} -> {e}", file=sys.stderr)
            exit_code = 1
            continue
        print(
            f"解決成功: {text}\n"
            f"  channelId        = {entry['channelId']}\n"
            f"  uploadsPlaylistId= {entry['uploadsPlaylistId']}\n"
            f"  name             = {entry['name']}"
        )
        if not args.no_save:
            before = json.dumps(allowlist, sort_keys=True, ensure_ascii=False)
            upsert_channel(allowlist, entry, genre=args.genre, note=args.note)
            after = json.dumps(allowlist, sort_keys=True, ensure_ascii=False)
            if before != after:
                # 成功ごとに随時保存（後続の失敗で成功分が失われないように）。
                save_allowlist(allowlist_path, allowlist)
                print(f"  -> allowlist 保存: {allowlist_path}")
            else:
                print("  -> allowlist 変更なし")

    if exit_code != 0:
        print("一部の入力で解決に失敗しました（成功分は保存済み）。", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
