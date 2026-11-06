"""Direct first-party Chinese editorial RSS signals (free, no scraping/login).

Editorial coverage is NOT consumer demand, wholesale availability, or sales.
Store only publication-dated articles with explicit product nouns; track feed
health independently so a failed/blocked feed cannot imply missing demand.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

from radar.candidate_discovery import ensure_candidates_table, extract_terms, extract_unmapped_terms
from radar.core import DEFAULT_DB, connection, iso_dt, utcnow

LOG = logging.getLogger(__name__)
MAX_FEED_BYTES = 1_500_000
MAX_ARTICLES = 120
MAX_AGE_DAYS = 21


@dataclass(frozen=True)
class Feed:
    name: str
    rss_url: str
    domain: str
    kind: str = "editorial"


# Endpoints documented by their publishers. 36Kr may return a challenge page:
# that MUST be recorded as a failure, never treated as an empty feed.
FEEDS = (
    Feed("IT之家", "https://www.ithome.com/rss/", "ithome.com"),
    Feed("爱范儿", "https://www.ifanr.com/feed", "ifanr.com"),
    Feed("36氪", "https://36kr.com/feed-article", "36kr.com"),
    Feed("什么值得买 · 好价", "https://feed.smzdm.com/", "smzdm.com", "consumer_deals"),
    Feed("什么值得买 · 评测", "https://post.smzdm.com/feed", "smzdm.com", "reviews"),
)


def ensure_china_feed_tables(con) -> None:
    con.executescript("""
      CREATE TABLE IF NOT EXISTS china_direct_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        publisher TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        title_fingerprint TEXT NOT NULL UNIQUE,
        published_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        matched_labels TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_china_direct_date ON china_direct_articles(published_at);
      CREATE TABLE IF NOT EXISTS china_retail_quotes (
        url TEXT PRIMARY KEY, publisher TEXT NOT NULL, title TEXT NOT NULL,
        price_cny REAL NOT NULL, published_at TEXT NOT NULL, observed_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS china_feed_checks (
        publisher TEXT PRIMARY KEY,
        feed_url TEXT NOT NULL,
        checked_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('ok', 'error')),
        fetched INTEGER NOT NULL DEFAULT 0,
        relevant INTEGER NOT NULL DEFAULT 0,
        new_articles INTEGER NOT NULL DEFAULT 0,
        error_code TEXT
      );
    """)


def _node_text(node, names) -> str:
    for name in names:
        child = node.find(name)
        if child is not None and child.text and child.text.strip():
            return child.text.strip()
    return ""


def _timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return None  # publication time must include timezone
    return dt.astimezone(timezone.utc)


def clean_article_url(url: str, domain: str) -> str | None:
    """Only original publisher HTTPS article URLs; strip common tracking params."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        hosts = {domain, "www." + domain}
        if domain == "smzdm.com":
            hosts |= {"post.smzdm.com", "faxian.smzdm.com", "fx.smzdm.com", "show.smzdm.com"}
        if (parts.scheme != "https" or parts.username or parts.password or parts.port
                or host not in hosts):
            return None
        if not parts.path or parts.path == "/":
            return None
        params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                  if not k.lower().startswith("utm_") and k.lower() not in {"spm", "from", "source", "ref"}]
        return urlunsplit(("https", host, parts.path, urlencode(params), ""))
    except ValueError:
        return None


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title).casefold()
    return "".join(c for c in text if unicodedata.category(c)[0] in ("L", "N"))


def price_from_deal_title(title: str) -> float | None:
    """Conservative CNY retail promo quote, never wholesale or a completed sale.

Only accept one explicit price, without coupon, multi-buy or range caveats.
    """
    if any(term in title for term in ("用券", "需买", "满减", "满折", "元起", "券后", "拍下", "起售", "折后", "元/", "元每", "凑单")):
        return None
    found = re.findall(r"(?<![0-9])(\d{1,5}(?:\.\d{1,2})?)\s*元(?![0-9])", title)
    if len(found) != 1:
        return None
    amount = float(found[0])
    return amount if 1 <= amount <= 20_000 else None


def parse_direct_feed(content: bytes, domain: str) -> list[dict]:
    """RSS 2.0 and Atom, accepting only dated publisher-hosted article links."""
    if len(content) > MAX_FEED_BYTES:
        raise ValueError("Feed exceeds byte limit")
    root = ET.fromstring(content)
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag == "rss":
        nodes = root.findall("./channel/item")
        atom = False
    elif tag == "feed":
        nodes = root.findall("{http://www.w3.org/2005/Atom}entry") or root.findall("entry")
        atom = True
    else:
        raise ValueError("Document is not RSS or Atom")
    result = []
    for item in nodes[:MAX_ARTICLES]:
        if atom:
            title = _node_text(item, ["{http://www.w3.org/2005/Atom}title", "title"])
            published = _node_text(item, ["{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated", "published", "updated"])
            link = next((x.attrib.get("href", "") for x in item.findall("{http://www.w3.org/2005/Atom}link") + item.findall("link")
                        if x.attrib.get("rel", "alternate") == "alternate"), "")
        else:
            title = _node_text(item, ["title"])
            published = _node_text(item, ["pubDate", "{http://purl.org/dc/elements/1.1/}date"])
            link = _node_text(item, ["link"])
        dt = _timestamp(published)
        article_url = clean_article_url(link, domain)
        if not title or not dt or not article_url or not normalize_title(title):
            continue
        result.append({"title": re.sub(r"\s+", " ", title).strip()[:450],
                       "url": article_url, "published_at": dt})
    return result


