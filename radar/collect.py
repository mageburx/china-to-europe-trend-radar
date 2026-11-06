"""Collect Google News RSS mentions by market. Does NOT infer product sales."""
from __future__ import annotations
import argparse
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
import requests
from radar.core import connection, seed_catalog, add_observation, MARKETS

LOG = logging.getLogger(__name__)
LOCALES = {"CN": ("zh-CN", "CN", "CN:zh-Hans"),
           "PT": ("pt-PT", "PT", "PT:pt-150"),
           "ES": ("es", "ES", "ES:es")}


def news_url(query: str, market: str) -> str:
    lang, geo, ceid = LOCALES[market]
    return "https://news.google.com/rss/search?" + urlencode({
        "q": f"{query} when:14d", "hl": lang, "gl": geo, "ceid": ceid})


def parse_rss(xml: bytes) -> list[dict]:
    root = ET.fromstring(xml)
    output = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not title or not url or not pub:
            continue
        try:
            date = parsedate_to_datetime(pub)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, IndexError):
            continue
        source = item.find("source")
        source_name = (source.text or "Google News") if source is not None else "Google News"
        output.append({"title":title, "url":url, "observed_at":date,
                       "source":f"Google News / {source_name}"})
    return output


def collect(db: Path, max_items: int = 15, timeout: int = 12,
            markets: tuple[str,...] = MARKETS, limit_products: int | None = None) -> dict:
    con = connection(db)
    seed_catalog(con)
    products = list(con.execute("SELECT * FROM products ORDER BY id"))
    if limit_products:
        products = products[:limit_products]
    inserted, fetched, failures = 0,0,[]
    session = requests.Session()
    session.headers.update({"User-Agent":"ChinaEuropeTrendRadar/0.1 (research; RSS only)",
                            "Accept":"application/rss+xml, application/xml;q=0.9"})
    for p in products:
        for market in markets:
            query = p[f"{market.lower()}_query"]
            url = news_url(query, market)
            try:
                response = session.get(url, timeout=timeout)
                response.raise_for_status()
                items = parse_rss(response.content)[:max_items]
                fetched += len(items)
                for item in items:
                    inserted += add_observation(con, p["id"], market, **item)
                con.commit()
                LOG.info("%s %s: %d menções", p["id"], market, len(items))
            except (requests.RequestException, ET.ParseError, ValueError) as exc:
                failures.append(f"{p['id']}/{market}: {str(exc)[:180]}")
                LOG.warning("Falha: %s", failures[-1])
    con.close()
    return {"queries":len(products)*len(markets), "fetched":fetched,
            "inserted":inserted,"errors":failures}


def main() -> None:
    ap=argparse.ArgumentParser(description="Recolhe menções RSS CN/PT/ES; não são vendas.")
    ap.add_argument("--db",type=Path,default=None)
    ap.add_argument("--max-items",type=int,default=15)
    ap.add_argument("--timeout",type=int,default=12)
    ap.add_argument("--limit-products",type=int,default=None)
    ap.add_argument("--markets",default="CN,PT,ES")
    args=ap.parse_args()
    markets=tuple(x.strip().upper() for x in args.markets.split(","))
    if any(x not in MARKETS for x in markets):
        ap.error("--markets aceita CN,PT,ES")
    logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    from radar.core import DEFAULT_DB
    res=collect(args.db or DEFAULT_DB,max_items=args.max_items,timeout=args.timeout,
                markets=markets,limit_products=args.limit_products)
    print(res)
    if res["errors"] and res["fetched"] == 0:
        raise SystemExit("Nenhuma fonte RSS acessível; ver erros acima.")

if __name__ == "__main__":
    main()
