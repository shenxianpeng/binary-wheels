"""Download upstream files and check them against pinned sha256 hashes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from . import PROJECT_URL, __version__

USER_AGENT = f"binwheel/{__version__} (+{PROJECT_URL})"


class ChecksumError(RuntimeError):
    pass


def _request(url: str, accept: str | None = None) -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def read_url(url: str) -> bytes:
    with urllib.request.urlopen(_request(url), timeout=60) as resp:
        return resp.read()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> Path:
    """Download url to dest (atomic: a partial download never sits at dest)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(_request(url), timeout=300) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out, 1 << 20)
    tmp.replace(dest)
    return dest


def fetch_verified(url: str, dest: Path, expected_sha256: str) -> Path:
    """Return dest, downloading it if missing; always verify the hash."""
    if not dest.exists() or sha256_file(dest) != expected_sha256:
        download(url, dest)
    actual = sha256_file(dest)
    if actual != expected_sha256:
        dest.unlink()
        raise ChecksumError(f"sha256 mismatch for {url}\n  expected {expected_sha256}\n  got      {actual}")
    return dest


def parse_checksums(text: str) -> dict[str, str]:
    """Parse `sha256sum` style output: '<hex>  <name>' (a leading '*' marks binary mode)."""
    result = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and len(parts[0]) == 64:
            result[parts[1].lstrip("*")] = parts[0].lower()
    return result


def latest_release_tag(repo: str) -> str:
    data = json.loads(read_url(f"https://api.github.com/repos/{repo}/releases/latest"))
    return data["tag_name"]


def _safe_member(name: str) -> str:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"refusing unsafe archive member {name!r}")
    return str(p)


def read_member(archive: Path, member: str) -> bytes:
    """Read one file out of a .tar.gz/.tgz/.zip archive, or the file itself if member is ''."""
    if member == "":
        return archive.read_bytes()
    member = _safe_member(member)
    name = archive.name
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            return zf.read(member)
    if name.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar")):
        with tarfile.open(archive) as tf:
            info = tf.getmember(member)
            if not info.isfile():
                raise ValueError(f"{member!r} in {name} is not a regular file")
            f = tf.extractfile(info)
            assert f is not None
            return f.read()
    raise ValueError(f"don't know how to read members of {name!r}")
