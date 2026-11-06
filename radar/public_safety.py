"""Redact legacy local financial records before publishing the public SQLite snapshot.

This is a defense-in-depth guard. It cannot erase values from prior Git commits;
never store private commercial data in the tracked SQLite file in the first place.
"""
from __future__ import annotations
from pathlib import Path
from radar.core import DEFAULT_DB, connection


def scrub_public_db(db: Path = DEFAULT_DB) -> int:
    con = connection(db)
    try:
        count = con.execute('SELECT count(*) FROM assessments').fetchone()[0]
        con.execute('DELETE FROM assessments')
        con.commit()
        return count
    finally:
        con.close()


if __name__ == '__main__':
    print('Removidos registos locais de avaliação antes do commit público:', scrub_public_db())
