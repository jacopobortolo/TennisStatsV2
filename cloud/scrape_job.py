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
import logging
import sys
from datetime import datetime

logger = logging.getLogger("cloud.scrape_job")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Cloud scrape job (Turso)")
    parser.add_argument("--top", type=int, default=150)
    parser.add_argument("--no-extended", action="store_true")
    parser.add_argument("--min-year", type=int, default=2025)
    parser.add_argument("--monday-boost", action="store_true")
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
    if args.monday_boost and datetime.today().weekday() == 0:
        top_n = max(top_n, 1000)
        logger.info("Monday boost active: top_n=%d", top_n)

    db = RemoteTennisDatabase()

    try:
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
                cache_expire_hours=6,
                min_year=args.min_year,
            )
            if not matches_df.empty:
                db.import_scraped_matches(
                    matches_df, scraped_player_names=scraped_names)
            if rankings:
                db.import_scraped_rankings(rankings)

        if not args.no_extended:
            for tour in ("atp", "wta"):
                logger.info("=== %s: extended stats ===", tour.upper())
                scrape_top_players_extended_stats(
                    top_n=min(150, top_n), tour=tour,
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
