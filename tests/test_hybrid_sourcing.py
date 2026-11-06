from __future__ import annotations
import csv
import sqlite3
from datetime import datetime,timezone,timedelta
from pathlib import Path

import pytest

from radar.core import connection,seed_catalog
from radar.public_market_offers import (
    validated_url,parse_product_page,collect_public_offers,summaries,ensure_tables,
)
from radar.private_quotes import import_csv, FIELDS, PRIVATE_DB
from radar.sourcing_shortlist import shortlist
from radar.report import create_report

NOW=datetime(2026,11,6,12,tzinfo=timezone.utc)
ALIBABA='https://www.alibaba.com/product-detail/Wholesale-Hot-Selling-Adjustable-Drawer-Dividers_1600310190378.html'
LEROY='https://www.leroymerlin.pt/produtos/divisorias-de-gaveta-4-unidades-71583622.html'


def html(offers):
    import json
    return ('<html><head><title>Adjustable drawer dividers pack of four</title>'
            '<script type="application/ld+json">'+json.dumps({
                '@context':'https://schema.org','@type':'Product','name':'Drawer dividers',
                'offers':offers})+'</script></head></html>')


def test_validated_allowlist():
    assert validated_url(ALIBABA,'CN','Alibaba.com') == ALIBABA
    for url in ['http://www.alibaba.com/product-detail/item',
                'https://evil-alibaba.com/product-detail/x',
                'https://www.alibaba.com.evil.org/product-detail/x',
                'https://www.alibaba.com/redirect?next=https://evil.com',
                'https://user:pass@www.alibaba.com/product-detail/item',
                'https://www.alibaba.com/']:
        with pytest.raises(ValueError):validated_url(url,'CN','Alibaba.com')
    with pytest.raises(ValueError): validated_url(LEROY,'ES','Leroy Merlin')


def test_structured_offer_precise():
    data=parse_product_page(html({'@type':'Offer','price':'12.35','priceCurrency':'EUR'}))
    assert data['price']==12.35 and data['currency']=='EUR'
    assert parse_product_page(html({'@type':'AggregateOffer','lowPrice':2,'highPrice':4,'priceCurrency':'USD'}))['price'] is None
    assert parse_product_page(html([{'@type':'Offer','price':'2','priceCurrency':'USD'},
                                    {'@type':'Offer','price':'3','priceCurrency':'USD'}]))['price'] is None
    assert parse_product_page(html({'@type':'Offer','price':'NaN','priceCurrency':'USD'}))['price'] is None
    assert parse_product_page(html({'@type':'Offer','price':'100','priceCurrency':'BTC'}))['price'] is None
    assert parse_product_page('<title>Login for account captcha</title>')['title'] is None


def test_price_meta_fallback_only_with_currency():
    s='<title>Drawer organizer for kitchen storage</title>' \
      '<meta property="product:price:amount" content="19.99">' \
      '<meta property="product:price:currency" content="EUR">'
    assert parse_product_page(s)['price']==19.99
    assert parse_product_page(s.replace('product:price:currency','other:price:currency'))['price'] is None


class Response:
    def __init__(self, body=b'',status=200,ctype='text/html'):
        self.status_code=status
        self.headers={'content-type':ctype}
        self.encoding='utf-8'
        self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def iter_content(self,chunk_size):
        yield self.body

class Session:
    def __init__(self, responses):self.responses=iter(responses);self.calls=[]
    def get(self, url, **kwargs):
        self.calls.append((url,kwargs))
        return next(self.responses)


def watchlist(path):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=('product_id','market','merchant','url'))
        w.writeheader()
        w.writerows([{'product_id':'drawer_dividers','market':'CN','merchant':'Alibaba.com','url':ALIBABA},
                     {'product_id':'drawer_dividers','market':'PT','merchant':'Leroy Merlin','url':LEROY}])


def test_collection_records_provenance_without_market_equivalence(tmp_path):
    p=tmp_path/'watch.csv';watchlist(p)
    sess=Session([Response(html({'@type':'Offer','price':9,'priceCurrency':'USD'}).encode()),
                  Response(status=403)])
    db=tmp_path/'radar.sqlite'
    res=collect_public_offers(db,p,sess,NOW)
    assert res['checked']==2 and res['price_observed']==1 and res['blocked']==1
    assert all(not kwargs['allow_redirects'] for _,kwargs in sess.calls)
    with connection(db) as con:
        items=summaries(con,NOW)
        assert len(items)==2 and items[0]['price']==9.0
        assert items[1]['price'] is None
        assert len(shortlist(con))==8
        assert next(i for i in shortlist(con) if i['id']=='drawer_dividers')['equivalent_specs_verified'] is False
    with connection(db) as con:
        assert summaries(con,NOW+timedelta(days=3))[0]['status']=='stale'
        assert summaries(con,NOW+timedelta(days=3))[0]['price'] is None


def test_public_report_never_contains_local_private_quotes(tmp_path):
    db=tmp_path/'radar.sqlite'
    with connection(db) as con:
        seed_catalog(con);ensure_tables(con)
        con.execute('''INSERT INTO public_product_offers(product_id,url,market,merchant,status,title,price,currency,checked_at)
         VALUES(?,?,?,?,?,?,?,?,?)''',('drawer_dividers',ALIBABA,'CN','Alibaba.com','price_observed',
                  'Drawer dividers',1.99,'USD',NOW.isoformat()))
        con.commit()
    html_text=create_report(tmp_path/'p.html',db,public=True).read_text()
    assert 'public_market_offers' in html_text and 'sourcing_shortlist' in html_text
    assert 'Supplier Secret Ltd' not in html_text
    assert 'Pesquisa comercial pendente' in html_text


def test_private_import_does_not_touch_public_database(tmp_path,monkeypatch):
    from radar import private_quotes as pq
    local_root=tmp_path
    monkeypatch.setattr(pq,'ROOT',local_root)
    monkeypatch.setattr(pq,'PRIVATE_DB',local_root/'data/private_sourcing.sqlite')
    file=tmp_path/'quotes.csv'
    row=dict.fromkeys(FIELDS,'')
    row.update(product_id='drawer_dividers',platform='Alibaba',source_url=ALIBABA,
               supplier='Supplier Secret Ltd',unit_price='2.2',currency='EUR',
               units_per_pack='4',moq='10',incoterm='EXW',shipping_eur='3',customs_eur='1',
               eu_source_url=LEROY,eu_price_eur='33.9',eu_units_per_pack='4',
               same_specs_confirmed='no',compliance_status='pending',
               observed_at=NOW.isoformat(),notes='Confidential negotiations')
    with file.open('w',newline='') as fh:
        writer=csv.DictWriter(fh,fieldnames=FIELDS);writer.writeheader();writer.writerow(row)
    res=import_csv(file,db=local_root/'data/private_sourcing.sqlite')
    assert res['imported']==1
    assert not (local_root/'data/radar.sqlite').exists()
    con=sqlite3.connect(local_root/'data/private_sourcing.sqlite')
    assert con.execute('select supplier from private_quotes').fetchone()[0]=='Supplier Secret Ltd'
    with pytest.raises(ValueError):import_csv(file,db=local_root/'data/radar.sqlite')
    row['unit_price']='NaN'
    with file.open('w',newline='') as fh:
        writer=csv.DictWriter(fh,fieldnames=FIELDS);writer.writeheader();writer.writerow(row)
    with pytest.raises(ValueError):import_csv(file,db=local_root/'data/private_sourcing.sqlite')
