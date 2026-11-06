"""No-network tests for direct RSS provenance, quality and failure handling."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

from radar.china_feeds import (FEEDS, collect_direct_china, clean_article_url,
                               direct_feed_summary, parse_direct_feed)
from radar.core import connection
from radar.report import create_report

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)


def rss(title='新款汽车后备箱收纳箱发布', url='https://www.ithome.com/0/123/456.htm', days=0):
    date = format_datetime(NOW - timedelta(days=days))
    return (f'<rss version="2.0"><channel><item><title>{title}</title>'
            f'<link>{url}</link><pubDate>{date}</pubDate></item></channel></rss>').encode()


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content, self.status_code = content, status_code


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, timeout, allow_redirects):
        assert allow_redirects is False and timeout > 0
        self.calls.append(url)
        return self.responses[url]


def test_strict_first_party_links():
    assert clean_article_url('https://www.ithome.com/0/123/456.htm?utm_source=rss#top', 'ithome.com') == 'https://www.ithome.com/0/123/456.htm'
    for bad in ['https://ithome.com.evil.org/0/1', 'http://www.ithome.com/a',
                'https://127.0.0.1/a', 'https://www.ithome.com:444/a',
                'https://user@www.ithome.com/a', 'https://www.ithome.com/']:
        assert clean_article_url(bad, 'ithome.com') is None


def test_rss_parsing_and_atom_dates():
    parsed = parse_direct_feed(rss(), 'ithome.com')
    assert parsed and parsed[0]['title'] == '新款汽车后备箱收纳箱发布'
    atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            b'<title>Apple gadget product</title>'
            b'<link href="https://www.ithome.com/0/000/123.htm"/>'
            b'<updated>2026-11-06T08:00:00Z</updated></entry></feed>')
    assert len(parse_direct_feed(atom, 'ithome.com')) == 1
    assert parse_direct_feed(rss(url='https://evil.com/a'), 'ithome.com') == []
    with pytest.raises(ValueError, match='RSS or Atom'):
        parse_direct_feed(b'<html><body>Just a moment</body></html>', 'ithome.com')


def test_ingestion_creates_only_dated_product_leads(tmp_path: Path):
    feed = FEEDS[0]
    session = FakeSession({feed.rss_url: FakeResponse(rss())})
    db = tmp_path/'china.sqlite'
    r = collect_direct_china(db, session=session, now=NOW, feeds=(feed,))
    assert r['successful'] == 1 and r['articles_seen'] == 1
    assert r['product_headlines'] == 1 and r['new_articles'] == 1 and r['new_leads'] == 1
    assert collect_direct_china(db, session=session, now=NOW, feeds=(feed,))['new_articles'] == 0
    with connection(db) as con:
        count = con.execute('SELECT COUNT(*) FROM candidate_leads').fetchone()[0]
        assert count == 1
        assert con.execute('SELECT source FROM candidate_leads').fetchone()[0] == 'RSS direto / IT之家'
        assert con.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 0
        assert direct_feed_summary(con, now=NOW)['feeds'][0]['fresh'] is True
    html = create_report(tmp_path/'site.html',db,public=True).read_text(encoding='utf8')
    assert 'Fontes chinesas originais' in html and '新款汽车后备箱收纳箱发布' in html
    assert 'https://www.ithome.com/0/123/456.htm' in html


def test_unrelated_and_stale_posts_are_not_trends(tmp_path):
    feed = FEEDS[0]
    db = tmp_path/'china.sqlite'
    s = FakeSession({feed.rss_url: FakeResponse(rss(title='中国经济增长与汽车行业研讨',days=24))})
    r = collect_direct_china(db,session=s, now=NOW,feeds=(feed,))
    assert r['new_articles'] == 0 and r['articles_seen'] == 0
    s.responses[feed.rss_url] = FakeResponse(rss(title='新一代电动汽车发布'))
    r2 = collect_direct_china(db,session=s,now=NOW,feeds=(feed,))
    assert r2['articles_seen'] == 1 and r2['product_headlines'] == 0


def test_blocked_feed_never_means_no_products(tmp_path):
    feed = FEEDS[2]
    r = collect_direct_china(tmp_path/'fail.sqlite',
            session=FakeSession({feed.rss_url: FakeResponse(b'<html>checking</html>',403)}),
            now=NOW, feeds=(feed,))
    assert r['successful'] == 0 and r['errors'] == ['36氪: HTTP_403']
    with connection(tmp_path/'fail.sqlite') as con:
        check = direct_feed_summary(con, now=NOW)['feeds'][0]
        assert check['status'] == 'error' and not check['fresh']
        assert check['fetched'] == 0


def test_exact_duplicate_titles_across_publishers_not_double_counted(tmp_path):
    feed1, feed2 = FEEDS[:2]
    same_title = '后备箱收纳箱新品上市'
    session = FakeSession({
        feed1.rss_url: FakeResponse(rss(title=same_title)),
        feed2.rss_url: FakeResponse(rss(title=same_title,url='https://www.ifanr.com/12345')),
    })
    result = collect_direct_china(tmp_path/'dedup.sqlite',session=session,
                                  now=NOW,feeds=(feed1,feed2))
    assert result['successful'] == 2 and result['product_headlines'] == 2
    assert result['new_articles'] == 1 and result['new_leads'] == 1


def test_feed_over_bytes_limit():
    with pytest.raises(ValueError,match='limit'):
        parse_direct_feed(b' ' * 1_500_001,'ithome.com')
