"""Hand-curated, public price references with provenance and package size.

These are dated snapshots from public indexed pages, not live collector results,
verified supplier quotes, or comparable SKUs. Never compute spreads as profit.
"""
from __future__ import annotations
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from radar.core import ROOT
from radar.public_market_offers import validated_url

REFERENCES=ROOT/'config'/'public_reference_snapshots.csv'


def load_references(path:Path=REFERENCES, today:date|None=None):
    today=today or date.today()
    with Path(path).open(encoding='utf-8-sig',newline='') as file:
        reader=csv.DictReader(file)
        fields={'product_id','market','merchant','url','observed_date','price','currency',
                'units_per_pack','description','evidence_method'}
        if not fields.issubset(reader.fieldnames or []):
            raise ValueError('Colunas de referência incompletas')
        out=[]
        for row in reader:
            url=validated_url(row['url'],row['market'],row['merchant'])
            d=date.fromisoformat(row['observed_date'])
            if d>today or (today-d).days>90:
                continue  # Not a fresh reference
            try:
                price=Decimal(row['price'])
                units=int(row['units_per_pack'])
            except (InvalidOperation,ValueError):
                continue
            if not price.is_finite() or not (0<price<1_000_000) or not (1<=units<=10_000):
                continue
            if row['currency'] not in ('EUR','CNY','USD') or row['evidence_method']!='public_index_snapshot':
                continue
            out.append({'product_id':row['product_id'],'market':row['market'],
                        'merchant':row['merchant'],'url':url,'observed_date':row['observed_date'],
                        'price':float(price),'currency':row['currency'],
                        'units_per_pack':units,'description':row['description'][:300],
                        'evidence_method':row['evidence_method'],
                        'equivalent_sku_confirmed':False})
        return out[:10]
