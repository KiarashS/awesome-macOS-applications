#!/usr/bin/env python3
"""Build the data behind awesome-macOS-applications.

Reads the repositories in a public GitHub star list, enriches each one with
metadata from the REST API, sorts them into categories and tags, and writes:

  site/data/apps.json   the payload the website loads
  data/list-cache.json  the last known good set of repo names
  README.md             the same list as a plain markdown awesome-list

Run it with no arguments; everything is configurable through the environment:

  GITHUB_USER   the account that owns the star list      (default KiarashS)
  LIST_SLUG     the star list slug                       (default app)
  GITHUB_TOKEN  optional, raises the API rate limit and is used by CI

Offline or when GitHub's HTML changes shape, --offline falls back to the
cached repo names so the metadata refresh still runs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from appicons import IconStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SITE = ROOT / "site"
SITE_DATA = SITE / "data" / "apps.json"
LIST_CACHE = ROOT / "data" / "list-cache.json"
ICON_CACHE = ROOT / "data" / "icon-cache.json"
README = ROOT / "README.md"

GITHUB_USER = os.environ.get("GITHUB_USER", "KiarashS")
LIST_SLUG = os.environ.get("LIST_SLUG", "app")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""

LIST_URL = f"https://github.com/stars/{GITHUB_USER}/lists/{LIST_SLUG}"
API = "https://api.github.com"
UA = "awesome-macOS-applications-builder"

# A rebuild that loses most of the list is a scraping failure, not an edit.
MIN_KEEP_RATIO = 0.6


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def http_get(url: str, accept: str = "application/vnd.github+json", tries: int = 4) -> bytes:
    headers = {"User-Agent": UA, "Accept": accept}
    if TOKEN and url.startswith(API):
        headers["Authorization"] = f"Bearer {TOKEN}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"

    last: Exception | None = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 404:
                raise
            # Secondary rate limits and 5xx are worth waiting out.
            if exc.code in (403, 429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise
    raise RuntimeError(f"GET {url} failed: {last}")


def api_json(path: str):
    return json.loads(http_get(f"{API}{path}"))


# --------------------------------------------------------------------------
# Step 1 — which repositories are in the list
# --------------------------------------------------------------------------

# Every repo row on a star list page carries the repo's own path in a handful
# of places. Collecting several shapes and intersecting them with "looks like
# owner/repo" survives the small markup changes GitHub makes now and then.
HREF_PATTERNS = (
    re.compile(r'href="/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)"[^>]*itemprop="name codeRepository"'),
    re.compile(r'<h3>\s*<a[^>]+href="/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)"'),
    re.compile(r'href="/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/(?:stargazers|forks)"'),
    re.compile(r'id="user-list-repo-\d+"[^>]*>.*?href="/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)"', re.S),
)

NOT_A_REPO_OWNER = {"stars", "orgs", "topics", "sponsors", "features", "collections", "apps"}


def parse_list_page(html: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in HREF_PATTERNS:
        for name in pattern.findall(html):
            owner = name.split("/")[0]
            if owner in NOT_A_REPO_OWNER or name.endswith(".git"):
                continue
            if name not in seen:
                seen.add(name)
                found.append(name)
    return found


def scrape_list() -> list[str]:
    """Walk every page of the star list and return owner/repo names in order."""
    names: list[str] = []
    seen: set[str] = set()
    page = 1
    while page <= 25:  # a hard stop; no star list is 750 repos long
        url = LIST_URL if page == 1 else f"{LIST_URL}?page={page}"
        html = http_get(url, accept="text/html").decode("utf-8", "replace")
        found = parse_list_page(html)
        fresh = [n for n in found if n not in seen]
        if not fresh:
            break
        for name in fresh:
            seen.add(name)
            names.append(name)
        print(f"  page {page}: +{len(fresh)} (total {len(names)})", file=sys.stderr)
        if 'rel="next"' not in html and "next_page" not in html:
            break
        page += 1
    return names


def load_cache() -> list[str]:
    if LIST_CACHE.exists():
        return json.loads(LIST_CACHE.read_text())["repos"]
    return []


def resolve_repo_names(offline: bool) -> list[str]:
    cached = load_cache()
    if offline:
        if not cached:
            sys.exit("--offline was requested but data/list-cache.json is empty")
        print(f"offline: using {len(cached)} cached repo names", file=sys.stderr)
        return cached

    print(f"reading star list {LIST_URL}", file=sys.stderr)
    try:
        names = scrape_list()
    except Exception as exc:  # network or markup trouble
        if not cached:
            sys.exit(f"could not read the star list and there is no cache: {exc}")
        print(f"warning: star list unreadable ({exc}); falling back to cache", file=sys.stderr)
        return cached

    if not names:
        if not cached:
            sys.exit("the star list returned no repositories and there is no cache")
        print("warning: star list parsed to zero repos; falling back to cache", file=sys.stderr)
        return cached

    if cached and len(names) < len(cached) * MIN_KEEP_RATIO:
        print(
            f"warning: only found {len(names)} repos where the cache has {len(cached)};"
            " treating that as a parse failure and keeping the cache",
            file=sys.stderr,
        )
        return cached

    return names


# --------------------------------------------------------------------------
# Step 2 — metadata
# --------------------------------------------------------------------------

# Leading decoration: emoji, dingbats, arrows, variation selectors and any
# separator people put after them ("\U0001F680 - A launcher" -> "A launcher").
EMOJI_LEAD = re.compile(
    "^(?:[\\s\\u2000-\\u2BFF\\u3000-\\u33FF\\uFE0F\\u200D"
    "\\U0001F000-\\U0001FAFF]|[-|:])+"
)


def clean_description(text: str | None) -> str:
    if not text:
        return ""
    text = EMOJI_LEAD.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(" -–—·|")
    if not text:
        return ""
    return text[0].upper() + text[1:]


def shorten(text: str, limit: int = 190) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return cut + "…"


def fetch_repo(full_name: str) -> dict | None:
    try:
        raw = api_json(f"/repos/{full_name}")
    except urllib.error.HTTPError as exc:
        print(f"  ! {full_name}: HTTP {exc.code}, skipped", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  ! {full_name}: {exc}, skipped", file=sys.stderr)
        return None

    owner = raw.get("owner") or {}
    license_info = raw.get("license") or {}
    return {
        "full_name": raw.get("full_name", full_name),
        "name": raw.get("name", full_name.split("/")[-1]),
        "owner": owner.get("login", full_name.split("/")[0]),
        "avatar": owner.get("avatar_url") or f"https://github.com/{full_name.split('/')[0]}.png?size=160",
        "url": raw.get("html_url", f"https://github.com/{full_name}"),
        "homepage": (raw.get("homepage") or "").strip(),
        "description": clean_description(raw.get("description")),
        "language": raw.get("language") or "",
        "topics": [t for t in (raw.get("topics") or []) if t],
        "stars": raw.get("stargazers_count", 0),
        "forks": raw.get("forks_count", 0),
        "license": license_info.get("spdx_id") if license_info.get("spdx_id") not in (None, "NOASSERTION") else "",
        "archived": bool(raw.get("archived")),
        "default_branch": raw.get("default_branch") or "main",
        "pushed_at": raw.get("pushed_at") or "",
        "created_at": raw.get("created_at") or "",
    }


def fetch_tree(full_name: str, ref: str):
    """Recursive git tree for a ref: (entries, was_truncated)."""
    data = api_json(f"/repos/{full_name}/git/trees/{urllib.parse.quote(ref)}?recursive=1")
    return data.get("tree") or [], bool(data.get("truncated"))


def fetch_bytes(url: str) -> bytes:
    return http_get(url, accept="*/*")


# --------------------------------------------------------------------------
# Step 3 — categorise and tag
# --------------------------------------------------------------------------


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


TAXONOMY = load_json(SCRIPTS / "taxonomy.json", {})
OVERRIDES = load_json(SCRIPTS / "overrides.json", {})


def word_re(keyword: str) -> re.Pattern:
    """Match a keyword on word boundaries, treating - _ and space alike."""
    parts = [re.escape(p) for p in re.split(r"[\s_-]+", keyword.lower()) if p]
    if not parts:
        return re.compile(r"(?!x)x")
    return re.compile(r"(?<![a-z0-9])" + r"[\s_\-]*".join(parts) + r"(?![a-z0-9])")


_KEYWORD_CACHE: dict[str, re.Pattern] = {}


def matches(haystack: str, keywords: list[str]) -> bool:
    for keyword in keywords:
        pattern = _KEYWORD_CACHE.get(keyword)
        if pattern is None:
            pattern = _KEYWORD_CACHE[keyword] = word_re(keyword)
        if pattern.search(haystack):
            return True
    return False


def build_haystack(repo: dict) -> str:
    name = re.sub(r"[.\-_]+", " ", repo["name"])
    # Split CamelCase so "DockDoor" also reads as "dock door".
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    pieces = [name, repo["description"], " ".join(repo["topics"]), repo["language"]]
    return " ".join(pieces).lower()


def categorise(repo: dict, haystack: str) -> dict:
    for rule in TAXONOMY["categories"]:
        if matches(haystack, rule["keywords"]):
            return rule
    fallback = TAXONOMY.get("fallback_category", "system")
    for rule in TAXONOMY["categories"]:
        if rule["id"] == fallback:
            return rule
    return TAXONOMY["categories"][-1]


def tag(repo: dict, haystack: str, category: dict) -> list[str]:
    tags: list[str] = []
    for rule in TAXONOMY["tags"]:
        if matches(haystack, rule["keywords"]) and rule["tag"] not in tags:
            tags.append(rule["tag"])

    language_tag = TAXONOMY.get("language_tags", {}).get(repo["language"])
    if language_tag and language_tag not in tags:
        tags.append(language_tag)

    if repo["archived"] and "Archived" not in tags:
        tags.insert(0, "Archived")

    if not tags:
        tags.append(category["label"])

    return tags[: TAXONOMY.get("max_tags", 6)]


def apply_overrides(app: dict) -> dict:
    override = OVERRIDES.get(app["full_name"])
    if not override:
        return app
    if override.get("category"):
        app["category"] = override["category"]
    if override.get("description"):
        app["description"] = override["description"]
    if override.get("tags"):
        app["tags"] = list(dict.fromkeys(override["tags"]))[: TAXONOMY.get("max_tags", 6)]
    if override.get("add_tags"):
        merged = app["tags"] + [t for t in override["add_tags"] if t not in app["tags"]]
        app["tags"] = merged[: TAXONOMY.get("max_tags", 6)]
    return app


def describe(repo: dict) -> str:
    if repo["description"]:
        return shorten(repo["description"])
    if repo["topics"]:
        return "A macOS app for " + ", ".join(repo["topics"][:3]) + "."
    return "No description on GitHub — open the repository to see what it does."


# --------------------------------------------------------------------------
# Step 4 — write everything out
# --------------------------------------------------------------------------


def build_payload(apps: list[dict]) -> dict:
    used = {app["category"] for app in apps}
    categories = [
        {
            "id": rule["id"],
            "label": rule["label"],
            "icon": rule["icon"],
            "color": rule.get("color", "#0a84ff"),
            "blurb": rule["blurb"],
            "count": sum(1 for a in apps if a["category"] == rule["id"]),
        }
        for rule in TAXONOMY["categories"]
        if rule["id"] in used
    ]

    counts: dict[str, int] = {}
    for app in apps:
        for name in app["tags"]:
            counts[name] = counts.get(name, 0) + 1
    tags = [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"user": GITHUB_USER, "list": LIST_SLUG, "url": LIST_URL},
        "totals": {
            "apps": len(apps),
            "categories": len(categories),
            "tags": len(tags),
            "stars": sum(a["stars"] for a in apps),
        },
        "categories": categories,
        "tags": tags,
        "apps": apps,
    }


CATEGORY_EMOJI = {
    "notch": "🌗", "clipboard": "📋", "packages": "📦", "virtualization": "🧊",
    "capture": "📸", "downloads": "⬇️", "reading": "📚", "ai": "✨",
    "launchers": "🔍", "focus": "⏱️", "windows": "🪟", "menubar": "📊",
    "terminal": "⌨️", "security": "🔒", "network": "🌐", "input": "🖱️",
    "media": "🎬", "notes": "📝", "files": "🗂️", "system": "⚙️",
    "devtools": "🛠️", "fun": "🎈", "web": "🧩",
}


def write_readme(payload: dict, site_url: str) -> None:
    apps = payload["apps"]
    lines = [
        "# awesome-macOS-applications",
        "",
        "> A tagged, categorised browser for the macOS apps I keep an eye on —",
        f"> generated nightly from my [**{LIST_SLUG}** star list]({LIST_URL}) on GitHub.",
        "",
        f"**[Browse it as a macOS desktop →]({site_url})**",
        "",
        f"`{payload['totals']['apps']} apps` · `{payload['totals']['categories']} categories` · "
        f"`{payload['totals']['tags']} tags` · `{payload['totals']['stars']:,} stars`",
        "",
        f"Last updated {payload['generated_at'][:10]}. Nothing here is edited by hand: star a repo",
        "into the list and it shows up on the next run.",
        "",
        "## Contents",
        "",
    ]

    for category in payload["categories"]:
        emoji = CATEGORY_EMOJI.get(category["id"], "\u2022")
        lines.append(f"- {emoji} [{category['label']}](#cat-{category['id']}) ({category['count']})")
    lines.append("")

    for category in payload["categories"]:
        emoji = CATEGORY_EMOJI.get(category["id"], "\u2022")
        # An explicit anchor, because GitHub's own slug for an emoji-led
        # heading picks up a leading hyphen and is easy to get wrong.
        lines += [f'<a id="cat-{category["id"]}"></a>', "",
                  f"## {emoji} {category['label']}", "", f"_{category['blurb']}_", ""]
        rows = [a for a in apps if a["category"] == category["id"]]
        rows.sort(key=lambda a: -a["stars"])
        for app in rows:
            tags = " ".join(f"`{t}`" for t in app["tags"])
            stars = f"★ {app['stars']:,}" if app["stars"] else "★ 0"
            lines.append(
                f"- **[{app['name']}]({app['url']})** — {app['description']}  \n"
                f"  <sub>{stars} · {app['owner']}{' · ' + app['language'] if app['language'] else ''} · {tags}</sub>"
            )
        lines.append("")

    lines += [
        "## How this is built",
        "",
        f"1. `scripts/build.py` reads every page of the [star list]({LIST_URL}).",
        "2. Each repository is looked up through the GitHub REST API for its description,",
        "   topics, language and star count.",
        "3. `scripts/taxonomy.json` turns that into one category and up to six tags;",
        "   `scripts/overrides.json` pins anything the rules get wrong.",
        "4. The result is written to `site/data/apps.json` and to this README.",
        "",
        "The workflow in `.github/workflows/update.yml` runs it every night at",
        "**03:00 Asia/Tehran** (23:30 UTC) and redeploys the site.",
        "",
        "```bash",
        "python3 scripts/build.py            # refresh everything",
        "python3 scripts/build.py --offline  # skip scraping, reuse data/list-cache.json",
        "```",
        "",
        "## License",
        "",
        "The tooling and the site are MIT. Every application listed belongs to its own",
        "authors and carries its own license.",
        "",
    ]

    README.write_text("\n".join(lines))


def check_only() -> int:
    """Cheap membership poll: has the star list gained or lost repositories?

    Prints 'changed' or 'unchanged' on stdout for a workflow to branch on.
    A scrape that fails prints 'unchanged' on purpose — a network blip should
    not kick off a rebuild, and the daily full run will catch up regardless.
    """
    cached = load_cache()
    try:
        found = scrape_list()
    except Exception as exc:
        print(f"star list unreadable ({exc}); reporting unchanged", file=sys.stderr)
        print("unchanged")
        return 0

    if not found:
        print("star list parsed to zero repos; reporting unchanged", file=sys.stderr)
        print("unchanged")
        return 0

    added = [n for n in found if n not in set(cached)]
    removed = [n for n in cached if n not in set(found)]

    if cached and len(found) < len(cached) * MIN_KEEP_RATIO:
        print(f"only found {len(found)} of {len(cached)} cached repos; treating as a "
              "parse failure and reporting unchanged", file=sys.stderr)
        print("unchanged")
        return 0

    for name in added:
        print(f"  + {name}", file=sys.stderr)
    for name in removed:
        print(f"  - {name}", file=sys.stderr)

    print("changed" if (added or removed) else "unchanged")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="use data/list-cache.json instead of scraping")
    parser.add_argument("--skip-icons", action="store_true", help="leave site/icons alone")
    parser.add_argument("--check-only", action="store_true",
                        help="print 'changed' or 'unchanged' and exit, without rebuilding")
    parser.add_argument("--site-url", default=os.environ.get("SITE_URL", f"https://{GITHUB_USER.lower()}.github.io/awesome-macOS-applications/"))
    args = parser.parse_args()

    if args.check_only:
        return check_only()

    names = resolve_repo_names(args.offline)
    print(f"resolving metadata for {len(names)} repositories", file=sys.stderr)

    apps: list[dict] = []
    kept_names: list[str] = []
    branches: list[str] = []
    for index, full_name in enumerate(names, 1):
        repo = fetch_repo(full_name)
        if repo is None:
            continue
        kept_names.append(repo["full_name"])
        branches.append(repo["default_branch"])
        haystack = build_haystack(repo)
        category = categorise(repo, haystack)
        app = {
            "full_name": repo["full_name"],
            "name": repo["name"],
            "owner": repo["owner"],
            "avatar": repo["avatar"],
            "icon": "",
            "url": repo["url"],
            "homepage": repo["homepage"],
            "description": describe(repo),
            "category": category["id"],
            "tags": tag(repo, haystack, category),
            "topics": repo["topics"][:8],
            "language": repo["language"],
            "stars": repo["stars"],
            "forks": repo["forks"],
            "license": repo["license"],
            "archived": repo["archived"],
            "pushed_at": repo["pushed_at"],
        }
        apps.append(apply_overrides(app))
        if index % 20 == 0:
            print(f"  {index}/{len(names)}", file=sys.stderr)

    if not apps:
        sys.exit("no repositories resolved; refusing to overwrite existing data")

    branch_of = dict(zip([a["full_name"] for a in apps], branches))
    apps.sort(key=lambda a: (-a["stars"], a["name"].lower()))

    if not args.skip_icons:
        print("resolving application icons", file=sys.stderr)
        icons = IconStore(SITE, ICON_CACHE, fetch_tree, fetch_bytes,
                          log=lambda m: print(m, file=sys.stderr))
        for app in apps:
            app["icon"] = icons.resolve(app["full_name"], branch_of[app["full_name"]]) or ""
        pruned = icons.prune({a["full_name"] for a in apps})
        icons.save()
        stats = icons.stats
        print(f"  icons: {stats['downloaded']} downloaded, {stats['cached']} unchanged, "
              f"{stats['none']} none found, {stats['failed']} failed, {pruned} pruned",
              file=sys.stderr)

    payload = build_payload(apps)

    SITE_DATA.parent.mkdir(parents=True, exist_ok=True)
    SITE_DATA.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    LIST_CACHE.parent.mkdir(parents=True, exist_ok=True)
    LIST_CACHE.write_text(
        json.dumps(
            {"updated_at": payload["generated_at"], "source": LIST_URL, "repos": kept_names},
            indent=2,
        )
        + "\n"
    )

    write_readme(payload, args.site_url)

    print(
        f"wrote {len(apps)} apps across {len(payload['categories'])} categories "
        f"and {len(payload['tags'])} tags",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
