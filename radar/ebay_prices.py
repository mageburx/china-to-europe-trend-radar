"""Zero-paid-API European retail price evidence (eBay Spain).

Requires optional *free* eBay developer credentials in environment variables.
Listings are keyword matches, not validated equivalent products, completed
sales, landed costs, or market saturation. Never stores credentials or private
supplier data. Production API only; modest daily queries.
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit
import unicodedata

import requests

from radar.core import DEFAULT_DB, connection, seed_catalog, iso_dt, utcnow

MARKETPLACE = "EBAY_ES"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
MAX_PRODUCTS = 8
MAX_ITEMS = 10


def ensure_ebay_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS ebay_listings (
      product_id TEXT NOT NULL,
      marketplace TEXT NOT NULL,
      item_id TEXT NOT NULL,
      title TEXT NOT NULL,
      url TEXT NOT NULL,
      price_eur REAL NOT NULL,
      first_seen TEXT NOT NULL,
      last_seen TEXT NOT NULL,
      PRIMARY KEY(product_id,marketplace,item_id)
    );
    CREATE INDEX IF NOT EXISTS idx_ebay_recent ON ebay_listings(product_id,last_seen);
    CREATE TABLE IF NOT EXISTS ebay_daily_prices (
      product_id TEXT NOT NULL,
      marketplace TEXT NOT NULL,
      item_id TEXT NOT NULL,
      observed_day TEXT NOT NULL,
      price_eur REAL NOT NULL,
      PRIMARY KEY(product_id,marketplace,item_id,observed_day)
    );
    CREATE TABLE IF NOT EXISTS ebay_checks (
      product_id TEXT NOT NULL,
      marketplace TEXT NOT NULL,
      checked_at TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('ok','error')),
      qualifying_listings INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(product_id,marketplace)
    );
    """)


def normalize(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.lower())
                   if not unicodedata.combining(c))


def relevant_listing(query: str, title: str) -> bool:
    """Weak proxy for relevance, never proof of the same product or variant."""
    terms = {x for x in re.findall(r"[a-z0-9]+", normalize(query)) if len(x) >= 4}
    words = set(re.findall(r"[a-z0-9]+", normalize(title)))
    return len(terms & words) >= min(2, len(terms)) and bool(terms)


