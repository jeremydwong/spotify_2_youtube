#!/usr/bin/env python3
"""Log in to YouTube Music by reading cookies from a local browser.

No DevTools and no copy/paste: the `.youtube.com` cookies are read from the
browser's own cookie store (using yt-dlp's well-maintained extractor), turned
into the minimal header set ytmusicapi needs, checked against the live
service, and saved to the credentials file (oauth.json by default).

ytmusicapi only needs the `cookie` header plus `x-goog-authuser`; it computes
the SAPISIDHASH `authorization` value itself on every request from the
SAPISID cookie. The placeholder `authorization` written here only tells
ytmusicapi that this is a browser-cookie login.

Platform notes:
  - Firefox: works everywhere, even while Firefox is open.
  - Chrome/Edge/Brave/... on macOS: macOS asks once for Keychain access to
    "Chrome Safe Storage" (click "Allow").  On Linux the desktop keyring is used.
  - Safari (macOS): the terminal needs Full Disk Access.
  - Chrome/Edge on Windows: since Chrome 127 cookies use app-bound encryption,
    which other programs cannot read.  Use Firefox, or the manual method.

Cookie values are never printed.
"""

import json
import os
import sys
from argparse import ArgumentParser
from typing import Callable, Dict, List, Optional, Tuple

YTM_ORIGIN = "https://music.youtube.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
#  Cookies ytmusicapi/YouTube need for a signed-in session.
REQUIRED_COOKIES = ("SAPISID", "__Secure-3PAPISID")
SUPPORTED_BROWSERS = (
    "chrome",
    "firefox",
    "safari",
    "edge",
    "brave",
    "chromium",
    "opera",
    "vivaldi",
    "whale",
)
MAX_ACCOUNTS = 10


class LoginError(Exception):
    """A login problem with a message meant for the user."""


def read_youtube_cookies(browser: str, profile: Optional[str] = None) -> Dict[str, str]:
    """Return {name: value} of the browser's youtube.com cookies."""
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ImportError:
        raise LoginError(
            "Automatic login needs the 'yt-dlp' package.  Install it with:\n"
            "    pip install yt-dlp"
        )

    class _QuietLogger:
        def debug(self, msg, *a, **k):
            pass

        info = debug

        def warning(self, msg, *a, **k):
            print(f"  (browser) {msg}", file=sys.stderr)

        def error(self, msg, *a, **k):
            print(f"  (browser) {msg}", file=sys.stderr)

    try:
        jar = extract_cookies_from_browser(browser, profile, logger=_QuietLogger())
    except Exception as e:  # yt-dlp raises various errors per platform
        raise LoginError(_explain_cookie_error(browser, e))

    cookies: Dict[str, str] = {}
    for c in jar:
        domain = (c.domain or "").lstrip(".")
        if domain == "youtube.com" or domain.endswith(".youtube.com"):
            #  Prefer the most specific domain if a name appears twice.
            if c.name not in cookies or domain.endswith("music.youtube.com"):
                cookies[c.name] = c.value
    return cookies


def _explain_cookie_error(browser: str, e: Exception) -> str:
    msg = str(e)
    hint = ""
    if sys.platform == "win32" and browser in ("chrome", "edge", "brave", "chromium"):
        hint = (
            "On Windows, Chrome-based browsers encrypt cookies so that other programs "
            "cannot read them (app-bound encryption).  Use '--browser firefox', or the "
            "manual method in the README."
        )
    elif "Permission" in msg or "Operation not permitted" in msg:
        hint = (
            "Permission denied.  For Safari, give your terminal app Full Disk Access "
            "(System Settings > Privacy & Security > Full Disk Access)."
        )
    elif "could not copy" in msg.lower() or "locked" in msg.lower():
        hint = f"Close {browser} completely and try again."
    elif "keyring" in msg.lower() or "keychain" in msg.lower():
        hint = "Access to the browser's password storage was denied; allow it and try again."
    return f"Could not read cookies from {browser}: {msg}" + (
        f"\n{hint}" if hint else ""
    )


def build_headers(cookies: Dict[str, str], authuser: int = 0) -> Dict[str, str]:
    """Minimal header set for a ytmusicapi browser login."""
    missing = [n for n in REQUIRED_COOKIES if n not in cookies]
    if missing:
        raise LoginError(
            "You don't seem to be signed in to YouTube in this browser "
            f"(missing cookie {', '.join(missing)}).  Open {YTM_ORIGIN}, sign in, "
            "and try again."
        )
    return {
        "user-agent": USER_AGENT,
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": YTM_ORIGIN,
        "x-origin": YTM_ORIGIN,
        "x-goog-authuser": str(authuser),
        #  Placeholder: marks this as a browser login; ytmusicapi recomputes it.
        "authorization": "SAPISIDHASH 0_0",
        "cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
    }


