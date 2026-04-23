# TennisStatsV2

Two ways to run the app:

| Mode      | Database              | Continuous updates with PC off? | Setup     |
| --------- | --------------------- | -------------------------------- | --------- |
| **Local** (default)    | local `tennis.db`     | Only via Windows Task Scheduler  | Easy      |
| **Cloud** (`cloud/`)   | Turso (libSQL) remote | Yes, hourly via GitHub Actions   | 10 min    |

---

## Local mode

```powershell
pip install -r requirements.txt
python -m tennis_app          # full UI
python -m tennis_app.cron     # headless scrape (used by Task Scheduler)
```

Recent local-mode improvements:

- **Concurrency-safe DB** — `RLock` + `busy_timeout=10000` prevents the
  "database is locked" error when the live scrape and background
  extended-stats workers run simultaneously.
- **Unique staging tables** — each `import_scraped_matches` call uses a
  UUID-suffixed staging table, so parallel imports never collide.
- **Parallel scraping** — the per-player HTTP loop uses an 8-thread
  pool, ~6-8× faster on top-150 refreshes.
- **Background scrape pause** — the extended-stats worker now pauses
  when "Scrape Live Data" is clicked, then resumes after.
- **Headless cron** — `python -m tennis_app.cron` runs the same pipeline
  as the UI button, with no Qt dependency, so it can be triggered by
  Windows Task Scheduler.

### Schedule hourly scrapes (PC must be on)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
```

This creates a "TennisStats Hourly Scrape" task that runs `scrape.bat`
every hour.  See `scripts/install_task.ps1` for parameters.

---

## Cloud mode (GitHub Actions + Turso)

See [`cloud/README.md`](cloud/README.md) for the full setup.  TL;DR:

1. `turso db create tennis-stats` → save URL + auth token
2. Add the two values as GitHub repo secrets
3. Push `.github/workflows/scrape.yml` — hourly cron starts immediately
4. `python -m cloud.run_app` on the desktop reads from the synced replica

Both modes share the entire `tennis_app/` source tree; the cloud variant
is a thin overlay (~200 LoC) that swaps the SQLite connection for a
libSQL embedded replica.
