from pathlib import Path
from unittest.mock import patch
import pytest
from radar.platform_collect import platform_for, extract_title, collect_platform_links
from radar.core import connection


def test_allowlist():
    assert platform_for('https://www.douyin.com/video/123') == 'Douyin'
    assert platform_for('https://detail.1688.com/offer/123.html') == '1688'
    for bad in ('http://www.douyin.com/video/123', 'https://evil-douyin.com/post',
                'https://127.0.0.1:443/', 'https://www.douyin.com.evil.org/',
                'https://www.douyin.com:444/video/123'):
        with pytest.raises(ValueError):
            platform_for(bad)


def test_extract_title():
    assert extract_title('<html><head><meta property="og:title" content="Organizador de gavetas ajustável e prático"></head></html>') == 'Organizador de gavetas ajustável e prático'
    assert extract_title('<title>抖音</title>') is None
    assert extract_title('<title>Please complete captcha</title>') is None


def test_collect_validated_url(tmp_path: Path):
    csv_file = tmp_path / 'links.csv'
    csv_file.write_text('product_id,url\ndrawer_dividers,https://www.douyin.com/video/123\n', encoding='utf-8')
    db = tmp_path / 'db.sqlite'
    with patch('radar.platform_collect.fetch_public_title', return_value=('Novo organizador modular de gavetas', 'https://www.douyin.com/video/123')):
        r = collect_platform_links(db=db, links=csv_file)
        assert r['new_links'] == 1 and r['found'] == 1 and r['errors'] == []
        assert collect_platform_links(db=db, links=csv_file)['new_links'] == 0
    with connection(db) as con:
        row = con.execute('SELECT platform, status FROM platform_evidence WHERE product_id=?', ('drawer_dividers',)).fetchone()
        assert row['platform'] == 'Douyin' and row['status'] == 'found'
        assert con.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 0


def test_empty_csv(tmp_path: Path):
    csv_file = tmp_path / 'empty.csv'
    csv_file.write_text('product_id,url\n', encoding='utf-8')
    result = collect_platform_links(tmp_path/'empty.sqlite', csv_file)
    assert result['checked'] == 0


def test_unavailable_platform_link_does_not_count_as_trend(tmp_path: Path):
    csv_file = tmp_path / 'links.csv'
    csv_file.write_text('product_id,url\ncar_gap,https://www.douyin.com/video/456\n',encoding='utf-8')
    db = tmp_path / 'db.sqlite'
    with patch('radar.platform_collect.fetch_public_title',return_value=None):
        result=collect_platform_links(db=db,links=csv_file)
    assert result['skipped']==1
    with connection(db) as con:
        assert con.execute("SELECT status FROM platform_evidence").fetchone()[0]=='not_public'
        assert con.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 0
