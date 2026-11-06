"""Opt-in read-only 1688 offer watchlist. No private quotes or invented wholesale prices.

Only explicit CNY Offer prices in public Product JSON-LD qualify; pages with
client-side-only prices are title_only. An observed price is not a negotiated
wholesale price, MOQ, landed cost, sale, stock or market demand. Limits and
allowlists deliberately prevent bulk scraping and cross-domain redirects.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import requests
from radar.core import DEFAULT_DB, ROOT, connection, iso_dt, seed_catalog, utcnow

LOG = logging.getLogger(__name__)
WATCHLIST = ROOT / "config" / "wholesale_watchlist.csv"
ECB_XML = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
MAX_WATCHED = 8
MAX_BYTES = 350_000
MAX_FX_BYTES = 80_000
OFFER_RE = re.compile(r"^/offer/([0-9]{7,16})\.html$")
BAD_TITLES = ("1688", "请登录", "安全验证", "验证码", "login", "captcha", "just a moment", "access denied")


def normalized_offer_url(url: str) -> tuple[str, str]:
    p = urlsplit(url)
    if (p.scheme != "https" or (p.hostname or "").lower() != "detail.1688.com"
            or p.port is not None or p.username or p.password or p.fragment):
        raise ValueError("Apenas URLs HTTPS de detalhe 1688 autorizadas")
    m = OFFER_RE.fullmatch(p.path)
    if not m:
        raise ValueError("URL sem ID de produto 1688")
    return "https://detail.1688.com" + p.path, m.group(1)


class ProductMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta_title = ""
        self.in_title = False
        self.in_ld = False
        self.ld_chunks: list[str] = []
        self._ld_current: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta" and (a.get("property") or "").lower() == "og:title":
            self.meta_title = (a.get("content") or "").strip()
        elif tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self.in_ld = True
            self._ld_current = []

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "script" and self.in_ld:
            self.in_ld = False
            self.ld_chunks.append("".join(self._ld_current))

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_ld:
            self._ld_current.append(data)


def _type_is(value, wanted: str) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(isinstance(v, str) and v.rsplit("/", 1)[-1] == wanted for v in values)


def _walk_products(obj):
    if isinstance(obj, list):
        for node in obj:
            yield from _walk_products(node)
    elif isinstance(obj, dict):
        if _type_is(obj.get("@type"), "Product"):
            yield obj
        if "@graph" in obj:
            yield from _walk_products(obj["@graph"])


def _single_cny_price(product: dict) -> float | None:
    offers = product.get("offers")
    if isinstance(offers, list):
        if len(offers) != 1:
            return None
        offers = offers[0]
    if not isinstance(offers, dict) or not _type_is(offers.get("@type"), "Offer"):
        return None
    # Do NOT parse price ranges, variant prices, marketing copy or Product.price.
    if offers.get("priceCurrency") != "CNY" or "lowPrice" in offers or "highPrice" in offers:
        return None
    try:
        price = Decimal(str(offers["price"]))
    except (KeyError, TypeError, InvalidOperation, ValueError):
        return None
    if not price.is_finite() or not Decimal("0.01") <= price <= Decimal("100000"):
        return None
    return float(price)


def parse_public_product_html(html: str) -> dict:
    """Do not accept numbers from free text or ambiguous offers."""
    parser = ProductMetadata()
    parser.feed(html)
    title = " ".join((parser.meta_title or parser.title).split())[:250]
    if not title or len(title) < 8 or title.casefold() in BAD_TITLES or any(
        bad in title.casefold() for bad in BAD_TITLES if len(bad) > 4
    ):
        return {"title": None, "price_cny": None}
    prices = []
    for raw in parser.ld_chunks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for product in _walk_products(data):
            price = _single_cny_price(product)
            if price is not None:
                prices.append(price)
    return {"title": title, "price_cny": prices[0] if len(prices) == 1 else None}


def _response_body(response, limit: int) -> bytes:
    body = bytearray()
    for part in response.iter_content(chunk_size=16384):
        body.extend(part)
        if len(body) > limit:
            raise ValueError("document_too_large")
    return bytes(body)


def fetch_public_product(session, url: str, timeout=10) -> dict:
    canonical, _ = normalized_offer_url(url)
    # Do not follow redirects, including legitimate ones that might lead to login.
    with session.get(canonical, timeout=timeout, stream=True, allow_redirects=False,
                     headers={"Accept": "text/html"}) as response:
        if response.status_code in (401, 403, 429) or 300 <= response.status_code < 400:
            return {"status": "blocked", "title": None, "price_cny": None}
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", "").lower():
            return {"status": "unavailable", "title": None, "price_cny": None}
        page = _response_body(response, MAX_BYTES)
        decoded = page.decode(response.encoding or "utf-8", errors="replace")
        item = parse_public_product_html(decoded)
        return {"status": "price_observed" if item["price_cny"] is not None else
                ("title_only" if item["title"] else "unavailable"), **item}


def ensure_wholesale_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS wholesale_checks (
      product_id TEXT NOT NULL, offer_id TEXT NOT NULL, url TEXT NOT NULL,
      checked_at TEXT NOT NULL, status TEXT NOT NULL,
      title TEXT, price_cny REAL, error_code TEXT,
      PRIMARY KEY(product_id,offer_id)
    );
    CREATE TABLE IF NOT EXISTS wholesale_price_history (
      product_id TEXT NOT NULL, offer_id TEXT NOT NULL,
      observed_day TEXT NOT NULL, price_cny REAL NOT NULL,
      PRIMARY KEY(product_id,offer_id,observed_day)
    );
    CREATE TABLE IF NOT EXISTS fx_reference_cny (
      day TEXT PRIMARY KEY, cny_per_eur REAL NOT NULL,
      checked_at TEXT NOT NULL
    );
    """)


