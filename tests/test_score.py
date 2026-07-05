"""score.py のユニットテスト（fail-closed 検証・バッチ・障害時の挙動。ネット不使用）。"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import score  # noqa: E402


def _resp(results, stop_reason="end_turn"):
    """Anthropic 応答（成功形）を模す。"""
    return {"stop_reason": stop_reason,
            "content": [{"type": "text", "text": json.dumps({"results": results})}]}


class FakeClient:
    """create_message の応答をキューで返す注入用クライアント。"""

    def __init__(self, responses=None, raise_error=False):
        self._responses = list(responses or [])
        self._raise = raise_error
        self.calls = 0

    def create_message(self, system, user, schema, max_tokens):
        self.calls += 1
        if self._raise:
            raise score.ScoreError("boom")
        return self._responses.pop(0)


class TestValidateResults(unittest.TestCase):
    def setUp(self):
        self.valid_ids = {"vidaaaa0001", "vidaaaa0002"}

    def test_valid_row_kept(self):
        rows = [{"videoId": "vidaaaa0001", "score": 80, "label": "cinematic", "reason": "監督名あり",
                 "genre": "cm"}]
        out = score.validate_results(rows, self.valid_ids, set(), 60)
        self.assertEqual(out, {"vidaaaa0001": {"score": 80, "label": "cinematic",
                                               "reason": "監督名あり", "genre": "cm"}})

    def test_genre_missing_or_invalid_becomes_none(self):
        # genre 欠落・enum外・"other" は None に倒す（fail-open：行は残す）。
        for g in (None, "other", "unknown", "MV"):
            row = {"videoId": "vidaaaa0001", "score": 80, "label": "cinematic", "reason": "r"}
            if g is not None:
                row["genre"] = g
            out = score.validate_results([row], self.valid_ids, set(), 60)
            self.assertEqual(out["vidaaaa0001"]["genre"], None)

    def test_genre_valid_kept(self):
        for g in ("mv", "shortfilm", "cm", "brand", "animation"):
            row = {"videoId": "vidaaaa0001", "score": 80, "label": "cinematic", "reason": "r", "genre": g}
            out = score.validate_results([row], self.valid_ids, set(), 60)
            self.assertEqual(out["vidaaaa0001"]["genre"], g)

    def test_unknown_videoid_dropped(self):
        rows = [{"videoId": "zzzzzzzzzzz", "score": 80, "label": "cinematic", "reason": "x"}]
        self.assertEqual(score.validate_results(rows, self.valid_ids, set(), 60), {})

    def test_duplicate_dropped(self):
        seen = {"vidaaaa0001"}
        rows = [{"videoId": "vidaaaa0001", "score": 80, "label": "cinematic", "reason": "x"}]
        self.assertEqual(score.validate_results(rows, self.valid_ids, seen, 60), {})

    def test_score_out_of_range_dropped(self):
        for bad in (-1, 101, 1000):
            rows = [{"videoId": "vidaaaa0001", "score": bad, "label": "casual", "reason": "x"}]
            self.assertEqual(score.validate_results(rows, self.valid_ids, set(), 60), {})

    def test_score_non_int_dropped(self):
        for bad in ("80", 80.0, True, None):
            rows = [{"videoId": "vidaaaa0001", "score": bad, "label": "casual", "reason": "x"}]
            self.assertEqual(score.validate_results(rows, self.valid_ids, set(), 60), {})

    def test_label_invalid_dropped(self):
        rows = [{"videoId": "vidaaaa0001", "score": 50, "label": "great", "reason": "x"}]
        self.assertEqual(score.validate_results(rows, self.valid_ids, set(), 60), {})

    def test_reason_empty_or_too_long_dropped(self):
        rows = [{"videoId": "vidaaaa0001", "score": 50, "label": "casual", "reason": "   "}]
        self.assertEqual(score.validate_results(rows, self.valid_ids, set(), 60), {})
        rows = [{"videoId": "vidaaaa0001", "score": 50, "label": "casual", "reason": "あ" * 61}]
        self.assertEqual(score.validate_results(rows, self.valid_ids, set(), 60), {})


class TestScoreCandidates(unittest.TestCase):
    def _cands(self, n):
        return [{"videoId": f"vid{str(i).zfill(8)}", "title": "t", "channelTitle": "c",
                 "description": "d", "categoryId": "1", "durationSeconds": 100, "tags": []}
                for i in range(n)]

    def test_disabled_returns_empty_without_client_call(self):
        client = FakeClient()
        out = score.score_candidates(self._cands(2), {"discovery": {"llm": {"enabled": False}}}, client)
        self.assertEqual(out, {})
        self.assertEqual(client.calls, 0)

    def test_empty_candidates(self):
        client = FakeClient()
        self.assertEqual(score.score_candidates([], {"discovery": {"llm": {"enabled": True}}}, client), {})
        self.assertEqual(client.calls, 0)

    def test_scores_returned(self):
        cands = self._cands(2)
        resp = _resp([
            {"videoId": "vid00000000", "score": 90, "label": "cinematic", "reason": "良"},
            {"videoId": "vid00000001", "score": 20, "label": "casual", "reason": "vlog"},
        ])
        client = FakeClient([resp])
        out = score.score_candidates(cands, {"discovery": {"llm": {"enabled": True}}}, client)
        self.assertEqual(out["vid00000000"]["score"], 90)
        self.assertEqual(out["vid00000001"]["label"], "casual")
        self.assertEqual(client.calls, 1)

    def test_batching_multiple_calls(self):
        cands = self._cands(5)
        responses = [
            _resp([{"videoId": "vid00000000", "score": 70, "label": "uncertain", "reason": "r"},
                   {"videoId": "vid00000001", "score": 70, "label": "uncertain", "reason": "r"}]),
            _resp([{"videoId": "vid00000002", "score": 70, "label": "uncertain", "reason": "r"},
                   {"videoId": "vid00000003", "score": 70, "label": "uncertain", "reason": "r"}]),
            _resp([{"videoId": "vid00000004", "score": 70, "label": "uncertain", "reason": "r"}]),
        ]
        client = FakeClient(responses)
        cfg = {"discovery": {"llm": {"enabled": True, "batch_size": 2}}}
        out = score.score_candidates(cands, cfg, client)
        self.assertEqual(len(out), 5)
        self.assertEqual(client.calls, 3)  # 2+2+1

    def test_bad_stop_reason_skips_batch(self):
        cands = self._cands(1)
        resp = _resp([{"videoId": "vid00000000", "score": 90, "label": "cinematic", "reason": "x"}],
                     stop_reason="max_tokens")
        client = FakeClient([resp])
        out = score.score_candidates(cands, {"discovery": {"llm": {"enabled": True}}}, client)
        self.assertEqual(out, {})  # 信頼できない応答は棄却（fail-closed）

    def test_parse_error_skips_batch(self):
        cands = self._cands(1)
        bad = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "not json"}]}
        client = FakeClient([bad])
        out = score.score_candidates(cands, {"discovery": {"llm": {"enabled": True}}}, client)
        self.assertEqual(out, {})

    def test_api_error_propagates(self):
        client = FakeClient(raise_error=True)
        with self.assertRaises(score.ScoreError):
            score.score_candidates(self._cands(1), {"discovery": {"llm": {"enabled": True}}}, client)


class TestHelpers(unittest.TestCase):
    def test_make_candidate_truncates(self):
        item = {"id": "vidaaaa0001",
                "snippet": {"title": "T", "channelTitle": "C", "description": "あ" * 800,
                            "categoryId": "10", "tags": [str(i) for i in range(50)]}}
        c = score.make_candidate(item, duration_seconds=123)
        self.assertEqual(len(c["description"]), 500)
        self.assertEqual(len(c["tags"]), 20)
        self.assertEqual(c["durationSeconds"], 123)
        self.assertEqual(c["videoId"], "vidaaaa0001")

    def test_build_user_message_has_delimiters(self):
        msg = score.build_user_message([{"videoId": "vidaaaa0001"}])
        self.assertIn("<candidates>", msg)
        self.assertIn("</candidates>", msg)
        self.assertIn("vidaaaa0001", msg)

    def test_build_user_message_no_preferences_block_when_empty(self):
        self.assertNotIn("<preferences>", score.build_user_message([{"videoId": "v"}]))
        self.assertNotIn("<preferences>", score.build_user_message([{"videoId": "v"}], {}))

    def test_build_user_message_includes_preferences(self):
        prefs = {"good": [{"title": "良作", "genre": "mv", "channelTitle": "A"}]}
        msg = score.build_user_message([{"videoId": "v"}], prefs)
        self.assertIn("<preferences>", msg)
        self.assertIn("</preferences>", msg)
        self.assertIn("良作", msg)


class TestBuildPreferences(unittest.TestCase):
    def _items(self):
        return [
            {"videoId": "v1", "rating": "good", "title": "良01", "genre": "mv",
             "channelTitle": "A", "ratedAt": "2026-06-23T00:00:00Z"},
            {"videoId": "v2", "rating": "bad", "title": "悪01", "genre": "cm",
             "channelTitle": "B", "ratedAt": "2026-06-24T00:00:00Z"},
            {"videoId": "v3", "rating": "good", "title": "良02", "genre": None,
             "channelTitle": "C", "ratedAt": "2026-06-25T00:00:00Z"},
        ]

    def test_splits_good_bad(self):
        out = score.build_preferences({"items": self._items()})
        self.assertEqual(len(out["good"]), 2)
        self.assertEqual(len(out["bad"]), 1)
        self.assertEqual(out["bad"][0]["title"], "悪01")
        # 送るのは title/genre/channelTitle のみ（videoId は載せない）。
        self.assertEqual(set(out["good"][0].keys()), {"title", "genre", "channelTitle"})

    def test_accepts_plain_list(self):
        out = score.build_preferences(self._items())
        self.assertEqual(len(out["good"]), 2)

    def test_empty_or_invalid_returns_empty(self):
        self.assertEqual(score.build_preferences(None), {})
        self.assertEqual(score.build_preferences({}), {})
        self.assertEqual(score.build_preferences({"items": []}), {})

    def test_max_examples_cap_prefers_recent(self):
        items = [{"videoId": f"v{i}", "rating": "good", "title": f"t{i}",
                  "ratedAt": f"2026-06-{10 + i:02d}T00:00:00Z"} for i in range(5)]
        out = score.build_preferences({"items": items}, max_examples=2)
        self.assertEqual(len(out["good"]), 2)
        # ratedAt 降順優先 → 最新 t4, t3
        self.assertEqual([g["title"] for g in out["good"]], ["t4", "t3"])

    def test_title_truncated(self):
        items = [{"videoId": "v", "rating": "good", "title": "あ" * 200}]
        out = score.build_preferences({"items": items}, max_title_chars=80)
        self.assertEqual(len(out["good"][0]["title"]), 80)

    def test_score_candidates_passes_preferences(self):
        cands = [{"videoId": "vid00000000", "title": "t", "channelTitle": "c",
                  "description": "d", "categoryId": "1", "durationSeconds": 100, "tags": []}]
        resp = _resp([{"videoId": "vid00000000", "score": 88, "label": "cinematic", "reason": "良"}])
        client = FakeClient([resp])
        captured = {}
        orig = client.create_message

        def spy(system, user, schema, max_tokens):
            captured["user"] = user
            return orig(system, user, schema, max_tokens)

        client.create_message = spy
        feedback = {"items": [{"videoId": "x", "rating": "good", "title": "好み作品",
                               "genre": "mv", "channelTitle": "A", "ratedAt": "2026-06-23T00:00:00Z"}]}
        out = score.score_candidates(cands, {"discovery": {"llm": {"enabled": True}}},
                                     client, feedback=feedback)
        self.assertEqual(out["vid00000000"]["score"], 88)
        self.assertIn("<preferences>", captured["user"])
        self.assertIn("好み作品", captured["user"])


if __name__ == "__main__":
    unittest.main()
