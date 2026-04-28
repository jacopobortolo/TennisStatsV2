"""
Cloud scrape job — runs inside GitHub Actions.

Strategy:
1. Open a libSQL connection to Turso (writable).
2. Pull the current schema/data into a local replica file.
3. Run the standard ``tennis_app.cron`` pipeline against the writable
   connection.  All inserts/updates are pushed to Turso.

This script is what the GitHub Actions workflow invokes hourly.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from datetime import datetime

import pandas as pd
import requests

logger = logging.getLogger("cloud.scrape_job")


PLAYERS_URLS = {
    "atp": "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_players.csv",
    "wta": "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_players.csv",
}


def _seed_players_if_empty(db):
    """Populate Turso ``players`` table on first run.

    The scraper's name-resolution logic (``_resolve_ranking_name``) relies on
    ``players`` to expand truncated ranking names like 'Daniel Mérida' into
    the full 'Daniel Mérida Aguilar' that tennisabstract uses in its URLs.
    Without this the scrape silently misses any player whose ranking name
    is shorter than their CSV name.
    """
    try:
        rs = db.conn.execute("SELECT COUNT(*) FROM players")
        existing = rs.fetchone()[0]
    except Exception:
        existing = 0
    if existing and existing > 100:
        logger.info("players table already seeded (%d rows)", existing)
        return

    logger.info("Seeding players table from Sackmann CSVs...")
    for tour, url in PLAYERS_URLS.items():
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            df = pd.read_csv(
                io.StringIO(resp.text),
                dtype={"player_id": str, "dob": str, "wikidata_id": str},
                low_memory=False,
            )
            if df.empty:
                continue
            df["tour"] = tour
            # Use the to_sql shim installed by RemoteTennisDatabase
            df.to_sql("players", db.conn, if_exists="append",
                      index=False, chunksize=500)
            logger.info("  %s: seeded %d players", tour.upper(), len(df))
        except Exception:
            logger.exception("Failed to seed %s players", tour)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Cloud scrape job (Turso)")
    parser.add_argument("--top", type=int, default=1000,
                        help="Top-N players to refresh per tour (default 1000)")
    parser.add_argument("--no-extended", action="store_true")
    parser.add_argument("--min-year", type=int, default=2025)
    parser.add_argument("--monday-boost", action="store_true",
                        help="(legacy, no-op — top is already full)")
    parser.add_argument("--seed-players-only", action="store_true",
                        help="Only seed the players table, then exit")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from .db import RemoteTennisDatabase
    from tennis_app.core.data_manager import (
        scrape_top_players_matches,
        scrape_top_players_extended_stats,
    )

    top_n = args.top
    logger.info("Cloud scrape: top_n=%d per tour", top_n)

    db = RemoteTennisDatabase()

    try:
        # Seed the players table on first run so name resolution works.
        _seed_players_if_empty(db)
        if args.seed_players_only:
            logger.info("Seed-only mode: done.")
            return 0
        # Cloud mode is "live-only": we don't import the 1.7M-row Sackmann
        # historical CSVs into Turso (would take hours via HTTP).  Use local
        # mode for full archive queries; cloud mode = always-fresh top-N.
        for tour in ("atp", "wta"):
            logger.info("=== %s: scraping top %d ===", tour.upper(), top_n)
            matches_df, rankings, scraped_names = scrape_top_players_matches(
                top_n=top_n, tour=tour,
                progress_callback=lambda c, t, m: logger.info(
                    "  [%d/%d] %s", c, t, m),
                db=db,
                cache_expire_hours=24,
                min_year=args.min_year,
            )
            if not matches_df.empty:
                db.import_scraped_matches(
                    matches_df, scraped_player_names=scraped_names,
                    replace_existing=False)
            if rankings:
                db.import_scraped_rankings(rankings)

        if not args.no_extended:
            for tour in ("atp", "wta"):
                logger.info("=== %s: extended stats (top %d) ===",
                            tour.upper(), top_n)
                scrape_top_players_extended_stats(
                    top_n=top_n, tour=tour,
                    progress_callback=lambda c, t, m: logger.info(
                        "  [%d/%d] %s", c, t, m),
                    db=db,
                )

        # Final commit (no-op for remote — every call already round-trips)
        logger.info("Cloud scrape complete")
    except Exception:
        logger.exception("Cloud scrape failed")
        return 1
    finally:
        try:
            db.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
