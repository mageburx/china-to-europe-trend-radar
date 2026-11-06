"""Free press-signal cross-check of Chinese candidate leads in Portugal/Spain.

The counts are *news RSS mentions*, not product listings, sales, availability,
market share, importability or an actual retail supply gap. No paid APIs.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path
import logging
import requests
import xml.etree.ElementTree as ET

from radar.collect import news_url, parse_rss
from radar.core import DEFAULT_DB, connection, iso_dt, utcnow
from radar.candidate_discovery import ensure_candidates_table

LOG = logging.getLogger(__name__)
# Portuguese and Spanish search-language mapping for *known* Chinese terms.
# Unmapped raw Chinese terms remain research leads; never fabricate a translation.
MARKET_TERMS = {
    "Organizadores de bagageira": ("organizador bagageira carro", "organizador maletero coche"),
    "Organizadores para bancos automóveis": ("organizador entre bancos carro", "organizador asiento coche"),
    "Mini caixotes de lixo para carro": ("caixote lixo carro", "papelera coche"),
    "Caixas de arrumação dobráveis": ("caixa arrumação dobrável", "caja almacenamiento plegable"),
    "Divisórias para gavetas": ("divisórias para gavetas", "separadores cajones"),
    "Organizadores de cabos": ("organizador cabos", "organizador cables"),
    "Organizadores de viagem": ("organizador viagem bagagem", "organizador viaje equipaje"),
    "Escovas para animais": ("escova animais pelo", "cepillo mascotas pelo"),
    "Fontes de água para animais": ("fonte água gatos", "fuente agua gatos"),
    "Coleiras refletoras para animais": ("coleira refletora cães", "collar reflectante perro"),
    "Tapetes antiderrapantes de carro": ("tapete antiderrapante carro", "alfombrilla antideslizante coche"),
    "Suportes de telemóvel para carro": ("suporte telemóvel carro", "soporte móvil coche"),
    "Redes de arrumação de bagageira": ("rede arrumação bagageira", "red organizadora maletero"),
    "Escovas reutilizáveis para limpeza": ("escova limpeza reutilizável", "cepillo limpieza reutilizable"),
    "Sacos de lavagem para roupa": ("saco lavagem roupa", "bolsa lavado ropa"),
    "Soluções de arrumação para cozinha": ("organizador cozinha", "organizador cocina"),
}


def ensure_market_tables(con) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS candidate_market_evidence (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      label_pt TEXT NOT NULL,
      market TEXT NOT NULL CHECK (market IN ('PT','ES')),
      source TEXT NOT NULL,
      title TEXT NOT NULL,
      url TEXT NOT NULL,
      published_at TEXT NOT NULL,
      ingested_at TEXT NOT NULL,
      UNIQUE (label_pt,market,url)
    );
    CREATE INDEX IF NOT EXISTS idx_market_evidence_date ON candidate_market_evidence(label_pt,market,published_at);
    CREATE TABLE IF NOT EXISTS candidate_market_checks (
      label_pt TEXT NOT NULL,
      market TEXT NOT NULL CHECK (market IN ('PT','ES')),
      checked_at TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('ok','error')),
      fetched INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (label_pt,market)
    );
    """)


