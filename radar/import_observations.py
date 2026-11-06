"""Import manually verified source observations from a CSV file.

Fields: product_id,market,source,title,url,observed_at,metric_type,metric_value.
Source can be Douyin / Xiaohongshu / Taobao / 1688 etc, but the user must
provide verifiable source URLs and observed dates. No fabricated counts.
"""
import argparse
import csv
from datetime import datetime
from pathlib import Path
from radar.core import DEFAULT_DB, connection, seed_catalog, add_observation


def import_csv(filename: Path, db: Path = DEFAULT_DB) -> tuple[int,int]:
    con = connection(db)
    seed_catalog(con)
    valid = {r[0] for r in con.execute("SELECT id FROM products")}
    added = skipped = 0
    with filename.open(newline="", encoding="utf-8-sig") as f:
        for idx, row in enumerate(csv.DictReader(f),2):
            if row["product_id"] not in valid:
                raise ValueError(f"Linha {idx}: produto desconhecido: {row['product_id']}")
            try:
                date = datetime.fromisoformat(row["observed_at"].replace("Z","+00:00"))
                number = float(row["metric_value"]) if row.get("metric_value") else None
                new=add_observation(con,row["product_id"],row["market"],row["source"],
                    row["title"],row["url"],date,row.get("metric_type") or None,number)
            except (ValueError, KeyError) as exc:
                raise ValueError(f"Linha {idx}: {exc}") from exc
            added+=int(new)
            skipped+=int(not new)
    con.commit()
    con.close()
    return added,skipped

if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("filename",type=Path)
    ap.add_argument("--db",type=Path,default=DEFAULT_DB)
    args=ap.parse_args()
    print("Inseridas / duplicadas:", import_csv(args.filename,args.db))
