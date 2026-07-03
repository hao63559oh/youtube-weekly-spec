"""resolve.py のユニットテスト（純ロジック中心。ネット不使用）。"""
import sys
import unittest
from pathlib import Path

# scripts/ を import path に追加。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import resolve  # noqa: E402


class TestParseChannelInput(unittest.TestCase):
    def test_handle_forms(self):
        self.assertEqual(resolve.parse_channel_input("@MyChannel"),
                         {"param": "forHandle", "value": "MyChannel"})
        # @ 無しもハンドル扱い。
        self.assertEqual(resolve.parse_channel_input("MyChannel"),
                         {"param": "forHandle", "value": "MyChannel"})
        self.assertEqual(resolve.parse_channel_input("https://www.youtube.com/@SomeHandle"),
                         {"param": "forHandle", "value": "SomeHandle"})

    def test_channel_url_and_raw_id(self):
        cid = "UC1234567890123456789012"
        self.assertEqual(resolve.parse_channel_input(f"https://www.youtube.com/channel/{cid}"),
                         {"param": "id", "value": cid})
        self.assertEqual(resolve.parse_channel_input(cid),
                         {"param": "id", "value": cid})

    def test_legacy_user_url(self):
        self.assertEqual(resolve.parse_channel_input("https://www.youtube.com/user/legacyname"),
                         {"param": "forUsername", "value": "legacyname"})

    def test_invalid_inputs_raise(self):
        for bad in ["", "   ", "https://www.youtube.com/c/custom",
                    "https://www.youtube.com/channel/notavalidid"]:
            with self.assertRaises(ValueError):
                resolve.parse_channel_input(bad)


class TestExtractEntry(unittest.TestCase):
    def _resp(self):
        return {"items": [{
            "id": "UCabcabcabcabcabcabcabca",
            "snippet": {"title": "Test Studio"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UUabcabcabcabcabcabcabca"}},
        }]}

    def test_extract_ok(self):
        self.assertEqual(resolve.extract_entry(self._resp()), {
            "channelId": "UCabcabcabcabcabcabcabca",
            "uploadsPlaylistId": "UUabcabcabcabcabcabcabca",
            "name": "Test Studio",
        })

    def test_empty_items_raises(self):
        with self.assertRaises(ValueError):
            resolve.extract_entry({"items": []})

    def test_missing_uploads_raises(self):
        with self.assertRaises(ValueError):
            resolve.extract_entry({"items": [{"id": "UCx", "contentDetails": {}}]})


class TestResolveChannel(unittest.TestCase):
    def test_with_injected_fetcher(self):
        captured = {}

        def fake_fetcher(params):
            captured.update(params)
            return {"items": [{
                "id": "UCabcabcabcabcabcabcabca",
                "snippet": {"title": "Test"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UUabcabcabcabcabcabcabca"}},
            }]}

        entry = resolve.resolve_channel("@Handle", fetcher=fake_fetcher)
        self.assertEqual(captured["forHandle"], "Handle")
        self.assertEqual(entry["channelId"], "UCabcabcabcabcabcabcabca")


class TestUpsertChannel(unittest.TestCase):
    def setUp(self):
        self.entry = {"channelId": "UC1", "uploadsPlaylistId": "UU1", "name": "n1"}

    def test_add_new(self):
        al = {"channels": []}
        resolve.upsert_channel(al, self.entry, genre="mv", note="x")
        self.assertEqual(len(al["channels"]), 1)
        self.assertEqual(al["channels"][0]["genre"], "mv")
        self.assertEqual(al["channels"][0]["note"], "x")

    def test_update_preserves_genre_when_omitted(self):
        al = {"channels": []}
        resolve.upsert_channel(al, self.entry, genre="mv")
        updated = dict(self.entry, uploadsPlaylistId="UU_new", name="n2")
        resolve.upsert_channel(al, updated)  # genre 無指定
        self.assertEqual(len(al["channels"]), 1)
        self.assertEqual(al["channels"][0]["genre"], "mv")  # 既存維持
        self.assertEqual(al["channels"][0]["uploadsPlaylistId"], "UU_new")
        self.assertEqual(al["channels"][0]["name"], "n2")


class TestMainIncrementalSave(unittest.TestCase):
    def test_success_saved_even_if_later_input_fails(self):
        import json
        import tempfile

        # _default_fetcher を差し替え: good は解決成功、bad は items 空で失敗。
        original = resolve._default_fetcher

        def fake(params):
            if params.get("forHandle") == "good":
                return {"items": [{
                    "id": "UCgoodgoodgoodgoodgoodgo",
                    "snippet": {"title": "Good"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UUgoodgoodgoodgoodgoodgo"}},
                }]}
            return {"items": []}

        resolve._default_fetcher = fake
        try:
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "allowlist.json"
                path.write_text('{"channels": []}', encoding="utf-8")
                rc = resolve.main(["@good", "@bad", "--genre", "mv", "--allowlist", str(path)])
                self.assertEqual(rc, 1)  # 一部失敗で非ゼロ
                saved = json.loads(path.read_text(encoding="utf-8"))
                ids = [c["channelId"] for c in saved["channels"]]
                self.assertIn("UCgoodgoodgoodgoodgoodgo", ids)  # 成功分は随時保存済み
        finally:
            resolve._default_fetcher = original


if __name__ == "__main__":
    unittest.main()
