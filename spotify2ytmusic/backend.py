#!/usr/bin/env python3

import json
import sys
import os
import time
import re
import unicodedata

from ytmusicapi import YTMusic
from typing import Optional, Union, Iterator, Dict, List
from collections import namedtuple
from dataclasses import dataclass, field

SongInfo = namedtuple("SongInfo", ["title", "artist", "album"])

#  YouTube Music limits playlist titles to 150 characters.
YT_MAX_TITLE_LEN = 150
#  ytmusicapi refuses titles containing these; map them to look-alikes instead.
_TITLE_CHAR_REPLACEMENTS = {"<": "\u2039", ">": "\u203a"}


def sanitize_playlist_title(title: Optional[str], fallback: str = "") -> str:
    """Make a Spotify playlist name safe to use as a YouTube Music playlist title.

    - Replaces '<' and '>' (rejected by ytmusicapi/YT Music) with look-alikes.
    - Removes control characters (newlines, tabs, zero-width chars...).
    - Collapses runs of whitespace and trims.
    - Truncates to YouTube's 150 character limit.
    """
    title = unicodedata.normalize("NFC", str(title or ""))
    for bad, good in _TITLE_CHAR_REPLACEMENTS.items():
        title = title.replace(bad, good)
    title = "".join(
        " " if unicodedata.category(ch) in ("Cc", "Zl", "Zp") else ch
        for ch in title
        if unicodedata.category(ch) != "Cf"
    )
    title = re.sub(r"\s+", " ", title).strip()
    title = title[:YT_MAX_TITLE_LEN].rstrip()
    return title or fallback


def _normalize_title_key(title: Optional[str]) -> str:
    """Key for comparing playlist titles: sanitized, NFKC-normalized, case-folded."""
    return unicodedata.normalize("NFKC", sanitize_playlist_title(title)).casefold()


def _normalize_song_key(text: Optional[str]) -> str:
    """Loose key for comparing song titles / artist names between services.

    Drops bracketed parts ("(Remastered 2011)", "[feat. X]") and " - ..." suffixes,
    then keeps only letters/digits, case-folded.
    """
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    text = re.sub(r"[\[(].*?[\])]", "", text)
    text = re.split(r"\s[-\u2013\u2014]\s", text)[0]
    return "".join(ch for ch in text if ch.isalnum())


DEFAULT_AUTH_FILE = "oauth.json"


def get_ytmusic(auth_file: Optional[str] = None) -> YTMusic:
    """Return a logged-in YTMusic client.

    The credentials file is `auth_file`, else $S2YT_AUTH_FILE, else "oauth.json".
    """
    auth_file = auth_file or os.environ.get("S2YT_AUTH_FILE") or DEFAULT_AUTH_FILE
    if not os.path.exists(auth_file):
        print(f"ERROR: No file '{auth_file}' exists in the current directory.")
        print(
            "       Have you logged in to YTMusic?  Run 's2yt_login --browser chrome'"
        )
        print("       (or see 'Generate YouTube Music Credentials' in the README).")
        sys.exit(1)

    try:
        return YTMusic(auth_file)
    except json.decoder.JSONDecodeError as e:
        print(f"ERROR: JSON Decode error while trying start YTMusic: {e}")
        print(f"       This typically means a problem with the '{auth_file}' file.")
        print("       Log in again with 's2yt_login --browser chrome'.")
        sys.exit(1)


def parse_spotify_playlist_id(value: str) -> str:
    """Accept a Spotify playlist ID, URL (open.spotify.com/playlist/<id>?si=...) or URI."""
    value = value.strip()
    m = re.search(r"playlist[/:]([A-Za-z0-9]+)", value)
    return m.group(1) if m else value


def parse_ytmusic_playlist_id(value: str) -> str:
    """Accept a YTMusic playlist ID or URL (music.youtube.com/playlist?list=<id>)."""
    value = value.strip()
    m = re.search(r"[?&]list=([A-Za-z0-9_-]+)", value)
    if m:
        value = m.group(1)
    #  The VL prefix is the "browse" form of a playlist ID.
    return value[2:] if value.startswith("VLPL") else value


