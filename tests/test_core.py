from datetime import datetime, timedelta, timezone
from pathlib import Path
from radar.core import connection, seed_catalog, add_observation, summaries, score_product, contribution, save_assessment
from radar.collect import parse_rss, news_url

NOW=datetime(2026,11,6,12,tzinfo=timezone.utc)


def test_rss_parser():
    rss=b'<rss><channel><item><title>Example</title><link>https://example.org/article</link><pubDate>Thu, 05 Nov 2026 11:00:00 GMT</pubDate></item></channel></rss>'
    r=parse_rss(rss)
    assert r[0]["title"]=="Example"
    assert r[0]["observed_at"].year==2026


def test_news_url_is_encoded():
    url=news_url("汽车 座椅", "CN")
    assert "news.google.com/rss" in url and "%E6%B1%BD" in url


def test_scoring_unknown_is_not_qualified():
    out=score_product(5,0,4,0,{})
    assert out["status"]=="Dados insuficientes"
    assert out["score"]<85


def test_blocked_always_blocked():
    out=score_product(25,2,25,2,{"competition_eu_count":0,"consumer_price_eur":49,
                 "landed_cost_eur":7,"compliance_status":"blocked","visual_score":10})
    assert out["status"]=="Bloqueado (conformidade)"


def test_contribution_vat():
    profit,margin=contribution({"consumer_price_eur":24.6,"landed_cost_eur":10,
                                "delivery_eur":2,"marketing_eur":3})
    assert profit==5.0 and margin==0.25


def test_database_dedup_and_evidence(tmp_path:Path):
    con=connection(tmp_path/"radar.sqlite")
    seed_catalog(con)
    dt=NOW-timedelta(days=1)
    assert add_observation(con,"car_gap","CN","News","Article","https://example.org/a",dt)
    assert not add_observation(con,"car_gap","CN","News","Article","https://example.org/a",dt)
    assert add_observation(con,"car_gap","PT","News","Article","https://example.org/a",dt)
    con.commit()
    p=next(p for p in summaries(con,NOW) if p["id"]=="car_gap")
    assert p["cn7"]==1 and p["pt7"]==1
    save_assessment(con,"car_gap",competition_eu_count=2,visual_score=5,compliance_status="review")
    a=con.execute("SELECT * FROM assessments WHERE product_id='car_gap'").fetchone()
    assert a["competition_eu_count"]==2
    con.close()


def test_static_report_contains_real_json(tmp_path:Path):
    from radar.report import create_report
    html=create_report(tmp_path/"dashboard.html",db=tmp_path/"data.sqlite").read_text(encoding="utf-8")
    assert '"products"' in html
    assert '__RADAR_PAYLOAD__' not in html
    assert '<title>China → Europe Trend Radar</title>' in html


def test_public_report_does_not_expose_commercial_fields(tmp_path:Path):
    from radar.core import save_assessment
    from radar.report import create_report
    db=tmp_path/"data.sqlite"
    con=connection(db)
    seed_catalog(con)
    save_assessment(con,"car_gap",consumer_price_eur=55,landed_cost_eur=7,notes="SEGREDO_FORNECEDOR_ABC")
    con.close()
    html=create_report(tmp_path/"index.html",db=db,public=True).read_text(encoding="utf-8")
    assert "SEGREDO_FORNECEDOR_ABC" not in html
    assert '"public_demo": true' in html
    assert '"consumer_price_eur"' not in html
