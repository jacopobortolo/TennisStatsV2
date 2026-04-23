"""
Cloud-mode database connector for TennisStatsV2.

Uses Turso's libSQL Python driver (`libsql_experimental`) with embedded
replica sync: the desktop app opens a local SQLite file that auto-syncs
to a remote Turso database.  Reads are local (zero latency); writes are
forwarded to the remote.

Falls back to plain ``sqlite3`` against a local snapshot if the libsql
driver is unavailable or sync fails (so the desktop app still works
offline).

Configuration via environment variables (or ``cloud/.env``):
    TURSO_DATABASE_URL   libsql://<db>-<org>.turso.io
    TURSO_AUTH_TOKEN     long-lived auth token from `turso db tokens create`
    TURSO_LOCAL_PATH     optional, defaults to ~/.tennis_analytics/cloud.db

Read-only desktop apps should leave write operations to the GitHub
Actions cron job (see ``.github/workflows/scrape.yml``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_dotenv():
    """Cheap .env loader so users don't need python-dotenv."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def get_local_replica_path() -> Path:
    raw = os.environ.get("TURSO_LOCAL_PATH")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".tennis_analytics" / "cloud.db"


def open_connection(read_only: bool = True, sync_interval: int = 60):
    """Open a libSQL embedded-replica connection.

    Parameters
    ----------
    read_only : bool
        If True (default for desktop), opens a local replica that pulls
        from the remote on a schedule.  If False, opens a read/write
        connection (used by the scrape job).
    sync_interval : int
        Seconds between automatic background syncs of the replica.
    """
    url   = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    local = get_local_replica_path()
    local.parent.mkdir(parents=True, exist_ok=True)

    if not url:
        logger.warning("TURSO_DATABASE_URL not set — opening local snapshot only")
        import sqlite3
        return sqlite3.connect(str(local), check_same_thread=False)

    try:
        import libsql_experimental as libsql  # type: ignore
    except ImportError:
        logger.error(
            "libsql_experimental not installed; run `pip install "
            "libsql-experimental`. Falling back to local snapshot."
        )
        import sqlite3
        return sqlite3.connect(str(local), check_same_thread=False)

    logger.info("Opening Turso replica %s -> %s (sync every %ds)",
                url, local, sync_interval)
    conn = libsql.connect(
        str(local),
        sync_url=url,
        auth_token=token,
        sync_interval=sync_interval,
    )
    try:
        conn.sync()
    except Exception as exc:
        logger.warning("Initial sync failed (%s); using cached replica", exc)
    return conn


class CloudTennisDatabase:
    """Drop-in replacement for ``TennisDatabase`` that talks to Turso.

    Reuses the entire SQL surface of the local TennisDatabase by
    monkey-patching the ``conn`` attribute.  This avoids forking thousands
    of lines of query code.
    """

    def __init__(self, read_only: bool = True, sync_interval: int = 60):
        from tennis_app.core import database as _db

        # Create a real TennisDatabase but swap its connection.
        # We do NOT call its __init__ (which would create local file);
        # instead we replicate the bits we need.
        self._impl = object.__new__(_db.TennisDatabase)
        self._impl.db_path = get_local_replica_path()
        self._impl.conn = open_connection(read_only=read_only,
                                          sync_interval=sync_interval)
        try:
            self._impl.conn.row_factory  # libsql supports it
        except AttributeError:
            pass
        else:
            import sqlite3
            self._impl.conn.row_factory = sqlite3.Row

        import threading
        self._impl._write_lock = threading.RLock()

        # Pragmas (no-op on Turso remote, applied to local replica)
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA cache_size=-64000",
            "PRAGMA temp_store=MEMORY",
            "PRAGMA busy_timeout=10000",
        ):
            try:
                self._impl.conn.execute(pragma)
            except Exception:
                pass

        # Ensure schema (safe on existing DB)
        try:
            self._impl._create_tables()
        except Exception as exc:
            logger.warning("Schema check failed: %s", exc)

    def __getattr__(self, item):
        # Delegate everything else to the wrapped TennisDatabase
        return getattr(self._impl, item)

    def sync(self):
        """Force an immediate replica sync."""
        if hasattr(self._impl.conn, "sync"):
            self._impl.conn.sync()