def parse_listing(item: dict, query: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    item_id = item.get("itemId")
    title = item.get("title")
    url = item.get("itemWebUrl")
    price = item.get("price")
    if not all(isinstance(x, str) and x for x in (item_id, title, url)):
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        return None
    if (parsed.hostname or "").lower() not in {"www.ebay.es", "ebay.es"}:
        return None
    if not relevant_listing(query, title):
        return None
    if not isinstance(price, dict) or price.get("currency") != "EUR":
        return None
    try:
        euros = Decimal(str(price.get("value")))
    except (TypeError, ValueError, InvalidOperation):
        return None
    if not euros.is_finite() or euros <= Decimal("0.01") or euros > Decimal("10000"):
        return None
    return {"item_id": item_id[:160], "title": title[:350], "url": url,
            "price_eur": float(euros)}


def fetch_token(session, client_id: str, client_secret: str, timeout: int) -> str:
    res = session.post(TOKEN_URL, data={"grant_type": "client_credentials",
                       "scope": "https://api.ebay.com/oauth/api_scope"},
                       auth=(client_id, client_secret), timeout=timeout,
                       headers={"Content-Type":"application/x-www-form-urlencoded"})
    res.raise_for_status()
    token = res.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Resposta de autenticação sem access_token")
    return token


def collect_ebay(db: Path = DEFAULT_DB, max_products: int = MAX_PRODUCTS,
                 max_items: int = MAX_ITEMS, timeout: int = 12,
                 session=None, client_id: str | None = None,
                 client_secret: str | None = None, now=None) -> dict:
    if not 1 <= max_products <= 8 or not 1 <= max_items <= 10:
        raise ValueError("Limites: 1–8 produtos, 1–10 anúncios por pesquisa")
    result = {"status": "not_configured", "queries": 0, "qualified": 0,
              "new_quotes": 0, "errors": []}
    client_id = client_id or os.environ.get("EBAY_CLIENT_ID", "")
    client_secret = client_secret or os.environ.get("EBAY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return result
    now = now or utcnow()
    owns = session is None
    session = session or requests.Session()
    try:
        token = fetch_token(session, client_id, client_secret, timeout)
    except (requests.RequestException, ValueError) as exc:
        result["status"] = "auth_error"
        result["errors"].append(type(exc).__name__)  # no secrets, URLs or response dumps
        if owns:
            session.close()
        return result
    con = connection(db)
    try:
        seed_catalog(con)
        ensure_ebay_tables(con)
        products = con.execute("SELECT id,es_query FROM products ORDER BY id LIMIT ?",
                               (max_products,)).fetchall()
        for p in products:
            result["queries"] += 1
            status = "ok"
            qualified = 0
            try:
                response = session.get(SEARCH_URL,
                    params={"q": p["es_query"], "limit": max_items}, timeout=timeout,
                    headers={"Authorization": f"Bearer {token}",
                             "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE,
                             "Accept": "application/json"})
                response.raise_for_status()
                obj = response.json()
                if not isinstance(obj, dict) or not isinstance(obj.get("itemSummaries", []), list):
                    raise ValueError("Resposta de pesquisa inválida")
                for item in obj.get("itemSummaries", [])[:max_items]:
                    q = parse_listing(item, p["es_query"])
                    if not q:
                        continue
                    qualified += 1
                    cur = con.execute("""INSERT OR IGNORE INTO ebay_daily_prices
                      (product_id,marketplace,item_id,observed_day,price_eur)
                      VALUES (?,?,?,?,?)""",
                      (p["id"], MARKETPLACE, q["item_id"], now.date().isoformat(),
                       q["price_eur"]))
                    result["new_quotes"] += int(cur.rowcount > 0)
                    con.execute("""UPDATE ebay_daily_prices SET price_eur=?
                        WHERE product_id=? AND marketplace=? AND item_id=? AND observed_day=?""",
                        (q["price_eur"],p["id"],MARKETPLACE,q["item_id"],now.date().isoformat()))
                    con.execute("""INSERT INTO ebay_listings
                       (product_id,marketplace,item_id,title,url,price_eur,first_seen,last_seen)
                       VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(product_id,marketplace,item_id)
                       DO UPDATE SET title=excluded.title,url=excluded.url,
                         price_eur=excluded.price_eur,last_seen=excluded.last_seen""",
                       (p["id"],MARKETPLACE,q["item_id"],q["title"],q["url"],
                        q["price_eur"],iso_dt(now),iso_dt(now)))
            except (requests.RequestException, ValueError, KeyError) as exc:
                status = "error"
                result["errors"].append(f"{p['id']}: {type(exc).__name__}")
            con.execute("""INSERT INTO ebay_checks(product_id,marketplace,checked_at,status,qualifying_listings)
               VALUES (?,?,?,?,?) ON CONFLICT(product_id,marketplace)
               DO UPDATE SET checked_at=excluded.checked_at,status=excluded.status,
               qualifying_listings=excluded.qualifying_listings""",
               (p["id"],MARKETPLACE,iso_dt(now),status,qualified))
            con.commit()
            result["qualified"] += qualified
        result["status"] = "complete" if not result["errors"] else "partial"
    finally:
        con.close()
        if owns:
            session.close()
    return result


def price_summaries(con, now=None) -> list[dict]:
    ensure_ebay_tables(con)
    now = now or utcnow()
    fresh = iso_dt(now - timedelta(hours=72))
    checks = {row["product_id"]: dict(row) for row in
              con.execute("SELECT * FROM ebay_checks WHERE marketplace=?", (MARKETPLACE,))}
    ids = [r[0] for r in con.execute("SELECT id FROM products ORDER BY id")]
    out=[]
    for product_id in ids:
        check = checks.get(product_id)
        ok = bool(check and check["status"] == "ok" and check["checked_at"] >= fresh)
        prices = [r[0] for r in con.execute(
            "SELECT price_eur FROM ebay_listings WHERE product_id=? AND marketplace=? AND last_seen>=?",
            (product_id, MARKETPLACE, fresh))] if ok else []
        out.append({"product_id": product_id,"market":"ES", "source":"eBay Browse API",
                    "checked_at":check["checked_at"] if check else None,
                    "status":"ok" if ok else "not_verified",
                    "listing_count":len(prices),
                    "minimum_eur":round(min(prices),2) if prices else None,
                    "median_eur":round(median(prices),2) if prices else None,
                    "maximum_eur":round(max(prices),2) if prices else None})
    return out


def main():
    parser=argparse.ArgumentParser(description="Consulta até 8 produtos no eBay ES; exige credenciais gratuitas")
    parser.add_argument("--db",type=Path,default=DEFAULT_DB)
    parser.add_argument("--max-products",type=int,default=8)
    parser.add_argument("--max-items",type=int,default=10)
    args=parser.parse_args()
    result=collect_ebay(args.db,max_products=args.max_products,max_items=args.max_items)
    print(result)
    if result["status"] == "auth_error":
        raise SystemExit("Credenciais eBay não foram aceites")

if __name__ == "__main__":
    main()
