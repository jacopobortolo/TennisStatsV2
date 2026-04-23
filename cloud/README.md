# Cloud version — GitHub Actions + Turso

This folder contains the **24/7 cloud variant** of TennisStatsV2.  The
desktop UI is identical to the local app, but the database lives on
[Turso](https://turso.tech) (libSQL / SQLite-compatible) and is kept
fresh by an hourly [GitHub Actions](https://docs.github.com/en/actions)
cron job — so the data stays current even when your PC is off.

```
┌────────────────────┐  hourly cron     ┌──────────────┐
│ GitHub Actions     │ ───────────────► │ Turso (free) │
│  cloud.scrape_job  │                  │  libSQL DB   │
└────────────────────┘                  └──────┬───────┘
                                               │ embedded
                                               │ replica
                                               ▼
                                       ┌──────────────┐
                                       │ Desktop app  │
                                       │ cloud.run_app│
                                       └──────────────┘
```

## Free-tier limits (more than enough for this app)

| Service         | Limit                                  |
| --------------- | -------------------------------------- |
| Turso           | 500 DBs · 9 GB storage · 1 B row reads/mo |
| GitHub Actions  | 2,000 min/mo (public repo: unlimited) |

A single hourly scrape uses ~3-5 minutes → ~120 min/mo, well inside the
free quota.

---

## One-time setup

### 1. Create the Turso database

```powershell
# Install the Turso CLI (Windows: scoop / winget; macOS/Linux: brew)
winget install ChiselStrike.Turso
turso auth signup           # or: turso auth login
turso db create tennis-stats
turso db tokens create tennis-stats --expiration none
turso db show tennis-stats --url
```

Save the URL (`libsql://tennis-stats-<org>.turso.io`) and the token.

### 2. Add GitHub secrets

In your repo → **Settings → Secrets and variables → Actions → New
repository secret**:

| Name                  | Value                          |
| --------------------- | ------------------------------ |
| `TURSO_DATABASE_URL`  | `libsql://tennis-stats-...turso.io` |
| `TURSO_AUTH_TOKEN`    | the long token from the CLI    |

### 3. Seed the remote DB (one-time only)

The first run will be slow because the DB is empty.  You can either:

- **Let it self-populate** — the workflow will create tables and start
  scraping; after ~3-4 cycles you'll have current top-150 matches.

- **Bootstrap from your local DB** (recommended):

  ```powershell
  cd C:\Users\jacop\Desktop\TennisStatsV2
  turso db shell tennis-stats < (sqlite3 .\tennis_stats.db .dump)
  ```

  *(Use Git Bash / WSL on Windows since `<` redirection differs in
  PowerShell — alternatively pipe with `Get-Content`.)*

### 4. Enable the workflow

Push the `.github/workflows/scrape.yml` file to GitHub.  The schedule
(`7 * * * *`) starts running automatically.  Trigger one manually first
to verify: **Actions → Hourly Tennis Scrape → Run workflow**.

---

## Running the desktop app in cloud mode

```powershell
cd C:\Users\jacop\Desktop\TennisStatsV2
copy cloud\.env.example cloud\.env
# edit cloud\.env: paste TURSO_DATABASE_URL + TURSO_AUTH_TOKEN
pip install -r requirements.txt
pip install libsql-experimental
python -m cloud.run_app
```

The app downloads a local replica (`~/.tennis_analytics/cloud.db`) on
first launch, then keeps it in sync with Turso every 60 seconds in the
background.  Subsequent launches open instantly from the cached replica.

### Offline behaviour

If Turso is unreachable (no network, expired token, etc.) the app
silently keeps using the last-synced local replica.  You'll see a single
warning in the logs but the UI stays fully functional.

---

## Files

| Path                                | Purpose                              |
| ----------------------------------- | ------------------------------------ |
| `cloud/db.py`                       | Turso connection + read-only wrapper |
| `cloud/run_app.py`                  | Desktop entrypoint (cloud mode)      |
| `cloud/scrape_job.py`               | What the GH Actions workflow runs    |
| `cloud/.env.example`                | Template for local credentials       |
| `.github/workflows/scrape.yml`      | Hourly cron workflow                 |
| `requirements-cloud.txt`            | Server-side deps (no PySide6)        |

---

## Troubleshooting

**`libsql_experimental` install fails on Windows**
The wheel is published only for CPython 3.9-3.12.  Use Python 3.12
(`pyenv` or the standalone installer) for the cloud-mode desktop app.
The GitHub Actions job already pins 3.12 in the workflow.

**"database is locked" in Actions**
The decorator + `busy_timeout=10000` from the local fix is included in
the cloud version too, so this should not happen.  If it does, lower the
top-N (`workflow_dispatch` → `top_n: 50`) to shorten the run.

**Scrape takes >50 min and times out**
Default workflow timeout is 50 min.  Bump it in `scrape.yml` or split
into two jobs (matches / extended-stats).
