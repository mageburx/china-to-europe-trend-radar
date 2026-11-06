"""Broad Chinese product-news inbox; stores unclassified article leads only."""
import argparse
import logging
from pathlib import Path
import requests
import xml.etree.ElementTree as ET
from radar.collect import news_url, parse_rss
from radar.core import DEFAULT_DB, connection, iso_dt, utcnow

QUERIES={
    "Casa e organização":"家居 收纳 创新 好物",
    "Automóvel":"汽车 内饰 实用 配件 新品",
    "Animais":"宠物 用品 创新 新品",
    "Viagem":"旅行 收纳 新品 实用",
    "Produtos para arrumação": "折叠收纳箱 理线器 厨房收纳",
    "Acessórios para automóvel": "车载垃圾桶 车载手机支架 后备箱收纳",
    "Produtos para gatos": "猫砂盆 宠物饮水机 宠物梳毛",
}


def discover(db:Path=DEFAULT_DB, max_items:int=15, timeout:int=12)->dict:
    con=connection(db)
    session=requests.Session()
    session.headers.update({"User-Agent":"ChinaEuropeTrendRadar/0.1 (research; RSS only)"})
    stored=0;errors=[]
    for category, query in QUERIES.items():
        try:
            response=session.get(news_url(query,"CN"),timeout=timeout)
            response.raise_for_status()
            for item in parse_rss(response.content)[:max_items]:
                cur=con.execute("""INSERT OR IGNORE INTO discoveries
                    (category,source,title,url,observed_at,ingested_at)
                    VALUES (?,?,?,?,?,?)""",
                    (category,item["source"],item["title"],item["url"],
                     iso_dt(item["observed_at"]),iso_dt(utcnow())))
                stored+=cur.rowcount
            con.commit()
        except (requests.RequestException,ET.ParseError,ValueError) as exc:
            errors.append(f"{category}: {str(exc)[:160]}")
            logging.warning(errors[-1])
    con.close()
    return {"queries":len(QUERIES),"new_leads":stored,"errors":errors}

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",type=Path,default=DEFAULT_DB)
    ap.add_argument("--timeout",type=int,default=12)
    ap.add_argument("--max-items",type=int,default=15)
    args=ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    result=discover(args.db,args.max_items,args.timeout)
    print(result)