def parse_ecb_rate(xml: bytes, now: datetime | None = None) -> tuple[str, float]:
    if len(xml) > MAX_FX_BYTES:
        raise ValueError("FX XML too large")
    now = now or utcnow()
    root = ET.fromstring(xml)
    for date_node in root.iter():
        if "time" not in date_node.attrib:
            continue
        day = date_node.attrib["time"]
        try:
            observed = datetime.fromisoformat(day).date()
        except ValueError:
            continue
        if observed > now.date() or (now.date() - observed).days > 7:
            continue
        for item in date_node.iter():
            if item.attrib.get("currency") == "CNY":
                price = Decimal(item.attrib.get("rate", "0"))
                if price.is_finite() and Decimal("0.1") < price < Decimal("100"):
                    return day, float(price)
    raise ValueError("Sem CNY de referência recente")


def update_fx(con, session, now, timeout=10) -> bool:
    try:
        with session.get(ECB_XML, timeout=timeout, stream=True, allow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code != 200 or "xml" not in response.headers.get("content-type", "").lower():
                raise ValueError("invalid FX response")
            day, rate = parse_ecb_rate(_response_body(response, MAX_FX_BYTES), now)
        con.execute("INSERT OR REPLACE INTO fx_reference_cny(day,cny_per_eur,checked_at) VALUES(?,?,?)",
                    (day, rate, iso_dt(now)))
        con.commit()
        return True
    except (requests.RequestException, ValueError, ET.ParseError, InvalidOperation) as exc:
        LOG.warning("Taxa BCE indisponível: %s", type(exc).__name__)
        return False


def collect_wholesale(db: Path = DEFAULT_DB, watchlist: Path = WATCHLIST,
                      timeout: int = 10, max_offers: int = MAX_WATCHED,
                      session=None, now: datetime | None = None, fetch_fx=True) -> dict:
    if not 1 <= max_offers <= MAX_WATCHED:
        raise ValueError("max_offers: 1..8")
    now = now or utcnow()
    result = {"checked": 0, "price_observed": 0, "title_only": 0,
              "blocked": 0, "unavailable": 0, "fx_updated": False, "errors": []}
    with watchlist.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) > MAX_WATCHED or not {"product_id", "url"}.issubset(rows[0].keys() if rows else []):
        raise ValueError("Watchlist inválida ou acima do limite")
    own = session is None
    session = session or requests.Session()
    con = connection(db)
    try:
        seed_catalog(con)
        ensure_wholesale_tables(con)
        products = {r[0] for r in con.execute("SELECT id FROM products")}
        seen = set()
        for row in rows[:max_offers]:
            product = (row.get("product_id") or "").strip()
            offer_id = None
            url = None
            try:
                url, offer_id = normalized_offer_url((row.get("url") or "").strip())
                if product not in products or (product, offer_id) in seen:
                    raise ValueError("Produto inválido ou oferta duplicada")
                seen.add((product, offer_id))
                result["checked"] += 1
                record = fetch_public_product(session, url, timeout)
                status = record["status"]
                result[status] += 1
                con.execute("""INSERT INTO wholesale_checks
                (product_id,offer_id,url,checked_at,status,title,price_cny,error_code)
                VALUES(?,?,?,?,?,?,?,NULL) ON CONFLICT(product_id,offer_id)
                DO UPDATE SET checked_at=excluded.checked_at,status=excluded.status,
                  title=excluded.title,price_cny=excluded.price_cny,error_code=NULL""",
                    (product,offer_id,url,iso_dt(now),status,record["title"],record["price_cny"]))
                if status == "price_observed":
                    con.execute("""INSERT INTO wholesale_price_history
                    (product_id,offer_id,observed_day,price_cny) VALUES(?,?,?,?)
                    ON CONFLICT(product_id,offer_id,observed_day)
                    DO UPDATE SET price_cny=excluded.price_cny""",
                        (product, offer_id, now.date().isoformat(), record["price_cny"]))
                con.commit()
            except (ValueError, requests.RequestException, UnicodeError) as exc:
                result["errors"].append(f"{product or 'linha sem ID'}: {type(exc).__name__}")
                # Do not preserve any earlier price as a fresh current observation.
                if product in products and offer_id and url and (product,offer_id) in seen:
                    con.execute("""INSERT INTO wholesale_checks
                    (product_id,offer_id,url,checked_at,status,title,price_cny,error_code)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(product_id,offer_id)
                    DO UPDATE SET checked_at=excluded.checked_at,status='error',price_cny=NULL,error_code=excluded.error_code""",
                        (product,offer_id,url,iso_dt(now),'error',None,None,type(exc).__name__))
                    con.commit()
        if fetch_fx:
            result["fx_updated"] = update_fx(con,session,now,timeout)
    finally:
        con.close()
        if own:
            session.close()
    return result