def songs_from_spotify_tracks(tracks: List[Dict]) -> List[SongInfo]:
    """Convert Spotify playlist items (as saved by spotify_backup) to SongInfo."""
    songs = []
    for item in tracks:
        track = item.get("track") if isinstance(item, dict) else None
        if not track:
            continue
        try:
            songs.append(
                SongInfo(
                    track["name"], track["artists"][0]["name"], track["album"]["name"]
                )
            )
        except (TypeError, KeyError, IndexError):
            print(f"WARNING: Skipping malformed Spotify track: {item!r:.200}")
    return songs


def _ytmusic_create_playlist(
    yt: YTMusic, title: str, description: str, privacy_status: str = "PRIVATE"
) -> str:
    """Wrapper on ytmusic.create_playlist

    This wrapper does retries with back-off because sometimes YouTube Music will
    rate limit requests or otherwise fail.

    privacy_status can be: PRIVATE, PUBLIC, or UNLISTED
    """
    title = sanitize_playlist_title(title, fallback="Untitled Spotify Playlist")
    description = sanitize_playlist_title(description, fallback=title)

    def _create(
        yt: YTMusic, title: str, description: str, privacy_status: str
    ) -> Union[str, dict]:
        exception_sleep = 5
        for _ in range(10):
            try:
                """Create a playlist on YTMusic, retrying if it fails."""
                id = yt.create_playlist(
                    title=title, description=description, privacy_status=privacy_status
                )
                return id
            except Exception as e:
                print(
                    f"ERROR: (Retrying create_playlist: {title}) {e} in {exception_sleep} seconds"
                )
                time.sleep(exception_sleep)
                exception_sleep *= 2

        return {
            "s2yt error": f'ERROR: Could not create playlist "{title}" after multiple retries'
        }

    id = _create(yt, title, description, privacy_status)
    #  create_playlist returns a dict if there was an error
    if isinstance(id, dict):
        print(f"ERROR: Failed to create playlist (name: {title}): {id}")
        sys.exit(1)

    time.sleep(1)  # seems to be needed to avoid missing playlist ID error

    return id


def load_playlists_json(filename: str = "playlists.json", encoding: str = "utf-8"):
    """Load the `playlists.json` Spotify playlist file"""
    with open(filename, "r", encoding=encoding) as f:
        return json.load(f)


def create_playlist(pl_name: str, privacy_status: str = "PRIVATE") -> None:
    """Create a YTMusic playlist


    Args:
        `pl_name` (str): The name of the playlist to create. It should be different to "".

        `privacy_status` (str: PRIVATE, PUBLIC, UNLISTED) The privacy setting of created playlist.
    """
    yt = get_ytmusic()

    id = _ytmusic_create_playlist(
        yt, title=pl_name, description=pl_name, privacy_status=privacy_status
    )
    print(f"Playlist ID: {id}")


def iter_spotify_liked_albums(
    spotify_playlist_file: str = "playlists.json",
    spotify_encoding: str = "utf-8",
) -> Iterator[SongInfo]:
    """Songs from liked albums on Spotify."""
    spotify_pls = load_playlists_json(spotify_playlist_file, spotify_encoding)

    if "albums" not in spotify_pls:
        return None

    for album in [x["album"] for x in spotify_pls["albums"]]:
        for track in album["tracks"]["items"]:
            yield SongInfo(track["name"], track["artists"][0]["name"], album["name"])


