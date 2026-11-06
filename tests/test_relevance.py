from datetime import datetime,timezone,timedelta
from pathlib import Path
from radar.core import connection,seed_catalog,add_observation,summaries
from radar.relevance import relevant_headline
from radar.report import create_report

NOW=datetime(2026,11,6,12,0,tzinfo=timezone.utc)


def test_generic_suv_article_excluded():
    assert not relevant_headline('car_trunk','CN','大五座SUV装载力横评','Google News / 新浪网')
    assert relevant_headline('car_trunk','CN','后备箱收纳箱新品上市','Google News / 新浪网')
    assert relevant_headline('car_trunk','PT','Organizador de Bagageira Dobravel','Google News / exemplo')
    assert not relevant_headline('shoe_racks','CN','住宅室内鞋柜装修','Google News / News')
    assert relevant_headline('car_trunk','CN','documentado manualmente','Douyin / página pública')


def test_scores_and_public_sources_ignore_loosely_matched_news(tmp_path:Path):
    db=tmp_path/'a.sqlite'
    with connection(db) as con:
        seed_catalog(con)
        add_observation(con,'car_trunk','CN','Google News / CN',
                        'Novo SUV apresenta amplo porta-bagagens','https://news.google.com/a',NOW-timedelta(hours=2))
        add_observation(con,'car_trunk','CN','Google News / CN',
                        '后备箱收纳箱新品上市','https://news.google.com/b',NOW-timedelta(hours=2))
        con.commit()
        products=summaries(con,now=NOW)
        assert next(x for x in products if x['id']=='car_trunk')['cn7']==1
    html=create_report(tmp_path/'index.html',db,public=True).read_text(encoding='utf-8')
    assert '后备箱收纳箱新品上市' in html
    assert 'Novo SUV apresenta' not in html
