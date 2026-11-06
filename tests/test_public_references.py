from datetime import date
from pathlib import Path
import csv
from radar.public_references import load_references
from radar.report import create_report
from radar.core import connection,seed_catalog


def test_real_public_snapshots_date_and_pack_size():
    out=load_references(today=date(2026,11,6))
    assert len(out)==2
    assert out[0]['price']==1.44 and out[0]['units_per_pack']==1
    assert out[1]['price']==52.53 and out[1]['units_per_pack']==4
    assert not any(x['equivalent_sku_confirmed'] for x in out)
    assert load_references(today=date(2027,3,1))==[]


def test_public_report_shows_snapshot_provenance(tmp_path):
    db=tmp_path/'r.sqlite'
    with connection(db) as con:seed_catalog(con)
    h=create_report(tmp_path/'out.html',db,public=True).read_text()
    assert 'public_reference_snapshots' in h
    assert 'Referências documentadas manualmente' in h
    assert 'equivalent_sku_confirmed' in h


def test_no_bad_currency_or_future_price(tmp_path):
    path=tmp_path/'refs.csv'
    cols=['product_id','market','merchant','url','observed_date','price','currency','units_per_pack','description','evidence_method']
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for v in ['NaN','5.00']:
            w.writerow(dict(product_id='drawer_dividers',market='CN',merchant='Alibaba.com',
             url='https://www.alibaba.com/product-detail/foo',observed_date='2026-11-06',price=v,
             currency='EUR' if v=='NaN' else 'BTC',units_per_pack=1,description='x',evidence_method='public_index_snapshot'))
    assert load_references(path,date(2026,11,6))==[]