def iter_spotify_playlist(
    src_pl_id: Optional[str] = None,
    spotify_playlist_file: str = "playlists.json",
    spotify_encoding: str = "utf-8",
    reverse_playlist: bool = True,
) -> Iterator[SongInfo]:
    """Songs from a specific album ("Liked Songs" if None)

    Args:
        `src_pl_id` (Optional[str], optional): The ID of the source playlist. Defaults to None.
        `spotify_playlist_file` (str, optional): The path to the playlists backup files. Defaults to "playlists.json".
        `spotify_encoding` (str, optional): Characters encoding. Defaults to "utf-8".
        `reverse_playlist` (bool, optional): Is the playlist reversed when loading?  Defaults to True.

    Yields:
        Iterator[SongInfo]: The song's information
    """
    spotify_pls = load_playlists_json(spotify_playlist_file, spotify_encoding)

    def find_spotify_playlist(spotify_pls: Dict, src_pl_id: Union[str, None]) -> Dict:
        """Return the spotify playlist that matches the `src_pl_id`.

        Args:
            `spotify_pls`: The playlist datastrcuture saved by spotify-backup.
            `src_pl_id`: The ID of a playlist to find, or None for the "Liked Songs" playlist.
        """
        for src_pl in spotify_pls["playlists"]:
            if src_pl_id is None and str(src_pl.get("name")) == "Liked Songs":
                return src_pl
            if src_pl_id is not None and str(src_pl.get("id")) == src_pl_id:
                return src_pl
        raise ValueError(f"Could not find Spotify playlist {src_pl_id}")

    src_pl = find_spotify_playlist(spotify_pls, src_pl_id)
    src_pl_name = src_pl["name"]

    print(f"== Spotify Playlist: {src_pl_name}")

    pl_tracks = src_pl["tracks"]
    if reverse_playlist:
        pl_tracks = reversed(pl_tracks)

    for src_track in pl_tracks:
        if src_track["track"] is None:
            print(
                f"WARNING: Spotify track seems to be malformed, Skipping.  Track: {src_track!r}"
            )
            continue

        try:
            src_album_name = src_track["track"]["album"]["name"]
            src_track_artist = src_track["track"]["artists"][0]["name"]
        except TypeError as e:
            print(f"ERROR: Spotify track seems to be malformed.  Track: {src_track!r}")
            raise e
        src_track_name = src_track["track"]["name"]

        yield SongInfo(src_track_name, src_track_artist, src_album_name)


def get_playlist_id_by_name(
    yt: YTMusic, title: str, exclude: Optional[set] = None
) -> Optional[str]:
    """Look up a YTMusic playlist ID by name.

    Args:
        `yt` (YTMusic): _description_
        `title` (str): _description_

    Returns:
        Optional[str]: The playlist ID or None if not found.
    """
    #  ytmusicapi seems to run into some situations where it gives a Traceback on listing playlists
    #  https://github.com/sigma67/ytmusicapi/issues/539
    try:
        playlists = yt.get_library_playlists(limit=5000)
    except KeyError as e:
        print("=" * 60)
        print(f"Attempting to look up playlist '{title}' failed with KeyError: {e}")
        print(
            "This is a bug in ytmusicapi that prevents 'copy_all_playlists' from working."
        )
        print(
            "You will need to manually copy playlists using s2yt_list_playlists and s2yt_copy_playlist"
        )
        print(
            "until this bug gets resolved.  Try `pip install --upgrade ytmusicapi` just to verify"
        )
        print("you have the latest version of that library.")
        print("=" * 60)
        raise

    exclude = exclude or set()
    playlists = [pl for pl in playlists if pl.get("playlistId") not in exclude]
    candidates = [title, sanitize_playlist_title(title)]
    for pl in playlists:
        if pl.get("title") in candidates:
            return pl["playlistId"]
    #  Fall back to a normalized comparison, since YT Music may store the title
    #  slightly differently (whitespace, unicode normalization, case).
    wanted = _normalize_title_key(title)
    for pl in playlists:
        if _normalize_title_key(pl.get("title")) == wanted:
            return pl["playlistId"]

    return None


@dataclass
class ResearchDetails:
    query: Optional[str] = field(default=None)
    songs: Optional[List[Dict]] = field(default=None)
    suggestions: Optional[List[str]] = field(default=None)