def wholesale_summaries(con, now=None) -> dict:
    ensure_wholesale_tables(con)
    now = now or utcnow()
    deadline = iso_dt(now - timedelta(hours=72))
    rate_row = con.execute("SELECT * FROM fx_reference_cny ORDER BY day DESC LIMIT 1").fetchone()
    rate = None
    if rate_row:
        try:
            if 0 <= (now.date() - datetime.fromisoformat(rate_row["day"]).date()).days <= 7:
                rate = rate_row["cny_per_eur"]
        except ValueError:
            pass
    offers = []
    for row in con.execute("SELECT * FROM wholesale_checks ORDER BY product_id,offer_id"):
        fresh = row["checked_at"] >= deadline
        amount = row["price_cny"] if fresh and row["status"] == "price_observed" else None
        offers.append({"product_id":row["product_id"],"offer_id":row["offer_id"],
                       "url":row["url"], "title":row["title"],
                       "checked_at":row["checked_at"],
                       "status":row["status"] if fresh else "stale",
                       "price_cny":amount,
                       "reference_eur":round(amount / rate, 2) if rate and amount else None})
    return {"offers":offers, "fx":({"day":rate_row["day"],"cny_per_eur":rate} if rate else None)}


def main():
    cli=argparse.ArgumentParser(description="Verifica até 8 páginas públicas do 1688, sem login nem bypass")
    cli.add_argument("--db",type=Path,default=DEFAULT_DB)
    cli.add_argument("--watchlist",type=Path,default=WATCHLIST)
    cli.add_argument("--timeout",type=int,default=10)
    cli.add_argument("--max-offers",type=int,default=MAX_WATCHED)
    args=cli.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(collect_wholesale(args.db,args.watchlist,args.timeout,args.max_offers))


if __name__ == "__main__":
    main()
