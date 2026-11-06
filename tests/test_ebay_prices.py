from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
from radar.core import connection
from radar.ebay_prices import (collect_ebay, ensure_ebay_tables, parse_listing,
                               price_summaries, relevant_listing)
from radar.report import create_report

NOW=datetime(2026,11,6,12,0,tzinfo=timezone.utc)


def test_no_credentials_no_live_requests(tmp_path:Path,monkeypatch):
    monkeypatch.delenv('EBAY_CLIENT_ID',raising=False)
    monkeypatch.delenv('EBAY_CLIENT_SECRET',raising=False)
    db=tmp_path/'radar.sqlite'
    result=collect_ebay(db,max_products=1)
    assert result['status']=='not_configured'
    assert not db.exists()


def test_listing_safety_and_weak_matching():
    item={'itemId':'123','title':'Organizador maletero plegable para coche',
          'itemWebUrl':'https://www.ebay.es/itm/123','price':{'currency':'EUR','value':'18.20'}}
    out=parse_listing(item,'organizador maletero plegable')
    assert out and out['price_eur']==18.2
    assert not parse_listing({**item,'itemWebUrl':'http://127.0.0.1/private'},'organizador maletero')
    assert not parse_listing({**item,'price':{'currency':'CNY','value':'18.20'}},'organizador maletero')
    assert not parse_listing({**item,'title':'Cable USB'},'organizador maletero')
    assert relevant_listing('clips organizador cables','Organizador de cables')


class FakeResponse:
    def __init__(self,obj): self.obj=obj
    def raise_for_status(self): pass
    def json(self): return self.obj

class FakeSession:
    def __init__(self): self.calls=[]
    def post(self,url,**kw):
        self.calls.append(('post',url,kw))
        return FakeResponse({'access_token':'fake-token'})
    def get(self,url,**kw):
        self.calls.append(('get',url,kw))
        return FakeResponse({'itemSummaries':[
          {'itemId':'123','title':'Organizador maletero plegable para coche',
           'itemWebUrl':'https://www.ebay.es/itm/123',
           'price':{'currency':'EUR','value':'24.50'}},
          {'itemId':'124','title':'Bolsa para portátil','itemWebUrl':'https://www.ebay.es/itm/124',
           'price':{'currency':'EUR','value':'10.0'}},
        ]})


def test_api_query_dedup_and_public_report(tmp_path:Path):
    db=tmp_path/'radar.sqlite'
    session=FakeSession()
    one=collect_ebay(db,max_products=8,max_items=10,now=NOW,session=session,
                     client_id='test',client_secret='test')
    assert one['status']=='complete' and one['queries']==8
    assert one['qualified'] >= 1
    assert len([x for x in session.calls if x[0]=='get'])==8
    with connection(db) as con:
        ensure_ebay_tables(con)
        n=con.execute('SELECT COUNT(*) FROM ebay_daily_prices').fetchone()[0]
        assert n >= 1
        s=price_summaries(con,now=NOW)
        assert any(x['status']=='ok' and x['listing_count'] for x in s)
    two=collect_ebay(db,max_products=8,now=NOW,session=FakeSession(),
                     client_id='test',client_secret='test')
    assert two['new_quotes']==0
    html=create_report(tmp_path/'site.html',db,public=True).read_text()
    assert 'Preços anunciados em Espanha' in html
    assert 'ebay_prices' in html
    assert 'fake-token' not in html
    assert 'client_secret' not in html
    with connection(db) as con:
        old=price_summaries(con,now=NOW+timedelta(days=4))
        assert all(x['status']=='not_verified' for x in old)
