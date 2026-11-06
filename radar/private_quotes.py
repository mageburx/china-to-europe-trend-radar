"""Local only: import user-verified supplier quotes into an untracked private SQLite DB.

Never imported by the public GitHub Pages renderer. The importer does not validate
claims with suppliers; every quote remains *user-reported*, not independently verified.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from radar.core import ROOT
import sqlite3

PRIVATE_DB=ROOT/'data'/'private_sourcing.sqlite'
FIELDS=('product_id','platform','source_url','supplier','unit_price','currency','units_per_pack',
        'moq','incoterm','shipping_eur','customs_eur','eu_source_url','eu_price_eur',
        'eu_units_per_pack','same_specs_confirmed','compliance_status','observed_at','notes')


def safe_url(url):
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port is not None:
        raise ValueError('URL de prova tem de ser HTTPS sem credenciais')
    if p.hostname in ('localhost','127.0.0.1') or p.hostname.endswith('.local'):
        raise ValueError('URL local não é uma prova pública')
    return url


def decimal_or_none(value,required=False):
    if not value and not required:return None
    try:r=Decimal(value)
    except (InvalidOperation,TypeError):raise ValueError('Valor monetário inválido')
    if not r.is_finite() or r<0 or (required and r<=0) or r>100000000:
        raise ValueError('Valor monetário fora dos limites')
    return float(r)


def validate(row,valid_ids):
    if (row.get('product_id') or '').strip() not in valid_ids:
        raise ValueError('Produto fora do catálogo')
    currency=(row.get('currency') or '').upper()
    if currency not in ('CNY','EUR','USD'):
        raise ValueError('Moeda desconhecida')
    source=safe_url((row.get('source_url') or '').strip())
    euurl=(row.get('eu_source_url') or '').strip()
    if euurl:safe_url(euurl)
    quantities=[]
    for col in ('units_per_pack','moq','eu_units_per_pack'):
        try:value=int(row.get(col) or '0')
        except ValueError:raise ValueError(f'{col} inválido')
        if not 0<value<1000000:raise ValueError(f'{col} inválido')
        quantities.append(value)
    date=datetime.fromisoformat((row.get('observed_at') or '').replace('Z','+00:00'))
    if date.tzinfo is None:raise ValueError('Data com timezone obrigatória')
    same=(row.get('same_specs_confirmed') or 'no').lower()
    if same not in ('yes','no'):raise ValueError('same_specs_confirmed: yes/no')
    compliance=(row.get('compliance_status') or 'pending').lower()
    if compliance not in ('pending','review','cleared','blocked'):
        raise ValueError('Estado de conformidade inválido')
    return (row['product_id'],row.get('platform') or 'manual',source,
            (row.get('supplier') or '').strip()[:200],
            decimal_or_none(row.get('unit_price'),True),currency,*quantities[:2],
            (row.get('incoterm') or '').strip()[:20],
            decimal_or_none(row.get('shipping_eur')),
            decimal_or_none(row.get('customs_eur')),
            euurl,decimal_or_none(row.get('eu_price_eur')),
            quantities[2],same,compliance,date.isoformat(),
            (row.get('notes') or '').strip()[:1000])


def import_csv(filename:Path, db:Path=PRIVATE_DB, catalog=None)->dict:
    from radar.core import CATALOG
    catalog=catalog or CATALOG
    with Path(catalog).open(encoding='utf-8-sig',newline='') as f:
        valid_ids={r['id'] for r in csv.DictReader(f)}
    db=Path(db).resolve()
    if db.name!='private_sourcing.sqlite' or db.parent!= (ROOT/'data').resolve():
        raise ValueError('Base privada deve estar em data/private_sourcing.sqlite')
    with Path(filename).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not set(FIELDS).issubset(reader.fieldnames or []):
            raise ValueError('CSV não contém todas as colunas esperadas')
        entries=[validate(row,valid_ids) for row in reader]
    db.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(db) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS private_quotes(
        product_id TEXT,platform TEXT,source_url TEXT,supplier TEXT,unit_price REAL,
        currency TEXT,units_per_pack INTEGER,moq INTEGER,incoterm TEXT,
        shipping_eur REAL,customs_eur REAL,eu_source_url TEXT,eu_price_eur REAL,
        eu_units_per_pack INTEGER,same_specs_confirmed TEXT,
        compliance_status TEXT,observed_at TEXT,notes TEXT,
        PRIMARY KEY(product_id,source_url,observed_at))''')
        for entry in entries:
            con.execute('''INSERT OR REPLACE INTO private_quotes VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',entry)
        con.commit()
    return {'imported':len(entries),'database':str(db),'privacy':'local_only'}


def main():
    ap=argparse.ArgumentParser(description='Importar cotações fornecidas pelo utilizador, só localmente')
    ap.add_argument('csv',type=Path)
    args=ap.parse_args()
    print(import_csv(args.csv))

if __name__=='__main__':main()
