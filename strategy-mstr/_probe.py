"""Probe strategy.com HTML/JS for metric API URLs."""
from __future__ import annotations

import json
import re
import ssl
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "cache"
OUT.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
CTX = ssl.create_default_context()


def fetch(url: str) -> tuple[int, str, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Referer": "https://www.strategy.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=40, context=CTX) as resp:
        return resp.status, resp.headers.get("content-type", ""), resp.read()


def main() -> None:
    status, ctype, raw = fetch("https://www.strategy.com/")
    html = raw.decode("utf-8", "replace")
    (OUT / "homepage.html").write_text(html, encoding="utf-8")
    print(f"homepage status={status} type={ctype} bytes={len(raw)}")

    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    print("scripts:")
    for src in scripts:
        print(" ", src)

    next_data = re.findall(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, flags=re.S
    )
    print("next_data blobs:", len(next_data))
    if next_data:
        (OUT / "next_data.json").write_text(next_data[0], encoding="utf-8")
        print(next_data[0][:800])

    build_ids = re.findall(r'buildId["\']?\s*[:=]\s*["\']([^"\']+)', html)
    print("buildIds:", build_ids[:8])

    urls = set(re.findall(r"https?://[^\s\"'<>]+", html))
    print("absolute urls sample:")
    for u in sorted(urls):
        low = u.lower()
        if any(k in low for k in ("api", "json", "graphql", "metric", "contentstack", "massive", "polygon", "cdn")):
            print(" ", u[:240])

    # Follow JS bundles looking for API paths
    api_hits: set[str] = set()
    for src in scripts:
        if src.startswith("/"):
            url = "https://www.strategy.com" + src
        elif src.startswith("http"):
            url = src
        else:
            continue
        try:
            _, _, body = fetch(url)
        except Exception as exc:  # noqa: BLE001
            print("js fail", src, exc)
            continue
        text = body.decode("utf-8", "replace")
        name = re.sub(r"[^a-zA-Z0-9._-]+", "_", src)[-80:]
        (OUT / f"js_{name}").write_bytes(body[:2_000_000])
        found = re.findall(
            r"[\"'](/[a-zA-Z0-9_./?-]{6,}|https?://[^\"']+)[\"']",
            text,
        )
        for hit in found:
            low = hit.lower()
            if any(
                k in low
                for k in (
                    "api",
                    "metric",
                    "mnav",
                    "bitcoin",
                    "history",
                    "json",
                    "graphql",
                    "download",
                    "timeseries",
                    "contentstack",
                    "massive",
                    "polygon",
                )
            ):
                api_hits.add(hit[:300])
        print(f"js {src} bytes={len(body)} hits_so_far={len(api_hits)}")

    print("api-like strings:")
    for hit in sorted(api_hits):
        print(" ", hit)

    (OUT / "api_hits.json").write_text(
        json.dumps(sorted(api_hits), indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
