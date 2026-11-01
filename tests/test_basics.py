#!/usr/bin/env python

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from spotify2ytmusic import backend
from spotify2ytmusic.reverse_playlist import reverse_playlist

TEST_FILE = "tests/playliststest.json"
TEST_PL_ID = "68QlHDwCiXfhodLpS72iOx"


def make_yt(existing_tracks=None):
    yt = MagicMock()
    yt.get_playlist.return_value = {
        "title": "Test Playlist",
        "tracks": existing_tracks or [],
    }
    yt.add_playlist_items.return_value = None
    #  lookup_song: no album hits, song search returns a track derived from the query
    yt.search.side_effect = lambda query, filter: (
        []
        if filter == "albums"
        else [
            {
                "title": query.split(" by ")[0],
                "videoId": "vid-" + query,
                "artists": [{"name": query.split(" by ")[1]}],
                "album": {"name": "x"},
            }
        ]
    )
    return yt


def added_video_ids(yt):
    return [c.kwargs["videoIds"][0] for c in yt.add_playlist_items.call_args_list]


class TestCopier(unittest.TestCase):
    def test_copier_success(self):
        yt = make_yt()
        backend.copier(
            backend.iter_spotify_playlist(
                TEST_PL_ID, spotify_playlist_file=TEST_FILE, reverse_playlist=False
            ),
            dst_pl_id="dst_test",
            track_sleep=0,
            yt=yt,
        )
        self.assertTrue(yt.add_playlist_items.called)

    def test_copier_albums(self):
        yt = make_yt()
        backend.copier(
            backend.iter_spotify_liked_albums(spotify_playlist_file=TEST_FILE),
            dst_pl_id="dst_test",
            track_sleep=0,
            yt=yt,
        )
        yt.get_playlist.assert_any_call(playlistId="dst_test")

    def test_playlist_order_preserved(self):
        """Tracks must be added in Spotify order (YTMusic appends to the end)."""
        src = list(
            backend.iter_spotify_playlist(
                TEST_PL_ID, spotify_playlist_file=TEST_FILE, reverse_playlist=False
            )
        )
        yt = make_yt()
        with patch.object(backend, "get_ytmusic", return_value=yt), patch.object(
            backend, "load_playlists_json", return_value=json.load(open(TEST_FILE))
        ), patch.object(backend, "iter_spotify_playlist") as it:
            it.side_effect = lambda *a, **kw: iter(
                src[::-1] if kw.get("reverse_playlist") else src
            )
            yt.get_library_playlists.return_value = []
            yt.create_playlist.return_value = "NEWID"
            with patch.object(backend.time, "sleep"):
                backend.copy_all_playlists(track_sleep=0)
        expected = [f"vid-{t.title} by {t.artist}" for t in src]
        self.assertEqual(added_video_ids(yt), expected)

    def test_skip_tracks_already_present(self):
        src = list(
            backend.iter_spotify_playlist(
                TEST_PL_ID, spotify_playlist_file=TEST_FILE, reverse_playlist=False
            )
        )
        existing = [
            {
                "title": t.title + " (Remastered)",
                "videoId": f"old{i}",
                "artists": [{"name": t.artist}],
            }
            for i, t in enumerate(src[:10])
        ]
        yt = make_yt(existing)
        backend.copier(iter(src), "dst", track_sleep=0, yt=yt)
        added = added_video_ids(yt)
        for t in src[:10]:
            self.assertNotIn(f"vid-{t.title} by {t.artist}", added)
        self.assertIn(f"vid-{src[10].title} by {src[10].artist}", added)

    def test_playlist_is_complete(self):
        src = [backend.SongInfo("Song A", "Artist", "Alb")]
        yt = make_yt(
            [{"title": "song a", "videoId": "x", "artists": [{"name": "ARTIST"}]}]
        )
        self.assertTrue(backend.playlist_is_complete(yt, iter(src), "dst"))
        src.append(backend.SongInfo("Song B", "Artist", "Alb"))
        self.assertFalse(backend.playlist_is_complete(yt, iter(src), "dst"))


