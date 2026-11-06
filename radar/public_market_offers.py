"""Strict, low-volume watch of selected public, first-party product pages.

Does not scrape search results, bypass access controls, infer SKU equivalence,
MOQ, sales or profits. Prices are accepted only from a *single* schema.org
Product/Offer or a directly matching product-price meta field with currency.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import requests

from radar.core import ROOT, DEFAULT_DB, connection, iso_dt, seed_catalog, utcnow
from radar.wholesale_watch import _type_is, _walk_products

LOG = logging.getLogger(__name__)
WATCHLIST = ROOT / 'config' / 'public_offer_watchlist.csv'
ALLOWED = {'www.alibaba.com': ('CN', 'Alibaba.com'),
           'www.leroymerlin.pt': ('PT', 'Leroy Merlin')}
MAX_ITEMS = 8
MAX_BYTES = 450_000
CURRENCIES = {'EUR', 'CNY', 'USD'}


def validated_url(url: str, market: str, merchant: str) -> str:
    p = urlsplit(url)
    if (p.scheme != 'https' or p.username or p.password or p.port is not None
            or p.fragment or (p.hostname or '').lower() not in ALLOWED):
        raise ValueError('URL não permitida')
    expected = ALLOWED[p.hostname.lower()]
    if (market, merchant) != expected:
        raise ValueError('Mercado ou plataforma não corresponde ao domínio')
    if not p.path or p.path == '/' or len(p.query) > 128 or len(url) > 1200:
        raise ValueError('Exige URL de página de produto')
    if p.hostname == 'www.alibaba.com' and not p.path.startswith('/product-detail/'):
        raise ValueError('Alibaba: apenas páginas product-detail')
    if p.hostname == 'www.leroymerlin.pt' and not p.path.startswith('/produtos/'):
        raise ValueError('Leroy: apenas páginas produtos')
    return url


class ProductParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ''
        self.in_title = False
        self.in_ld = False
        self.ld = []
        self.current = []
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'title':
            self.in_title = True
        if tag == 'script' and (attrs.get('type') or '').lower() == 'application/ld+json':
            self.in_ld = True
            self.current = []
        if tag == 'meta':
            key = (attrs.get('property') or attrs.get('name') or '').lower()
            if key in ('og:title', 'product:price:amount', 'product:price:currency'):
                self.meta[key] = (attrs.get('content') or '').strip()

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        if tag == 'script' and self.in_ld:
            self.in_ld = False
            self.ld.append(''.join(self.current))

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_ld:
            self.current.append(data)


def _price(raw, currency):
    if not isinstance(raw, (str, int, float)) or currency not in CURRENCIES:
        return None
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return float(d) if d.is_finite() and Decimal('0.01') <= d <= Decimal('1000000') else None


def parse_product_page(html: str) -> dict:
    p = ProductParser()
    p.feed(html)
    title = ' '.join((p.meta.get('og:title') or p.title).split())[:250]
    if len(title) < 10 or any(x in title.lower() for x in (
            'login', 'captcha', 'access denied', 'just a moment', '请登录', '安全验证')):
        return {'title': None, 'price': None, 'currency': None, 'evidence': None}
    candidates = []
    for raw in p.ld:
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for product in _walk_products(obj):
            offers = product.get('offers')
            if isinstance(offers, list):
                if len(offers) != 1:
                    continue
                offers = offers[0]
            if not isinstance(offers, dict) or not _type_is(offers.get('@type'), 'Offer'):
                continue
            # Do not use AggregateOffer, tier pricing, variant-specific price ranges.
            if any(x in offers for x in ('lowPrice', 'highPrice', 'priceRange')):
                continue
            price = _price(offers.get('price'), offers.get('priceCurrency'))
            if price is not None:
                candidates.append((price, offers.get('priceCurrency')))
    if not candidates and p.meta.get('product:price:amount'):
        amount = _price(p.meta['product:price:amount'], p.meta.get('product:price:currency'))
        if amount is not None:
            candidates = [(amount, p.meta['product:price:currency'])]
    # Reject ambiguous / multiple conflicting prices.
    if len(candidates) != 1:
        return {'title': title, 'price': None, 'currency': None, 'evidence': None}
    return {'title': title, 'price': candidates[0][0], 'currency': candidates[0][1],
            'evidence': 'Product/Offer JSON-LD or product price meta'}


def ensure_tables(con):
    con.executescript('''
    CREATE TABLE IF NOT EXISTS public_product_offers (
      product_id TEXT NOT NULL, url TEXT NOT NULL,
      market TEXT NOT NULL, merchant TEXT NOT NULL,
      status TEXT NOT NULL, title TEXT, price REAL, currency TEXT,
      checked_at TEXT NOT NULL, error_code TEXT,
      PRIMARY KEY (product_id,url)
    );
    CREATE TABLE IF NOT EXISTS public_product_price_history (
      product_id TEXT NOT NULL, url TEXT NOT NULL, day TEXT NOT NULL,
      price REAL NOT NULL, currency TEXT NOT NULL,
      PRIMARY KEY (product_id,url,day)
    );
    ''')


def _read_bounded_response(response):
    b=bytearray()
    for chunk in response.iter_content(chunk_size=16384):
        b.extend(chunk)
        if len(b) > MAX_BYTES:
            raise ValueError('too_large')
    return bytes(b)


def collect_public_offers(db: Path=DEFAULT_DB, watchlist:Path=WATCHLIST, session=None,
                          now=None, timeout=8, max_items=MAX_ITEMS) -> dict:
    if not 1 <= max_items <= MAX_ITEMS:
        raise ValueError('max_items deve estar entre 1 e 8')
    with Path(watchlist).open(encoding='utf-8-sig',newline='') as file:
        reader=csv.DictReader(file)
        if not {'product_id','url','market','merchant'}.issubset(reader.fieldnames or []):
            raise ValueError('Cabeçalho da watchlist inválido')
        rows=list(reader)
    if len(rows)>MAX_ITEMS:
        raise ValueError('Máximo de 8 páginas públicas')
    own=session is None
    session=session or requests.Session()
    now=now or utcnow()
    out={'checked':0,'price_observed':0,'title_only':0,'blocked':0,'unavailable':0,'errors':[]}
    con=connection(db)
    try:
        seed_catalog(con);ensure_tables(con)
        ids={r[0] for r in con.execute('SELECT id FROM products')}
        seen=set()
        for i,row in enumerate(rows[:max_items],2):
            product=(row.get('product_id') or '').strip()
            url=(row.get('url') or '').strip()
            market=(row.get('market') or '').strip()
            merchant=(row.get('merchant') or '').strip()
            try:
                validated_url(url,market,merchant)
                if product not in ids or (product,url) in seen:
                    raise ValueError('Produto não catalogado ou URL duplicada')
                seen.add((product,url))
            except ValueError as e:
                out['errors'].append(f'linha {i}: {e}')
                continue
            out['checked']+=1
            status='unavailable';data={'title':None,'price':None,'currency':None}
            error=None
            try:
                with session.get(url,timeout=timeout,stream=True,allow_redirects=False,
                                 headers={'Accept':'text/html','User-Agent':'ChinaEuropeTrendRadar/0.9 (+public product research)'}) as resp:
                    if resp.status_code in (401,403,429) or 300<=resp.status_code<400:
                        status='blocked'
                    elif resp.status_code!=200:
                        status='unavailable'
                    elif 'html' in resp.headers.get('content-type','').lower():
                        data=parse_product_page(_read_bounded_response(resp).decode(resp.encoding or 'utf-8',errors='replace'))
                        status='price_observed' if data['price'] is not None else ('title_only' if data['title'] else 'unavailable')
            except (requests.RequestException,ValueError,UnicodeError) as e:
                status='error';error=type(e).__name__
                out['errors'].append(f'linha {i}: {error}')
            out[status]=out.get(status,0)+1
            con.execute('''INSERT INTO public_product_offers
                (product_id,url,market,merchant,status,title,price,currency,checked_at,error_code)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(product_id,url) DO UPDATE SET
                status=excluded.status,title=excluded.title,price=excluded.price,
                currency=excluded.currency,checked_at=excluded.checked_at,error_code=excluded.error_code''',
                (product,url,market,merchant,status,data['title'],data['price'],data['currency'],iso_dt(now),error))
            if status=='price_observed':
                con.execute('''INSERT OR REPLACE INTO public_product_price_history
                    (product_id,url,day,price,currency) VALUES (?,?,?,?,?)''',
                    (product,url,now.date().isoformat(),data['price'],data['currency']))
            con.commit()
    finally:
        con.close()
        if own: session.close()
    return out


def summaries(con, now=None):
    ensure_tables(con)
    now=now or utcnow()
    threshold=iso_dt(now-timedelta(hours=48))
    items=[]
    for r in con.execute('SELECT * FROM public_product_offers ORDER BY product_id,market,merchant'):
        row=dict(r)
        if row['checked_at']<threshold:
            row['status']='stale';row['price']=None;row['currency']=None
        items.append(row)
    return items


def main():
    ap=argparse.ArgumentParser(description='Watchlist curta de páginas públicas com preço estruturado')
    ap.add_argument('--db',type=Path,default=DEFAULT_DB)
    ap.add_argument('--watchlist',type=Path,default=WATCHLIST)
    ap.add_argument('--max-items',type=int,default=MAX_ITEMS)
    ap.add_argument('--timeout',type=int,default=8)
    a=ap.parse_args()
    print(collect_public_offers(db=a.db,watchlist=a.watchlist,timeout=a.timeout,max_items=a.max_items))

if __name__=='__main__':main()
