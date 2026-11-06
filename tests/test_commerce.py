from pathlib import Path
import sqlite3
import pytest
from radar.commerce import estimate_unit_economics, evidence_url
from radar.candidate_discovery import extract_terms, ensure_candidates_table, classify_inbox
from radar.core import connection, iso_dt, utcnow


def test_land_cost_and_net_contribution():
    # 4€ unit + (100€ freight + 50€ customs) / 50 = 7€ landed.
    result=estimate_unit_economics(4,50,100,50,24.6,0.23,3,2,1,1)
    assert result['unit_landed_eur']==7.0
    assert result['net_revenue_eur']==20.0
    assert result['contribution_eur']==6.0
    assert result['contribution_margin_pct']==30.0


def test_invalid_economics():
    for value in (-1, 'nan', 'inf'):
        with pytest.raises(ValueError):
            estimate_unit_economics(value,1,0,0,25)
    with pytest.raises(ValueError):
        estimate_unit_economics(2,0,0,0,25)
    with pytest.raises(ValueError):
        estimate_unit_economics(2,2.5,0,0,25)


def test_evidence_url_validation():
    assert evidence_url('https://example.com/offer/1') == 'https://example.com/offer/1'
    for bad in ('http://example.com', 'javascript:alert(1)', 'https://user:password@example.com', ''):
        with pytest.raises(ValueError):
            evidence_url(bad)


def test_chinese_keyword_discovery_is_not_a_sales_signal(tmp_path:Path):
    db=tmp_path/'test.sqlite'
    with connection(db) as con:
        con.execute('INSERT INTO discoveries(category,source,title,url,observed_at,ingested_at) VALUES (?,?,?,?,?,?)',
           ('Animais','Google News / test','新品猫饮水机发布', 'https://example.com/real-article', iso_dt(utcnow()), iso_dt(utcnow())))
        con.commit()
    assert extract_terms('新品猫饮水机发布') == [('Fontes de água para animais','猫饮水机')]
    assert classify_inbox(db)['new_unverified_leads']==1
    assert classify_inbox(db)['new_unverified_leads']==0
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT validation_status FROM candidate_leads').fetchone()[0]=='unverified'
        assert con.execute('SELECT count(*) FROM observations').fetchone()[0] == 0