def lookup_song(
    yt: YTMusic,
    track_name: str,
    artist_name: str,
    album_name,
    yt_search_algo: int,
    details: Optional[ResearchDetails] = None,
) -> dict:
    """Look up a song on YTMusic

    Given the Spotify track information, it does a lookup for the album by the same
    artist on YTMusic, then looks at the first 3 hits looking for a track with exactly
    the same name. In the event that it can't find that exact track, it then does
    a search of songs for the track name by the same artist and simply returns the
    first hit.

    The idea is that finding the album and artist and then looking for the exact track
    match will be more likely to be accurate than searching for the song and artist and
    relying on the YTMusic yt_search_algorithm to figure things out, especially for short tracks
    that might have many contradictory hits like "Survival by Yes".

    Args:
        `yt` (YTMusic)
        `track_name` (str): The name of the researched track
        `artist_name` (str): The name of the researched track's artist
        `album_name` (str): The name of the researched track's album
        `yt_search_algo` (int): 0 for exact matching, 1 for extended matching (search past 1st result), 2 for approximate matching (search in videos)
        `details` (ResearchDetails): If specified, more information about the search and the response will be populated for use by the caller.

    Raises:
        ValueError: If no track is found, it returns an error

    Returns:
        dict: The infos of the researched song
    """
    albums = yt.search(query=f"{album_name} by {artist_name}", filter="albums")
    for album in albums[:3]:
        # print(album)
        # print(f"ALBUM: {album['browseId']} - {album['title']} - {album['artists'][0]['name']}")

        try:
            for track in yt.get_album(album["browseId"])["tracks"]:
                if track["title"] == track_name:
                    return track
            # print(f"{track['videoId']} - {track['title']} - {track['artists'][0]['name']}")
        except Exception as e:
            print(f"Unable to lookup album ({e}), continuing...")

    query = f"{track_name} by {artist_name}"
    if details:
        details.query = query
        details.suggestions = yt.get_search_suggestions(query=query)
    songs = yt.search(query=query, filter="songs")

    match yt_search_algo:
        case 0:
            if details:
                details.songs = songs
            return songs[0]

        case 1:
            for song in songs:
                if (
                    song["title"] == track_name
                    and song["artists"][0]["name"] == artist_name
                    and song["album"]["name"] == album_name
                ):
                    return song
                # print(f"SONG: {song['videoId']} - {song['title']} - {song['artists'][0]['name']} - {song['album']['name']}")

            raise ValueError(
                f"Did not find {track_name} by {artist_name} from {album_name}"
            )

        case 2:
            #  This would need to do fuzzy matching
            for song in songs:
                # Remove everything in brackets in the song title
                song_title_without_brackets = re.sub(r"[\[(].*?[])]", "", song["title"])
                if (
                    (
                        song_title_without_brackets == track_name
                        and song["album"]["name"] == album_name
                    )
                    or (song_title_without_brackets == track_name)
                    or (song_title_without_brackets in track_name)
                    or (track_name in song_title_without_brackets)
                ) and (
                    song["artists"][0]["name"] == artist_name
                    or artist_name in song["artists"][0]["name"]
                ):
                    return song

            # Finds approximate match
            # This tries to find a song anyway. Works when the song is not released as a music but a video.
            else:
                track_name = track_name.lower()
                first_song_title = songs[0]["title"].lower()
                if (
                    track_name not in first_song_title
                    or songs[0]["artists"][0]["name"] != artist_name
                ):  # If the first song is not the one we are looking for
                    print("Not found in songs, searching videos")
                    new_songs = yt.search(
                        query=f"{track_name} by {artist_name}", filter="videos"
                    )  # Search videos

                    # From here, we search for videos reposting the song. They often contain the name of it and the artist. Like with 'Nekfeu - Ecrire'.
                    for new_song in new_songs:
                        new_song_title = new_song[
                            "title"
                        ].lower()  # People sometimes mess up the capitalization in the title
                        if (
                            track_name in new_song_title
                            and artist_name in new_song_title
                        ) or (track_name in new_song_title):
                            print("Found a video")
                            return new_song
                    else:
                        # Basically we only get here if the song isn't present anywhere on YouTube
                        raise ValueError(
                            f"Did not find {track_name} by {artist_name} from {album_name}"
                        )
                else:
                    return songs[0]


def _get_playlist_with_retry(
    yt: YTMusic, playlist_id: str, limit: Optional[int] = None, tries: int = 6
) -> dict:
    """yt.get_playlist with back-off: a just-created playlist is sometimes not
    readable for a few seconds, which used to abort the whole copy."""
    delay = 2
    for attempt in range(tries):
        try:
            if limit == 100:  # ytmusicapi's default; keep the call signature simple
                return yt.get_playlist(playlistId=playlist_id)
            return yt.get_playlist(playlistId=playlist_id, limit=limit)
        except Exception as e:
            if attempt == tries - 1:
                raise
            print(f"(Retrying get_playlist {playlist_id} in {delay}s: {str(e)[:80]})")
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def get_playlist_contents(yt: YTMusic, playlist_id: str):
    """Return (videoIds, {(song_key, artist_key)}) for every track in a YTMusic playlist."""
    try:
        tracks = _get_playlist_with_retry(yt, playlist_id, tries=3).get("tracks") or []
    except Exception as e:
        print(
            f"WARNING: Unable to read contents of YTMusic playlist {playlist_id}: {e}"
        )
        return set(), set()

    video_ids = set()
    song_keys = set()
    for track in tracks:
        if track.get("videoId"):
            video_ids.add(track["videoId"])
        title_key = _normalize_song_key(track.get("title"))
        for artist in track.get("artists") or []:
            song_keys.add((title_key, _normalize_song_key(artist.get("name"))))
    return video_ids, song_keys


