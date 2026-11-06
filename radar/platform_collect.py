"""Read public product/post metadata from curated Chinese-platform URLs.

Not a platform-wide crawler. No login, CAPTCHA bypass, private metrics or sales inference.
First/last check timestamps are not publication timestamps and never boost the trend score.
"""
from __future__ import annotations

import argparse
import csv
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urljoin

import requests

from radar.core import DEFAULT_DB, ROOT, connection, iso_dt, seed_catalog, utcnow

LINKS = ROOT / "config" / "platform_links.csv"
ALLOWED_HOSTS = {
    "www.douyin.com": "Douyin", "v.douyin.com": "Douyin",
    "www.xiaohongshu.com": "Xiaohongshu", "m.xiaohongshu.com": "Xiaohongshu",
    "xhslink.com": "Xiaohongshu",
    "detail.1688.com": "1688", "m.1688.com": "1688", "www.1688.com": "1688",
    "item.taobao.com": "Taobao", "item.m.taobao.com": "Taobao",
}
SKIP_TITLES = {
    "抖音", "douyin", "小红书", "xiaohongshu", "1688", "淘宝", "taobao",
    "just a moment...", "access denied", "verify you are human",
}
MAX_BYTES = 250_000


def platform_for(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or parts.port or host not in ALLOWED_HOSTS:
        raise ValueError(f"URL não permitida: {url[:120]}")
    return ALLOWED_HOSTS[host]


class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in ("og:title", "twitter:title", "description", "og:description"):
                value = (attrs.get("content") or "").strip()
                if value:
                    self.meta.setdefault(key, value)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def extract_title(html: str) -> str | None:
    parser = MetaParser()
    parser.feed(html)
    title = (parser.meta.get("og:title") or parser.meta.get("twitter:title") or parser.title).strip()
    title = " ".join(title.split())
    if not title or title.lower() in SKIP_TITLES or len(title) < 10:
        return None
    low = title.lower()
    if any(bad in low for bad in ("captcha", "请登录", "登录后", "人机验证", "安全验证", "sign in to view")):
        return None
    return title[:500]


def fetch_public_title(session: requests.Session, url: str, timeout: int = 10) -> tuple[str, str] | None:
    """Fail closed on non-allowlisted redirects, login walls and missing metadata."""
    for _ in range(4):
        platform_for(url)
        with session.get(url, timeout=timeout, allow_redirects=False, stream=True) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                redirected = urljoin(url, response.headers.get("Location", ""))
                platform_for(redirected)
                url = redirected
                continue
            response.raise_for_status()
            ctype = response.headers.get("content-type", "").lower()
            if "html" not in ctype:
                return None
            content = bytearray()
            for chunk in response.iter_content(8192):
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    return None
            encoding = response.encoding or "utf-8"
            title = extract_title(content.decode(encoding, errors="replace"))
            return (title, url) if title else None
    return None


def collect_platform_links(db: Path = DEFAULT_DB, links: Path = LINKS, timeout: int = 10) -> dict:
    """Persist curated public-page checks separately from scored market observations.

    Post publication dates, view counts and sales are deliberately NOT inferred.
    """
    if not links.exists():
        raise FileNotFoundError(links)
    con = connection(db)
    seed_catalog(con)
    valid_products = {row[0] for row in con.execute("SELECT id FROM products")}
    result = {"checked": 0, "new_links": 0, "found": 0, "skipped": 0, "errors": []}
    session = requests.Session()
    session.headers.update({"User-Agent": "ChinaEuropeTrendRadar/0.3 (public-metadata research)",
                            "Accept": "text/html"})
    try:
        with links.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"product_id", "url"}.issubset(reader.fieldnames or []):
                raise ValueError("CSV requer colunas product_id,url")
            for line_number, row in enumerate(reader, start=2):
                product_id = (row.get("product_id") or "").strip()
                url = (row.get("url") or "").strip()
                if not product_id and not url:
                    continue
                result["checked"] += 1
                try:
                    if product_id not in valid_products:
                        raise ValueError(f"Produto desconhecido: {product_id}")
                    platform = platform_for(url)
                    fetched = fetch_public_title(session, url, timeout=timeout)
                    title = fetched[0] if fetched else None
                    final_url = fetched[1] if fetched else url
                    platform_for(final_url)
                    status = "found" if title else "not_public"
                    now = iso_dt(utcnow())
                    existing = con.execute("SELECT 1 FROM platform_evidence WHERE url=?", (final_url,)).fetchone()
                    con.execute("""INSERT INTO platform_evidence
                        (url, product_id, platform, title, status, first_checked_at, last_checked_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(url) DO UPDATE SET
                          title=excluded.title,
                          status=excluded.status,
                          last_checked_at=excluded.last_checked_at""",
                        (final_url, product_id, platform, title, status, now, now))
                    con.commit()
                    if not existing:
                        result["new_links"] += 1
                    if title:
                        result["found"] += 1
                    else:
                        result["skipped"] += 1
                except (ValueError, requests.RequestException, UnicodeError) as exc:
                    result["errors"].append(f"linha {line_number}: {str(exc)[:160]}")
    finally:
        con.close()
        session.close()
    return result


def main():
    parser = argparse.ArgumentParser(description="Ler metadados de URLs chineses autorizados e públicos")
    parser.add_argument("--links", type=Path, default=LINKS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()
    print(collect_platform_links(db=args.db, links=args.links, timeout=args.timeout))


if __name__ == "__main__":
    main()
