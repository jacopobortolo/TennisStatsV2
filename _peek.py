import sqlite3
from tennis_app.core.database import get_db_path
db = get_db_path()
print('DB:', db)
c = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2)
for tour in ('atp','wta'):
    rows = c.execute("SELECT ranking_date, COUNT(*) FROM rankings WHERE tour=? AND ranking_date>=20260101 AND ranking_date<99999999 GROUP BY ranking_date ORDER BY ranking_date", (tour,)).fetchall()
    print(f'--- {tour.upper()} 2026 snapshots: {len(rows)} ---')
    for r in rows:
        print(r[0], r[1])