def playlist_is_complete(
    yt: YTMusic, src_tracks: Iterator[SongInfo], dst_pl_id: str
) -> bool:
    """True if every Spotify track already appears in the YTMusic playlist."""
    video_ids, song_keys = get_playlist_contents(yt, dst_pl_id)
    src_list = list(src_tracks)
    if not video_ids or not src_list:
        return False
    missing = [
        t
        for t in src_list
        if (_normalize_song_key(t.title), _normalize_song_key(t.artist))
        not in song_keys
    ]
    return not missing


def copier(
    src_tracks: Iterator[SongInfo],
    dst_pl_id: Optional[str] = None,
    dry_run: bool = False,
    track_sleep: float = 0.1,
    yt_search_algo: int = 0,
    *,
    yt: Optional[YTMusic] = None,
    skip_existing: bool = True,
):
    """Copy `src_tracks` into YTMusic playlist `dst_pl_id` (or "like" them if None).

    If `skip_existing` is set and the destination playlist already has tracks, any
    Spotify track already present in it (matched by title+artist, or by the videoId
    found on lookup) is skipped, so re-running a copy resumes instead of duplicating.
    """
    if yt is None:
        yt = get_ytmusic()

    existing_video_ids: set = set()
    existing_song_keys: set = set()
    if dst_pl_id is not None:
        try:
            yt_pl = _get_playlist_with_retry(yt, dst_pl_id, limit=100)
        except Exception as e:
            print(f"ERROR: Unable to find YTMusic playlist {dst_pl_id}: {e}")
            print(
                "       Make sure the YTMusic playlist ID is correct, it should be something like "
            )
            print("      'PL_DhcdsaJ7echjfdsaJFhdsWUd73HJFca'")
            sys.exit(1)
        print(f"== Youtube Playlist: {yt_pl['title']}")
        if skip_existing:
            existing_video_ids, existing_song_keys = get_playlist_contents(
                yt, dst_pl_id
            )
            if existing_video_ids:
                print(
                    f"   (playlist already has {len(existing_video_ids)} tracks; "
                    "tracks already present will be skipped)"
                )

    tracks_added_set = set()
    duplicate_count = 0
    error_count = 0
    already_present_count = 0

    for src_track in src_tracks:
        print(f"Spotify:   {src_track.title} - {src_track.artist} - {src_track.album}")

        if (
            _normalize_song_key(src_track.title),
            _normalize_song_key(src_track.artist),
        ) in existing_song_keys:
            print("  (already in YTMusic playlist, skipping)")
            already_present_count += 1
            continue

        try:
            dst_track = lookup_song(
                yt, src_track.title, src_track.artist, src_track.album, yt_search_algo
            )
        except Exception as e:
            print(f"ERROR: Unable to look up song on YTMusic: {e}")
            error_count += 1
            continue

        yt_artist_name = "<Unknown>"
        if "artists" in dst_track and len(dst_track["artists"]) > 0:
            yt_artist_name = dst_track["artists"][0]["name"]
        print(
            f"  Youtube: {dst_track['title']} - {yt_artist_name} - {dst_track['album'] if 'album' in dst_track else '<Unknown>'}"
        )

        if dst_track["videoId"] in existing_video_ids:
            print("  (already in YTMusic playlist, skipping)")
            already_present_count += 1
            continue

        if dst_track["videoId"] in tracks_added_set:
            print("(DUPLICATE, this track has already been added)")
            duplicate_count += 1
        tracks_added_set.add(dst_track["videoId"])

        if not dry_run:
            exception_sleep = 5
            for _ in range(10):
                try:
                    if dst_pl_id is not None:
                        yt.add_playlist_items(
                            playlistId=dst_pl_id,
                            videoIds=[dst_track["videoId"]],
                            duplicates=False,
                        )
                    else:
                        yt.rate_song(dst_track["videoId"], "LIKE")
                    break
                except Exception as e:
                    print(
                        f"ERROR: (Retrying add_playlist_items: {dst_pl_id} {dst_track['videoId']}) {e} in {exception_sleep} seconds"
                    )
                    time.sleep(exception_sleep)
                    exception_sleep *= 2

        if track_sleep:
            time.sleep(track_sleep)

    print()
    print(
        f"Added {len(tracks_added_set)} tracks, skipped {already_present_count} already present, "
        f"encountered {duplicate_count} duplicates, {error_count} errors"
    )


