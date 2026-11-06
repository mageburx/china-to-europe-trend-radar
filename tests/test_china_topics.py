from datetime import datetime,timezone
from pathlib import Path
from radar.china_feeds import FEEDS, collect_direct_china
from radar.china_topics import categorize_title, topic_summaries
from radar.core import connection
from radar.report import create_report

NOW=datetime(2026,11,6,10,tzinfo=timezone.utc)

class Response:
    status_code=200
    content='''<rss version="2.0"><channel><item>
      <title>新款开放式耳机体验</title><link>https://www.ithome.com/0/123/456.htm</link>
      <pubDate>Fri, 06 Nov 2026 09:00:00 +0000</pubDate>
    </item></channel></rss>'''.encode("utf-8")

class Session:
    def get(self,url,timeout,allow_redirects):return Response()


def test_topic_taxonomy_is_distinct_from_product_catalog():
    assert categorize_title('新款开放式耳机体验') == [('Gadgets de consumo','耳机')]
    assert categorize_title('政治新闻 GDP 增长') == []


def test_sector_mentions_are_research_only(tmp_path:Path):
    db=tmp_path/'test.sqlite'
    r=collect_direct_china(db,session=Session(),now=NOW,feeds=(FEEDS[0],))
    assert r['product_headlines']==0 and r['new_leads']==0
    with connection(db) as con:
        topics=topic_summaries(con,now=NOW)
        assert topics[0]['articles']==1 and topics[0]['topic']=='Gadgets de consumo'
        assert con.execute('SELECT count(*) FROM candidate_leads').fetchone()[0]==0
        assert con.execute('SELECT count(*) FROM observations').fetchone()[0]==0
    html=create_report(tmp_path/'report.html',db,public=True).read_text('utf-8')
    assert 'Temas de consumo na imprensa chinesa' in html
    assert '新款开放式耳机体验' in html
