"""fetch.py のユニットテスト + ドライラン結合テスト（spec 11.1 / 11.2）。"""
import json
import sys
import tempfile
import unittest
from datetime import timezone
from pathlib import Path

# scripts/ を import path に追加。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch  # noqa: E402
import score as score_mod  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONFIG = ROOT / "config.json"
NOW = "2026-06-19T08:00:00Z"


class TestTimeWeekDuration(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(fetch.parse_iso8601_duration("PT2M30S"), 150)
        self.assertEqual(fetch.parse_iso8601_duration("PT1H1M1S"), 3661)
        self.assertEqual(fetch.parse_iso8601_duration("PT45S"), 45)
        self.assertEqual(fetch.parse_iso8601_duration("P0D"), 0)
        self.assertEqual(fetch.parse_iso8601_duration(""), 0)
        self.assertEqual(fetch.parse_iso8601_duration("garbage"), 0)

    def test_iso_week_label(self):
        self.assertEqual(fetch.iso_week_label(fetch.parse_dt("2026-06-19T08:00:00Z")), "2026-W25")
        # ISO 週は年をまたぐことがある（境界確認）。
        self.assertEqual(fetch.iso_week_label(fetch.parse_dt("2026-01-01T00:00:00Z")), "2026-W01")

    def test_iso_z_normalizes_to_utc(self):
        self.assertEqual(fetch.iso_z(fetch.parse_dt("2026-06-19T08:00:00+09:00")),
                         "2026-06-18T23:00:00Z")

    def test_within_period_boundaries(self):
        after = fetch.parse_dt("2026-06-12T08:00:00Z")
        before = fetch.parse_dt("2026-06-19T08:00:00Z")
        self.assertTrue(fetch.within_period("2026-06-12T08:00:00Z", after, before))   # 下端含む
        self.assertTrue(fetch.within_period("2026-06-19T08:00:00Z", after, before))   # 上端含む
        self.assertFalse(fetch.within_period("2026-06-12T07:59:59Z", after, before))  # 直前
        self.assertFalse(fetch.within_period("2026-06-19T08:00:01Z", after, before))  # 直後
        self.assertFalse(fetch.within_period("not-a-date", after, before))


class TestSortMerge(unittest.TestCase):
    def test_sort_published_desc(self):
        vs = [{"videoId": "a", "publishedAt": "2026-01-01T00:00:00Z"},
              {"videoId": "b", "publishedAt": "2026-03-01T00:00:00Z"},
              {"videoId": "c", "publishedAt": "2026-02-01T00:00:00Z"}]
        self.assertEqual([v["videoId"] for v in fetch.sort_videos(vs, "publishedAt")],
                         ["b", "c", "a"])

    def test_sort_viewcount_null_last(self):
        vs = [{"videoId": "a", "viewCount": 10, "publishedAt": "2026-01-01T00:00:00Z"},
              {"videoId": "b", "viewCount": None, "publishedAt": "2026-02-01T00:00:00Z"},
              {"videoId": "c", "viewCount": 50, "publishedAt": "2026-01-01T00:00:00Z"}]
        self.assertEqual([v["videoId"] for v in fetch.sort_videos(vs, "viewCount")],
                         ["c", "a", "b"])

    def test_sort_score_null_last_tiebreak_published(self):
        vs = [{"videoId": "a", "score": 80, "publishedAt": "2026-01-01T00:00:00Z"},
              {"videoId": "b", "score": None, "publishedAt": "2026-02-01T00:00:00Z"},
              {"videoId": "c", "score": None, "publishedAt": "2026-03-01T00:00:00Z"}]
        # null 同士は publishedAt 降順 → c が b より先。
        self.assertEqual([v["videoId"] for v in fetch.sort_videos(vs, "score")],
                         ["a", "c", "b"])

    def test_merge_allowlist_priority(self):
        a = [{"videoId": "dup", "source": "allowlist"}, {"videoId": "x", "source": "allowlist"}]
        d = [{"videoId": "dup", "source": "discovery"}, {"videoId": "y", "source": "discovery"}]
        merged = fetch.merge_and_dedupe(a, d)
        by_id = {v["videoId"]: v for v in merged}
        self.assertEqual(len(merged), 3)
        self.assertEqual(by_id["dup"]["source"], "allowlist")  # allowlist 優先


class TestResolveUploadsMap(unittest.TestCase):
    def test_uses_saved_id(self):
        channels = [{"channelId": "UC1", "uploadsPlaylistId": "UU_saved"}]
        called = []

        def fn(ids):
            called.append(ids)
            return {}

        result = fetch.resolve_uploads_map(channels, fn)
        self.assertEqual(result, {"UC1": "UU_saved"})
        self.assertEqual(called, [])  # 保存済みなので channels.list は呼ばれない

    def test_resolves_via_channels_list(self):
        channels = [{"channelId": "UC2"}]
        result = fetch.resolve_uploads_map(channels, lambda ids: {"UC2": "UU_resolved"})
        self.assertEqual(result, {"UC2": "UU_resolved"})

    def test_uu_fallback(self):
        cid = "UCabcabcabcabcabcabcabca"
        channels = [{"channelId": cid}]
        # channels.list が解決できない → UC→UU 置換フォールバック。
        result = fetch.resolve_uploads_map(channels, lambda ids: {})
        self.assertEqual(result, {cid: "UU" + cid[2:]})


class TestBuildVideoObject(unittest.TestCase):
    def test_null_viewcount_when_missing(self):
        item = {"id": "vidaaaa0001", "snippet": {"title": "t", "channelId": "UC", "channelTitle": "c",
                                                 "publishedAt": "2026-06-18T10:00:00Z"},
                "statistics": {"likeCount": "5"}, "contentDetails": {"duration": "PT3M"},
                "status": {"embeddable": True}}
        v = fetch.build_video_object(item, source="allowlist", genre="mv")
        self.assertIsNone(v["viewCount"])      # 欠損は null（0 で代用しない）
        self.assertEqual(v["likeCount"], 5)
        self.assertEqual(v["durationSeconds"], 180)
        self.assertEqual(v["thumbnail"], "https://i.ytimg.com/vi/vidaaaa0001/hqdefault.jpg")
        self.assertEqual(v["url"], "https://www.youtube.com/watch?v=vidaaaa0001")
        self.assertEqual(v["source"], "allowlist")
        self.assertEqual(v["genre"], "mv")
        self.assertIsNone(v["score"])


class TestValidate(unittest.TestCase):
    def _payload(self):
        return fetch.build_week_payload(
            "2026-W25",
            fetch.parse_dt("2026-06-12T08:00:00Z"),
            fetch.parse_dt("2026-06-19T08:00:00Z"),
            [fetch.build_video_object(
                {"id": "vidaaaa0001",
                 "snippet": {"title": "t", "channelId": "UC", "channelTitle": "c",
                             "publishedAt": "2026-06-18T10:00:00Z"},
                 "statistics": {"viewCount": "1"}, "contentDetails": {"duration": "PT3M"},
                 "status": {"embeddable": True}}, "allowlist", "mv")],
        )

    def test_valid_passes(self):
        fetch.validate_week_payload(self._payload())  # 例外が出なければ OK

    def test_bad_video_id_raises(self):
        p = self._payload()
        p["videos"][0]["videoId"] = "bad"
        with self.assertRaises(ValueError):
            fetch.validate_week_payload(p)

    def test_count_mismatch_raises(self):
        p = self._payload()
        p["count"] = 99
        with self.assertRaises(ValueError):
            fetch.validate_week_payload(p)

    def test_bad_source_raises(self):
        p = self._payload()
        p["videos"][0]["source"] = "evil"
        with self.assertRaises(ValueError):
            fetch.validate_week_payload(p)


class TestDryRunIntegration(unittest.TestCase):
    def _run(self, data_dir, now=NOW):
        return fetch.main([
            "--dry-run",
            "--config", str(CONFIG),
            "--allowlist", str(FIXTURES / "allowlist.json"),
            "--fixtures", str(FIXTURES),
            "--data-dir", str(data_dir),
            "--now", now,
        ])

    def test_generates_expected_week(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            self.assertEqual(self._run(data), 0)
            wk = json.loads((data / "weeks" / "2026-W25.json").read_text(encoding="utf-8"))
            self.assertEqual(wk["week"], "2026-W25")
            self.assertEqual(wk["count"], 3)
            ids = [v["videoId"] for v in wk["videos"]]
            # 既定 publishedAt 降順、除外（埋め込み不可/短尺/期間外）が効いている。
            self.assertEqual(ids, ["vidaaaa0001", "vidaaaa0004", "vidbbbb0001"])
            self.assertNotIn("vidaaaa0002", ids)  # 埋め込み不可
            self.assertNotIn("vidaaaa0003", ids)  # 短尺
            self.assertNotIn("vidaaaa0005", ids)  # 期間外
            by = {v["videoId"]: v for v in wk["videos"]}
            self.assertEqual(by["vidaaaa0001"]["genre"], "mv")
            self.assertEqual(by["vidbbbb0001"]["genre"], "shortfilm")
            self.assertTrue(all(v["source"] == "allowlist" for v in wk["videos"]))
            self.assertIsNone(by["vidaaaa0004"]["viewCount"])  # 再生数非公開 → null
            # index.json に当該週が登録される。
            idx = json.loads((data / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(idx["weeks"][0]["week"], "2026-W25")
            self.assertEqual(idx["weeks"][0]["count"], 3)
            # all.json も main 経由で生成され、各 video に week が付与される。
            allj = json.loads((data / "all.json").read_text(encoding="utf-8"))
            self.assertEqual(allj["count"], 3)
            self.assertTrue(all(v["week"] == "2026-W25" for v in allj["videos"]))

    def test_idempotent_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            self.assertEqual(self._run(data), 0)
            first = (data / "weeks" / "2026-W25.json").read_bytes()
            self.assertEqual(self._run(data), 0)
            second = (data / "weeks" / "2026-W25.json").read_bytes()
            self.assertEqual(first, second)  # 同週再実行はバイト一致
            idx = json.loads((data / "index.json").read_text(encoding="utf-8"))
            self.assertEqual([w["week"] for w in idx["weeks"]], ["2026-W25"])  # 重複しない

    def test_zero_results_skips_write(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            # 期間内に何も無い未来週 → 週JSON も index も作らない。
            self.assertEqual(self._run(data, now="2026-09-01T08:00:00Z"), 0)
            self.assertFalse((data / "weeks").exists())
            self.assertFalse((data / "index.json").exists())


class TestDiscovery(unittest.TestCase):
    def _config(self):
        return {
            "region_code": "JP", "relevance_language": "ja",
            "discovery": {
                "enabled": True, "keywords": ["テスト検索KW"], "order": "relevance",
                "search_pages": 1, "exclude_categories": ["22"],
                "min_duration_sec": 30, "max_duration_sec": 1800, "max_candidates": 60,
                "llm": {"enabled": True, "score_threshold": 65},
            },
        }

    def _run_discovery(self, config=None, channels=None):
        client = fetch.FixtureClient(FIXTURES)
        score_client = fetch.FixtureScoreClient(FIXTURES)
        after = fetch.parse_dt("2026-06-12T08:00:00Z")
        before = fetch.parse_dt("2026-06-19T08:00:00Z")
        return fetch.collect_discovery_videos(
            client, config or self._config(),
            channels if channels is not None else [{"channelId": "UCaaaaaaaaaaaaaaaaaaaaaa"}],
            after, before, score_client=score_client,
        )

    def test_full_chain_adopts_only_valid(self):
        videos = self._run_discovery()
        ids = [v["videoId"] for v in videos]
        # 重複排除 / allowlist除外 / category22 / 短尺 / 埋め込み不可 / 閾値未満 が効き discov00001 のみ。
        self.assertEqual(ids, ["discov00001"])
        v = videos[0]
        self.assertEqual(v["source"], "discovery")
        self.assertIsNone(v["genre"])  # discovery は genre=null
        self.assertEqual(v["score"], 90)
        self.assertEqual(v["label"], "cinematic")
        self.assertEqual(v["reason"], "監督・撮影クレジットあり")

    def test_disabled_returns_empty(self):
        cfg = self._config()
        cfg["discovery"]["enabled"] = False
        self.assertEqual(self._run_discovery(cfg), [])

    def test_quota_guard_raises(self):
        cfg = self._config()
        cfg["discovery"]["search_unit_budget"] = 50  # 1×1×100=100 > 50
        with self.assertRaises(RuntimeError):
            self._run_discovery(cfg)

    def test_llm_failure_empties_discovery(self):
        # 採点クライアントが ScoreError を送出 → discovery は空（処理は止めない）。
        class FailingScore:
            def create_message(self, *a, **k):
                raise score_mod.ScoreError("outage")
        client = fetch.FixtureClient(FIXTURES)
        after = fetch.parse_dt("2026-06-12T08:00:00Z")
        before = fetch.parse_dt("2026-06-19T08:00:00Z")
        videos = fetch.collect_discovery_videos(
            client, self._config(), [{"channelId": "UCaaaaaaaaaaaaaaaaaaaaaa"}],
            after, before, score_client=FailingScore())
        self.assertEqual(videos, [])

    def test_end_to_end_merge_with_allowlist(self):
        # main 経由で allowlist + discovery がマージされること。
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            cfg_path = Path(d) / "config.json"
            base = json.loads(CONFIG.read_text(encoding="utf-8"))
            base["discovery"]["keywords"] = ["テスト検索KW"]
            cfg_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
            rc = fetch.main([
                "--dry-run", "--config", str(cfg_path),
                "--allowlist", str(FIXTURES / "allowlist.json"),
                "--fixtures", str(FIXTURES), "--data-dir", str(data), "--now", NOW,
            ])
            self.assertEqual(rc, 0)
            wk = json.loads((data / "weeks" / "2026-W25.json").read_text(encoding="utf-8"))
            by = {v["videoId"]: v for v in wk["videos"]}
            self.assertEqual(wk["count"], 4)  # allowlist 3 + discovery 1
            self.assertEqual(by["discov00001"]["source"], "discovery")
            self.assertEqual(by["discov00001"]["score"], 90)
            self.assertEqual(by["vidaaaa0001"]["source"], "allowlist")


class TestRebuildAll(unittest.TestCase):
    """all.json（全期間横断ビュー）の再構築（UI 絞り込み・横断ビュー用）。"""

    def _mk_video(self, vid):
        return fetch.build_video_object(
            {"id": vid,
             "snippet": {"title": "t", "channelId": "UC", "channelTitle": "c",
                         "publishedAt": "2026-06-18T10:00:00Z"},
             "statistics": {"viewCount": "1"}, "contentDetails": {"duration": "PT3M"},
             "status": {"embeddable": True}},
            "allowlist", "mv")

    def _seed_two_weeks(self, data):
        a, b = fetch.parse_dt("2026-06-12T08:00:00Z"), fetch.parse_dt("2026-06-19T08:00:00Z")
        c, d2 = fetch.parse_dt("2026-06-19T08:00:00Z"), fetch.parse_dt("2026-06-26T08:00:00Z")
        p25 = fetch.build_week_payload("2026-W25", a, b, [self._mk_video("vidaaaa0001")])
        p26 = fetch.build_week_payload("2026-W26", c, d2,
                                       [self._mk_video("vidbbbb0001"), self._mk_video("vidbbbb0002")])
        for p in (p25, p26):
            fetch.write_week_json(data, p)
            fetch.update_index_json(data, p, p["generatedAt"])

    def test_aggregates_newest_first_with_week_field(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            self._seed_two_weeks(data)
            fetch.rebuild_all_json(data, "2026-06-26T08:00:00Z")
            allj = json.loads((data / "all.json").read_text(encoding="utf-8"))
            self.assertEqual(allj["count"], 3)
            self.assertEqual(allj["updatedAt"], "2026-06-26T08:00:00Z")
            # 新しい週が先頭（W26 が W25 より前）。週内は週JSON の並び順を保持。
            self.assertEqual([v["week"] for v in allj["videos"]],
                             ["2026-W26", "2026-W26", "2026-W25"])
            self.assertEqual([v["videoId"] for v in allj["videos"]],
                             ["vidbbbb0001", "vidbbbb0002", "vidaaaa0001"])
            # 各 video に week が付与される。
            self.assertTrue(all(v.get("week") for v in allj["videos"]))

    def test_idempotent_byte_equal(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            self._seed_two_weeks(data)
            fetch.rebuild_all_json(data, "2026-06-26T08:00:00Z")
            first = (data / "all.json").read_bytes()
            fetch.rebuild_all_json(data, "2026-06-26T08:00:00Z")
            second = (data / "all.json").read_bytes()
            self.assertEqual(first, second)

    def test_uses_index_updated_at_when_omitted(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            self._seed_two_weeks(data)
            idx = json.loads((data / "index.json").read_text(encoding="utf-8"))
            fetch.rebuild_all_json(data)  # updated_at 省略 → index.updatedAt を採用
            allj = json.loads((data / "all.json").read_text(encoding="utf-8"))
            self.assertEqual(allj["updatedAt"], idx["updatedAt"])

    def test_empty_when_no_index(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            fetch.rebuild_all_json(data, "2026-06-26T08:00:00Z")
            allj = json.loads((data / "all.json").read_text(encoding="utf-8"))
            self.assertEqual(allj["count"], 0)
            self.assertEqual(allj["videos"], [])

    def test_dedupes_same_video_across_weeks_keeping_newest(self):
        # 同一 videoId が複数週に出た場合は 1 件に排除し、最新週の版（週バッジ）を残す。
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            a, b = fetch.parse_dt("2026-06-12T08:00:00Z"), fetch.parse_dt("2026-06-19T08:00:00Z")
            c, d2 = fetch.parse_dt("2026-06-19T08:00:00Z"), fetch.parse_dt("2026-06-26T08:00:00Z")
            # dup0001 は両週に、W26 には固有の new0001 も。
            p25 = fetch.build_week_payload("2026-W25", a, b, [self._mk_video("dupxxxx0001")])
            p26 = fetch.build_week_payload("2026-W26", c, d2,
                                           [self._mk_video("dupxxxx0001"), self._mk_video("newxxxx0001")])
            for p in (p25, p26):
                fetch.write_week_json(data, p)
                fetch.update_index_json(data, p, p["generatedAt"])
            fetch.rebuild_all_json(data, "2026-06-26T08:00:00Z")
            allj = json.loads((data / "all.json").read_text(encoding="utf-8"))
            self.assertEqual(allj["count"], 2)  # 3 件中 1 件が重複排除。
            ids = [v["videoId"] for v in allj["videos"]]
            self.assertEqual(ids, ["dupxxxx0001", "newxxxx0001"])  # 新しい週順・ユニーク。
            # 残った dup は最新週 W26 の版。
            dup = next(v for v in allj["videos"] if v["videoId"] == "dupxxxx0001")
            self.assertEqual(dup["week"], "2026-W26")


class TestRecentExclude(unittest.TestCase):
    """recent_exclude: 過去週参照で同一動画/同一 discovery チャンネルの再選出を防ぐ。"""

    NOW = "2026-07-08T00:00:00Z"  # iso_week = 2026-W28

    def _vid(self, vid, channel_id, source):
        return fetch.build_video_object(
            {"id": vid,
             "snippet": {"title": "t", "channelId": channel_id, "channelTitle": "c",
                         "publishedAt": "2026-06-18T10:00:00Z"},
             "statistics": {"viewCount": "1"}, "contentDetails": {"duration": "PT3M"},
             "status": {"embeddable": True}},
            source, None)

    def _seed(self, data, week, generated_at, videos):
        a = fetch.parse_dt("2026-06-01T00:00:00Z")
        p = fetch.build_week_payload(week, a, fetch.parse_dt(generated_at), videos)
        fetch.write_week_json(data, p)
        fetch.update_index_json(data, p, p["generatedAt"])
        return p

    def test_video_permanent_channel_windowed(self):
        # 37日前の週: 動画は video_days(3650)内で除外対象、チャンネルは channel_days(30)外で対象外。
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            self._seed(data, "2026-W23", "2026-06-01T00:00:00Z",
                       [self._vid("oldvid00001", "UColdxxxxxxxxxxxxxxxxxx", "discovery")])
            vids, chs = fetch.load_recent_picks(
                data / "weeks", fetch.parse_dt(self.NOW),
                video_days=3650, channel_days=30, exclude_week="2026-W28")
            self.assertIn("oldvid00001", vids)
            self.assertNotIn("UColdxxxxxxxxxxxxxxxxxx", chs)

    def test_channel_within_window(self):
        # 7日前の週の discovery チャンネルは channel_days(30)内で除外対象。
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            self._seed(data, "2026-W27", "2026-07-01T00:00:00Z",
                       [self._vid("recvid00001", "UCrecentxxxxxxxxxxxxxx", "discovery")])
            vids, chs = fetch.load_recent_picks(
                data / "weeks", fetch.parse_dt(self.NOW),
                video_days=3650, channel_days=30, exclude_week="2026-W28")
            self.assertIn("recvid00001", vids)
            self.assertIn("UCrecentxxxxxxxxxxxxxx", chs)

    def test_allowlist_channel_not_in_channel_set(self):
        # allowlist 由来は動画IDは除外対象だが、チャンネル除外の対象にはしない。
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            self._seed(data, "2026-W27", "2026-07-01T00:00:00Z",
                       [self._vid("allvid00001", "UCallowxxxxxxxxxxxxxxx", "allowlist")])
            vids, chs = fetch.load_recent_picks(
                data / "weeks", fetch.parse_dt(self.NOW),
                video_days=3650, channel_days=30, exclude_week="2026-W28")
            self.assertIn("allvid00001", vids)
            self.assertNotIn("UCallowxxxxxxxxxxxxxxx", chs)

    def test_exclude_week_skipped(self):
        # 今回の週ラベルと同じ週は自己除外を避けるためスキップ（同週再実行の冪等性）。
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            self._seed(data, "2026-W28", "2026-07-06T00:00:00Z",
                       [self._vid("selfvid0001", "UCselfxxxxxxxxxxxxxxxx", "discovery")])
            vids, chs = fetch.load_recent_picks(
                data / "weeks", fetch.parse_dt(self.NOW),
                video_days=3650, channel_days=30, exclude_week="2026-W28")
            self.assertEqual(vids, set())
            self.assertEqual(chs, set())

    def test_collect_discovery_excludes_recent_video(self):
        # 前段で recent_video_ids に一致する候補は videos.list/採点前に除外される。
        client = fetch.FixtureClient(FIXTURES)
        score_client = fetch.FixtureScoreClient(FIXTURES)
        after = fetch.parse_dt("2026-06-12T08:00:00Z")
        before = fetch.parse_dt("2026-06-19T08:00:00Z")
        cfg = TestDiscovery()._config()
        base = fetch.collect_discovery_videos(
            client, cfg, [{"channelId": "UCaaaaaaaaaaaaaaaaaaaaaa"}],
            after, before, score_client=score_client)
        self.assertEqual([v["videoId"] for v in base], ["discov00001"])
        excluded = fetch.collect_discovery_videos(
            client, cfg, [{"channelId": "UCaaaaaaaaaaaaaaaaaaaaaa"}],
            after, before, score_client=score_client,
            recent_video_ids={"discov00001"})
        self.assertEqual(excluded, [])


class TestMainErrors(unittest.TestCase):
    def test_missing_allowlist_returns_1(self):
        with tempfile.TemporaryDirectory() as d:
            rc = fetch.main([
                "--dry-run", "--config", str(CONFIG),
                "--allowlist", str(Path(d) / "nope.json"),
                "--fixtures", str(FIXTURES), "--data-dir", str(d), "--now", NOW,
            ])
            self.assertEqual(rc, 1)  # 欠損は明示エラーで非ゼロ終了

    def test_malformed_config_returns_1(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "config.json"
            bad.write_text("{ not json", encoding="utf-8")
            rc = fetch.main([
                "--dry-run", "--config", str(bad),
                "--allowlist", str(FIXTURES / "allowlist.json"),
                "--fixtures", str(FIXTURES), "--data-dir", str(d), "--now", NOW,
            ])
            self.assertEqual(rc, 1)


class TestLoadFeedback(unittest.TestCase):
    """feedback.json の fail-open 読込（spec 12.6）。"""

    def test_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(fetch.load_feedback(Path(d) / "nope.json"), {})

    def test_broken_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "feedback.json"
            p.write_text("{ not json", encoding="utf-8")
            self.assertEqual(fetch.load_feedback(p), {})

    def test_non_dict_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "feedback.json"
            p.write_text("[1, 2, 3]", encoding="utf-8")
            self.assertEqual(fetch.load_feedback(p), {})

    def test_valid_loads(self):
        fb = fetch.load_feedback(FIXTURES / "feedback.json")
        self.assertEqual(fb["version"], 1)
        self.assertEqual(len(fb["items"]), 2)

    def test_dry_run_with_feedback_unaffected_adoption(self):
        # feedback 同梱でも FixtureScoreClient の採点は不変 → discovery 採用は従来どおり。
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            cfg_path = Path(d) / "config.json"
            base = json.loads(CONFIG.read_text(encoding="utf-8"))
            base["discovery"]["keywords"] = ["テスト検索KW"]
            cfg_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
            rc = fetch.main([
                "--dry-run", "--config", str(cfg_path),
                "--allowlist", str(FIXTURES / "allowlist.json"),
                "--fixtures", str(FIXTURES), "--data-dir", str(data),
                "--feedback", str(FIXTURES / "feedback.json"), "--now", NOW,
            ])
            self.assertEqual(rc, 0)
            wk = json.loads((data / "weeks" / "2026-W25.json").read_text(encoding="utf-8"))
            by = {v["videoId"]: v for v in wk["videos"]}
            self.assertEqual(wk["count"], 4)  # allowlist 3 + discovery 1（feedback で変わらない）
            self.assertEqual(by["discov00001"]["score"], 90)


class TestIndexKeepPast(unittest.TestCase):
    def test_append_keeps_past_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            fetch.update_index_json(data, {"week": "2026-W25", "count": 3,
                                           "generatedAt": "2026-06-19T08:00:00Z"},
                                    "2026-06-19T08:00:00Z")
            fetch.update_index_json(data, {"week": "2026-W26", "count": 5,
                                           "generatedAt": "2026-06-26T08:00:00Z"},
                                    "2026-06-26T08:00:00Z")
            idx = json.loads((data / "index.json").read_text(encoding="utf-8"))
            self.assertEqual([w["week"] for w in idx["weeks"]], ["2026-W26", "2026-W25"])
            # 既存週の再更新は重複させず上書き。
            fetch.update_index_json(data, {"week": "2026-W25", "count": 9,
                                           "generatedAt": "2026-06-19T09:00:00Z"},
                                    "2026-06-19T09:00:00Z")
            idx = json.loads((data / "index.json").read_text(encoding="utf-8"))
            self.assertEqual([w["week"] for w in idx["weeks"]], ["2026-W26", "2026-W25"])
            w25 = next(w for w in idx["weeks"] if w["week"] == "2026-W25")
            self.assertEqual(w25["count"], 9)


class TestRoundRobin(unittest.TestCase):
    def test_interleaves_evenly(self):
        self.assertEqual(fetch._round_robin([[1, 2, 3], ["a", "b"], ["x"]]),
                         [1, "a", "x", 2, "b", 3])

    def test_empty_and_uneven(self):
        self.assertEqual(fetch._round_robin([[], [1], [2, 3]]), [1, 2, 3])
        self.assertEqual(fetch._round_robin([]), [])


class TestSelectByRatio(unittest.TestCase):
    def _v(self, vid, ch, genre, score):
        return {"videoId": vid, "channelId": ch, "genre": genre, "score": score}

    def test_per_channel_cap(self):
        # 同一チャンネルは最大2件（スコア上位を残す）。
        vids = [self._v(f"v{i:010d}", "chA", "cm", 90 - i) for i in range(5)]
        out = fetch.select_by_ratio(vids, max_videos=40, max_per_channel=2)
        self.assertEqual(len(out), 2)
        self.assertEqual([v["score"] for v in out], [90, 89])

    def test_genre_ratio_split(self):
        # cm/mv/other 各8件。max_videos=10, 4:4:2 → cm4/mv4/other2。
        cms = [self._v(f"c{i:09d}", f"cmch{i}", "cm", 80 - i) for i in range(8)]
        mvs = [self._v(f"m{i:09d}", f"mvch{i}", "mv", 70 - i) for i in range(8)]
        others = [self._v(f"o{i:09d}", f"och{i}", "brand", 60 - i) for i in range(8)]
        out = fetch.select_by_ratio(cms + mvs + others, max_videos=10,
                                    genre_ratio={"cm": 0.4, "mv": 0.4, "other": 0.2})
        buckets = {"cm": 0, "mv": 0, "other": 0}
        for v in out:
            buckets[fetch._genre_bucket(v["genre"])] += 1
        self.assertEqual(len(out), 10)
        self.assertEqual(buckets, {"cm": 4, "mv": 4, "other": 2})

    def test_underfill_redistributes(self):
        # mv が枠(4)に満たない(2件)場合、残枠は他ジャンルへスコア順で再配分し max_videos を満たす。
        cms = [self._v(f"c{i:09d}", f"cmch{i}", "cm", 80 - i) for i in range(8)]
        mvs = [self._v(f"m{i:09d}", f"mvch{i}", "mv", 70 - i) for i in range(2)]
        out = fetch.select_by_ratio(cms + mvs, max_videos=10,
                                    genre_ratio={"cm": 0.4, "mv": 0.4, "other": 0.2})
        self.assertEqual(len(out), 10)  # 2件不足でも他で埋めて10件
        self.assertEqual(sum(1 for v in out if v["genre"] == "mv"), 2)

    def test_sorted_by_score_desc(self):
        vids = [self._v("v0000000001", "a", "cm", 50),
                self._v("v0000000002", "b", "mv", 90),
                self._v("v0000000003", "c", "brand", 70)]
        out = fetch.select_by_ratio(vids, max_videos=40)
        self.assertEqual([v["score"] for v in out], [90, 70, 50])


if __name__ == "__main__":
    unittest.main()