def _copy_one_playlist(
    yt: YTMusic,
    spotify_playlist_id: str,
    dst_pl_id: str,
    spotify_playlists_encoding: str,
    dry_run: bool,
    track_sleep: float,
    yt_search_algo: int,
    reverse_playlist: bool,
) -> None:
    """Copy one Spotify playlist into an existing YTMusic playlist, skipping if already done."""

    def src_tracks():
        return iter_spotify_playlist(
            spotify_playlist_id,
            spotify_encoding=spotify_playlists_encoding,
            reverse_playlist=reverse_playlist,
        )

    if playlist_is_complete(yt, src_tracks(), dst_pl_id):
        print("== YTMusic playlist already contains every track, skipping.")
        return

    copier(src_tracks(), dst_pl_id, dry_run, track_sleep, yt_search_algo, yt=yt)


def copy_playlist(
    spotify_playlist_id: str,
    ytmusic_playlist_id: str,
    spotify_playlists_encoding: str = "utf-8",
    dry_run: bool = False,
    track_sleep: float = 0.1,
    yt_search_algo: int = 0,
    reverse_playlist: bool = False,
    privacy_status: str = "PRIVATE",
):
    """
    Copy a Spotify playlist to a YTMusic playlist.

    `ytmusic_playlist_id` may be an ID, "+Name" (look up by name, create if missing),
    or "" (use/create a playlist named after the Spotify playlist).

    Tracks are added in Spotify order: YTMusic appends new items to the end of a
    playlist, so the Spotify list must NOT be reversed (hence `reverse_playlist=False`).
    """
    print("Using search algo n°: ", yt_search_algo)
    yt = get_ytmusic()
    pl_name: str = ""

    if ytmusic_playlist_id.startswith("+"):
        pl_name = ytmusic_playlist_id[1:]
    elif ytmusic_playlist_id == "":
        spotify_pls: dict = load_playlists_json(encoding=spotify_playlists_encoding)
        for pl in spotify_pls["playlists"]:
            if pl.get("id") == spotify_playlist_id:
                pl_name = pl.get("name") or ""
        pl_name = pl_name or f"Unnamed Spotify Playlist {spotify_playlist_id}"

    if pl_name:
        pl_name = sanitize_playlist_title(pl_name, fallback=pl_name)
        ytmusic_playlist_id = get_playlist_id_by_name(yt, pl_name)
        print(f"Looking up playlist '{pl_name}': id={ytmusic_playlist_id}")

    if ytmusic_playlist_id is None:
        ytmusic_playlist_id = _ytmusic_create_playlist(
            yt,
            title=pl_name,
            description=pl_name,
            privacy_status=privacy_status,
        )
        print(f"NOTE: Created playlist '{pl_name}' with ID: {ytmusic_playlist_id}")

    _copy_one_playlist(
        yt,
        spotify_playlist_id,
        ytmusic_playlist_id,
        spotify_playlists_encoding,
        dry_run,
        track_sleep,
        yt_search_algo,
        reverse_playlist,
    )


