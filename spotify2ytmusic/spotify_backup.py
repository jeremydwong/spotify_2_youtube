#!/usr/bin/env python3
#
#  This file is licensed under the MIT license
#  This file originates from https://github.com/caseychu/spotify-backup

import base64
import codecs
import hashlib
import http.client
import http.server
import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


class SpotifyAPI:
    """Class to interact with the Spotify API using an OAuth token."""

    BASE_URL = "https://api.spotify.com/v1/"

    def __init__(self, auth):
        self._auth = auth

    def get(self, url, params={}, tries=3):
        """Fetch a resource from Spotify API."""
        url = self._construct_url(url, params)
        for _ in range(tries):
            try:
                req = self._create_request(url)
                return self._read_response(req)
            except Exception as err:
                print(f"Error fetching URL {url}: {err}")
                time.sleep(2)
        sys.exit("Failed to fetch data from Spotify API after retries.")

    def list(self, url, params={}):
        """Fetch paginated resources and return as a combined list."""
        response = self.get(url, params)
        items = response["items"]

        while response["next"]:
            response = self.get(response["next"])
            items += response["items"]
        return items

    @staticmethod
    def authorize(client_id, scope):
        """Open a browser for user authorization and return SpotifyAPI instance.

        Uses the Authorization Code flow with PKCE (Spotify removed the implicit
        "response_type=token" flow), which needs no client secret.
        """
        redirect_uri = f"http://127.0.0.1:{SpotifyAPI._SERVER_PORT}/redirect"
        code_verifier = secrets.token_urlsafe(64)[:128]
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(16)
        url = SpotifyAPI._construct_auth_url(
            client_id, scope, redirect_uri, code_challenge, state
        )
        print(f"Open this link if the browser doesn't open automatically: {url}")
        webbrowser.open(url)

        server = SpotifyAPI._AuthorizationServer("127.0.0.1", SpotifyAPI._SERVER_PORT)
        try:
            while True:
                server.handle_request()
        except SpotifyAPI._Authorization as auth:
            if auth.state != state:
                sys.exit("Spotify authorization failed: state mismatch.")
            access_token = SpotifyAPI._exchange_code(
                client_id, auth.code, redirect_uri, code_verifier
            )
            return SpotifyAPI(access_token)
        finally:
            server.server_close()

    @staticmethod
    def _construct_auth_url(client_id, scope, redirect_uri, code_challenge, state):
        return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "scope": scope,
                "redirect_uri": redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": code_challenge,
                "state": state,
            }
        )

    @staticmethod
    def _exchange_code(client_id, code, redirect_uri, code_verifier):
        """Exchange an authorization code for an access token (PKCE)."""
        data = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            }
        ).encode()
        req = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req) as res:
                return json.load(res)["access_token"]
        except urllib.error.HTTPError as e:
            sys.exit(
                f"Spotify token exchange failed: {e} {e.read().decode(errors='replace')}"
            )

    def _construct_url(self, url, params):
        """Construct a full API URL."""
        if not url.startswith(self.BASE_URL):
            url = self.BASE_URL + url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        return url

    def _create_request(self, url):
        """Create an authenticated request."""
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self._auth}")
        return req

    def _read_response(self, req):
        """Read and parse the response."""
        with urllib.request.urlopen(req) as res:
            reader = codecs.getreader("utf-8")
            return json.load(reader(res))

    _SERVER_PORT = 43019

    class _AuthorizationServer(http.server.HTTPServer):
        def __init__(self, host, port):
            super().__init__((host, port), SpotifyAPI._AuthorizationHandler)

        def handle_error(self, request, client_address):
            raise

    class _AuthorizationHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.startswith("/redirect"):
                self.send_error(404)
                return
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if "error" in query:
                self.wfile.write(
                    b"Spotify authorization failed. You may close this window."
                )
                raise SpotifyAPI._AuthorizationError(query["error"][0])
            self.wfile.write(b"Thanks! You may now close this window.")
            raise SpotifyAPI._Authorization(
                query.get("code", [""])[0], query.get("state", [""])[0]
            )

        def log_message(self, format, *args):
            pass

    class _Authorization(Exception):
        def __init__(self, code, state):
            self.code = code
            self.state = state

    class _AuthorizationError(Exception):
        pass


def fetch_user_data(spotify, dump):
    """Fetch playlists and liked songs based on the dump parameter."""
    playlists = []
    liked_albums = []

    if "liked" in dump:
        print("Loading liked albums and songs...")
        liked_tracks = spotify.list("me/tracks", {"limit": 50})
        liked_albums = spotify.list("me/albums", {"limit": 50})
        playlists.append({"name": "Liked Songs", "tracks": liked_tracks})

    if "playlists" in dump:
        print("Loading playlists...")
        playlist_data = spotify.list("me/playlists", {"limit": 50})
        for playlist in playlist_data:
            print(f"Loading playlist: {playlist['name']}")
            playlist["tracks"] = spotify.list(
                playlist["tracks"]["href"], {"limit": 100}
            )
        playlists.extend(playlist_data)

    return playlists, liked_albums


def fetch_playlist(spotify, playlist_id):
    """Fetch one playlist (with all its tracks) by ID.

    Works for your own playlists and for public or shared playlists of other
    users (e.g. a family member's playlist link).
    """
    playlist = spotify.get(f"playlists/{playlist_id}")
    #  Newer API responses name the track list "items", older ones "tracks".
    ref = playlist.get("tracks") or playlist.get("items") or {}
    href = ref.get("href") if isinstance(ref, dict) else None
    playlist["tracks"] = spotify.list(
        href or f"playlists/{playlist_id}/tracks", {"limit": 100}
    )
    return playlist


def authorize_default():
    """Log in to Spotify (read-only) using the browser."""
    return SpotifyAPI.authorize(
        client_id="5c098bcc800e45d49e476265bc9b6934",
        scope="playlist-read-private playlist-read-collaborative user-library-read",
    )


def write_to_file(file, format, playlists, liked_albums):
    """Write fetched data to a file in the specified format."""
    print(f"Writing to {file}...")
    with open(file, "w", encoding="utf-8") as f:
        if format == "json":
            json.dump({"playlists": playlists, "albums": liked_albums}, f)
        else:
            for playlist in playlists:
                f.write(playlist["name"] + "\r\n")
                for track in playlist["tracks"]:
                    if track["track"]:
                        f.write(
                            "{name}\t{artists}\t{album}\t{uri}\t{release_date}\r\n".format(
                                uri=track["track"]["uri"],
                                name=track["track"]["name"],
                                artists=", ".join(
                                    [
                                        artist["name"]
                                        for artist in track["track"]["artists"]
                                    ]
                                ),
                                album=track["track"]["album"]["name"],
                                release_date=track["track"]["album"]["release_date"],
                            )
                        )
                f.write("\r\n")


def main(dump="playlists,liked", format="json", file="playlists.json", token=""):
    print("Starting backup...")
    spotify = SpotifyAPI(token) if token else authorize_default()

    playlists, liked_albums = fetch_user_data(spotify, dump)
    write_to_file(file, format, playlists, liked_albums)
    print(f"Backup completed! Data written to {file}")


if __name__ == "__main__":
    main()
