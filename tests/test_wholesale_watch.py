import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from radar.core import connection
from radar.wholesale_watch import (
    normalized_offer_url, parse_public_product_html, parse_ecb_rate,
    collect_wholesale, wholesale_summaries, ensure_wholesale_tables
)
NOW = datetime(2026,11,6,12,0,tzinfo=timezone.utc)

def html_price(price='19.00',offers=None):
    obj={'@context':'https://schema.org','@type':'Product','name':'汽车后备箱收纳箱',
         'offers':offers if offers is not None else {'@type':'Offer','priceCurrency':'CNY','price':price}}
    return '<html><head><meta property="og:title" content="汽车后备箱收纳箱 收纳便携家用"></head><body><script type="application/ld+json">'+json.dumps(obj)+'</script></body></html>'

@pytest.mark.parametrize('url',[ 'https://evil.com/offer/669802549483.html',
    'http://detail.1688.com/offer/669802549483.html',
    'https://detail.1688.com.evil.org/offer/669802549483.html',
    'https://user:password@detail.1688.com/offer/669802549483.html',
    'https://detail.1688.com:8443/offer/669802549483.html',
    'https://detail.1688.com/offer/not-a-product.html',
    'https://detail.1688.com/offer/669802549483.html#fragment'])
def test_restrict_offer_urls(url):
    with pytest.raises(ValueError):normalized_offer_url(url)

def test_canonical_offer_url_strips_tracker():
    assert normalized_offer_url('https://detail.1688.com/offer/669802549483.html?spm=tracking') == (
        'https://detail.1688.com/offer/669802549483.html','669802549483')

def test_strict_jsonld_offer_single_cny_price():
    assert parse_public_product_html(html_price())['price_cny']==19.0
    assert parse_public_product_html('<title>Long Chinese product description</title><p>¥19.00</p>')['price_cny'] is None
    assert parse_public_product_html(html_price(offers={'@type':'AggregateOffer','priceCurrency':'CNY','lowPrice':'12','highPrice':'20'}))['price_cny'] is None
    assert parse_public_product_html(html_price(offers=[{'@type':'Offer','priceCurrency':'CNY','price':'10'}, {'@type':'Offer','priceCurrency':'CNY','price':'15'}]))['price_cny'] is None
    assert parse_public_product_html(html_price(offers={'@type':'Offer','priceCurrency':'EUR','price':'10'}))['price_cny'] is None
    assert parse_public_product_html(html_price(price='NaN'))['price_cny'] is None

def test_ecb_xml_rates_have_age_limit():
    xml=b'<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube><Cube time="2026-11-05"><Cube currency="CNY" rate="7.48" /></Cube></Cube></gesmes:Envelope>'
    assert parse_ecb_rate(xml,NOW)==('2026-11-05',7.48)
    with pytest.raises(ValueError):parse_ecb_rate(xml,NOW+timedelta(days=10))

class FakeResponse:
    headers={'content-type':'text/html; charset=utf-8'}
    encoding='utf-8'
    def __init__(self,status=200,body=b''):
        self.status_code=status;self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def raise_for_status(self):
        if self.status_code>=400:
            import requests
            raise requests.exceptions.HTTPError('blocked')
    def iter_content(self,chunk_size=16384):yield self.body

class FakeSession:
    def __init__(self,body):self.body=body;self.urls=[]
    def get(self,url,**kwargs):
        self.urls.append(url)
        assert kwargs['allow_redirects'] is False
        return FakeResponse(body=self.body)
    def close(self):pass

def test_collect_deduplicates_history_and_stale_not_shown(tmp_path:Path):
    csv=tmp_path/'watch.csv';csv.write_text('product_id,url\ncar_trunk,https://detail.1688.com/offer/624390853400.html\n')
    db=tmp_path/'db.sqlite';s=FakeSession(html_price().encode())
    result=collect_wholesale(db,csv,session=s,now=NOW,fetch_fx=False)
    assert result['checked']==1 and result['price_observed']==1
    assert all(urlsplit(u).hostname=='detail.1688.com' for u in s.urls)
    result=collect_wholesale(db,csv,session=s,now=NOW,fetch_fx=False)
    with connection(db) as con:
        assert con.execute('SELECT count(*) FROM wholesale_price_history').fetchone()[0]==1
        summary=wholesale_summaries(con,NOW)
        assert summary['offers'][0]['price_cny']==19
        assert summary['offers'][0]['reference_eur'] is None
        stale=wholesale_summaries(con,NOW+timedelta(days=5))
        assert stale['offers'][0]['price_cny'] is None
        assert stale['offers'][0]['status']=='stale'

def test_failed_check_clears_live_price(tmp_path:Path):
    csv=tmp_path/'watch.csv';csv.write_text('product_id,url\ncar_trunk,https://detail.1688.com/offer/624390853400.html\n')
    db=tmp_path/'db.sqlite'
    collect_wholesale(db,csv,session=FakeSession(html_price().encode()),now=NOW,fetch_fx=False)
    class BlockSession(FakeSession):
        def get(self,*args,**kwargs):return FakeResponse(status=403)
    result=collect_wholesale(db,csv,session=BlockSession(b''),now=NOW+timedelta(hours=1),fetch_fx=False)
    assert result['blocked']==1
    with connection(db) as con:
        assert wholesale_summaries(con,NOW+timedelta(hours=1))['offers'][0]['price_cny'] is None
