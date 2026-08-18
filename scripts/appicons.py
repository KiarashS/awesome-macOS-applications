"""Find each repository's real application icon and pull it into the site.

GitHub has no "give me this project's icon" endpoint, so this walks the repo's
git tree and scores every image path against the places macOS projects
actually keep their icon — an .appiconset first, then the conventional
icon/logo names at the root or under assets. The winner is downloaded into
site/icons/ so the site carries its own images instead of hotlinking raw
GitHub, which also means the icons survive in an offline copy.

Icons are cached by blob SHA: a repo whose icon has not changed is not
downloaded again on the next run.
"""

from __future__ import annotations

import io
import json
import re
import urllib.parse
from pathlib import Path

ICON_DIR_NAME = "icons"
MAX_BLOB_BYTES = 3 * 1024 * 1024
ICON_PX = 128

# Paths that hold build junk, fixtures or someone else's artwork.
EXCLUDE = re.compile(
    r"(^|/)(node_modules|Pods|Carthage|vendor|third_party|\.build|build|dist|out|"
    r"test|tests|__tests__|fixtures|example|examples|sample|samples|screenshot|"
    r"screenshots|preview|previews|docs?/images/screenshots)(/|$)",
    re.I,
)

APPICONSET = re.compile(r"\.appiconset/", re.I)
ICONISH = re.compile(r"(^|/)(app[-_ ]?icon|icon|logo|mark|brand)[-_. ]?[^/]*$", re.I)
SIZE_IN_NAME = re.compile(r"(\d{2,4})\s*x\s*\1|[-_@](\d{2,4})(px)?(?=[-_.@])|[-_](\d{2,4})\.", re.I)

RASTER = (".png",)
VECTOR = (".svg",)


def _declared_size(path: str) -> int:
    """Largest pixel size mentioned in the filename, 0 if none."""
    best = 0
    for match in SIZE_IN_NAME.finditer(path):
        for group in match.groups():
            if group and group.isdigit():
                best = max(best, int(group))
    if re.search(r"@3x", path, re.I):
        best = max(best, 3 * (best or 60))
    elif re.search(r"@2x", path, re.I):
        best = max(best, 2 * (best or 60))
    return best


def score_path(path: str, size: int) -> int:
    """How much this path looks like the project's own app icon."""
    lower = path.lower()
    if EXCLUDE.search(lower):
        return -1
    if not lower.endswith(RASTER + VECTOR):
        return -1
    if size and size > MAX_BLOB_BYTES:
        return -1

    score = 0
    if APPICONSET.search(lower):
        score += 700
        # macOS icon sets go up to 1024; 128-512 is the sweet spot for the web.
        px = _declared_size(lower)
        score += {512: 120, 256: 140, 128: 130, 1024: 60}.get(px, 40 if px else 20)
    elif re.search(r"(^|/)(icon|appicon|app[-_]icon)\.(png|svg)$", lower):
        score += 500
    elif re.search(r"(^|/)logo\.(png|svg)$", lower):
        score += 420
    elif ICONISH.search(lower):
        score += 300
    else:
        return -1

    depth = lower.count("/")
    score -= depth * 12                      # a root-level icon beats a buried one
    if lower.endswith(VECTOR):
        score += 25                          # vectors scale perfectly
    if re.search(r"(^|/)(assets|resources|images|img|art|design|\.github)(/|$)", lower):
        score += 30
    if re.search(r"dark|night|inverted|template|mono|small|16|32", lower):
        score -= 60                          # variants, not the primary icon
    return score


def pick_icon(tree: list[dict]) -> dict | None:
    """Best (path, sha, size) from a recursive git tree listing."""
    best, best_score = None, 0
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        score = score_path(entry["path"], entry.get("size") or 0)
        if score > best_score:
            best, best_score = entry, score
    return best


def raw_url(full_name: str, ref: str, path: str) -> str:
    quoted = urllib.parse.quote(path)
    return f"https://raw.githubusercontent.com/{full_name}/{ref}/{quoted}"


def _shrink(data: bytes, suffix: str) -> bytes:
    """Square off and downscale a raster icon, if Pillow is available."""
    if suffix != ".png":
        return data
    try:
        from PIL import Image
    except ImportError:
        return data
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGBA")
        if max(img.size) > ICON_PX:
            img.thumbnail((ICON_PX, ICON_PX), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "PNG", optimize=True)
        return out.getvalue()
    except Exception:
        return data


class IconStore:
    """Resolves and caches one icon per repository."""

    def __init__(self, site_dir: Path, cache_path: Path, fetch_tree, fetch_bytes, log=print):
        self.dir = site_dir / ICON_DIR_NAME
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = cache_path
        self.cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        self.fetch_tree = fetch_tree      # (full_name, ref) -> (entries, truncated)
        self.fetch_bytes = fetch_bytes    # url -> bytes
        self.log = log
        self.stats = {"cached": 0, "downloaded": 0, "none": 0, "failed": 0}

    def _slug(self, full_name: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]", "_", full_name.replace("/", "__"))

    def resolve(self, full_name: str, ref: str) -> str | None:
        """Return a site-relative icon path, or None to fall back to the avatar."""
        try:
            entries, truncated = self.fetch_tree(full_name, ref)
        except Exception as exc:
            self.log(f"  icon: {full_name}: tree unavailable ({exc})")
            self.stats["failed"] += 1
            return self._keep_existing(full_name)

        if truncated:
            # A partial tree can easily miss the icon; trust the previous find.
            return self._keep_existing(full_name)

        pick = pick_icon(entries)
        if not pick:
            self.stats["none"] += 1
            self.cache.pop(full_name, None)
            return None

        cached = self.cache.get(full_name)
        suffix = ".svg" if pick["path"].lower().endswith(".svg") else ".png"
        rel = f"{ICON_DIR_NAME}/{self._slug(full_name)}{suffix}"

        if cached and cached.get("sha") == pick["sha"] and (self.dir.parent / rel).exists():
            self.stats["cached"] += 1
            return rel

        try:
            blob = self.fetch_bytes(raw_url(full_name, ref, pick["path"]))
        except Exception as exc:
            self.log(f"  icon: {full_name}: download failed ({exc})")
            self.stats["failed"] += 1
            return self._keep_existing(full_name)

        if not blob or len(blob) > MAX_BLOB_BYTES:
            self.stats["failed"] += 1
            return self._keep_existing(full_name)

        # Drop any stale file for this repo under the other extension.
        other = self.dir / f"{self._slug(full_name)}{'.png' if suffix == '.svg' else '.svg'}"
        if other.exists():
            other.unlink()

        (self.dir.parent / rel).write_bytes(_shrink(blob, suffix))
        self.cache[full_name] = {"sha": pick["sha"], "path": pick["path"], "file": rel}
        self.stats["downloaded"] += 1
        return rel

    def _keep_existing(self, full_name: str) -> str | None:
        cached = self.cache.get(full_name)
        if cached and (self.dir.parent / cached["file"]).exists():
            self.stats["cached"] += 1
            return cached["file"]
        return None

    def prune(self, keep: set[str]) -> int:
        """Delete icon files for repos that have left the list."""
        wanted = {self._slug(name) for name in keep}
        removed = 0
        for path in self.dir.iterdir():
            if path.is_file() and path.stem not in wanted:
                path.unlink()
                removed += 1
        for name in list(self.cache):
            if name not in keep:
                del self.cache[name]
        return removed

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, indent=2, sort_keys=True) + "\n")
