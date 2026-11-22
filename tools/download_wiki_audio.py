"""Download every audio file used on Warframe wiki pages, for building a voice dataset.

Usage:
    uv run python tools/download_wiki_audio.py <output folder> [page ...]

Defaults to the Ordis/Quotes page. Files that are already downloaded get skipped,
so it's safe to rerun if it stops partway.
"""

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://wiki.warframe.com/api.php"
HEADERS = {"User-Agent": "ordis-voice-dataset/0.1 (personal project)"}
AUDIO_EXTS = (".ogg", ".mp3", ".wav", ".flac", ".opus")
DELAY = 0.2

def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()

def audio_urls(page: str) -> dict[str, str]:
    """Map file name -> download URL for every audio file used on a page."""

    params = {
        "action": "query",
        "format": "json",
        "titles": page,
        "generator": "images",
        "gimlimit": "max",
        "prop": "imageinfo",
        "iiprop": "url",
    }
    found: dict[str, str] = {}

    # The API pages its results, keep following "continue" until it stops
    while True:
        data = json.loads(get(f"{API}?{urllib.parse.urlencode(params)}"))

        for info in data.get("query", {}).get("pages", {}).values():
            name = info["title"].removeprefix("File:")
            if name.lower().endswith(AUDIO_EXTS) and "imageinfo" in info:
                found[name] = info["imageinfo"][0]["url"]

        if "continue" not in data:
            return found
        params.update(data["continue"])

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    parser.add_argument("pages", nargs="*", default=["Ordis/Quotes"])
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}
    for page in args.pages:
        urls = audio_urls(page)
        print(f"{page}: {len(urls)} audio files")
        files.update(urls)

    downloaded = failed = 0
    for i, (name, url) in enumerate(sorted(files.items()), 1):
        target = args.output / name.replace(" ", "_")
        if target.exists():
            continue

        try:
            target.write_bytes(get(url))
            downloaded += 1
            print(f"[{i}/{len(files)}] {name}")
        except Exception as err:
            failed += 1
            print(f"[{i}/{len(files)}] failed {name}: {e}")

        time.sleep(DELAY)

    print(f"\nDownloaded {downloaded}, failed {failed}, {len(files) - downloaded - failed} already there.")

if __name__ == "__main__":
    main()
