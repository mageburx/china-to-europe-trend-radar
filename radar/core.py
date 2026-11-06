"""SQLite persistence, product catalogue and conservative scoring.

Observations = mentions, NOT transactions, sales or consumer demand.
All computed scores are exploratory, never a purchase recommendation.
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "radar.sqlite"
CATALOG = ROOT / "config" / "products.csv"
MARKETS = ("CN", "PT", "ES")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def connection(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript("""
      CREATE TABLE IF NOT EXISTS products (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
        cn_query TEXT NOT NULL, pt_query TEXT NOT NULL, es_query TEXT NOT NULL,
        risk_level TEXT NOT NULL, notes TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL,
        market TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL,
        url TEXT NOT NULL, observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
        metric_type TEXT, metric_value REAL,
        UNIQUE(product_id, market, url),
        FOREIGN KEY(product_id) REFERENCES products(id)
      );
      CREATE INDEX IF NOT EXISTS idx_obs_product_date ON observations(product_id, observed_at);
      CREATE TABLE IF NOT EXISTS discoveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE, observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS assessments (
        product_id TEXT PRIMARY KEY,
        competition_eu_count INTEGER,
        consumer_price_eur REAL,
        landed_cost_eur REAL,
        delivery_eur REAL DEFAULT 0,
        marketing_eur REAL DEFAULT 0,
        fees_eur REAL DEFAULT 0,
        returns_eur REAL DEFAULT 0,
        compliance_status TEXT NOT NULL DEFAULT 'pending',
        visual_score INTEGER,
        notes TEXT DEFAULT '',
        updated_at TEXT,
        FOREIGN KEY(product_id) REFERENCES products(id)
      );
    """)
    con.commit()
    return con


def seed_catalog(con: sqlite3.Connection, filename: Path = CATALOG) -> int:
    with open(filename, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        con.execute("""INSERT INTO products
        (id,name,category,cn_query,pt_query,es_query,risk_level,notes)
        VALUES (:id,:name,:category,:cn_query,:pt_query,:es_query,:risk_level,:notes)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,category=excluded.category,
          cn_query=excluded.cn_query,pt_query=excluded.pt_query,
          es_query=excluded.es_query,risk_level=excluded.risk_level,
          notes=excluded.notes""", r)
    con.commit()
    return len(rows)


def add_observation(con: sqlite3.Connection, product_id: str, market: str,
                    source: str, title: str, url: str, observed_at: datetime,
                    metric_type: str | None = None, metric_value: float | None = None) -> bool:
    if market not in MARKETS:
        raise ValueError("market tem de ser CN, PT ou ES")
    if not url.startswith(("https://", "http://")):
        raise ValueError("Observação requer URL http(s) verificável")
    cur = con.execute("""INSERT OR IGNORE INTO observations
        (product_id,market,source,title,url,observed_at,ingested_at,metric_type,metric_value)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (product_id, market, source, title[:500], url, iso_dt(observed_at),
         iso_dt(utcnow()), metric_type, metric_value))
    return cur.rowcount == 1


def save_assessment(con: sqlite3.Connection, product_id: str, **values) -> None:
    allowed = ("competition_eu_count", "consumer_price_eur", "landed_cost_eur",
               "delivery_eur", "marketing_eur", "fees_eur", "returns_eur",
               "compliance_status", "visual_score", "notes")
    unexpected = set(values) - set(allowed)
    if unexpected:
        raise ValueError(f"Campos inválidos: {unexpected}")
    if values.get("compliance_status") not in (None, "pending", "review", "cleared", "blocked"):
        raise ValueError("Estado de conformidade inválido")
    for k in ("competition_eu_count", "visual_score"):
        if values.get(k) is not None and values[k] < 0:
            raise ValueError(f"{k} não pode ser negativo")
    if values.get("visual_score") is not None and values["visual_score"] > 10:
        raise ValueError("visual_score entre 0 e 10")
    for k in ("consumer_price_eur", "landed_cost_eur", "delivery_eur", "marketing_eur", "fees_eur", "returns_eur"):
        if values.get(k) is not None and values[k] < 0:
            raise ValueError(f"{k} não pode ser negativo")
    existing = con.execute("SELECT * FROM assessments WHERE product_id=?", (product_id,)).fetchone()
    merged = dict(existing) if existing else {"product_id": product_id}
    merged.update(values)
    merged.setdefault("compliance_status", "pending")
    merged["updated_at"] = iso_dt(utcnow())
    keys = ["product_id", *allowed, "updated_at"]
    columns = ",".join(keys)
    placeholders = ",".join("?" for _ in keys)
    updates = ",".join(f"{k}=excluded.{k}" for k in keys[1:])
    con.execute(f"INSERT INTO assessments ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(product_id) DO UPDATE SET {updates}",
                [merged.get(k) for k in keys])
    con.commit()


