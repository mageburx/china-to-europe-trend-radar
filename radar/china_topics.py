"""Broad consumer-product topics in original Chinese headlines.

A topic mention is a research direction, NOT a product listing, purchase, or trend.
Words are explicit and auditable; topics never contribute to opportunity scores.
"""
from __future__ import annotations

from datetime import timedelta
from radar.core import iso_dt, utcnow

# Keep separate from concrete SKU/product extraction in candidate_discovery.py.
TOPICS = {
    "Acessórios automóveis": ("车载", "行车记录仪", "汽车内饰", "汽车用品", "后备箱"),
    "Organização e casa": ("收纳", "家居用品", "置物架", "晾衣", "厨房好物"),
    "Acessórios para animais": ("宠物用品", "猫砂", "宠物饮水", "宠物玩具"),
    "Viagem e outdoor": ("户外装备", "露营装备", "旅行收纳", "行李箱", "折叠椅"),
    "Gadgets de consumo": ("耳机", "充电宝", "便携投影", "智能眼镜", "音箱", "手机配件", "无线充电"),
    "Pequenos eletrodomésticos": ("洗地机", "扫地机器人", "小家电", "除螨仪", "空气炸锅"),
}


def categorize_title(title: str) -> list[tuple[str, str]]:
    return [(name, next(word for word in words if word in title))
            for name, words in TOPICS.items() if any(word in title for word in words)]


def ensure_topic_table(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS china_topic_mentions (
      url TEXT NOT NULL, topic TEXT NOT NULL, matched_word TEXT NOT NULL,
      publisher TEXT NOT NULL, title TEXT NOT NULL,
      published_at TEXT NOT NULL,
      PRIMARY KEY (url,topic)
    );
    CREATE INDEX IF NOT EXISTS idx_topic_recent ON china_topic_mentions(published_at);
    """)


def topic_summaries(con, now=None) -> list[dict]:
    now = now or utcnow()
    ensure_topic_table(con)
    earliest = iso_dt(now - timedelta(days=14))
    end = iso_dt(now)
    rows = con.execute("""
        SELECT topic,COUNT(*) AS articles,COUNT(DISTINCT publisher) AS publishers,
               MAX(published_at) AS last_seen FROM china_topic_mentions
        WHERE published_at>=? AND published_at<=?
        GROUP BY topic ORDER BY articles DESC,topic ASC
    """, (earliest,end)).fetchall()
    result=[]
    for row in rows:
        examples=[dict(r) for r in con.execute("""
            SELECT publisher,title,url,published_at FROM china_topic_mentions
            WHERE topic=? AND published_at>=? AND published_at<=?
            ORDER BY published_at DESC LIMIT 3
        """,(row['topic'],earliest,end))]
        result.append({**dict(row),'examples':examples})
    return result
