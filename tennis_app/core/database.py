"""
SQLite database layer for fast querying of tennis data.
"""

import sqlite3
import logging
import threading
import unicodedata
import uuid
from pathlib import Path

import pandas as pd

from .data_manager import (get_data_dir, load_players, load_matches,
                           load_rankings, load_doubles,
                           scrape_player_matches,
                           scrape_current_rankings, scrape_top_players_matches)

logger = logging.getLogger(__name__)


def _strip_diacritics(text):
    """Remove diacritical marks from a string (e.g. João → Joao)."""
    if not text or not isinstance(text, str):
        return text
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _locked_write(method):
    """Decorator: serialize a write method through ``self._write_lock``.

    Required because the Qt UI shares a single SQLite connection across
    QThread workers (live scrape + background extended-stats), and SQLite
    write transactions are not concurrent-safe at the connection level
    even with WAL.
    """
    def wrapper(self, *args, **kwargs):
        with self._write_lock:
            return method(self, *args, **kwargs)
    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


def get_db_path():
    return get_data_dir() / "tennis.db"


class TennisDatabase:
    """SQLite-backed database for tennis analytics."""

    def __init__(self):
        self.db_path = get_db_path()
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Re-entrant write lock to serialize writes across QThread workers
        # (live scrape worker + background extended-stats worker share the
        # same connection and would otherwise raise "database is locked").
        self._write_lock = threading.RLock()
        # Performance pragmas
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")  # 64 MB cache
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA mmap_size=268435456")  # 256 MB mmap
        # Wait up to 10s for SQLite-level locks to clear before erroring
        self.conn.execute("PRAGMA busy_timeout=10000")
        self._create_tables()
        self._run_analyze_once()

    def _create_tables(self):
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS players (
                player_id TEXT,
                name_first TEXT,
                name_last TEXT,
                hand TEXT,
                dob TEXT,
                ioc TEXT,
                height REAL,
                wikidata_id TEXT,
                tour TEXT,
                PRIMARY KEY (player_id, tour)
            );

            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tourney_id TEXT,
                tourney_name TEXT,
                surface TEXT,
                draw_size INTEGER,
                tourney_level TEXT,
                tourney_date TEXT,
                match_num INTEGER,
                winner_id TEXT,
                winner_seed TEXT,
                winner_entry TEXT,
                winner_name TEXT,
                winner_hand TEXT,
                winner_ht REAL,
                winner_ioc TEXT,
                winner_age REAL,
                loser_id TEXT,
                loser_seed TEXT,
                loser_entry TEXT,
                loser_name TEXT,
                loser_hand TEXT,
                loser_ht REAL,
                loser_ioc TEXT,
                loser_age REAL,
                score TEXT,
                best_of INTEGER,
                round TEXT,
                minutes REAL,
                w_ace REAL, w_df REAL, w_svpt REAL, w_1stIn REAL,
                w_1stWon REAL, w_2ndWon REAL, w_SvGms REAL,
                w_bpSaved REAL, w_bpFaced REAL,
                l_ace REAL, l_df REAL, l_svpt REAL, l_1stIn REAL,
                l_1stWon REAL, l_2ndWon REAL, l_SvGms REAL,
                l_bpSaved REAL, l_bpFaced REAL,
                winner_rank REAL, winner_rank_points REAL,
                loser_rank REAL, loser_rank_points REAL,
                tour TEXT
            );

            CREATE TABLE IF NOT EXISTS rankings (
                ranking_date TEXT,
                rank INTEGER,
                player_id TEXT,
                points INTEGER,
                tour TEXT,
                age INTEGER,
                rank_diff INTEGER,
                pts_diff INTEGER,
                next_tournament TEXT,
                ioc TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_matches_winner ON matches(winner_id);
            CREATE INDEX IF NOT EXISTS idx_matches_loser ON matches(loser_id);
            CREATE INDEX IF NOT EXISTS idx_matches_tourney ON matches(tourney_name);
            CREATE INDEX IF NOT EXISTS idx_matches_surface ON matches(surface);
            CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(tourney_date);
            CREATE INDEX IF NOT EXISTS idx_matches_winner_name ON matches(winner_name);
            CREATE INDEX IF NOT EXISTS idx_matches_loser_name ON matches(loser_name);
            CREATE INDEX IF NOT EXISTS idx_rankings_player ON rankings(player_id);
            CREATE INDEX IF NOT EXISTS idx_players_name ON players(name_last, name_first);

            CREATE TABLE IF NOT EXISTS scrape_cache (
                player_name TEXT PRIMARY KEY,
                last_scraped TEXT NOT NULL,
                match_count INTEGER DEFAULT 0,
                last_match_date TEXT,
                activity_fingerprint TEXT
            );

            CREATE TABLE IF NOT EXISTS doubles_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tourney_id TEXT,
                tourney_name TEXT,
                surface TEXT,
                draw_size INTEGER,
                tourney_level TEXT,
                tourney_date TEXT,
                match_num INTEGER,
                winner1_id TEXT,
                winner2_id TEXT,
                winner_seed TEXT,
                winner_entry TEXT,
                loser1_id TEXT,
                loser2_id TEXT,
                loser_seed TEXT,
                loser_entry TEXT,
                score TEXT,
                best_of INTEGER,
                round TEXT,
                winner1_name TEXT, winner1_hand TEXT, winner1_ht REAL,
                winner1_ioc TEXT, winner1_age REAL,
                winner2_name TEXT, winner2_hand TEXT, winner2_ht REAL,
                winner2_ioc TEXT, winner2_age REAL,
                loser1_name TEXT, loser1_hand TEXT, loser1_ht REAL,
                loser1_ioc TEXT, loser1_age REAL,
                loser2_name TEXT, loser2_hand TEXT, loser2_ht REAL,
                loser2_ioc TEXT, loser2_age REAL,
                winner1_rank REAL, winner1_rank_points REAL,
                winner2_rank REAL, winner2_rank_points REAL,
                loser1_rank REAL, loser1_rank_points REAL,
                loser2_rank REAL, loser2_rank_points REAL,
                minutes REAL,
                w_ace REAL, w_df REAL, w_svpt REAL, w_1stIn REAL,
                w_1stWon REAL, w_2ndWon REAL, w_SvGms REAL,
                w_bpSaved REAL, w_bpFaced REAL,
                l_ace REAL, l_df REAL, l_svpt REAL, l_1stIn REAL,
                l_1stWon REAL, l_2ndWon REAL, l_SvGms REAL,
                l_bpSaved REAL, l_bpFaced REAL,
                tour TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_doubles_tourney ON doubles_matches(tourney_name);
            CREATE INDEX IF NOT EXISTS idx_doubles_date ON doubles_matches(tourney_date);
            CREATE INDEX IF NOT EXISTS idx_matches_tour ON matches(tour);
            CREATE INDEX IF NOT EXISTS idx_doubles_tour ON doubles_matches(tour);
            CREATE INDEX IF NOT EXISTS idx_rankings_tour ON rankings(tour);

            -- Extended stats tables (scraped from player-more.cgi)
            CREATE TABLE IF NOT EXISTS match_winners_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                player_name TEXT,
                opponent_name TEXT,
                tourney_name TEXT,
                tourney_date TEXT,
                round TEXT,
                surface TEXT,
                tourney_level TEXT,
                score TEXT,
                winners INTEGER,
                unforced_errors INTEGER,
                w_ue_ratio REAL,
                winner_pct REAL,
                ue_pct REAL,
                opp_winners INTEGER,
                opp_unforced_errors INTEGER,
                opp_w_ue_ratio REAL,
                opp_winner_pct REAL,
                opp_ue_pct REAL,
                net_points_won INTEGER,
                net_points_total INTEGER,
                opp_net_points_won INTEGER,
                opp_net_points_total INTEGER,
                tour TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_we_player ON match_winners_errors(player_name);
            CREATE INDEX IF NOT EXISTS idx_we_date ON match_winners_errors(tourney_date);

            CREATE TABLE IF NOT EXISTS match_serve_speed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                player_name TEXT,
                opponent_name TEXT,
                tourney_name TEXT,
                tourney_date TEXT,
                round TEXT,
                surface TEXT,
                tourney_level TEXT,
                first_serve_avg REAL,
                first_serve_stdev REAL,
                first_serve_median REAL,
                first_serve_max REAL,
                first_serve_min REAL,
                second_serve_avg REAL,
                second_serve_stdev REAL,
                second_serve_median REAL,
                second_serve_max REAL,
                second_serve_min REAL,
                speed_unit TEXT DEFAULT 'mph',
                tour TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_speed_player ON match_serve_speed(player_name);

            CREATE TABLE IF NOT EXISTS match_pbp_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                player_name TEXT,
                opponent_name TEXT,
                tourney_name TEXT,
                tourney_date TEXT,
                round TEXT,
                surface TEXT,
                tourney_level TEXT,
                aggressive_margin REAL,
                serve_plus1_ratio REAL,
                baseline_pct REAL,
                rally_length_avg REAL,
                ace_on_serve_plus1_pct REAL,
                hold_after_ace_pct REAL,
                return_pts_won_pct REAL,
                opp_aggressive_margin REAL,
                opp_serve_plus1_ratio REAL,
                opp_baseline_pct REAL,
                opp_rally_length_avg REAL,
                tour TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pbp_player ON match_pbp_stats(player_name);

            CREATE TABLE IF NOT EXISTS match_mcp_serve (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                player_name TEXT,
                opponent_name TEXT,
                tourney_name TEXT,
                tourney_date TEXT,
                round TEXT,
                surface TEXT,
                tourney_level TEXT,
                deuce_wide_pct REAL,
                deuce_body_pct REAL,
                deuce_t_pct REAL,
                ad_wide_pct REAL,
                ad_body_pct REAL,
                ad_t_pct REAL,
                ace_pct REAL,
                unreturned_pct REAL,
                first_wide_pct REAL,
                first_body_pct REAL,
                first_t_pct REAL,
                second_wide_pct REAL,
                second_body_pct REAL,
                second_t_pct REAL,
                tour TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mcp_serve_player ON match_mcp_serve(player_name);

            CREATE TABLE IF NOT EXISTS match_mcp_return (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                player_name TEXT,
                opponent_name TEXT,
                tourney_name TEXT,
                tourney_date TEXT,
                round TEXT,
                surface TEXT,
                tourney_level TEXT,
                return_in_play_pct REAL,
                return_depth_deep_pct REAL,
                return_fh_pct REAL,
                return_bh_pct REAL,
                first_return_in_play_pct REAL,
                second_return_in_play_pct REAL,
                tour TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mcp_return_player ON match_mcp_return(player_name);

            CREATE TABLE IF NOT EXISTS match_mcp_rally (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                player_name TEXT,
                opponent_name TEXT,
                tourney_name TEXT,
                tourney_date TEXT,
                round TEXT,
                surface TEXT,
                tourney_level TEXT,
                rally_length_avg REAL,
                fh_pct REAL,
                bh_pct REAL,
                net_approach_pct REAL,
                rally_won_0_4 REAL,
                rally_won_5_8 REAL,
                rally_won_9_plus REAL,
                tour TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mcp_rally_player ON match_mcp_rally(player_name);

            CREATE TABLE IF NOT EXISTS match_mcp_tactics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                player_name TEXT,
                opponent_name TEXT,
                tourney_name TEXT,
                tourney_date TEXT,
                round TEXT,
                surface TEXT,
                tourney_level TEXT,
                net_approach_pct REAL,
                net_points_won_pct REAL,
                dropshot_pct REAL,
                dropshot_won_pct REAL,
                serve_and_volley_pct REAL,
                sv_won_pct REAL,
                inside_in_fh_pct REAL,
                inside_out_fh_pct REAL,
                tour TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_mcp_tactics_player ON match_mcp_tactics(player_name);

            -- Cache for extended stats scraping
            CREATE TABLE IF NOT EXISTS extended_stats_cache (
                player_name TEXT PRIMARY KEY,
                last_scraped TEXT NOT NULL,
                tables_scraped TEXT,
                activity_fingerprint TEXT
            );
        """)
        # Migrate existing rankings table: add columns introduced with
        # the live-tennis.eu switch (safe to call repeatedly).
        for col, typ in [("age", "INTEGER"), ("rank_diff", "INTEGER"),
                         ("pts_diff", "INTEGER"), ("next_tournament", "TEXT"),
                         ("ioc", "TEXT")]:
            try:
                cur.execute(f"ALTER TABLE rankings ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # column already exists
        # Migrate existing players table: add tour column.
        try:
            cur.execute("ALTER TABLE players ADD COLUMN tour TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Migrate scrape_cache: add last_match_date column.
        try:
            cur.execute("ALTER TABLE scrape_cache ADD COLUMN last_match_date TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Migrate scrape_cache: add activity_fingerprint column.
        try:
            cur.execute("ALTER TABLE scrape_cache ADD COLUMN activity_fingerprint TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Migrate extended_stats_cache: add activity_fingerprint column.
        try:
            cur.execute("ALTER TABLE extended_stats_cache ADD COLUMN activity_fingerprint TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Migrate extended stats tables: add tourney_level column.
        for tbl in ("match_winners_errors", "match_serve_speed",
                     "match_pbp_stats", "match_mcp_serve",
                     "match_mcp_return", "match_mcp_rally",
                     "match_mcp_tactics"):
            try:
                cur.execute(f"ALTER TABLE {tbl} ADD COLUMN tourney_level TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists

        # --- Phase 2 composite indexes for faster queries ---
        cur.executescript("""
            -- Matches: composite indexes for common query patterns
            CREATE INDEX IF NOT EXISTS idx_matches_tour_date
                ON matches(tour, tourney_date);
            CREATE INDEX IF NOT EXISTS idx_matches_winner_tour
                ON matches(winner_id, tour, tourney_date);
            CREATE INDEX IF NOT EXISTS idx_matches_loser_tour
                ON matches(loser_id, tour, tourney_date);
            CREATE INDEX IF NOT EXISTS idx_matches_tourney_date
                ON matches(tourney_name, tourney_date);

            -- Rankings: composite for ranking lookups
            CREATE INDEX IF NOT EXISTS idx_rankings_pid_date
                ON rankings(player_id, ranking_date);
            CREATE INDEX IF NOT EXISTS idx_rankings_tour_date
                ON rankings(tour, ranking_date);

            -- Extended stats: composite (player_name, tourney_date) for
            -- all get_player_* queries that ORDER BY tourney_date DESC
            CREATE INDEX IF NOT EXISTS idx_we_player_date
                ON match_winners_errors(player_name, tourney_date);
            CREATE INDEX IF NOT EXISTS idx_speed_player_date
                ON match_serve_speed(player_name, tourney_date);
            CREATE INDEX IF NOT EXISTS idx_pbp_player_date
                ON match_pbp_stats(player_name, tourney_date);
            CREATE INDEX IF NOT EXISTS idx_mcp_serve_player_date
                ON match_mcp_serve(player_name, tourney_date);
            CREATE INDEX IF NOT EXISTS idx_mcp_return_player_date
                ON match_mcp_return(player_name, tourney_date);
            CREATE INDEX IF NOT EXISTS idx_mcp_rally_player_date
                ON match_mcp_rally(player_name, tourney_date);
            CREATE INDEX IF NOT EXISTS idx_mcp_tactics_player_date
                ON match_mcp_tactics(player_name, tourney_date);
        """)
        self.conn.commit()

    def _run_analyze_once(self):
        """Run ANALYZE once to update query planner statistics.
        Skips on subsequent launches by checking a user_version flag."""
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            logger.info("Running ANALYZE to update query planner statistics...")
            self.conn.execute("ANALYZE")
            self.conn.execute("PRAGMA user_version = 2")
            self.conn.commit()

    def has_data(self, tour="atp"):
        """Return True if this tour already has matches imported."""
        row = self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM matches WHERE tour = ? AND tourney_id != 'SCRAPED' LIMIT 1)",
            (tour,)).fetchone()
        return row[0] == 1

    def has_doubles_data(self, tour="atp"):
        """Return True if this tour already has doubles matches imported."""
        row = self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM doubles_matches WHERE tour = ? LIMIT 1)",
            (tour,)).fetchone()
        return row[0] == 1

    def import_doubles_only(self, tour="atp", year_start=1968, year_end=2024):
        """Import only doubles matches (used when singles already imported)."""
        doubles = load_doubles(tour, year_start, year_end)
        if not doubles.empty:
            self.conn.execute(
                "DELETE FROM doubles_matches WHERE tour = ?", (tour,))
            self.conn.commit()
            doubles["tour"] = tour
            _chunk = max(1, 999 // max(len(doubles.columns), 1))
            doubles.to_sql("doubles_matches", self.conn,
                           if_exists="append", index=False,
                           method="multi", chunksize=_chunk)
            self.conn.commit()
            logger.info("Imported %d doubles matches for %s", len(doubles), tour)

    def import_data(self, tour="atp", year_start=1968, year_end=2024,
                    progress_callback=None):
        """Import CSV data into the SQLite database."""
        if progress_callback:
            progress_callback(0, 4, "Loading players...")

        players = load_players(tour)
        if not players.empty:
            self._clear_table("players", tour)
            players["tour"] = tour
            players.to_sql("players", self.conn, if_exists="append", index=False)
            logger.info("Imported %d players", len(players))

        if progress_callback:
            progress_callback(1, 4, "Loading matches...")

        matches = load_matches(tour, year_start, year_end, include_qual=True)
        if not matches.empty:
            self._clear_matches(tour)
            matches["tour"] = tour
            # Normalize player names: strip diacritics for consistency
            for col in ("winner_name", "loser_name"):
                if col in matches.columns:
                    matches[col] = matches[col].apply(_strip_diacritics)
            _chunk = max(1, 999 // max(len(matches.columns), 1))
            matches.to_sql("matches", self.conn, if_exists="append", index=False,
                           method="multi", chunksize=_chunk)
            logger.info("Imported %d matches", len(matches))

        if progress_callback:
            progress_callback(2, 4, "Loading doubles...")

        doubles = load_doubles(tour, year_start, year_end)
        if not doubles.empty:
            self.conn.execute(
                "DELETE FROM doubles_matches WHERE tour = ?", (tour,))
            self.conn.commit()
            doubles["tour"] = tour
            _chunk = max(1, 999 // max(len(doubles.columns), 1))
            doubles.to_sql("doubles_matches", self.conn,
                           if_exists="append", index=False,
                           method="multi", chunksize=_chunk)
            logger.info("Imported %d doubles matches", len(doubles))

        if progress_callback:
            progress_callback(3, 4, "Loading rankings...")

        rankings = load_rankings(tour)
        if not rankings.empty:
            self._clear_rankings(tour)
            rankings = rankings.rename(columns={"player": "player_id"})
            # WTA CSVs have an extra 'tours' column not in our schema
            rankings = rankings.drop(columns=["tours"], errors="ignore")
            rankings["tour"] = tour
            _chunk = max(1, 999 // max(len(rankings.columns), 1))
            rankings.to_sql("rankings", self.conn, if_exists="append", index=False,
                           method="multi", chunksize=_chunk)
            logger.info("Imported %d ranking entries", len(rankings))

        if progress_callback:
            progress_callback(4, 4, "Import complete!")

        self.conn.commit()

    def _clear_table(self, table, tour):
        if table == "players":
            self.conn.execute("DELETE FROM players WHERE tour = ?", (tour,))
        self.conn.commit()

    def _clear_matches(self, tour):
        self.conn.execute(
            "DELETE FROM matches WHERE tour = ? AND tourney_id != 'SCRAPED'",
            (tour,))
        self.conn.commit()

    def _clear_rankings(self, tour):
        self.conn.execute(
            "DELETE FROM rankings WHERE tour = ? AND ranking_date != 'LIVE'",
            (tour,))
        self.conn.commit()

    def search_players(self, query, limit=20):
        """Search players by name (partial match), best matches first."""
        cur = self.conn.execute("""
            SELECT player_id, name_first, name_last, hand, dob, ioc, height, tour,
                   CASE
                     WHEN LOWER(name_first || ' ' || name_last) = LOWER(?) THEN 0
                     WHEN LOWER(name_last) = LOWER(?) THEN 1
                     WHEN LOWER(name_first) = LOWER(?) THEN 2
                     ELSE 3
                   END AS rank_score
            FROM players
            WHERE name_first || ' ' || name_last LIKE ?
            ORDER BY rank_score, name_last, name_first
            LIMIT ?
        """, (query, query, query, f"%{query}%", limit))
        return [dict(r) for r in cur.fetchall()]

    def get_player(self, player_id, tour=None):
        """Get a single player by ID."""
        if tour:
            cur = self.conn.execute(
                "SELECT * FROM players WHERE player_id = ? AND tour = ?",
                (player_id, tour))
        else:
            cur = self.conn.execute(
                "SELECT * FROM players WHERE player_id = ?", (player_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_player_matches(self, player_id, surface=None, tourney_level=None,
                           year=None, opponent_id=None, round_=None,
                           tour=None):
        """Get all matches for a player with optional filters."""
        conditions = ["(winner_id = ? OR loser_id = ?)"]
        params = [player_id, player_id]

        if tour:
            conditions.append("tour = ?")
            params.append(tour)

        if surface:
            conditions.append("surface = ?")
            params.append(surface)
        if tourney_level:
            conditions.append("tourney_level = ?")
            params.append(tourney_level)
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if opponent_id:
            conditions.append(
                "((winner_id = ? AND loser_id = ?) OR "
                "(winner_id = ? AND loser_id = ?))"
            )
            params.extend([player_id, opponent_id, opponent_id, player_id])
        if round_:
            conditions.append("round = ?")
            params.append(round_)

        where = " AND ".join(conditions)
        query = f"""
            SELECT * FROM matches
            WHERE {where}
            ORDER BY tourney_date DESC,
                CASE round
                    WHEN 'F' THEN 1
                    WHEN 'SF' THEN 2
                    WHEN 'QF' THEN 3
                    WHEN 'R16' THEN 4
                    WHEN 'R32' THEN 5
                    WHEN 'R64' THEN 6
                    WHEN 'R128' THEN 7
                    WHEN 'RR' THEN 8
                    ELSE 9
                END,
                match_num DESC
        """
        cur = self.conn.execute(query, params)
        return [dict(r) for r in cur.fetchall()]

    def get_player_career_stats(self, player_id, tour=None):
        """Calculate career statistics for a player."""
        tour_cond = " AND tour = ?" if tour else ""
        tour_params = (tour,) if tour else ()

        # Single query to get all breakdowns at once
        rows = self.conn.execute(f"""
            SELECT side, surface, tourney_level, round,
                   SUBSTR(tourney_date, 1, 4) as yr, COUNT(*) as cnt,
                   SUM(aces) as aces, SUM(dfs) as dfs,
                   SUM(svpt) as svpt, SUM(first_in) as first_in,
                   SUM(first_won) as first_won, SUM(second_won) as second_won,
                   SUM(bp_saved) as bp_saved, SUM(bp_faced) as bp_faced
            FROM (
                SELECT 'W' as side, surface, tourney_level, round, tourney_date,
                       w_ace as aces, w_df as dfs, w_svpt as svpt,
                       w_1stIn as first_in, w_1stWon as first_won,
                       w_2ndWon as second_won, w_bpSaved as bp_saved,
                       w_bpFaced as bp_faced
                FROM matches WHERE winner_id = ?{tour_cond}
                UNION ALL
                SELECT 'L' as side, surface, tourney_level, round, tourney_date,
                       l_ace as aces, l_df as dfs, l_svpt as svpt,
                       l_1stIn as first_in, l_1stWon as first_won,
                       l_2ndWon as second_won, l_bpSaved as bp_saved,
                       l_bpFaced as bp_faced
                FROM matches WHERE loser_id = ?{tour_cond}
            )
            GROUP BY side, surface, tourney_level, round, yr
        """, (player_id,) + tour_params + (player_id,) + tour_params).fetchall()

        wins = 0
        losses = 0
        surfaces = {}
        levels = {}
        rounds = {}
        titles = 0
        yearly = {}
        total_aces = 0
        total_dfs = 0
        total_svpt = 0
        total_first_in = 0
        total_first_won = 0
        total_second_won = 0
        total_bp_saved = 0
        total_bp_faced = 0

        level_names = {
            "G": "Grand Slam", "M": "Masters 1000", "A": "ATP 250/500",
            "D": "Davis Cup", "F": "Tour Finals",
        }

        for r in rows:
            side = r["side"]
            cnt = r["cnt"]
            surf = r["surface"] or "Unknown"
            lev = r["tourney_level"] or ""
            rnd = r["round"] or ""
            yr = r["yr"] or ""

            if side == "W":
                wins += cnt
                if rnd == "F":
                    titles += cnt
            else:
                losses += cnt

            # Surface
            surfaces.setdefault(surf, {"wins": 0, "losses": 0})
            surfaces[surf]["wins" if side == "W" else "losses"] += cnt

            # Level
            lev_name = level_names.get(lev)
            if lev_name:
                levels.setdefault(lev_name, {"wins": 0, "losses": 0})
                levels[lev_name]["wins" if side == "W" else "losses"] += cnt

            # Round
            if rnd in ("F", "SF", "QF", "R16", "R32", "R64", "R128", "RR"):
                rounds.setdefault(rnd, {"wins": 0, "losses": 0})
                rounds[rnd]["wins" if side == "W" else "losses"] += cnt

            # Yearly
            if yr:
                yearly.setdefault(yr, {"wins": 0, "losses": 0})
                yearly[yr]["wins" if side == "W" else "losses"] += cnt

            # Serve stats
            total_aces += int(r["aces"] or 0)
            total_dfs += int(r["dfs"] or 0)
            total_svpt += int(r["svpt"] or 0)
            total_first_in += int(r["first_in"] or 0)
            total_first_won += int(r["first_won"] or 0)
            total_second_won += int(r["second_won"] or 0)
            total_bp_saved += int(r["bp_saved"] or 0)
            total_bp_faced += int(r["bp_faced"] or 0)

        # Remove surfaces with 0 matches
        surfaces = {k: v for k, v in surfaces.items()
                    if v["wins"] + v["losses"] > 0
                    and k in ("Hard", "Clay", "Grass", "Carpet")}

        serve = {}
        if total_svpt > 0:
            serve = {
                "aces": total_aces,
                "double_faults": total_dfs,
                "first_serve_pct": round(
                    total_first_in / total_svpt * 100, 1) if total_svpt else 0,
                "first_serve_won_pct": round(
                    total_first_won / total_first_in * 100, 1
                ) if total_first_in else 0,
                "second_serve_won_pct": round(
                    total_second_won / (total_svpt - total_first_in) * 100, 1
                ) if (total_svpt - total_first_in) else 0,
                "bp_saved_pct": round(
                    total_bp_saved / (total_bp_faced or 1) * 100, 1),
            }

        return {
            "wins": wins,
            "losses": losses,
            "titles": titles,
            "surfaces": surfaces,
            "levels": levels,
            "rounds": rounds,
            "serve": serve,
            "yearly": yearly,
        }

    def get_head_to_head(self, player1_id, player2_id, tour=None):
        """Get head-to-head record and matches between two players."""
        tour_cond = " AND tour = ?" if tour else ""
        tour_params = (tour,) if tour else ()
        matches = self.conn.execute(f"""
            SELECT * FROM matches
            WHERE ((winner_id = ? AND loser_id = ?)
               OR (winner_id = ? AND loser_id = ?)){tour_cond}
            ORDER BY tourney_date DESC
        """, (player1_id, player2_id, player2_id, player1_id) + tour_params).fetchall()

        p1_wins = sum(1 for m in matches if m["winner_id"] == player1_id)
        p2_wins = sum(1 for m in matches if m["winner_id"] == player2_id)

        # By surface
        h2h_surfaces = {}
        for m in matches:
            s = m["surface"] or "Unknown"
            h2h_surfaces.setdefault(s, {"p1_wins": 0, "p2_wins": 0})
            if m["winner_id"] == player1_id:
                h2h_surfaces[s]["p1_wins"] += 1
            else:
                h2h_surfaces[s]["p2_wins"] += 1

        return {
            "p1_wins": p1_wins,
            "p2_wins": p2_wins,
            "total_matches": len(matches),
            "by_surface": h2h_surfaces,
            "matches": [dict(m) for m in matches],
        }

    def get_rankings(self, tour="atp", date=None, top_n=100):
        """Get rankings, optionally filtered by date."""
        if date:
            target_date = date
        else:
            # Get latest ranking date
            latest = self.conn.execute("""
                SELECT MAX(ranking_date) FROM rankings WHERE tour = ?
            """, (tour,)).fetchone()[0]
            if not latest:
                return [], None
            target_date = latest

        # Use LEFT JOIN so LIVE rankings (where player_id may be a name
        # string rather than a real ID) still appear
        cur = self.conn.execute("""
            SELECT r.ranking_date, r.rank, r.player_id, r.points,
                   r.tour, r.age, r.rank_diff, r.pts_diff, r.next_tournament,
                   p.name_first, p.name_last,
                   COALESCE(p.ioc, r.ioc) AS ioc
            FROM rankings r
            LEFT JOIN players p ON r.player_id = p.player_id
                                   AND r.tour = p.tour
            WHERE r.tour = ? AND r.ranking_date = ?
            ORDER BY r.rank
            LIMIT ?
        """, (tour, target_date, top_n))

        results = []
        for row in cur:
            d = dict(row)
            # For LIVE rankings where player_id is actually a name string
            if d.get("name_first") is None and d.get("name_last") is None:
                pid = d.get("player_id", "")
                # player_id holds the full name when unresolved
                parts = pid.split(None, 1)
                if len(parts) == 2:
                    d["name_first"] = parts[0]
                    d["name_last"] = parts[1]
                else:
                    d["name_first"] = ""
                    d["name_last"] = pid
            results.append(d)

        return results, target_date

    # ------------------------------------------------------------------
    # Rank-filling helpers
    # ------------------------------------------------------------------

    def _fill_missing_ranks(self, matches):
        """Fill missing ranks using cross-reference + bounded fallbacks."""
        if not matches:
            return

        # --- pass 1: collect known ranks per player name from this result set ---
        name_rank = {}
        for m in matches:
            wn = (m.get("winner_name") or "").lower()
            ln = (m.get("loser_name") or "").lower()
            wr = m.get("winner_rank")
            lr = m.get("loser_rank")
            if wr and wn and wn not in name_rank:
                name_rank[wn] = wr
            if lr and ln and ln not in name_rank:
                name_rank[ln] = lr

        # --- pass 2: fill directly from cross-reference ---
        still_missing = set()
        for m in matches:
            wn = (m.get("winner_name") or "").lower()
            ln = (m.get("loser_name") or "").lower()
            if not m.get("winner_rank") and wn:
                if wn in name_rank:
                    m["winner_rank"] = name_rank[wn]
                else:
                    still_missing.add((wn, m.get("winner_id"), m.get("winner_name")))
            if not m.get("loser_rank") and ln:
                if ln in name_rank:
                    m["loser_rank"] = name_rank[ln]
                else:
                    still_missing.add((ln, m.get("loser_id"), m.get("loser_name")))

        if not still_missing:
            return

        # Determine tournament date and tour for bounded fallbacks
        tourney_date = None
        tour = None
        for m in matches:
            td = m.get("tourney_date")
            if td:
                tourney_date = str(td)
            if m.get("tour"):
                tour = m.get("tour")
            if tourney_date and tour:
                break

        from datetime import datetime, timedelta
        td_date = None
        min_dt_90 = None
        max_dt_90 = None
        min_dt_120 = None
        allow_scraped_fallback = False
        if tourney_date:
            try:
                td_date = datetime.strptime(tourney_date[:8], "%Y%m%d")
                min_dt_90 = (td_date - timedelta(days=90)).strftime("%Y%m%d")
                max_dt_90 = (td_date + timedelta(days=90)).strftime("%Y%m%d")
                min_dt_120 = (td_date - timedelta(days=120)).strftime("%Y%m%d")
                allow_scraped_fallback = abs((datetime.now() - td_date).days) <= 30
            except ValueError:
                td_date = None

        # --- pass 3: nearby matches within ±90 days ---
        nearby_rank = {}
        remaining = list(still_missing)
        if td_date and min_dt_90 and max_dt_90 and remaining:
            missing_names = [pname for _, _, pname in remaining if pname]
            if missing_names:
                placeholders = ",".join("?" * len(missing_names))
                tour_clause_w = " AND tour = ?" if tour else ""
                tour_clause_l = " AND tour = ?" if tour else ""
                params = missing_names + [min_dt_90, max_dt_90]
                if tour:
                    params.append(tour)
                params += missing_names + [min_dt_90, max_dt_90]
                if tour:
                    params.append(tour)

                cur = self.conn.execute(f"""
                    SELECT name, rank_val, tourney_date FROM (
                        SELECT winner_name AS name,
                               winner_rank AS rank_val, tourney_date
                        FROM matches
                        WHERE winner_name IN ({placeholders})
                          AND winner_rank IS NOT NULL
                          AND tourney_date BETWEEN ? AND ?
                          {tour_clause_w}
                        UNION ALL
                        SELECT loser_name AS name,
                               loser_rank AS rank_val, tourney_date
                        FROM matches
                        WHERE loser_name IN ({placeholders})
                          AND loser_rank IS NOT NULL
                          AND tourney_date BETWEEN ? AND ?
                          {tour_clause_l}
                    )
                """, params)

                best = {}  # name_lower -> (rank, day_diff)
                for row in cur:
                    name_low = (row[0] or "").lower()
                    rank_val = row[1]
                    rd = str(row[2] or "")
                    try:
                        rd_date = datetime.strptime(rd[:8], "%Y%m%d")
                        diff = abs((rd_date - td_date).days)
                    except (ValueError, TypeError):
                        diff = 999999999
                    prev = best.get(name_low)
                    if prev is None or diff < prev[1]:
                        best[name_low] = (rank_val, diff)
                for name_low, (rank_val, _) in best.items():
                    nearby_rank[name_low] = rank_val

        # --- pass 4: historical rankings within previous 120 days (same tour) ---
        hist_rank = {}
        remaining_after_nearby = [
            (n, pid, pn) for n, pid, pn in remaining if n not in nearby_rank
        ]
        if td_date and min_dt_120 and tour and remaining_after_nearby:
            ids = list({str(pid) for _, pid, _ in remaining_after_nearby if pid})
            if ids:
                placeholders = ",".join("?" * len(ids))
                cur = self.conn.execute(f"""
                    SELECT player_id, rank
                    FROM rankings
                    WHERE player_id IN ({placeholders})
                      AND tour = ?
                      AND ranking_date BETWEEN ? AND ?
                      AND ranking_date NOT LIKE 'SCRAPED_%'
                      AND ranking_date != 'LIVE'
                    ORDER BY ranking_date DESC
                """, ids + [tour, min_dt_120, tourney_date])
                for row in cur:
                    pid = str(row[0])
                    if pid not in hist_rank:
                        hist_rank[pid] = row[1]

        # --- pass 5: scraped current rankings (last resort for recent events) ---
        scraped_rank = {}
        remaining2 = [
            (n, pid, pn) for n, pid, pn in remaining_after_nearby
            if str(pid or "") not in hist_rank
        ]
        if allow_scraped_fallback and remaining2 and tour:
            cur = self.conn.execute("""
                SELECT r.rank, p.name_first, p.name_last
                FROM rankings r
                JOIN players p ON r.player_id = p.player_id
                              AND r.tour = p.tour
                WHERE r.ranking_date LIKE 'SCRAPED_%SINGLES'
                  AND r.tour = ?
                ORDER BY r.ranking_date DESC
            """, (tour,))
            for row in cur:
                first = row[1] or ""
                last = row[2] or ""
                full = f"{first} {last}".strip().lower()
                if full and full not in scraped_rank:
                    scraped_rank[full] = row[0]

        # --- apply fallbacks ---
        for m in matches:
            for side in ("winner", "loser"):
                if not m.get(f"{side}_rank"):
                    name = (m.get(f"{side}_name") or "").lower()
                    pid = str(m.get(f"{side}_id") or "")
                    rank = (nearby_rank.get(name)
                            or hist_rank.get(pid)
                            or scraped_rank.get(name))
                    if rank:
                        m[f"{side}_rank"] = rank

    def get_tournament_results(self, tourney_name=None, year=None, tour=None):
        """Get tournament draw/results, filling missing ranks."""
        conditions = []
        params = []
        if tourney_name:
            conditions.append("tourney_name LIKE ?")
            params.append(f"%{tourney_name}%")
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tour:
            conditions.append("tour = ?")
            params.append(tour)

        where = " AND ".join(conditions) if conditions else "1=1"
        cur = self.conn.execute(f"""
            SELECT * FROM matches
            WHERE {where}
            ORDER BY tourney_date DESC, match_num DESC
        """, params)
        results = [dict(r) for r in cur.fetchall()]
        self._fill_missing_ranks(results)
        return results

    def get_doubles_tournament_results(self, tourney_name=None, year=None,
                                       tour=None):
        """Get doubles tournament draw/results, filling missing ranks."""
        conditions = []
        params = []
        if tourney_name:
            conditions.append("tourney_name LIKE ?")
            params.append(f"%{tourney_name}%")
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tour:
            conditions.append("tour = ?")
            params.append(tour)

        where = " AND ".join(conditions) if conditions else "1=1"
        cur = self.conn.execute(f"""
            SELECT * FROM doubles_matches
            WHERE {where}
            ORDER BY tourney_date DESC, match_num DESC
        """, params)
        return [dict(r) for r in cur.fetchall()]

    def get_tournament_list(self, year=None, tour=None):
        """Get list of unique tournaments."""
        conditions = []
        params = []
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tour:
            conditions.append("tour = ?")
            params.append(tour)
        where = " AND ".join(conditions) if conditions else "1=1"

        cur = self.conn.execute(f"""
            SELECT DISTINCT tourney_name, tourney_id, surface, tourney_level,
                   tourney_date
            FROM matches
            WHERE {where}
            ORDER BY tourney_date
        """, params)
        return [dict(r) for r in cur.fetchall()]

    def get_doubles_tournament_list(self, year=None, tour=None):
        """Get list of unique doubles tournaments."""
        conditions = []
        params = []
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tour:
            conditions.append("tour = ?")
            params.append(tour)
        where = " AND ".join(conditions) if conditions else "1=1"

        cur = self.conn.execute(f"""
            SELECT DISTINCT tourney_name, tourney_id, surface, tourney_level,
                   tourney_date
            FROM doubles_matches
            WHERE {where}
            ORDER BY tourney_date
        """, params)
        return [dict(r) for r in cur.fetchall()]

    def get_available_years(self, tour=None):
        """Get list of years with data."""
        if tour:
            cur = self.conn.execute("""
                SELECT DISTINCT SUBSTR(tourney_date, 1, 4) as year
                FROM matches WHERE tour = ?
                ORDER BY year
            """, (tour,))
        else:
            cur = self.conn.execute("""
                SELECT DISTINCT SUBSTR(tourney_date, 1, 4) as year
                FROM matches
                ORDER BY year
            """)
        return [r[0] for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Name-based queries (for scraped data without player IDs)
    # ------------------------------------------------------------------

    def search_players_by_name(self, name):
        """Find player_id by full or partial name. Returns list of (id, full_name)."""
        cur = self.conn.execute("""
            SELECT player_id, name_first, name_last
            FROM players
            WHERE name_first || ' ' || name_last LIKE ?
            ORDER BY name_last
            LIMIT 10
        """, (f"%{name}%",))
        return [(r[0], f"{r[1]} {r[2]}") for r in cur.fetchall()]

    def _resolve_player_id(self, name):
        """Try to find a player_id matching the given name."""
        if not name:
            return None
        results = self.search_players_by_name(name)
        if results:
            # Prefer exact match
            for pid, full_name in results:
                if full_name.lower() == name.lower():
                    return pid
            return results[0][0]
        return None

    # ------------------------------------------------------------------
    # Scraping integration
    # ------------------------------------------------------------------

    @_locked_write
    def import_scraped_matches(self, matches_df, progress_callback=None,
                               scraped_player_names=None,
                               replace_existing=True):
        """
        Import scraped match data into the database.

        Scraped matches are stored alongside CSV data. We try to resolve
        player names to IDs for compatibility with existing queries.

        Parameters
        ----------
        scraped_player_names : list[str], optional
            Names of the players whose full career was explicitly scraped.
            Old SCRAPED matches for these players will be deleted before
            re-importing.  If None, detected automatically via frequency.
        replace_existing : bool, default True
            If True, delete existing SCRAPED matches for *scraped_player_names*
            before inserting (full refresh).  If False, only insert rows
            that aren't already in the matches table (incremental refresh
            \u2014 use when *matches_df* contains only the most recent N
            matches per player).
        """
        if matches_df is None or matches_df.empty:
            return 0

        if progress_callback:
            progress_callback(0, 1, "Importing scraped matches...")

        # Normalize player names: strip diacritics for consistency
        matches_df = matches_df.copy()
        for col in ("winner_name", "loser_name"):
            if col in matches_df.columns:
                matches_df[col] = matches_df[col].apply(_strip_diacritics)

        # Build a name→id and name→ioc cache from existing players (single scan)
        name_cache = {}
        name_ioc_cache = {}
        cur = self.conn.execute(
            "SELECT player_id, name_first, name_last, ioc FROM players")
        for row in cur:
            first = row[1] or ""
            last = row[2] or ""
            full = f"{first} {last}".strip()
            if full:
                name_cache[full.lower()] = row[0]
                stripped = f"{_strip_diacritics(first)} {_strip_diacritics(last)}".strip().lower()
                if row[3]:
                    name_ioc_cache[full.lower()] = row[3]
                    if stripped != full.lower():
                        name_ioc_cache[stripped] = row[3]

        def resolve(name):
            if not name:
                return ""
            key = name.lower()
            if key in name_cache:
                return name_cache[key]
            # Try last-name-first style
            parts = name.split()
            if len(parts) >= 2:
                alt = " ".join(parts[1:]) + " " + parts[0]
                if alt.lower() in name_cache:
                    return name_cache[alt.lower()]
            return ""

        # Resolve winner/loser IDs
        matches_df = matches_df.copy()
        matches_df["winner_id"] = matches_df["winner_name"].apply(resolve)
        matches_df["loser_id"] = matches_df["loser_name"].apply(resolve)

        # NOTE: we do NOT fill missing ranks at import time.
        # orank values from tennisabstract are the correct rank at the
        # time of the match.  Filling with current scraped rankings would
        # store the WRONG rank for older tournaments.
        # Missing ranks are resolved at query time by _fill_missing_ranks()
        # which uses cross-reference within the tournament, nearby matches,
        # historical CSV rankings, and current scraped rankings (in that
        # order of preference).

        # Fill IOC from players table using the already-built name cache.
        # player_id is NOT unique across ATP/WTA tours, so using it as a
        # cache key causes collisions (e.g. ATP Shelton=USA overwritten by
        # WTA player with same id=COL).  Also, the scraped oioc field is
        # unreliable for recent matches, so we always prefer the players
        # table and ignore any oioc value from the scraped data.

        def fill_ioc_by_name(name):
            if not name:
                return None
            return name_ioc_cache.get(name.lower(), None)

        matches_df["winner_ioc"] = matches_df["winner_name"].apply(fill_ioc_by_name)
        matches_df["loser_ioc"] = matches_df["loser_name"].apply(fill_ioc_by_name)

        # Mark scraped data so we can clear/refresh it separately
        matches_df["tourney_id"] = matches_df["tourney_id"].fillna("")
        matches_df.loc[matches_df["tourney_id"] == "", "tourney_id"] = "SCRAPED"

        # Identify all "main" players being imported.
        # When scraped_player_names is provided (from the scraper), use those
        # directly.  Otherwise fall back to heuristic detection: players whose
        # total appearance count is high enough (within 50 % of the max) are
        # considered scraped players.
        if scraped_player_names:
            scraped_players = set(scraped_player_names)
        else:
            winner_counts = matches_df["winner_name"].value_counts()
            loser_counts = matches_df["loser_name"].value_counts()
            total_counts = winner_counts.add(loser_counts, fill_value=0)
            max_count = total_counts.max()
            # Players with at least 50 % of the max count are scraped players
            scraped_players = set(
                total_counts[total_counts >= max_count * 0.5].index)
        # Remove old scraped matches for ALL scraped players (single query).
        # Skipped when replace_existing=False (incremental mode \u2014 we trust
        # the dedup LEFT JOIN below to avoid duplicates and preserve any
        # older SCRAPED rows already in the DB).
        if scraped_players and replace_existing:
            placeholders = ",".join("?" for _ in scraped_players)
            player_list = list(scraped_players)
            self.conn.execute(
                f"DELETE FROM matches WHERE tourney_id = 'SCRAPED' "
                f"AND (winner_name IN ({placeholders}) "
                f"OR loser_name IN ({placeholders}))",
                player_list + player_list)

        # Remove duplicates within the DataFrame (same date + winner + loser + tourney)
        matches_df = matches_df.drop_duplicates(
            subset=["tourney_date", "winner_name", "loser_name", "tourney_name", "round"],
            keep="first",
        )

        # Avoid inserting matches that already exist (CSV or other scrapes).
        # Use a temp table + LEFT JOIN instead of loading all 1.7M rows into Python.
        # Lowercased tourney_name avoids duplicates like "Us Open" vs "US Open".
        # Unique staging name (UUID) so concurrent imports cannot collide.
        staging_name = f"_import_staging_{uuid.uuid4().hex[:12]}"
        try:
            matches_df.to_sql(staging_name, self.conn, if_exists="replace", index=False)
            self.conn.execute(f"""
                DELETE FROM {staging_name}
                WHERE rowid IN (
                    SELECT s.rowid
                    FROM {staging_name} s
                    JOIN matches m
                      ON m.tourney_date = s.tourney_date
                     AND m.winner_name = s.winner_name
                     AND m.loser_name = s.loser_name
                     AND LOWER(m.tourney_name) = LOWER(s.tourney_name)
                     AND m.round = s.round
                )
            """)
            count_row = self.conn.execute(
                f"SELECT COUNT(*) FROM {staging_name}").fetchone()
            new_count = count_row[0]
            if new_count > 0:
                staging_cols = [
                    row[1] for row in
                    self.conn.execute(f"PRAGMA table_info({staging_name})").fetchall()
                ]
                cols_csv = ", ".join(staging_cols)
                self.conn.execute(f"""
                    INSERT INTO matches ({cols_csv})
                    SELECT {cols_csv} FROM {staging_name}
                """)
                logger.info("Imported %d new scraped matches", new_count)
            else:
                logger.info("No new scraped matches to import")
        finally:
            self.conn.execute(f"DROP TABLE IF EXISTS {staging_name}")

        self.conn.commit()

        if progress_callback:
            progress_callback(1, 1, "Scraped matches imported!")

        return new_count

    @_locked_write
    def import_scraped_rankings(self, rankings_list, ranking_date="LIVE",
                                progress_callback=None):
        """
        Import scraped rankings from live-tennis/tennisabstract.
        
        ranking_date identifies the scraped snapshot (e.g. LIVE,
        SCRAPED_OFFICIAL_SINGLES, ...).
        """
        if not rankings_list:
            return 0

        if progress_callback:
            progress_callback(0, 1, "Importing scraped rankings...")

        tour = rankings_list[0].get("tour", "atp")

        # Remove previous snapshot for this tour+marker
        self.conn.execute(
            "DELETE FROM rankings WHERE ranking_date = ? AND tour = ?",
            (ranking_date, tour),
        )

        # Build name→id cache once (instead of per-player query)
        # Use diacritics-stripped keys so "Đoković" matches "Djokovic" etc.
        def _normalize_name(s):
            # Strip diacritics
            s = "".join(
                c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn"
            )
            # Normalise hyphens/punctuation to spaces
            s = s.replace("-", " ").replace("'", "")
            # Collapse whitespace
            return " ".join(s.split()).lower()

        name_cache = {}
        cur = self.conn.execute(
            "SELECT player_id, name_first, name_last FROM players")
        for row in cur:
            first = row[1] or ""
            last = row[2] or ""
            full = f"{first} {last}".strip()
            if full:
                name_cache[full.lower()] = row[0]
                norm = _normalize_name(full)
                if norm not in name_cache:
                    name_cache[norm] = row[0]

        records = []
        for entry in rankings_list:
            name = entry["name"]
            player_id = name_cache.get(name.lower())
            if player_id is None:
                player_id = name_cache.get(
                    _normalize_name(name), name)
            records.append({
                "ranking_date": ranking_date,
                "rank": entry["rank"],
                "player_id": player_id,
                "points": entry["points"],
                "tour": entry.get("tour", "atp"),
                "age": entry.get("age"),
                "rank_diff": entry.get("rank_diff"),
                "pts_diff": entry.get("pts_diff"),
                "next_tournament": entry.get("next_tournament", ""),
                "ioc": entry.get("country", ""),
            })

        if records:
            df = pd.DataFrame(records)
            df.to_sql("rankings", self.conn, if_exists="append", index=False)

        self.conn.commit()

        if progress_callback:
            progress_callback(1, 1, "Live rankings imported!")

        return len(records)

    @staticmethod
    def scraped_ranking_marker(source="LIVE", discipline="singles"):
        src = (source or "LIVE").upper()
        disc = (discipline or "singles").upper()
        return f"SCRAPED_{src}_{disc}"

    def refresh_scraped_rankings(self, tour="atp", discipline="singles",
                                 source="LIVE"):
        """Fetch + import one scraped ranking snapshot and return marker."""
        rankings = scrape_current_rankings(
            tour=tour,
            discipline=discipline,
            source=source,
        )
        marker = self.scraped_ranking_marker(source=source,
                                             discipline=discipline)
        if not rankings:
            # Keep DB clean if fetch failed.
            self.conn.execute(
                "DELETE FROM rankings WHERE ranking_date = ? AND tour = ?",
                (marker, tour),
            )
            self.conn.commit()
            return marker, 0

        count = self.import_scraped_rankings(rankings, ranking_date=marker)
        return marker, count

    def get_scraped_match_count(self):
        """Return how many scraped matches are in the database."""
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM matches WHERE tourney_id = 'SCRAPED'")
        return cur.fetchone()[0]

    # ------------------------------------------------------------------
    # Scrape cache management
    # ------------------------------------------------------------------

    def is_player_cache_valid(self, player_name, expire_hours=6):
        """Check if a player's scraped data is still fresh.

        Simple time-based check: returns True if the player was scraped
        less than *expire_hours* ago.  The caller is responsible for
        choosing an appropriate *expire_hours* (e.g. via activity
        fingerprint comparison).

        *expire_hours* = 0 always forces a refresh.
        """
        row = self.conn.execute(
            "SELECT last_scraped FROM scrape_cache "
            "WHERE player_name = ?",
            (player_name,)
        ).fetchone()
        if not row:
            return False
        if expire_hours == 0:
            return False
        from datetime import datetime, timedelta
        try:
            last = datetime.fromisoformat(row[0])
            return datetime.now() - last < timedelta(hours=expire_hours)
        except (ValueError, TypeError):
            return False

    def has_new_activity(self, player_name, new_fingerprint):
        """Return True if the player's activity fingerprint has changed.

        The OFFICIAL rankings "previous" column concatenates results
        from the last 1-2 weeks (e.g. ``"Lost in MC R64Lost in BCN R2"``).
        When old results drop off, the string shrinks but no new match
        data exists.

        To avoid false positives we check whether the *new* current and
        previous texts already appear inside the *old* combined
        fingerprint.  Only truly new text triggers a re-scrape.

        Returns True (needs scraping) when:
        - player not in cache
        - new current or previous text is NOT already present in the
          stored fingerprint (genuinely new result)
        Returns False (skip) when:
        - fingerprints match exactly
        - new text is a subset of the old combined text (old results
          just dropped off, or current moved to previous)
        - a field merely cleared (went from something to empty)
        """
        row = self.conn.execute(
            "SELECT activity_fingerprint FROM scrape_cache "
            "WHERE player_name = ?",
            (player_name,)
        ).fetchone()
        if not row or row[0] is None:
            return True  # never scraped or no fingerprint stored
        old_fp = row[0]
        if old_fp == new_fingerprint:
            return False
        # Combine old current+previous into one reference string.
        old_combined = old_fp.replace("|", "")
        new_cur, new_prev = (new_fingerprint.split("|", 1) + [""])[:2]
        # Only trigger if a non-empty new part is NOT already present
        # in the old combined text.
        cur_is_new = bool(new_cur) and new_cur not in old_combined
        prev_is_new = bool(new_prev) and new_prev not in old_combined
        return cur_is_new or prev_is_new

    @_locked_write
    def update_scrape_cache(self, player_name, match_count,
                            last_match_date=None,
                            activity_fingerprint=None):
        """Record that a player was just scraped."""
        from datetime import datetime
        self.conn.execute(
            "INSERT OR REPLACE INTO scrape_cache "
            "(player_name, last_scraped, match_count, last_match_date, "
            "activity_fingerprint) "
            "VALUES (?, ?, ?, ?, ?)",
            (player_name, datetime.now().isoformat(), match_count,
             last_match_date, activity_fingerprint)
        )
        self.conn.commit()

    def get_stale_players(self, player_names, expire_hours=6):
        """Return the subset of player_names whose cache is expired or missing."""
        if not player_names:
            return []
        stale = []
        for name in player_names:
            if not self.is_player_cache_valid(name, expire_hours):
                stale.append(name)
        return stale

    # ------------------------------------------------------------------
    # Extended stats queries
    # ------------------------------------------------------------------

    def get_player_winners_errors(self, player_name, surface=None, year=None, tourney_level=None):
        """Get winners/errors data for a player."""
        conditions = ["player_name = ?"]
        params = [player_name]
        if surface:
            conditions.append("surface = ?")
            params.append(surface)
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tourney_level:
            conditions.append("tourney_level = ?")
            params.append(tourney_level)
        where = " AND ".join(conditions)
        cur = self.conn.execute(
            f"SELECT * FROM match_winners_errors WHERE {where} "
            "ORDER BY tourney_date DESC", params)
        return [dict(r) for r in cur.fetchall()]

    def get_player_serve_speed(self, player_name, surface=None, year=None, tourney_level=None):
        """Get serve speed data for a player."""
        conditions = ["player_name = ?"]
        params = [player_name]
        if surface:
            conditions.append("surface = ?")
            params.append(surface)
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tourney_level:
            conditions.append("tourney_level = ?")
            params.append(tourney_level)
        where = " AND ".join(conditions)
        cur = self.conn.execute(
            f"SELECT * FROM match_serve_speed WHERE {where} "
            "ORDER BY tourney_date DESC", params)
        return [dict(r) for r in cur.fetchall()]

    def get_player_pbp_stats(self, player_name, surface=None, year=None, tourney_level=None):
        """Get point-by-point stats for a player."""
        conditions = ["player_name = ?"]
        params = [player_name]
        if surface:
            conditions.append("surface = ?")
            params.append(surface)
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tourney_level:
            conditions.append("tourney_level = ?")
            params.append(tourney_level)
        where = " AND ".join(conditions)
        cur = self.conn.execute(
            f"SELECT * FROM match_pbp_stats WHERE {where} "
            "ORDER BY tourney_date DESC", params)
        return [dict(r) for r in cur.fetchall()]

    def get_player_mcp_serve(self, player_name, surface=None, year=None, tourney_level=None):
        """Get MCP serve direction data for a player."""
        conditions = ["player_name = ?"]
        params = [player_name]
        if surface:
            conditions.append("surface = ?")
            params.append(surface)
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tourney_level:
            conditions.append("tourney_level = ?")
            params.append(tourney_level)
        where = " AND ".join(conditions)
        cur = self.conn.execute(
            f"SELECT * FROM match_mcp_serve WHERE {where} "
            "ORDER BY tourney_date DESC", params)
        return [dict(r) for r in cur.fetchall()]

    def get_player_mcp_return(self, player_name, surface=None, year=None, tourney_level=None):
        """Get MCP return data for a player."""
        conditions = ["player_name = ?"]
        params = [player_name]
        if surface:
            conditions.append("surface = ?")
            params.append(surface)
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tourney_level:
            conditions.append("tourney_level = ?")
            params.append(tourney_level)
        where = " AND ".join(conditions)
        cur = self.conn.execute(
            f"SELECT * FROM match_mcp_return WHERE {where} "
            "ORDER BY tourney_date DESC", params)
        return [dict(r) for r in cur.fetchall()]

    def get_player_mcp_rally(self, player_name, surface=None, year=None, tourney_level=None):
        """Get MCP rally data for a player."""
        conditions = ["player_name = ?"]
        params = [player_name]
        if surface:
            conditions.append("surface = ?")
            params.append(surface)
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tourney_level:
            conditions.append("tourney_level = ?")
            params.append(tourney_level)
        where = " AND ".join(conditions)
        cur = self.conn.execute(
            f"SELECT * FROM match_mcp_rally WHERE {where} "
            "ORDER BY tourney_date DESC", params)
        return [dict(r) for r in cur.fetchall()]

    def get_player_mcp_tactics(self, player_name, surface=None, year=None, tourney_level=None):
        """Get MCP tactics data for a player."""
        conditions = ["player_name = ?"]
        params = [player_name]
        if surface:
            conditions.append("surface = ?")
            params.append(surface)
        if year:
            conditions.append("tourney_date BETWEEN ? AND ?")
            params.extend([f"{year}0000", f"{year}9999"])
        if tourney_level:
            conditions.append("tourney_level = ?")
            params.append(tourney_level)
        where = " AND ".join(conditions)
        cur = self.conn.execute(
            f"SELECT * FROM match_mcp_tactics WHERE {where} "
            "ORDER BY tourney_date DESC", params)
        return [dict(r) for r in cur.fetchall()]

    def get_extended_stats_count(self, player_name):
        """Return count of extended stats records per table for a player."""
        tables = [
            "match_winners_errors", "match_serve_speed", "match_pbp_stats",
            "match_mcp_serve", "match_mcp_return", "match_mcp_rally",
            "match_mcp_tactics",
        ]
        # Single query with UNION ALL instead of 7 separate queries
        parts = [
            f"SELECT '{t}' as tbl, COUNT(*) as cnt FROM {t} WHERE player_name = ?"
            for t in tables
        ]
        query = " UNION ALL ".join(parts)
        cur = self.conn.execute(query, [player_name] * len(tables))
        return {row[0]: row[1] for row in cur.fetchall()}

    @_locked_write
    def import_extended_stats(self, table_name, records, player_name):
        """Import extended stats records, replacing existing data for the player."""
        if not records:
            return 0
        # Clear existing records for this player in this table
        self.conn.execute(
            f"DELETE FROM {table_name} WHERE player_name = ?",
            (player_name,))
        import pandas as pd
        df = pd.DataFrame(records)
        df.to_sql(table_name, self.conn, if_exists="append", index=False)

        # Back-fill surface and tourney_level using in-memory lookup
        rows = self.conn.execute(
            f"SELECT id, tourney_name, tourney_date, opponent_name "
            f"FROM {table_name} "
            f"WHERE player_name = ? AND ("
            f"  surface IS NULL OR surface = '' "
            f"  OR tourney_level IS NULL OR tourney_level = '')",
            (player_name,)).fetchall()
        if rows:
            # Collect years we need, then build lookup in one query
            years = {str(r[2])[:4] for r in rows if r[2]}
            if years:
                # Build range conditions for each year (index-friendly)
                range_conds = " OR ".join(
                    "tourney_date BETWEEN ? AND ?" for _ in years)
                range_params = []
                for y in years:
                    range_params.extend([f"{y}0000", f"{y}9999"])
                match_rows = self.conn.execute(
                    f"SELECT tourney_name, tourney_date, winner_name, "
                    f"loser_name, surface, tourney_level FROM matches "
                    f"WHERE {range_conds}",
                    range_params).fetchall()
                # Build lookup: (year, last_name) -> [(tourney_name, surface, level)]
                lookup = {}
                for mtn, mtd, wn, ln, surf, lvl in match_rows:
                    year = str(mtd)[:4] if mtd else ""
                    for full_name in (wn, ln):
                        if not full_name:
                            continue
                        last = full_name.rsplit(" ", 1)[-1] if " " in full_name else full_name
                        key = (year, last)
                        if key not in lookup:
                            lookup[key] = []
                        lookup[key].append((mtn or "", surf, lvl))

                updates = []
                for rid, etn, etd, opp in rows:
                    year = str(etd)[:4] if etd else ""
                    last = opp.rsplit(" ", 1)[-1] if opp and " " in opp else (opp or "")
                    candidates = lookup.get((year, last), [])
                    surf = lvl = None
                    for mtn, ms, ml in candidates:
                        if etn and etn in mtn:
                            surf, lvl = ms, ml
                            break
                    if surf is None:
                        for mtn, ms, ml in candidates:
                            if etn and mtn and (etn in mtn or mtn in etn):
                                surf, lvl = ms, ml
                                break
                    if surf or lvl:
                        updates.append((surf, lvl, rid))
                if updates:
                    self.conn.executemany(
                        f"UPDATE {table_name} SET surface = COALESCE(?, surface), "
                        f"tourney_level = COALESCE(?, tourney_level) WHERE id = ?",
                        updates)
        self.conn.commit()
        return len(records)

    def backfill_extended_stats_surface(self):
        """Backfill surface and tourney_level for all extended stats tables
        by matching against the matches table using Python-side lookup."""
        tables = [
            "match_winners_errors", "match_serve_speed", "match_pbp_stats",
            "match_mcp_serve", "match_mcp_return", "match_mcp_rally",
            "match_mcp_tactics",
        ]
        # Build lookup from matches: (year, last_name) -> list of (tourney_name, surface, level)
        cur = self.conn.execute(
            "SELECT tourney_name, tourney_date, winner_name, loser_name, "
            "surface, tourney_level FROM matches")
        lookup = {}  # (year, last_name) -> [(tourney_name, surface, level)]
        for row in cur:
            tn, td, wn, ln, surf, lvl = row
            year = str(td)[:4] if td else ""
            for full_name in (wn, ln):
                if not full_name:
                    continue
                last = full_name.rsplit(" ", 1)[-1] if " " in full_name else full_name
                key = (year, last)
                if key not in lookup:
                    lookup[key] = []
                lookup[key].append((tn or "", surf, lvl))

        for table in tables:
            # Fetch rows needing backfill
            rows = self.conn.execute(
                f"SELECT id, tourney_name, tourney_date, opponent_name "
                f"FROM {table} "
                f"WHERE surface IS NULL OR surface = '' "
                f"   OR tourney_level IS NULL OR tourney_level = ''"
            ).fetchall()
            if not rows:
                continue
            updates = []
            for rid, etn, etd, opp in rows:
                year = str(etd)[:4] if etd else ""
                candidates = lookup.get((year, opp), [])
                surf = None
                lvl = None
                for mtn, ms, ml in candidates:
                    if etn and etn in mtn:
                        surf, lvl = ms, ml
                        break
                if surf is None:
                    # Fallback: try partial match
                    for mtn, ms, ml in candidates:
                        if etn and mtn and (etn in mtn or mtn in etn):
                            surf, lvl = ms, ml
                            break
                if surf or lvl:
                    updates.append((surf, lvl, rid))
            if updates:
                self.conn.executemany(
                    f"UPDATE {table} SET surface = COALESCE(?, surface), "
                    f"tourney_level = COALESCE(?, tourney_level) WHERE id = ?",
                    updates)
        self.conn.commit()

    def is_extended_cache_valid(self, player_name, expire_hours=168):
        """Check if extended stats cache is fresh (default: 1 week)."""
        row = self.conn.execute(
            "SELECT last_scraped FROM extended_stats_cache WHERE player_name = ?",
            (player_name,)).fetchone()
        if not row:
            return False
        from datetime import datetime, timedelta
        try:
            last = datetime.fromisoformat(row[0])
            return datetime.now() - last < timedelta(hours=expire_hours)
        except (ValueError, TypeError):
            return False

    @_locked_write
    def update_extended_stats_cache(self, player_name, tables_scraped,
                                    activity_fingerprint=None):
        """Record that extended stats were scraped for a player."""
        from datetime import datetime
        self.conn.execute(
            "INSERT OR REPLACE INTO extended_stats_cache "
            "(player_name, last_scraped, tables_scraped, activity_fingerprint) "
            "VALUES (?, ?, ?, ?)",
            (player_name, datetime.now().isoformat(),
             ",".join(tables_scraped), activity_fingerprint))
        self.conn.commit()

    def has_extended_new_activity(self, player_name, new_fingerprint):
        """Return True if extended stats should be re-scraped based on
        activity fingerprint change."""
        row = self.conn.execute(
            "SELECT activity_fingerprint FROM extended_stats_cache "
            "WHERE player_name = ?",
            (player_name,)).fetchone()
        if not row or row[0] is None:
            return True
        old_fp = row[0]
        if old_fp == new_fingerprint:
            return False
        old_combined = old_fp.replace("|", "")
        new_cur, new_prev = (new_fingerprint.split("|", 1) + [""])[:2]
        cur_is_new = bool(new_cur) and new_cur not in old_combined
        prev_is_new = bool(new_prev) and new_prev not in old_combined
        return cur_is_new or prev_is_new

    def close(self):
        self.conn.close()
