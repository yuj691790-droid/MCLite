"""HTTP helpers: retries, mirror rewriting, SHA-1 verified downloads.

Standard library only (``urllib`` + threads). Files are written to a
``.part`` sibling and only moved into place after verification, so a killed
launcher never leaves a half-written jar behind.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import __version__

USER_AGENT = f"MCLite/{__version__} (+https://github.com/yuj691790-droid/MC-Mini)"
BMCLAPI = "https://bmclapi2.bangbang93.com"

CHUNK = 1024 * 128


class HttpError(Exception):
    """Non-retryable HTTP failure (4xx or exhausted retries)."""


class DownloadError(HttpError):
    """A download failed verification or transport."""


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mirror_url(url: str) -> str | None:
    """Translate a Mojang URL to its BMCLAPI equivalent, if one exists."""
    if url.startswith("https://libraries.minecraft.net/"):
        return BMCLAPI + "/maven/" + url[len("https://libraries.minecraft.net/") :]
    if url.startswith("https://resources.download.minecraft.net/"):
        return BMCLAPI + "/assets/" + url[len("https://resources.download.minecraft.net/") :]
    return None


def candidate_urls(url: str, use_mirror: bool) -> list[str]:
    """Ordered URL candidates: mirror first (when enabled), official as fallback."""
    mirror = mirror_url(url) if use_mirror else None
    return [mirror, url] if mirror else [url]


def _open(url: str, timeout: float, headers: dict[str, str] | None = None):
    request = urllib.request.Request(url, method="GET")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept-Encoding", "identity")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_bytes(
    url: str,
    *,
    timeout: float = 30.0,
    retries: int = 3,
    headers: dict[str, str] | None = None,
) -> bytes:
    """GET ``url`` and return the body, retrying transport errors."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with _open(url, timeout, headers) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 404):
                raise HttpError(f"HTTP {exc.code} for {url}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt + 1 < retries:
            time.sleep(0.5 * (attempt + 1))
    raise HttpError(f"GET {url} failed after {retries} attempts: {last}")


def fetch_json(url: str, **kwargs) -> dict:
    return json.loads(fetch_bytes(url, **kwargs).decode("utf-8"))


@dataclass
class DownloadTask:
    url: str
    dest: Path
    sha1: str | None = None
    size: int | None = None
    sha256: str | None = None
    # Extra URLs tried in order if ``url`` fails (already ordered by caller).
    fallback: Sequence[str] = field(default_factory=tuple)

    def candidates(self) -> list[str]:
        return [self.url, *self.fallback]


class Downloader:
    """Small concurrent downloader with byte progress."""

    def __init__(self, *, retries: int = 4, timeout: float = 30.0, workers: int = 8) -> None:
        self.retries = retries
        self.timeout = timeout
        self.workers = workers

    # -- single file -----------------------------------------------------
    def ensure(
        self,
        task: DownloadTask,
        progress: Callable[[int], None] | None = None,
    ) -> bool:
        """Download one task unless the destination already matches.

        Returns True when bytes were written, False when already present.
        """
        dest = Path(task.dest)
        if dest.is_file():
            if task.sha1 or task.sha256:
                try:
                    if task.sha256:
                        matched = sha256_file(dest) == task.sha256
                    else:
                        matched = sha1_file(dest) == task.sha1
                    if matched:
                        if progress:
                            progress(dest.stat().st_size)
                        return False
                except OSError:
                    pass
            elif task.size is not None and dest.stat().st_size == task.size:
                if progress:
                    progress(dest.stat().st_size)
                return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        for url in task.candidates():
            if not url:
                continue
            try:
                self._download_one(url, dest, task, progress)
                return True
            except Exception as exc:  # noqa: BLE001 - collect and try the next candidate
                errors.append(f"{url}: {exc}")
        raise DownloadError(f"could not download {dest.name}: " + " | ".join(errors))

    def _download_one(
        self,
        url: str,
        dest: Path,
        task: DownloadTask,
        progress: Callable[[int], None] | None,
    ) -> None:
        part = dest.with_name(dest.name + ".part")
        last: Exception | None = None
        for attempt in range(self.retries):
            existing = part.stat().st_size if part.is_file() else 0
            if task.size is not None and existing > task.size:
                part.unlink(missing_ok=True)
                existing = 0
            headers = {"Range": f"bytes={existing}-"} if existing else None
            try:
                with _open(url, self.timeout, headers) as response:
                    status = getattr(response, "status", None) or response.getcode()
                    resumed = existing > 0 and status == 206
                    if existing and not resumed:
                        # Server ignored Range: start over cleanly.
                        existing = 0
                    with open(part, "ab" if resumed else "wb") as out:
                        if progress and resumed:
                            progress(existing)
                        while True:
                            chunk = response.read(CHUNK)
                            if not chunk:
                                break
                            out.write(chunk)
                            if progress:
                                progress(len(chunk))
                if task.size is not None and part.stat().st_size != task.size:
                    raise DownloadError(f"size mismatch for {dest.name}")
                if task.sha1 and sha1_file(part) != task.sha1:
                    raise DownloadError(f"sha1 mismatch for {dest.name}")
                if task.sha256 and sha256_file(part) != task.sha256:
                    raise DownloadError(f"sha256 mismatch for {dest.name}")
                os.replace(part, dest)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                # Corrupt bytes can never be resumed into a valid file.
                if "mismatch" in str(exc):
                    part.unlink(missing_ok=True)
                if attempt + 1 < self.retries:
                    time.sleep(0.8 * (attempt + 1))
        raise DownloadError(str(last))

    # -- many files ------------------------------------------------------
    def ensure_all(
        self,
        tasks: Iterable[DownloadTask],
        progress: Callable[[int, int], None] | None = None,
    ) -> list[DownloadError]:
        """Download many tasks; ``progress(done, total)`` is byte based.

        Returns the list of failures instead of raising, so a caller can
        report partial success.
        """
        pending = [t for t in tasks if t.url]
        total = sum((t.size or 0) for t in pending)
        done = 0
        lock = threading.Lock()
        failures: list[DownloadError] = []

        if progress:
            progress(0, total)

        def worker(task: DownloadTask) -> None:
            nonlocal done
            if task.sha1 and task.dest.is_file():
                try:
                    if sha1_file(task.dest) == task.sha1:
                        with lock:
                            done += task.dest.stat().st_size
                            if progress:
                                progress(done, total)
                        return
                except OSError:
                    pass

            def advance(count: int) -> None:
                nonlocal done
                with lock:
                    done += count
                    if progress:
                        progress(done, total)

            self.ensure(task, advance)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(worker, task): task for task in pending}
            for future in as_completed(futures):
                exc = future.exception()
                if exc is not None:
                    failures.append(DownloadError(str(exc)))
        return failures


def _body_of(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def post_json(
    url: str,
    payload: dict,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict]:
    """POST JSON, returning ``(status, body)`` even for 4xx auth errors."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = _body_of(exc)
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"error": body or f"HTTP {exc.code}"}


def post_form(
    url: str,
    fields: dict[str, str],
    *,
    timeout: float = 30.0,
) -> tuple[int, dict]:
    """POST ``application/x-www-form-urlencoded`` (OAuth endpoints)."""
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = _body_of(exc)
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"error": body or f"HTTP {exc.code}"}
