### Overview

This is a set of scripts for copying "liked" songs and playlists from Spotify to YTMusic. It provides a GUI (implemented by Yoween, formerly called spotify_to_ytmusic_gui).

---

### About this fork

This is [jeremydwong/spotify_2_youtube](https://github.com/jeremydwong/spotify_2_youtube), a fork of
[linsomniac/spotify_to_ytmusic](https://github.com/linsomniac/spotify_to_ytmusic).
It was created while migrating a real library (170 Spotify playlists, ~6,000 tracks,
435 liked songs) and fixes every problem hit along the way. Everything else works
as in upstream.

#### Bug fixes

| # | Problem | Fix |
|---|---------|-----|
| 1 | **Playlists were copied in reverse order.** Playlists were reversed "so they end up in the same order as on Spotify", but YTMusic *appends* new items, so the copy came out backwards. (Liked songs had the opposite problem in the CLI.) | Playlists are now copied in Spotify order by default (`--reverse-playlist` to opt back in). Liked songs are reversed by default in the CLI as well as the GUI, because YTMusic lists the most recently liked first. Verified against a live account. |
| 2 | **Spotify backup no longer worked.** Spotify removed the implicit-grant OAuth flow (`response_type=token`), so login failed with `response_type must be code`. | `spotify_backup.py` now uses the Authorization Code flow with PKCE (no client secret needed), with a `state` check. |
| 3 | **Every YTMusic request failed with `400 Bad Request` after copying headers from Chrome.** Chrome's header view includes HTTP/2 pseudo-headers (`:authority`, `:path`, …) on separate lines, which ytmusicapi parsed into invalid header names. | `ytmusic_credentials.py` drops invalid header names from the saved credentials. |
| 4 | **Playlist names with special characters.** ytmusicapi refuses titles containing `<` or `>`, and the retry loop then backed off for ~85 minutes before exiting. Control and zero-width characters, stray whitespace and names over 150 characters caused similar problems or duplicate playlists on re-runs. | Names are sanitized before creating playlists: `<` `>` become `‹` `›`, control and zero-width characters are removed, whitespace is collapsed, and names are cut to 150 characters. Lookups by name tolerate whitespace, Unicode normalization and case differences. |
| 5 | **File name and encoding bugs.** `reverse_playlist.py` built its backup name with `split(".")[0]`, which breaks on `./playlists.json` and `my.playlists.json`. It also had an inverted "file exists" check (it never ran without `-r`) and read and wrote without an explicit encoding (non-ASCII names break on Windows). | Uses `os.path.splitext` and UTF-8, and checks for the *backup* file. `ytmusic_credentials.py` and `load_playlists_json` also use UTF-8 explicitly, and the JSON file handle is closed properly. |
| 6 | **A copy aborted partway through.** A newly created playlist is sometimes not readable for a few seconds, and the copier called `sys.exit(1)` on the first failure. | Reading a playlist is retried with back-off. |
| 7 | **`copy_all_playlists` created a new YTMusic client per playlist.** | One client is shared for the whole run. |
| 8 | **Same-name playlists were merged.** Two Spotify playlists with the same name (or names differing only in case, e.g. `best soul` / `Best soul`) were copied into one YTMusic playlist. | Exact name matches are preferred, and a YTMusic playlist is used at most once per run. |
| 9 | **Empty Spotify playlists created empty YTMusic playlists.** | Skipped. |
| 10 | **`raw_headers.txt` was tracked in git**, so pasting your cookies into it and committing would publish your Google session. | The file is untracked and git-ignored. Create it yourself (see below). |
| 11 | The unit tests patched a module path that no longer existed, so all of them failed. | Tests were fixed and extended (order, skipping, renaming, reordering, file names). |

#### New features

- **`s2yt_login --browser chrome`: log in without DevTools.** It reads your YouTube Music
  login straight from a browser where you're already signed in, lets you pick the Google
  account if several are signed in, checks that the login works, and saves `oauth.json`
  as a private file. See [Login to YTMusic](#login-to-ytmusic). (Phase 1 of the roadmap.)
- **`s2yt_reorder_playlist <spotify> <ytmusic>`** reorders *one* YouTube Music playlist to
  match a Spotify playlist. Both can be given as links, and the Spotify playlist can be fetched
  live, e.g. a public or shared playlist from a family member. Use `--auth` to work on a
  different YouTube Music account.

- **Existing playlists are compared, not duplicated.** If a YTMusic playlist with the
  same name already exists, its contents are read and compared with Spotify (by
  title and artist, and by the YouTube video ID found on lookup). Complete playlists are
  skipped, and partly copied ones only get the missing tracks. Re-running
  `s2yt_copy_all_playlists` after a crash or timeout is fast and safe.
- **`s2yt_reorder_playlists`** puts playlists copied by older versions (in reversed order)
  back into Spotify order, *in place*. Nothing is added or removed. By default it only
  touches playlists that are mostly backwards, and `--dry-run` shows what would move.

#### Known issues

- **Match accuracy with the default search (`--algo 0`).** In our migration, about 9% of
  lookups (540 of 5,990) returned a YouTube song whose title differs from the Spotify
  title. Some are harmless (remasters, translated opera titles); others are clearly wrong
  (e.g. "Holiday Road" matched "Not Too Late"). Algorithm 0 falls back to the *first*
  search result without checking it. See the roadmap below.
- Songs that appear more than once in a Spotify playlist appear only once on YTMusic,
  because the copy adds tracks with `duplicates=False`.

#### Roadmap: logging in to YouTube Music without DevTools

Copying request headers out of DevTools is the hardest step for most users. It is
also error-prone (see bug 3). The plan below removes it. It is based on how
ytmusicapi actually authenticates: it only needs the **`cookie`** header and
**`x-goog-authuser`**, and it computes the `SAPISIDHASH` authorization from the `SAPISID`
cookie on its own. So reading the cookies is enough; no other header is needed.

Background research (as of late 2026):

- ytmusicapi's OAuth flow needs your own Google Cloud client ID and secret, and has been
  unreliable since Google disabled OAuth for the web client
  ([#679](https://github.com/sigma67/ytmusicapi/issues/679),
  [#921](https://github.com/sigma67/ytmusicapi/issues/921)). Browser cookies remain the
  supported path ([docs](https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html)).
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) already maintains cross-platform cookie
  extraction (`yt_dlp.cookies.extract_cookies_from_browser`) for Firefox, Safari, Chrome,
  Edge, Brave, Opera, Vivaldi and Chromium on Windows, macOS and Linux.
- On **Windows, Chrome 127+ uses app-bound encryption**, which external tools cannot
  decrypt without admin rights or code injection
  ([yt-dlp #10927](https://github.com/yt-dlp/yt-dlp/issues/10927)). We will not do either.
- **Chrome 136+ ignores `--remote-debugging-port` on the default profile**, but allows it
  with a separate `--user-data-dir`
  ([Chrome blog](https://developer.chrome.com/blog/remote-debugging-port)).

**Phase 1: `s2yt_login --browser <firefox|chrome|edge|brave|safari|...>`** (the main fix).
✅ *Implemented* in `spotify2ytmusic/ytmusic_login.py`: cookie reading, account detection,
validation, and saving with `0600` permissions. Verified against a live account with two
signed-in Google accounts. Still to do: the GUI button.

1. Read the `.youtube.com` cookies from the user's browser using yt-dlp's extractor
   (an optional dependency: `pip install spotify2ytmusic[login]`).
   - macOS Chromium browsers: macOS shows one Keychain prompt ("Chrome Safe Storage");
     the user clicks *Allow*.
   - Safari: needs Full Disk Access for the terminal; the tool explains this if access is denied.
   - Firefox: works everywhere, even while Firefox is open.
2. Build the minimal header set: `cookie`, `x-goog-authuser`, `origin`, `user-agent`.
3. **Pick the account automatically.** Try `x-goog-authuser` 0, 1, 2, … and call
   `get_account_info()` for each. If more than one Google account is signed in, list the
   names and let the user choose.
4. **Validate before saving.** Call `get_library_playlists(limit=1)`. On failure, show a
   specific message ("not signed in to music.youtube.com in Chrome", "Keychain access
   denied", …) instead of a stack trace.
5. Write `oauth.json` with `0600` permissions, and never print cookie values.
6. GUI: replace the "Login to YT Music" tab with a browser drop-down and a *Log in* button.

**Phase 2: built-in login window (fallback for Windows + Chrome, or no supported browser)**

1. Launch the user's own Chrome or Edge binary as a *normal* browser (no WebDriver flags,
   so Google sign-in is not blocked) with a dedicated profile,
   `--user-data-dir=<app-data>/s2yt-profile`, opened on `music.youtube.com`.
2. The user signs in once, then closes the window.
3. Relaunch the same profile headless with `--remote-debugging-port` bound to `127.0.0.1`,
   read the cookies over the DevTools protocol (`Storage.getCookies`), and exit.
   This is allowed on non-default profiles since Chrome 136, and avoids app-bound
   encryption entirely, because the browser decrypts its own cookies.
4. The profile persists, so later logins (e.g. after the session expires) need no user
   action at all.

**Phase 3: make the manual paste foolproof** (still available as a last resort)

- Accept anything the user can copy: Chrome "separate lines", Chrome/Firefox raw, Firefox
  *Copy Request Headers*, *Copy as cURL* (bash and cmd), *Copy as fetch*, a HAR file, or
  just the cookie string.
- Fill in `x-goog-authuser` when it is missing, strip pseudo-headers, and validate
  right away with a clear message.
- GUI: a paste box instead of editing `raw_headers.txt`.

**Phase 4: resilience**

- Detect an expired session mid-copy (HTTP 401/403, or 400 with an HTML body). Pause,
  re-run the login from phase 1 or 2 automatically, and resume. Resuming is already
  safe because existing playlist contents are compared.
- Better matching, to address the known issue above: score candidates by title, artist,
  album and duration (Spotify provides `duration_ms`) instead of taking the first hit.
  Then try the next algorithm when the score is low, and write a
  `low_confidence_matches.csv` report so users can review the uncertain ones.

Security notes for all phases: cookies give full access to the Google account. They
are read only from the local machine, stored only in `oauth.json` (mode `0600`,
git-ignored), never logged, and never sent anywhere except YouTube Music. The README
will tell users to sign out of YouTube Music when they are done, which invalidates the session.

---

### Preparation/Pre-Conditions

1. **Install Python and Git** (you may already have them installed).
2. **Uninstall the pip package from the original repository** (if you previously installed `linsomniac/spotify_to_ytmusic`):

   On Windows:

   ```bash
   python -m pip uninstall spotify2ytmusic
   ```

   On Linux or Mac:

   ```bash
   python3 -m pip uninstall spotify2ytmusic
   ```

---

### Setup Instructions

#### 1. Clone, Create a Virtual Environment, and Install Required Packages

Start by creating and activating a Python virtual environment to isolate dependencies.

```bash
git clone https://github.com/jeremydwong/spotify_2_youtube.git
cd spotify_2_youtube
```

On Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install ytmusicapi tk
```

On Linux or Mac:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install ytmusicapi tk
```

---

#### 2. Generate YouTube Music Credentials

**Easiest: automatic login (new in this fork).** Sign in to https://music.youtube.com
in your browser, then run:

```bash
pip install yt-dlp
python3 -m spotify2ytmusic login --browser chrome    # or firefox, safari, edge, brave
```

On a Mac, macOS asks once to allow access to "Chrome Safe Storage". Click **Allow**.
If several Google accounts are signed in, you'll be asked which one to use. This writes
`oauth.json`, and you can skip the manual steps below. On **Windows**, Chrome and Edge
cookies can't be read by other programs, so use `--browser firefox` or the manual
method.

**Manual method** (if automatic login doesn't work for you):

To use the YouTube Music API, you need to generate valid credentials. Follow these steps:

1. **Log in to YouTube Music**: Open YouTube Music in Firefox or Chrome and make sure you are logged in.
2. **Open the Inspection Tool**: Press `F12` (Windows/Linux) or `Cmd+Option+I` (Mac), or right-click and select _Inspect_.
3. **Access the Network Tab**: Navigate to the Network tab and filter by `browse`. Click something on the page (e.g. _Library_) if the list is empty.
4. **Select a Request**: Click one of the requests under the filtered results and locate the _Request Headers_ section.
5. **Toggle RAW View** (Firefox; optional in Chrome): The _Raw_ checkbox is at the right end of the _Request Headers_ heading. In Chrome you can also just select the request header lines directly.
6. **Copy Headers**: Copy everything in the _Request Headers_ section. Make sure the `cookie` line is included, and if you can, `x-goog-authuser` too (if it's missing, add the two lines `x-goog-authuser` and `0` for your first signed-in Google account).
7. **Paste into `raw_headers.txt`**: Create a file named `raw_headers.txt` in the main directory of this project and paste the copied content into it. This file contains your login cookies: it is git-ignored, so keep it that way and delete it when you're done.

**Run the Script**:

Execute the following command to generate the credentials file:

On Windows:

```bash
python spotify2ytmusic/ytmusic_credentials.py
```

On Linux or Mac:

```bash
python3 spotify2ytmusic/ytmusic_credentials.py
```

**Important**: After running this script, the authentication file will be created.
When you launch the GUI in the next step, it will automatically detect this file and log in to YouTube Music without requiring manual input. You’ll see a log message confirming this:

```
File detected, auto login
```

The GUI will **ignore the 'Login to YT Music' tab** and jump straight to the 'Spotify Backup' tab.

---

#### 3. Use the GUI for Migration

Now you can use the graphical user interface (GUI) to migrate your playlists and liked songs to YouTube Music. Start the GUI with the following command:

On Windows:

```bash
python -m spotify2ytmusic gui
```

On Linux or Mac:

```bash
python3 -m spotify2ytmusic gui
```

---

### GUI Features

Once the GUI is running, you can:

- **Backup Your Spotify Playlists**: Save your playlists and liked songs into the file `playlists.json`.
- **Load Liked Songs**: Migrate your Spotify liked songs to YouTube Music.
- **List Playlists**: View your playlists and their details.
- **Copy All Playlists**: Migrate all Spotify playlists to YouTube Music.
- **Copy a Specific Playlist**: Select and migrate a specific Spotify playlist to YouTube Music.

---

### Import Your Liked Songs - Tab 3

#### Click the `import` button, and wait until it finished and switched to the next tab

It will go through your Spotify liked songs, and like them on YTMusic. It will display
the song from Spotify and then the song that it found on YTMusic that it is liking. I've
spot-checked my songs and it seems to be doing a good job of matching YTMusic songs with
Spotify. So far I haven't seen a single failure across a couple hundred songs, but more
esoteric titles it may have issues with.

### List Your Playlists - Tab 4

#### Click the `list` button, and wait until it finished and switched to the next tab

This will list the playlists you have on both Spotify and YTMusic, so you can individually copy them.

### Copy Your Playlists - Tab 5

You can either copy **all** playlists, or do a more surgical copy of individual playlists.
Copying all playlists will use the name of the Spotify playlist as the destination playlist name on YTMusic.

#### To copy all the playlists click the `copy` button, and wait until it finished and switched to the next tab

**NOTE**: This does not copy the Liked playlist (see above to do that).

### Copy specific Playlist - Tab 6

In the list output, find the "playlist id" (the first column) of the Spotify playlist and of the YTMusic playlist.

#### Then fill both input fields and click the `copy` button

The copy playlist will take the name of the YTMusic playlist and will create the
playlist if it does not exist, if you start the YTMusic playlist with a "+":

Re-running "copy_playlist" or "load_liked" in the event that it fails should be safe, it
will not duplicate entries on the playlist.

## Command Line Usage

### Ways to Run

**NOTE**: There are two possible ways to run these commands, one is via standalone commands
if the application was installed, which takes the form of: `s2yt_load_liked`

If not fully installed, you can replace the "s2yt\_" with "python -m spotify2ytmusic", for
example: `s2yt_load_liked` becomes `python -m spotify2ytmusic load_liked`

### Login to YTMusic

Run `s2yt_login --browser chrome` (or `firefox`, `safari`, `edge`, `brave`, …) while
signed in to YouTube Music in that browser. Options:

- `--account N` chooses the Google account (0 = first) without asking.
- `--profile NAME` reads from a browser profile other than the default.
- `--output FILE` writes the credentials somewhere other than `oauth.json`, e.g. to
  keep a second person's login. Commands that support `--auth FILE` (or the
  `S2YT_AUTH_FILE` environment variable) will then use it.

Or see "Generate YouTube Music Credentials" above for the manual method.

When you are completely done, sign out of YouTube Music in the browser. That ends the
session, so the saved credentials stop working.

### Backup Your Spotify Playlists

Run `spotify2ytmusic/spotify_backup.py` and it will help you authorize access to your spotify account.

Run: `python3 spotify_backup.py playlists.json --dump=liked,playlists --format=json`

This will save your playlists and liked songs into the file "playlists.json".

### Import Your Liked Songs

Run: `s2yt_load_liked`

It will go through your Spotify liked songs, and like them on YTMusic. It will display
the song from spotify and then the song that it found on YTMusic that it is liking. I've
spot-checked my songs and it seems to be doing a good job of matching YTMusic songs with
Spotify. So far I haven't seen a single failure across a couple thousand songs, but more
esoteric titles it may have issues with.

### Import Your Liked Albums

Run: `s2yt_load_liked_albums`

Spotify stores liked albums outside of the "Liked Songs" playlist. This is the command to
load your liked albums into YTMusic liked songs.

### List Your Playlists

Run `s2yt_list_playlists`

This will list the playlists you have on both Spotify and YTMusic. You will need to
individually copy them.

### Copy Your Playlists

You can either copy **all** playlists, or do a more surgical copy of individual playlists.
Copying all playlists will use the name of the Spotify playlist as the destination
playlist name on YTMusic. To copy all playlists, run:

`s2yt_copy_all_playlists`

**NOTE**: This does not copy the Liked playlist (see above to do that).

In the list output above, find the "playlist id" (the first column) of the Spotify playlist,
and of the YTMusic playlist, and then run:

`s2yt_copy_playlist <SPOTIFY_PLAYLIST_ID> <YTMUSIC_PLAYLIST_ID>`

If you need to create a playlist, you can run:

`s2yt_create_playlist "<PLAYLIST_NAME>"`

_Or_ the copy playlist can take the name of the YTMusic playlist and will create the
playlist if it does not exist, if you start the YTMusic playlist with a "+":

`s2yt_copy_playlist <SPOTIFY_PLAYLIST_ID> +<YTMUSIC_PLAYLIST_NAME>`

For example:

`s2yt_copy_playlist SPOTIFY_PLAYLIST_ID "+Feeling Like a PUNK"`

Re-running "copy_playlist" or "copy_all_playlists" in the event that it fails is safe:
existing YTMusic playlists are compared with Spotify, complete ones are skipped, and only
missing tracks are added.

Tracks are added in Spotify order. Use `--reverse-playlist` if you want the reverse.
(`--no-reverse-playlist` is still accepted but no longer needed.)

### Reorder One Playlist to Match Spotify

`s2yt_reorder_playlist <SPOTIFY_PLAYLIST> <YTMUSIC_PLAYLIST>`

Both arguments can be links, IDs or names:

```bash
s2yt_reorder_playlist "https://open.spotify.com/playlist/37i9dQZF1DX..." \
                      "https://music.youtube.com/playlist?list=PL..." --dry-run
```

- The Spotify playlist is taken from `playlists.json` if it's there. Otherwise it is
  fetched live, and a browser window opens to log in to Spotify (read-only). Public
  playlists, and playlists shared with you, work even if someone else owns them.
  `--fetch` always fetches live.
- `--dry-run` prints the planned order and the number of moves, without changing anything.
- `--auth FILE` uses another YouTube Music login, for a playlist on someone else's
  account (create it with `s2yt_login --output FILE`).
- Nothing is added or removed; tracks are only moved. Tracks that don't match any
  Spotify track stay right after the track they currently follow.

### Fix Playlists Copied in Reverse Order

Older versions copied playlists backwards. To put them back in Spotify order in place
(nothing is added or removed), run:

`s2yt_reorder_playlists --dry-run` to see which playlists would change, then

`s2yt_reorder_playlists`

Only playlists that are mostly in reverse order are changed.

### Searching for YTMusic Tracks

This is mostly for debugging, but there is a command to search for tracks in YTMusic:

## `s2yt_search --artist <ARTIST> --album <ALBUM> <TRACK_NAME>`

## Details About Search Algorithms

The function first searches for albums by the given artist name on YTMusic.

It then iterates over the first three album results and tries to find a track with
the exact same name as the given track name. If it finds a match, it returns the
track information.

If the function can't find the track in the albums, it then searches for songs by the
given track name and artist name.

Depending on the yt_search_algo parameter, it performs one of the following actions:

If yt_search_algo is 0, it simply returns the first song result.

If yt_search_algo is 1, it iterates over the song results and returns the first song
that matches the track name, artist name, and album name exactly. If it can't find a
match, it raises a ValueError.

If yt_search_algo is 2, it performs a fuzzy match. It removes everything in brackets
in the song title and checks for a match with the track name, artist name, and album
name. If it can't find a match, it then searches for videos with the track name and
artist name. If it still can't find a match, it raises a ValueError.

If the function can't find the track using any of the above methods, it raises a
ValueError.

## FAQ

- My copy is failing after 20-40 minutes. Is my session timing out?

Try playing music in the browser on Youtube Music while you are loading the playlists,
this has been reported to keep the session from timing out.

- Does this run on mobile?

No, this runs on Linux/Windows/MacOS.

- How does the lookup algorithm work?

  Given the Spotify track information, it does a lookup for the album by the same artist
  on YTMusic, then looks at the first 3 hits looking for a track with exactly the same
  name. In the event that it can't find that exact track, it then does a search of songs
  for the track name by the same artist and simply returns the first hit.

  The idea is that finding the album and artist and then looking for the exact track match
  will be more likely to be accurate than searching for the song and artist and relying on
  the YTMusic algorithm to figure things out, especially for short tracks that might be
  have many contradictory hits like "Survival by Yes".

- My copy is failing with repeated "ERROR: (Retrying) Server returned HTTP 400: Bad
  Request".

  If _every_ request fails (even listing playlists) and you copied the headers from
  Chrome, re-run `ytmusic_credentials.py` with this fork. It removes the invalid header
  names that Chrome's copy produces. Otherwise, try running with "--track-sleep=3" argument to do a 3 second sleep between tracks. This
  will take much longer, but may succeed where faster rates have failed.

## License

Creative Commons Zero v1.0 Universal

spotify-backup.py licensed under MIT License.
See <https://github.com/caseychu/spotify-backup> for more information.

[//]: # " vim: set tw=90 ts=4 sw=4 ai: "