def copy_all_playlists(
    track_sleep: float = 0.1,
    dry_run: bool = False,
    spotify_playlists_encoding: str = "utf-8",
    yt_search_algo: int = 0,
    reverse_playlist: bool = False,
    privacy_status: str = "PRIVATE",
):
    """
    Copy all Spotify playlists (except Liked Songs) to YTMusic playlists.

    Playlists that already exist on YTMusic (matched by name) are compared track by
    track: complete ones are skipped, partial ones only get their missing tracks added.
    """
    spotify_pls = load_playlists_json(encoding=spotify_playlists_encoding)
    yt = get_ytmusic()
    used_dst_ids: set = set()

    for src_pl in spotify_pls["playlists"]:
        if str(src_pl.get("name")) == "Liked Songs":
            continue

        pl_name = sanitize_playlist_title(
            src_pl.get("name"), fallback=f"Unnamed Spotify Playlist {src_pl['id']}"
        )

        if not any(t.get("track") for t in src_pl.get("tracks") or []):
            print(f"Skipping Spotify playlist '{pl_name}': it has no tracks.\n")
            continue

        #  Exclude playlists already used this run, so two Spotify playlists with
        #  the same (or case-insensitively same) name are not merged into one.
        dst_pl_id = get_playlist_id_by_name(yt, pl_name, exclude=used_dst_ids)
        print(f"Looking up playlist '{pl_name}': id={dst_pl_id}")
        if dst_pl_id is None:
            if dry_run:
                print(f"DRY RUN: would create playlist '{pl_name}', skipping.")
                continue
            dst_pl_id = _ytmusic_create_playlist(
                yt, title=pl_name, description=pl_name, privacy_status=privacy_status
            )
            print(f"NOTE: Created playlist '{pl_name}' with ID: {dst_pl_id}")
        used_dst_ids.add(dst_pl_id)

        _copy_one_playlist(
            yt,
            src_pl["id"],
            dst_pl_id,
            spotify_playlists_encoding,
            dry_run,
            track_sleep,
            yt_search_algo,
            reverse_playlist,
        )
        print("\nPlaylist done!\n")

    print("All done!")


def plan_playlist_order(
    src_tracks: List[SongInfo], yt_tracks: List[Dict]
) -> List[Dict]:
    """Return `yt_tracks` sorted into the order of `src_tracks`.

    Each YTMusic item is matched to the first unused Spotify track with the same
    (title, artist) key, falling back to title only. Items that match nothing
    (e.g. songs added by hand on YTMusic) stay right after their current predecessor.
    """
    by_full: Dict[tuple, List[int]] = {}
    by_title: Dict[str, List[int]] = {}
    for i, t in enumerate(src_tracks):
        tk = _normalize_song_key(t.title)
        by_full.setdefault((tk, _normalize_song_key(t.artist)), []).append(i)
        by_title.setdefault(tk, []).append(i)

    used: set = set()

    def position(item: Dict) -> float:
        tk = _normalize_song_key(item.get("title"))
        candidates: List[int] = []
        for artist in item.get("artists") or []:
            candidates += by_full.get((tk, _normalize_song_key(artist.get("name"))), [])
        candidates += by_title.get(tk, [])
        for i in candidates:
            if i not in used:
                used.add(i)
                return i
        return float("inf")

    #  Unmatched items (title differs too much, or added by hand) stay glued to
    #  the item before them, so a playlist that is already in order is untouched.
    keyed = []
    prev = -1.0
    for n, item in enumerate(yt_tracks):
        pos = position(item)
        if pos == float("inf"):
            pos = prev
        keyed.append((pos, n, item))
        prev = pos
    return [item for _, _, item in sorted(keyed, key=lambda x: (x[0], x[1]))]


def reorder_playlist(
    yt: YTMusic,
    src_tracks: List[SongInfo],
    playlist_id: str,
    dry_run: bool = False,
    move_sleep: float = 0.1,
    only_if_reversed: bool = True,
) -> int:
    """Reorder a YTMusic playlist in place to follow the Spotify order.

    Fixes playlists copied by older versions that reversed the track order.
    Nothing is added or removed. Returns the number of moves performed.

    With `only_if_reversed` (default), playlists that are mostly in Spotify order
    already are left alone: small differences there usually come from songs that
    were matched to a different YouTube song, and "fixing" them does more harm.
    """
    yt_tracks = [
        t
        for t in _get_playlist_with_retry(yt, playlist_id).get("tracks") or []
        if t.get("setVideoId")
    ]
    desired = plan_playlist_order(src_tracks, yt_tracks)
    current = [t["setVideoId"] for t in yt_tracks]
    target = [t["setVideoId"] for t in desired]
    if current == target:
        return 0
    if only_if_reversed:
        rank = {sv: i for i, sv in enumerate(target)}
        ranks = [rank[sv] for sv in current]
        descending = sum(1 for a, b in zip(ranks, ranks[1:]) if b < a)
        if descending * 2 <= len(ranks) - 1:
            return 0

    moves = 0
    #  Build the order from the back: place target[i] directly before target[i+1].
    for i in range(len(target) - 2, -1, -1):
        item, successor = target[i], target[i + 1]
        if current.index(item) + 1 == current.index(successor):
            continue
        if not dry_run:
            for attempt in range(5):
                try:
                    yt.edit_playlist(playlist_id, moveItem=(item, successor))
                    break
                except Exception as e:
                    if attempt == 4:
                        raise
                    print(f"(Retrying move in {2 ** (attempt + 1)}s: {str(e)[:80]})")
                    time.sleep(2 ** (attempt + 1))
            if move_sleep:
                time.sleep(move_sleep)
        current.remove(item)
        current.insert(current.index(successor), item)
        moves += 1
    assert current == target
    return moves


