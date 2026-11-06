from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path

from radar.candidate_discovery import ensure_candidates_table, extract_terms, extract_unmapped_terms
from radar.core import connection, iso_dt
from radar.market_gap import collect_market_checks, ensure_market_tables, market_gap_summaries
from radar.report import create_report

NOW=datetime(2026,11,6,12,0,tzinfo=timezone.utc)


def insert_lead(con, label, title='China article'):
    con.execute('''INSERT INTO candidate_leads
     (label_pt,matched_cn,category,source,title,url,published_at,detected_at)
     VALUES(?,?,?,?,?,?,?,?)''', (label,'车载垃圾桶','Automóvel','News CN','China article',
     f'https://news.google.com/articles/{len(list(con.execute("select id from candidate_leads")))+1}',
     iso_dt(NOW-timedelta(hours=6)),iso_dt(NOW)))
    con.commit()


class FakeResponse:
    def __init__(self, content):
        self.content=content
    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, fail=False):
        self.calls=[];self.fail=fail
    def get(self,url,timeout):
        self.calls.append(url)
        if self.fail:
            from requests.exceptions import ConnectionError
            raise ConnectionError('network unavailable')
        pub=format_datetime(NOW-timedelta(hours=2))
        xml=(f'<rss><channel><item><title>Produto anunciado</title>'
             f'<link>https://news.google.com/item-{len(self.calls)}</link>'
             f'<pubDate>{pub}</pubDate><source>Jornal EU</source></item></channel></rss>')
        return FakeResponse(xml.encode())


def test_unmapped_extraction_requires_product_noun():
    assert extract_terms('智能车载垃圾桶受欢迎')
    assert extract_unmapped_terms('创新办公桌折叠收纳袋新品') == [] or all(
        label.startswith('A identificar:') for label,_ in extract_unmapped_terms('创新办公桌折叠收纳袋新品'))
    assert extract_unmapped_terms('工业2026年经济增长全球报告') == []


def test_market_checks_are_real_queries_and_idempotent(tmp_path:Path):
    db=tmp_path/'market.sqlite'
    with connection(db) as con:
        ensure_candidates_table(con)
        insert_lead(con,'Mini caixotes de lixo para carro')
    session=FakeSession()
    first=collect_market_checks(db,max_topics=2,session=session,now=NOW)
    assert first['candidates']==1 and first['checks']==2 and first['articles_inserted']==2
    assert len(session.calls)==2 and all('news.google.com' in x for x in session.calls)
    second=collect_market_checks(db,max_topics=2,session=FakeSession(),now=NOW)
    assert second['articles_inserted']==0
    with connection(db) as con:
        rows=market_gap_summaries(con,now=NOW)
    assert len(rows)==1 and rows[0]['pt_articles']==1 and rows[0]['es_articles']==1
    assert rows[0]['status']=='Pista isolada na imprensa CN'


def test_market_gap_requires_fresh_successful_checks(tmp_path:Path):
    db=tmp_path/'a.sqlite'
    with connection(db) as con:
        ensure_candidates_table(con)
        insert_lead(con,'Mini caixotes de lixo para carro')
        insert_lead(con,'Mini caixotes de lixo para carro')
        assert market_gap_summaries(con,now=NOW)[0]['status']=='Comparação PT/ES pendente'
    result=collect_market_checks(db,session=FakeSession(fail=True),now=NOW)
    assert len(result['failures'])==2
    with connection(db) as con:
        r=market_gap_summaries(con,now=NOW)[0]
        assert r['status']=='Comparação PT/ES pendente'


def test_untranslated_never_requests_eu(tmp_path:Path):
    db=tmp_path/'b.sqlite'
    with connection(db) as con:
        ensure_candidates_table(con)
        insert_lead(con,'A identificar: 收纳盒')
    s=FakeSession()
    res=collect_market_checks(db,session=s,now=NOW)
    assert res['candidates']==0 and s.calls==[]
    with connection(db) as con:
        assert market_gap_summaries(con,now=NOW)[0]['status']=='Sem tradução de pesquisa PT/ES'


def test_public_report_contains_cross_market_section(tmp_path:Path):
    db=tmp_path/'a.sqlite'
    with connection(db) as con:
        ensure_candidates_table(con)
        insert_lead(con,'Mini caixotes de lixo para carro')
    html=create_report(tmp_path/'public.html',db,public=True).read_text(encoding='utf-8')
    assert 'China → Europa: pistas comparadas' in html
    assert 'Mini caixotes de lixo para carro' in html
    assert 'SUPABASE_PUBLISHABLE_KEY' not in html
