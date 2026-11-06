"""Conservative product keyword extraction from Chinese-sector news headlines.

These are UNVERIFIED research leads; no sales, social engagement, or demand is inferred.
Only processes public articles already stored in the `discoveries` inbox.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from radar.core import DEFAULT_DB, connection, iso_dt, utcnow

# Prefer specific nouns over generic terms such as 新品 (new products).
# A generic Chinese-language article may mention the item without selling it.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "Organizadores de bagageira": ("后备箱收纳箱", "后备箱整理箱", "后备箱收纳"),
    "Organizadores para bancos automóveis": ("座椅缝隙收纳", "车载收纳盒", "汽车座椅收纳"),
    "Mini caixotes de lixo para carro": ("车载垃圾桶", "汽车垃圾桶"),
    "Caixas de arrumação dobráveis": ("折叠收纳箱", "折叠储物箱"),
    "Divisórias para gavetas": ("抽屉分隔板", "抽屉分隔"),
    "Organizadores de cabos": ("理线器", "理线夹", "电线收纳"),
    "Organizadores de viagem": ("旅行收纳袋", "行李收纳袋", "压缩收纳袋"),
    "Escovas para animais": ("宠物梳毛", "宠物刷毛", "宠物梳子"),
    "Fontes de água para animais": ("宠物饮水机", "猫饮水机"),
    "Coleiras refletoras para animais": ("反光宠物项圈", "反光狗项圈"),
}


def extract_terms(title: str) -> list[tuple[str, str]]:
    """Returns (PT product category, Chinese matched token), never a demand score."""
    if not title:
        return []
    matched = []
    for name, variants in KEYWORDS.items():
        token = next((term for term in variants if term in title), None)
        if token:
            matched.append((name, token))
    return matched


def ensure_candidates_table(con) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS candidate_leads (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      label_pt TEXT NOT NULL,
      matched_cn TEXT NOT NULL,
      category TEXT NOT NULL,
      source TEXT NOT NULL,
      title TEXT NOT NULL,
      url TEXT NOT NULL,
      published_at TEXT NOT NULL,
      detected_at TEXT NOT NULL,
      validation_status TEXT NOT NULL DEFAULT 'unverified',
      UNIQUE(label_pt, url)
    );
    CREATE INDEX IF NOT EXISTS idx_candidate_published ON candidate_leads(published_at);
    """)


def classify_inbox(db: Path = DEFAULT_DB) -> dict:
    con = connection(db)
    ensure_candidates_table(con)
    scanned = new = 0
    try:
        for row in con.execute("SELECT category,source,title,url,observed_at FROM discoveries"):
            scanned += 1
            for label, token in extract_terms(row["title"]):
                cur = con.execute("""INSERT OR IGNORE INTO candidate_leads
                    (label_pt,matched_cn,category,source,title,url,published_at,detected_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (label, token, row["category"], row["source"], row["title"],
                     row["url"], row["observed_at"], iso_dt(utcnow())))
                new += cur.rowcount
        con.commit()
    finally:
        con.close()
    return {"articles_scanned": scanned, "new_unverified_leads": new}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(classify_inbox(args.db))