def contribution(a: dict, vat: float = 0.23) -> tuple[float | None, float | None]:
    price, landed = a.get("consumer_price_eur"), a.get("landed_cost_eur")
    if price is None or landed is None or price <= 0:
        return None, None
    net = price / (1 + vat)
    cost = landed + sum(float(a.get(k) or 0) for k in
                        ("delivery_eur", "marketing_eur", "fees_eur", "returns_eur"))
    profit = round(net - cost, 2)
    return profit, round(profit / net, 4)


def score_product(cn7: int, cnprev: int, eu7: int, euprev: int,
                  assessment: dict | None = None) -> dict:
    a = assessment or {}
    # Small mention counts must never produce high scores purely through growth rates.
    cn_growth = (cn7 - cnprev) / max(cnprev, 1)
    cn_component = round(min(25, 3 * cn7 + 4 * max(0, min(cn_growth, 2)))) if cn7 else 0
    eu_component = min(20, 4 * eu7)
    competition = a.get("competition_eu_count")
    competition_component = (max(0, 15 - 3 * competition) if competition is not None else 0)
    profit, margin = contribution(a)
    margin_component = round(min(20, max(0, margin * 80))) if margin is not None else 0
    compliance = a.get("compliance_status", "pending")
    risk_component = 10 if compliance == "cleared" else 0
    visual_component = max(0, min(10, int(a.get("visual_score") or 0)))
    score = sum([cn_component, eu_component, competition_component,
                 margin_component, risk_component, visual_component])
    complete = (cn7 >= 3 and eu7 >= 2 and competition is not None
                and margin is not None and compliance == "cleared")
    if compliance == "blocked":
        status = "Bloqueado (conformidade)"
    elif complete and score >= 85:
        status = "Rever para amostra"
    elif complete:
        status = "Monitorizar"
    else:
        status = "Dados insuficientes"
    return dict(score=score, status=status, cn_growth=round(cn_growth, 2),
                profit=profit, margin=margin, complete=complete,
                evidence_count=cn7 + eu7,
                components={"China":cn_component,"Europa":eu_component,
                            "Concorrência":competition_component,"Margem":margin_component,
                            "Conformidade":risk_component,"Demonstração":visual_component})


def summaries(con: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    recent = iso_dt(now - timedelta(days=7))
    previous = iso_dt(now - timedelta(days=14))
    end = iso_dt(now)
    obs = con.execute("""SELECT product_id, market,
      SUM(CASE WHEN observed_at>=? AND observed_at<=? THEN 1 ELSE 0 END) AS cur,
      SUM(CASE WHEN observed_at>=? AND observed_at<? THEN 1 ELSE 0 END) AS prev
      FROM observations WHERE observed_at>=? AND observed_at<=?
      GROUP BY product_id, market""", (recent,end,previous,recent,previous,end)).fetchall()
    lookup = {(r["product_id"], r["market"]): (int(r["cur"] or 0),int(r["prev"] or 0)) for r in obs}
    result = []
    for p in con.execute("SELECT * FROM products ORDER BY name"):
        a = con.execute("SELECT * FROM assessments WHERE product_id=?", (p["id"],)).fetchone()
        assessment = dict(a) if a else {}
        cn7, cnprev = lookup.get((p["id"],"CN"), (0,0))
        pt7, ptprev = lookup.get((p["id"],"PT"), (0,0))
        es7, esprev = lookup.get((p["id"],"ES"), (0,0))
        s = score_product(cn7, cnprev, pt7+es7, ptprev+esprev, assessment)
        result.append({**dict(p),**s, "cn7":cn7,"cnprev":cnprev,
                       "pt7":pt7,"es7":es7,"eu7":pt7+es7,
                       "compliance":assessment.get("compliance_status","pending"),
                       "assessment":assessment})
    return sorted(result, key=lambda p:(p["complete"],p["score"],p["evidence_count"]), reverse=True)
