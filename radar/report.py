"""Export a dependency-free, read-only, interactive HTML snapshot."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from radar.core import DEFAULT_DB, ROOT, connection, seed_catalog, summaries, utcnow, iso_dt


def create_report(path:Path=ROOT/"dashboard_offline.html",db:Path=DEFAULT_DB)->Path:
    con=connection(db)
    seed_catalog(con)
    payload={
      "generated_at":iso_dt(utcnow()),
      "products":summaries(con),
      "observations":[dict(r) for r in con.execute("SELECT product_id,market,source,title,url,observed_at FROM observations ORDER BY observed_at DESC LIMIT 500")],
      "discoveries":[dict(r) for r in con.execute("SELECT category,source,title,url,observed_at FROM discoveries ORDER BY observed_at DESC LIMIT 200")]
    }
    con.close()
    template=(ROOT/"radar"/"offline_template.html").read_text(encoding="utf-8")
    embedded=json.dumps(payload,ensure_ascii=False).replace("<","\\u003c").replace("&","\\u0026")
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(template.replace("__RADAR_PAYLOAD__",embedded),encoding="utf-8")
    return path

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",type=Path,default=DEFAULT_DB)
    ap.add_argument("--out",type=Path,default=ROOT/"dashboard_offline.html")
    args=ap.parse_args()
    print("Dashboard HTML:",create_report(args.out,args.db))