def collect_market_checks(db: Path = DEFAULT_DB, max_topics: int = 6,
                          max_items: int = 10, timeout: int = 10,
                          session: requests.Session | None = None,
                          now=None) -> dict:
    """Query PT/ES only for recently mentioned Chinese candidates, with strict bounds."""
    if max_topics < 0 or max_topics > 20 or max_items < 0 or max_items > 100:
        raise ValueError("Limites fora do intervalo (topics 0–20, items 0–100)")
    now = now or utcnow()
    earliest = iso_dt(now - timedelta(days=14))
    latest = iso_dt(now + timedelta(days=1))
    con = connection(db)
    ensure_candidates_table(con)
    ensure_market_tables(con)
    candidates = con.execute("""
       SELECT label_pt, COUNT(*) AS mentions
       FROM candidate_leads
       WHERE published_at>=? AND published_at<=?
       GROUP BY label_pt ORDER BY mentions DESC, label_pt LIMIT ?
    """, (earliest, iso_dt(now), max(max_topics * 5, 1))).fetchall()
    selected = [r["label_pt"] for r in candidates if r["label_pt"] in MARKET_TERMS][:max_topics]
    owns_session = session is None
    session = session or requests.Session()
    if owns_session:
        session.headers.update({"User-Agent":"ChinaEuropeTrendRadar/0.5 (free public news RSS research)"})
    results = {"candidates":len(selected), "checks":0, "articles_inserted":0, "failures":[]}
    try:
        for label in selected:
            for market, query in zip(("PT","ES"), MARKET_TERMS[label]):
                try:
                    response = session.get(news_url(query,market), timeout=timeout)
                    response.raise_for_status()
                    items = parse_rss(response.content)[:max_items]
                    for item in items:
                        published = iso_dt(item["observed_at"])
                        if not earliest <= published <= latest:
                            continue
                        if not item["url"].startswith(("https://", "http://")):
                            continue
                        cur = con.execute("""INSERT OR IGNORE INTO candidate_market_evidence
                          (label_pt,market,source,title,url,published_at,ingested_at)
                          VALUES(?,?,?,?,?,?,?)""",(label,market,item["source"],
                               item["title"][:500],item["url"],published,iso_dt(now)))
                        results["articles_inserted"] += cur.rowcount
                    con.execute("""INSERT INTO candidate_market_checks(label_pt,market,checked_at,status,fetched)
                        VALUES(?,?,?,?,?) ON CONFLICT(label_pt,market) DO UPDATE SET
                        checked_at=excluded.checked_at,status=excluded.status,fetched=excluded.fetched""",
                        (label,market,iso_dt(now),"ok",len(items)))
                    con.commit()
                    results["checks"] += 1
                except (requests.RequestException, ET.ParseError, ValueError) as exc:
                    LOG.warning("Falha %s/%s: %s",label,market,exc)
                    results["failures"].append(f"{label}/{market}: {str(exc)[:100]}")
                    # Fail closed: keep existing data, but mark last check as error.
                    con.execute("""INSERT INTO candidate_market_checks(label_pt,market,checked_at,status,fetched)
                        VALUES(?,?,?,?,0) ON CONFLICT(label_pt,market) DO UPDATE SET
                        checked_at=excluded.checked_at,status=excluded.status,fetched=0""",
                        (label,market,iso_dt(now),"error"))
                    con.commit()
    finally:
        if owns_session:
            session.close()
        con.close()
    return results


def market_gap_summaries(con, now=None) -> list[dict]:
    """Conservatively group distinct press sources, without pretending to measure demand."""
    ensure_candidates_table(con)
    ensure_market_tables(con)
    now = now or utcnow()
    threshold = iso_dt(now - timedelta(days=14))
    end = iso_dt(now)
    labels = con.execute("""SELECT label_pt,category,COUNT(DISTINCT url) cn_articles,
                             COUNT(DISTINCT source) cn_publishers,MAX(published_at) last_cn
                        FROM candidate_leads WHERE published_at>=? AND published_at<=?
                        GROUP BY label_pt,category ORDER BY cn_articles DESC,last_cn DESC""",(threshold,end)).fetchall()
    out = []
    for row in labels:
        label = row["label_pt"]
        counts = {r["market"]:dict(r) for r in con.execute("""SELECT market,
                     COUNT(DISTINCT url) articles,COUNT(DISTINCT source) publishers
                     FROM candidate_market_evidence
                     WHERE label_pt=? AND published_at>=? AND published_at<=?
                     GROUP BY market""",(label,threshold,end))}
        checks = {r["market"]:dict(r) for r in con.execute("""SELECT market,status,checked_at
                     FROM candidate_market_checks WHERE label_pt=?""",(label,))}
        # A successful check expires after 48h; otherwise cannot claim *even* no RSS matches.
        fresh_checks = all(m in checks and checks[m]["status"] == "ok" and
                           checks[m]["checked_at"] >= iso_dt(now-timedelta(hours=48))
                           for m in ("PT","ES"))
        cn = row["cn_articles"]
        pt = counts.get("PT",{}).get("articles",0)
        es = counts.get("ES",{}).get("articles",0)
        if label not in MARKET_TERMS:
            status = "Sem tradução de pesquisa PT/ES"
        elif not fresh_checks:
            status = "Comparação PT/ES pendente"
        elif cn < 2:
            status = "Pista isolada na imprensa CN"
        elif pt + es == 0:
            status = "Menções CN; nenhuma encontrada no RSS PT/ES"
        else:
            status = "Menções também no RSS PT/ES"
        out.append({
            "label":label,"category":row["category"],
            "cn_articles":cn,"cn_publishers":row["cn_publishers"],
            "pt_articles":pt,"es_articles":es,
            "last_cn":row["last_cn"],"market_checked":fresh_checks,
            "status":status,
        })
    return out


def main():
    parser = argparse.ArgumentParser(description="Verificar menções RSS PT/ES de pistas chinesas")
    parser.add_argument("--db",type=Path,default=DEFAULT_DB)
    parser.add_argument("--max-topics",type=int,default=6)
    parser.add_argument("--max-items",type=int,default=10)
    parser.add_argument("--timeout",type=int,default=10)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(collect_market_checks(args.db, args.max_topics,args.max_items,args.timeout))

if __name__=="__main__":
    main()