def collect_direct_china(db: Path = DEFAULT_DB, session=None, timeout: int = 12,
                         now: datetime | None = None, feeds=FEEDS) -> dict:
    now = now or utcnow()
    earliest = now - timedelta(days=MAX_AGE_DAYS)
    result = {"feeds_checked": 0, "successful": 0, "articles_seen": 0,
              "product_headlines": 0, "new_articles": 0, "new_leads": 0, "errors": []}
    own_session = session is None
    session = session or requests.Session()
    if own_session:
        session.headers.update({"User-Agent": "ChinaEuropeTrendRadar/0.7 (+https://github.com/mageburx/china-to-europe-trend-radar)",
                                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
    con = connection(db)
    ensure_candidates_table(con)
    ensure_china_feed_tables(con)
    try:
        for feed in feeds:
            result["feeds_checked"] += 1
            seen = relevant = added = 0
            code = None
            status = "ok"
            try:
                response = session.get(feed.rss_url, timeout=timeout, allow_redirects=False)
                if response.status_code != 200:
                    raise ValueError("HTTP_" + str(response.status_code))
                body = response.content
                items = parse_direct_feed(body, feed.domain)
                for entry in items:
                    if not earliest <= entry["published_at"] <= now + timedelta(hours=2):
                        continue
                    seen += 1
                    title = entry["title"]
                    matches = list(dict.fromkeys(extract_terms(title) + extract_unmapped_terms(title)))
                    if not matches:
                        continue
                    relevant += 1
                    key = hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()
                    cur = con.execute("""INSERT OR IGNORE INTO china_direct_articles
                       (publisher,title,url,title_fingerprint,published_at,ingested_at,matched_labels)
                       VALUES (?,?,?,?,?,?,?)""",
                       (feed.name, title, entry["url"], key, iso_dt(entry["published_at"]),
                        iso_dt(now), ", ".join(label for label, _ in matches)))
                    if not cur.rowcount:
                        continue  # duplicate article/title, not an independent signal
                    added += 1
                    if feed.kind == "consumer_deals":
                        amount = price_from_deal_title(title)
                        if amount is not None:
                            con.execute("""INSERT OR IGNORE INTO china_retail_quotes
                              (url,publisher,title,price_cny,published_at,observed_at)
                              VALUES (?,?,?,?,?,?)""",
                              (entry["url"],feed.name,title,amount,
                               iso_dt(entry["published_at"]),iso_dt(now)))
                    for label, phrase in matches:
                        c = con.execute("""INSERT OR IGNORE INTO candidate_leads
                            (label_pt,matched_cn,category,source,title,url,published_at,detected_at)
                            VALUES (?,?,?,?,?,?,?,?)""",
                            (label, phrase, "Consumo / Produtos", "RSS direto / " + feed.name,
                             title, entry["url"], iso_dt(entry["published_at"]), iso_dt(now)))
                        result["new_leads"] += int(bool(c.rowcount))
                result["successful"] += 1
                result["articles_seen"] += seen
                result["product_headlines"] += relevant
                result["new_articles"] += added
            except (requests.RequestException, ET.ParseError, ValueError, TypeError) as exc:
                status = "error"
                code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("HTTP_") else type(exc).__name__
                code = code[:60]
                result["errors"].append(feed.name + ": " + code)
                LOG.warning("Feed %s indisponível: %s", feed.name, code)
            con.execute("""INSERT INTO china_feed_checks
                (publisher,feed_url,checked_at,status,fetched,relevant,new_articles,error_code)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(publisher) DO UPDATE SET
                feed_url=excluded.feed_url,checked_at=excluded.checked_at,
                status=excluded.status,fetched=excluded.fetched,relevant=excluded.relevant,
                new_articles=excluded.new_articles,error_code=excluded.error_code""",
                (feed.name, feed.rss_url, iso_dt(now), status, seen, relevant, added, code))
            con.commit()
    finally:
        con.close()
        if own_session:
            session.close()
    return result


def direct_feed_summary(con, now=None) -> dict:
    ensure_china_feed_tables(con)
    now = now or utcnow()
    checks = [dict(row) for row in con.execute("SELECT * FROM china_feed_checks ORDER BY publisher")]
    for item in checks:
        checked = datetime.fromisoformat(item["checked_at"])
        item["fresh"] = item["status"] == "ok" and checked >= now - timedelta(hours=30)
    articles = [dict(row) for row in con.execute("""SELECT publisher,title,url,published_at,matched_labels
             FROM china_direct_articles WHERE published_at >= ? AND published_at <= ?
             ORDER BY published_at DESC LIMIT 80""",
             (iso_dt(now - timedelta(days=21)), iso_dt(now)))]
    quotes = [dict(row) for row in con.execute("""SELECT publisher,title,url,price_cny,published_at
                FROM china_retail_quotes WHERE published_at>=? AND published_at<=?
                ORDER BY published_at DESC LIMIT 40""",
                (iso_dt(now-timedelta(days=14)),iso_dt(now)))]
    return {"feeds": checks, "articles": articles, "quotes": quotes}


def main() -> None:
    ap = argparse.ArgumentParser(description="Pesquisa gratuita em RSS chineses originais; não mede vendas")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--timeout", type=int, default=12)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(collect_direct_china(args.db, timeout=args.timeout))


if __name__ == "__main__":
    main()