def reorder_all_playlists(
    dry_run: bool = False, spotify_playlists_encoding: str = "utf-8"
) -> None:
    """Reorder every YTMusic playlist that matches a Spotify playlist by name."""
    spotify_pls = load_playlists_json(encoding=spotify_playlists_encoding)
    yt = get_ytmusic()
    used: set = set()
    for src_pl in spotify_pls["playlists"]:
        if str(src_pl.get("name")) == "Liked Songs":
            continue
        src = list(
            iter_spotify_playlist(
                src_pl["id"],
                spotify_encoding=spotify_playlists_encoding,
                reverse_playlist=False,
            )
        )
        if not src:
            continue
        name = sanitize_playlist_title(src_pl.get("name"), fallback="")
        pl_id = get_playlist_id_by_name(yt, name, exclude=used) if name else None
        if pl_id is None:
            print(f"   no YTMusic playlist named '{name}', skipping")
            continue
        used.add(pl_id)
        moves = reorder_playlist(yt, src, pl_id, dry_run=dry_run)
        verb = "would move" if dry_run else "moved"
        print(f"   {'in order' if not moves else f'{verb} {moves} tracks'}")
    print("All done!")


def resolve_ytmusic_playlist(yt: YTMusic, value: str) -> Optional[str]:
    """Resolve a YTMusic playlist given as ID, URL, or name ("+Name" or "Name")."""
    if value.startswith("+"):
        return get_playlist_id_by_name(yt, value[1:])
    pl_id = parse_ytmusic_playlist_id(value)
    if re.fullmatch(r"(PL|OLAK5uy_|RD|LM)[A-Za-z0-9_-]+", pl_id) or "list=" in value:
        return pl_id
    return get_playlist_id_by_name(yt, value)


def reorder_one_playlist(
    src_tracks: List[SongInfo],
    ytmusic_playlist: str,
    dry_run: bool = False,
    only_if_reversed: bool = False,
    auth_file: Optional[str] = None,
) -> int:
    """Reorder one YTMusic playlist in place to match the given Spotify track list.

    Unlike `reorder_all_playlists`, this does a full reorder by default
    (`only_if_reversed=False`), since the user picked the playlist explicitly.
    Nothing is added or removed. Returns the number of moves.
    """
    yt = get_ytmusic(auth_file)
    pl_id = resolve_ytmusic_playlist(yt, ytmusic_playlist)
    if pl_id is None:
        print(f"ERROR: Could not find YTMusic playlist '{ytmusic_playlist}'.")
        sys.exit(1)
    pl = _get_playlist_with_retry(yt, pl_id)
    yt_tracks = [t for t in pl.get("tracks") or [] if t.get("setVideoId")]
    print(f"== YTMusic playlist: {pl.get('title')} ({len(yt_tracks)} tracks)")
    print(f"== Spotify order:    {len(src_tracks)} tracks")

    planned = plan_playlist_order(src_tracks, yt_tracks)
    if dry_run:
        print("\nPlanned order:")
        for n, t in enumerate(planned, 1):
            artist = ((t.get("artists") or [{}])[0] or {}).get("name", "")
            print(f"  {n:3}. {t.get('title')} - {artist}")

    moves = reorder_playlist(
        yt, src_tracks, pl_id, dry_run=dry_run, only_if_reversed=only_if_reversed
    )
    if moves == 0:
        print("\nAlready in order, nothing to do.")
    else:
        print(f"\n{'Would move' if dry_run else 'Moved'} {moves} tracks.")
    return moves
