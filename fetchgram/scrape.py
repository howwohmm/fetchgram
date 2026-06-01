"""
fetchgram.scrape
----------------
Downloads an Instagram profile (images only, no videos) into
<data_dir>/<handle>/raw/ using the instaloader Python library.

Mirrors the proven flags from instagram-archive/tools/_driver.sh and
run_zach.sh:
  --login=<user>  (session file loaded from default instaloader location)
  --no-videos  --no-video-thumbnails
  --no-compress-json
  --dirname-pattern=<data_dir>/{profile}
  --request-timeout=300
  stdin </dev/null equivalent  (non-interactive; handled by library usage)
  retry up to MAX_ATTEMPTS=40 with 180s backoff on failure
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Matches the proven shell scripts: 40 attempts, 180 s backoff
MAX_ATTEMPTS = 40
BACKOFF_SECONDS = 180


def _build_loader(
    raw_dir: Path,
    login: Optional[str] = None,
) -> "instaloader.Instaloader":  # type: ignore[name-defined]
    """
    Construct and return a configured Instaloader instance.

    Parameters
    ----------
    raw_dir:
        The directory that will receive downloaded files.  Passed as
        dirname_pattern so instaloader puts everything directly inside it
        without an extra sub-folder.
    login:
        Instagram username whose saved session should be loaded.  If None
        the download runs anonymously (rate-limited and may fail for private
        profiles).
    """
    import instaloader  # local import so the module can be imported without it

    il = instaloader.Instaloader(
        dirname_pattern=str(raw_dir),   # <data_dir>/<handle>/raw
        download_videos=False,          # --no-videos
        download_video_thumbnails=False,# --no-video-thumbnails
        compress_json=False,            # --no-compress-json
        download_pictures=True,
        save_metadata=True,
        request_timeout=300.0,
        quiet=True,                     # suppress instaloader's own stdout chatter
        sleep=True,                     # keep built-in rate-limiter active
    )

    if login:
        # Try to load a previously-saved instaloader session file.
        # instaloader stores sessions at ~/.config/instaloader/session-<username>
        # (or the XDG path on Linux).  If the file is missing we fall back to
        # anonymous mode with a warning rather than crashing.
        try:
            il.load_session_from_file(login)
            logger.info("Loaded session for %s", login)
        except FileNotFoundError:
            logger.warning(
                "No saved session found for '%s'. "
                "Run `instaloader --login=%s` once interactively to create one. "
                "Proceeding anonymously.",
                login,
                login,
            )

    return il


def scrape_profile(
    handle: str,
    data_dir: str = "./fetchgram-data",
    login: Optional[str] = None,
    count: Optional[int] = None,
    force: bool = False,
) -> str:
    """
    Download an Instagram profile's posts (images only) into
    ``<data_dir>/<handle>/raw/``.

    Parameters
    ----------
    handle:
        Instagram handle (without ``@``).
    data_dir:
        Root output directory.  Defaults to ``./fetchgram-data``.
    login:
        Instagram username for authenticated download.  Uses the session file
        previously saved by ``instaloader --login=<user>``.  If None, proceeds
        anonymously.
    count:
        Maximum number of posts to download.  None means all available posts
        (mirrors omitting ``--count`` in the shell scripts).
    force:
        If True, ignore and delete any existing ``.download.done`` marker and
        re-download from scratch.

    Returns
    -------
    str
        Absolute path to the raw download directory
        ``<data_dir>/<handle>/raw/``.

    Raises
    ------
    RuntimeError
        If the download fails on every retry attempt.
    """
    import instaloader  # late import

    data_root = Path(data_dir).expanduser().resolve()
    raw_dir = data_root / handle / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    done_marker = data_root / handle / ".download.done"
    if force and done_marker.exists():
        logger.info("--force set: removing stale .download.done marker for %s.", handle)
        done_marker.unlink()
    if done_marker.exists():
        logger.info("Download already complete for %s (marker exists). Skipping.", handle)
        return str(raw_dir)

    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info("Download attempt %d/%d for @%s", attempt, MAX_ATTEMPTS, handle)
        try:
            il = _build_loader(raw_dir, login=login)
            profile = instaloader.Profile.from_username(il.context, handle)
            posts_iter = profile.get_posts()

            il.posts_download_loop(
                posts=posts_iter,
                target=raw_dir,
                max_count=count,
                owner_profile=profile,
            )

            # Success — write done marker and return
            done_marker.touch()
            logger.info("Download complete for @%s -> %s", handle, raw_dir)
            return str(raw_dir)

        except instaloader.exceptions.LoginRequiredException:
            logger.error(
                "Profile @%s requires login. Pass --login <username>.", handle
            )
            raise  # No point retrying without credentials

        except instaloader.exceptions.ProfileNotExistsException:
            logger.error("Profile @%s does not exist.", handle)
            raise  # No point retrying a nonexistent profile

        except instaloader.exceptions.TooManyRequestsException as exc:
            last_exc = exc
            logger.warning(
                "Rate-limited on attempt %d for @%s. Backoff %ds.",
                attempt,
                handle,
                BACKOFF_SECONDS,
            )

        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "Attempt %d for @%s failed: %s. Backoff %ds.",
                attempt,
                handle,
                exc,
                BACKOFF_SECONDS,
            )

        if attempt < MAX_ATTEMPTS:
            logger.info("Sleeping %ds before retry…", BACKOFF_SECONDS)
            time.sleep(BACKOFF_SECONDS)

    raise RuntimeError(
        f"Download of @{handle} failed after {MAX_ATTEMPTS} attempts. "
        f"Last error: {last_exc}"
    )