class TestReorder(unittest.TestCase):
    def _yt_items(self, titles):
        return [
            {"title": t, "artists": [{"name": "A"}], "setVideoId": f"s{i}"}
            for i, t in enumerate(titles)
        ]

    def test_reverses_back(self):
        src = [backend.SongInfo(t, "A", "x") for t in "abcdef"]
        items = self._yt_items(list("fedcba"))
        yt = MagicMock()
        yt.get_playlist.return_value = {"tracks": items}
        moves = backend.reorder_playlist(yt, src, "pl", move_sleep=0)
        self.assertEqual(moves, 5)
        self.assertEqual(yt.edit_playlist.call_count, 5)
        #  replay the moves and check the final order
        order = [i["setVideoId"] for i in items]
        for c in yt.edit_playlist.call_args_list:
            item, succ = c.kwargs["moveItem"]
            order.remove(item)
            order.insert(order.index(succ), item)
        titles = {i["setVideoId"]: i["title"] for i in items}
        self.assertEqual([titles[s] for s in order], list("abcdef"))

    def test_in_order_and_extras(self):
        src = [backend.SongInfo(t, "A", "x") for t in "abc"]
        yt = MagicMock()
        yt.get_playlist.return_value = {
            "tracks": self._yt_items(["a", "b", "c", "mine"])
        }
        self.assertEqual(backend.reorder_playlist(yt, src, "pl"), 0)
        planned = backend.plan_playlist_order(
            src, self._yt_items(["c", "mine", "a", "b"])
        )
        self.assertEqual([p["title"] for p in planned], ["a", "b", "c", "mine"])
        #  an unmatched title in an in-order playlist must not trigger moves
        yt.get_playlist.return_value = {
            "tracks": self._yt_items(["a", "B (Live)x", "c"])
        }
        self.assertEqual(backend.reorder_playlist(yt, src, "pl"), 0)


class TestTitles(unittest.TestCase):
    def test_sanitize(self):
        s = backend.sanitize_playlist_title
        self.assertEqual(s("Rock <3"), "Rock ‹3")
        self.assertNotIn(">", s("a -> b"))
        self.assertEqual(s("  multi\n line\tname  "), "multi line name")
        self.assertEqual(s("zero​width"), "zerowidth")
        self.assertEqual(len(s("x" * 300)), backend.YT_MAX_TITLE_LEN)
        self.assertEqual(s("   ", fallback="fb"), "fb")
        self.assertEqual(s("Café 🎶 日本語"), "Café 🎶 日本語")

    def test_lookup_by_name_normalized(self):
        yt = MagicMock()
        yt.get_library_playlists.return_value = [
            {"title": "Rock ‹3", "playlistId": "A"},
            {"title": "Café", "playlistId": "B"},
        ]
        self.assertEqual(backend.get_playlist_id_by_name(yt, "Rock <3"), "A")
        self.assertEqual(backend.get_playlist_id_by_name(yt, "Café"), "B")
        self.assertIsNone(backend.get_playlist_id_by_name(yt, "Nope"))

    def test_lookup_prefers_exact_and_excludes(self):
        yt = MagicMock()
        yt.get_library_playlists.return_value = [
            {"title": "best soul", "playlistId": "lower"},
            {"title": "Best soul", "playlistId": "upper"},
        ]
        f = backend.get_playlist_id_by_name
        self.assertEqual(f(yt, "Best soul"), "upper")
        self.assertEqual(f(yt, "best soul"), "lower")
        self.assertEqual(f(yt, "BEST SOUL", exclude={"lower"}), "upper")
        self.assertIsNone(f(yt, "best soul", exclude={"lower", "upper"}))


class TestReverseFile(unittest.TestCase):
    def test_backup_name_with_dots(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "my.play.lists.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"playlists": [{"name": "Ünï", "tracks": [1, 2, 3]}]}, f)
            self.assertEqual(reverse_playlist(p, verbose=False), 0)
            self.assertTrue(
                os.path.exists(os.path.join(d, "my.play.lists_backup.json"))
            )
            with open(p, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["playlists"][0]["tracks"], [3, 2, 1])


if __name__ == "__main__":
    unittest.main()