def _default_client_factory(headers: Dict[str, str]):
    from ytmusicapi import YTMusic

    return YTMusic(dict(headers))


def find_accounts(
    cookies: Dict[str, str],
    client_factory: Callable = _default_client_factory,
    max_accounts: int = MAX_ACCOUNTS,
) -> List[Tuple[int, str]]:
    """Return [(authuser index, "Name (@handle)"), ...] for signed-in accounts."""
    accounts: List[Tuple[int, str]] = []
    seen = set()
    for authuser in range(max_accounts):
        try:
            yt = client_factory(build_headers(cookies, authuser))
            info = yt.get_account_info()
        except LoginError:
            raise
        except Exception:
            break  # no account at this index
        label = info.get("accountName") or "?"
        if info.get("channelHandle"):
            label += f" ({info['channelHandle']})"
        if label in seen:  # Google repeats the last account past the end
            break
        seen.add(label)
        accounts.append((authuser, label))
    return accounts


def validate(
    headers: Dict[str, str], client_factory: Callable = _default_client_factory
):
    """Make one real library request; raise LoginError if it fails."""
    try:
        client_factory(headers).get_library_playlists(limit=1)
    except Exception as e:
        raise LoginError(f"YouTube Music rejected the login: {str(e)[:200]}")


def save_headers(headers: Dict[str, str], path: str) -> None:
    """Write the credentials file readable only by the current user."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(headers, f, indent=4)
    os.chmod(path, 0o600)


def login(
    browser: str,
    output: str = "oauth.json",
    profile: Optional[str] = None,
    account: Optional[int] = None,
    choose: Callable[[List[Tuple[int, str]]], int] = None,
    client_factory: Callable = _default_client_factory,
) -> str:
    """Full flow: read cookies, pick the account, validate, save.  Returns the account label."""
    print(f"Reading YouTube cookies from {browser}...")
    cookies = read_youtube_cookies(browser, profile)
    build_headers(cookies)  # raises a clear error if not signed in

    accounts = find_accounts(cookies, client_factory)
    if not accounts:
        raise LoginError(
            f"Found YouTube cookies in {browser}, but no usable signed-in account. "
            f"Open {YTM_ORIGIN} in {browser}, make sure you are signed in, and retry."
        )

    if account is not None:
        match = [a for a in accounts if a[0] == account]
        if not match:
            raise LoginError(
                f"No account number {account}.  Signed-in accounts: "
                + ", ".join(f"{i}: {label}" for i, label in accounts)
            )
        authuser, label = match[0]
    elif len(accounts) == 1:
        authuser, label = accounts[0]
    else:
        authuser = (choose or _ask_account)(accounts)
        label = dict(accounts)[authuser]

    headers = build_headers(cookies, authuser)
    validate(headers, client_factory)
    save_headers(headers, output)
    print(f"Logged in to YouTube Music as {label}.  Saved to {output} (private file).")
    return label


def _ask_account(accounts: List[Tuple[int, str]]) -> int:
    listing = "\n".join(f"  {i}: {label}" for i, label in accounts)
    no_prompt = LoginError(
        f"Several Google accounts are signed in:\n{listing}\n"
        "Choose one by running again with --account NUMBER, e.g. --account "
        f"{accounts[0][0]}"
    )
    if not sys.stdin or not sys.stdin.isatty():
        raise no_prompt
    print("Several Google accounts are signed in:")
    print(listing)
    while True:
        try:
            answer = input("Which account should be used? Number: ").strip()
        except EOFError:
            raise no_prompt
        if answer.isdigit() and int(answer) in dict(accounts):
            return int(answer)
        print("Please type one of the numbers above.")


def main(argv: Optional[List[str]] = None) -> int:
    parser = ArgumentParser(
        description="Log in to YouTube Music using the cookies of a browser where "
        "you are already signed in.  No DevTools or copy/paste needed."
    )
    parser.add_argument(
        "--browser",
        default="chrome",
        choices=SUPPORTED_BROWSERS,
        help="Browser to read the login from (default: chrome).",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Browser profile name or path, if not the default profile.",
    )
    parser.add_argument(
        "--account",
        type=int,
        default=None,
        help="Which signed-in Google account to use (0 = first).  Asked if several.",
    )
    parser.add_argument(
        "--output",
        default="oauth.json",
        help="Credentials file to write (default: oauth.json).",
    )
    args = parser.parse_args(argv)
    try:
        login(args.browser, args.output, args.profile, args.account)
    except LoginError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
