from pathlib import Path
from radar.core import connection, seed_catalog, save_assessment
from radar.public_safety import scrub_public_db
from radar.report import create_report


def test_sanitization_of_public_sqlite(tmp_path:Path):
    db=tmp_path/'db.sqlite'
    with connection(db) as con:
        seed_catalog(con)
        save_assessment(con,'car_gap',consumer_price_eur=99,landed_cost_eur=11,
                        notes='CONFIDENTIAL TEST SUPPLIER',compliance_status='cleared')
    assert scrub_public_db(db)==1
    assert scrub_public_db(db)==0
    with connection(db) as con:
        assert con.execute('SELECT count(*) FROM assessments').fetchone()[0]==0
    output=tmp_path/'site.html'
    create_report(path=output,db=db,public=True)
    html=output.read_text(encoding='utf-8')
    assert 'CONFIDENTIAL TEST SUPPLIER' not in html
    assert '99.0' not in html
