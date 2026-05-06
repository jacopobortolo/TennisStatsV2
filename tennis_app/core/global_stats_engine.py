"""Computation engine for the Global Tennis Stats page."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .stats_engine import parse_score


LEVEL_LABELS = {
    "G": "Grand Slam",
    "M": "Masters 1000",
    "F": "ATP Finals",
    "A": "Tour-level",
    "C": "Challenger",
    "D": "Team Cup",
}
LEVEL_FILTERS = {
    "Grand Slam": "G",
    "Masters 1000": "M",
    "ATP Finals": "F",
    "ATP 500": "A",
    "ATP 250": "A",
    "Challenger": "C",
}


class GlobalStatsEngine:
    """Run leaderboard-style global-stat queries against a TennisDatabase."""

    ROUND_RANK = {
        "Q1": 1, "Q2": 2, "Q3": 3,
        "R128": 4, "R64": 5, "R32": 6, "R16": 7,
        "QF": 8, "SF": 9, "F": 10, "W": 11,
    }
    NEXT_ROUND = {
        "Q1": "Q2", "Q2": "Q3", "Q3": "R128",
        "R128": "R64", "R64": "R32", "R32": "R16",
        "R16": "QF", "QF": "SF", "SF": "F", "F": "W",
    }
    HOME_SLAMS = {
        "AUS": "Australian Open",
        "FRA": "Roland Garros",
        "GBR": "Wimbledon",
        "USA": "US Open",
    }

    def __init__(self, db):
        self.db = db
        self.conn = db.conn

    def compute(self, stat_id: str, filters: dict | None = None, limit: int = 50):
        filters = filters or {}
        method = getattr(self, f"_stat_{stat_id}", None)
        if method is None:
            return self._unsupported(stat_id)
        rows = method(filters, limit)
        if isinstance(rows, dict):
            return rows
        return {
            "columns": ["Rank", "Player", "Value", "Detail"],
            "rows": self._rank_rows(rows, limit),
            "note": "",
        }

    # ------------------------------------------------------------------
    # Generic SQL helpers
    # ------------------------------------------------------------------

    def _where(self, filters, alias="m", include_level=True,
               include_surface=True, include_round=True):
        conditions = [f"({alias}.is_upcoming = 0 OR {alias}.is_upcoming IS NULL)"]
        params = []
        tour = filters.get("tour")
        if tour and tour != "All":
            conditions.append(f"{alias}.tour = ?")
            params.append(tour.lower())
        if include_surface:
            surface = filters.get("surface")
            if surface and surface != "All":
                conditions.append(f"{alias}.surface = ?")
                params.append(surface)
        if include_level:
            level = LEVEL_FILTERS.get(filters.get("level"))
            if level:
                conditions.append(f"{alias}.tourney_level = ?")
                params.append(level)
        min_year = filters.get("min_year")
        max_year = filters.get("max_year")
        if not min_year and not max_year:
            year_from, year_to = self._era_years(filters.get("era"))
            min_year = year_from
            max_year = year_to
        if min_year:
            conditions.append(f"SUBSTR({alias}.tourney_date, 1, 4) >= ?")
            params.append(str(min_year))
        if max_year:
            conditions.append(f"SUBSTR({alias}.tourney_date, 1, 4) <= ?")
            params.append(str(max_year))
        if include_round:
            round_ = filters.get("round")
            if round_ and round_ != "All":
                conditions.append(f"{alias}.round = ?")
                params.append(round_)
        return " AND ".join(conditions), params

    def _player_match_cte(self, filters, extra_where=""):
        where, params = self._where(filters)
        if extra_where:
            where = f"{where} AND {extra_where}"
        sql = f"""
            WITH player_matches AS (
                SELECT winner_name AS player, 1 AS won, tourney_date,
                       tourney_name, tourney_level, surface, round,
                       winner_rank AS player_rank, loser_rank AS opponent_rank
                FROM matches m
                WHERE {where} AND winner_name IS NOT NULL AND winner_name != ''
                UNION ALL
                SELECT loser_name AS player, 0 AS won, tourney_date,
                       tourney_name, tourney_level, surface, round,
                       loser_rank AS player_rank, winner_rank AS opponent_rank
                FROM matches m
                WHERE {where} AND loser_name IS NOT NULL AND loser_name != ''
            )
        """
        return sql, params + params

    @staticmethod
    def _era_years(era):
        if era in (None, "All-time", "Open Era"):
            return None, None
        if era == "2000s":
            return 2000, 2009
        if era == "2010s":
            return 2010, 2019
        if era == "2020s":
            return 2020, 2029
        return None, None

    @staticmethod
    def _date_year(date_text):
        text = str(date_text or "")
        try:
            return int(text[:4])
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _fmt_pct(value):
        return f"{value:.1f}%"

    @staticmethod
    def _rank_rows(rows, limit):
        ranked = []
        for index, row in enumerate(rows[:limit], start=1):
            ranked.append([str(index), row[0], str(row[1]), row[2] if len(row) > 2 else ""])
        return ranked

    def _unsupported(self, stat_id):
        return {
            "columns": ["Rank", "Player", "Value", "Detail"],
            "rows": [],
            "note": f"{stat_id} is in the catalog but is not wired to a query yet.",
        }

    def _query(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    @staticmethod
    def _parse_match_date(date_text):
        text = str(date_text or "")[:8]
        if len(text) >= 8:
            try:
                return datetime.strptime(text, "%Y%m%d")
            except ValueError:
                pass
        if len(text) >= 4:
            try:
                return datetime.strptime(text[:4], "%Y")
            except ValueError:
                pass
        return None

    @classmethod
    def _round_rank(cls, round_name):
        return cls.ROUND_RANK.get(round_name or "", 0)

    @classmethod
    def _round_reached_by_winner(cls, round_name):
        return cls.NEXT_ROUND.get(round_name or "", round_name or "")

    @staticmethod
    def _event_key(row):
        tourney_id = row.get("tourney_id") or ""
        return "|".join((
            tourney_id,
            row.get("tourney_name") or "",
            str(row.get("tourney_date") or ""),
        ))

    def _ordered_match_rows(self, filters, include_round=False, forced_level=None):
        local_filters = dict(filters)
        if forced_level:
            local_filters["level"] = forced_level
        where, params = self._where(local_filters, include_round=include_round)
        return self._query(f"""
            SELECT tourney_id, tourney_name, tourney_date, tourney_level,
                   surface, match_num, round, winner_name, loser_name,
                   winner_entry, loser_entry, winner_ioc, loser_ioc,
                   score, best_of
            FROM matches m
            WHERE {where}
              AND winner_name IS NOT NULL AND winner_name != ''
              AND loser_name IS NOT NULL AND loser_name != ''
            ORDER BY tourney_date, tourney_name, match_num,
                     CASE round
                         WHEN 'Q1' THEN 1 WHEN 'Q2' THEN 2 WHEN 'Q3' THEN 3
                         WHEN 'R128' THEN 4 WHEN 'R64' THEN 5 WHEN 'R32' THEN 6
                         WHEN 'R16' THEN 7 WHEN 'QF' THEN 8 WHEN 'SF' THEN 9
                         WHEN 'F' THEN 10 ELSE 11 END
        """, params)

    def _event_results(self, filters, forced_level=None):
        rows = self._ordered_match_rows(filters, forced_level=forced_level)
        events = {}

        def update_player(row, player_name, reached_round, entry, ioc):
            if not player_name:
                return
            event_key = self._event_key(row)
            key = (player_name, event_key)
            reached_rank = self._round_rank(reached_round)
            current = events.get(key)
            if current is None:
                current = {
                    "player": player_name,
                    "event_key": event_key,
                    "tourney_name": row.get("tourney_name") or "",
                    "tourney_date": row.get("tourney_date") or "",
                    "tourney_level": row.get("tourney_level") or "",
                    "surface": row.get("surface") or "",
                    "best_round": reached_round,
                    "best_rank": reached_rank,
                    "entry": entry or "",
                    "ioc": ioc or "",
                }
                events[key] = current
                return
            if reached_rank > current["best_rank"]:
                current["best_round"] = reached_round
                current["best_rank"] = reached_rank
            if entry and not current["entry"]:
                current["entry"] = entry
            if ioc and not current["ioc"]:
                current["ioc"] = ioc

        for row in rows:
            update_player(
                row, row.get("winner_name"),
                self._round_reached_by_winner(row.get("round")),
                row.get("winner_entry"), row.get("winner_ioc"))
            update_player(
                row, row.get("loser_name"), row.get("round"),
                row.get("loser_entry"), row.get("loser_ioc"))
        return sorted(
            events.values(),
            key=lambda item: (item["player"], item["tourney_date"], item["tourney_name"]),
        )

    def _streak_from_boolean_events(self, event_rows, label_func, success_func):
        results = []
        grouped = defaultdict(list)
        for event in event_rows:
            grouped[label_func(event)].append(event)
        for label, events in grouped.items():
            best = current = 0
            best_start = best_end = current_start = None
            for event in events:
                if success_func(event):
                    if current == 0:
                        current_start = event
                    current += 1
                    if current > best:
                        best = current
                        best_start = current_start
                        best_end = event
                else:
                    current = 0
                    current_start = None
            if best:
                player = label[0] if isinstance(label, tuple) else label
                extra = label[1] if isinstance(label, tuple) and len(label) > 1 else ""
                first_year = self._date_year(best_start["tourney_date"] if best_start else "")
                last_year = self._date_year(best_end["tourney_date"] if best_end else "")
                detail_parts = [part for part in (extra, f"{first_year}-{last_year}") if part]
                results.append((player, best, " - ".join(detail_parts)))
        return results

    def _win_streak(self, filters, limit, group_attr=None):
        cte, params = self._player_match_cte(filters)
        rows = self._query(cte + """
            SELECT player, won, tourney_date, tourney_name, tourney_level, surface, round
            FROM player_matches
            ORDER BY player,
                     CASE WHEN ? = 'level' THEN tourney_level ELSE '' END,
                     CASE WHEN ? = 'surface' THEN surface ELSE '' END,
                     tourney_date, tourney_name,
                     CASE round
                         WHEN 'Q1' THEN 1 WHEN 'Q2' THEN 2 WHEN 'Q3' THEN 3
                         WHEN 'R128' THEN 4 WHEN 'R64' THEN 5 WHEN 'R32' THEN 6
                         WHEN 'R16' THEN 7 WHEN 'QF' THEN 8 WHEN 'SF' THEN 9
                         WHEN 'F' THEN 10 ELSE 11 END
        """, params + [group_attr or "", group_attr or ""])
        groups = defaultdict(list)
        for row in rows:
            extra = ""
            if group_attr == "level":
                extra = LEVEL_LABELS.get(row["tourney_level"], row["tourney_level"] or "")
            elif group_attr == "surface":
                extra = row["surface"] or ""
            groups[(row["player"], extra)].append(row)

        results = []
        for (player, extra), matches in groups.items():
            best = current = 0
            best_start = best_end = current_start = None
            for match in matches:
                if match["won"]:
                    if current == 0:
                        current_start = match
                    current += 1
                    if current > best:
                        best = current
                        best_start = current_start
                        best_end = match
                else:
                    current = 0
                    current_start = None
            if best:
                first_year = self._date_year(best_start["tourney_date"] if best_start else "")
                last_year = self._date_year(best_end["tourney_date"] if best_end else "")
                detail = " - ".join(part for part in (extra, f"{first_year}-{last_year}") if part)
                results.append((player, best, detail))
        return sorted(results, key=lambda row: (-row[1], row[0]))[:limit]

    def _score_match_rows(self, filters):
        rows = self._ordered_match_rows(filters, include_round=True)
        parsed_rows = []
        for row in rows:
            parsed = parse_score(row.get("score") or "")
            if parsed:
                parsed_rows.append((row, parsed))
        return parsed_rows

    # ------------------------------------------------------------------
    # Titles / finals
    # ------------------------------------------------------------------

    def _stat_most_titles_overall(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, COUNT(*) AS value,
                   MIN(SUBSTR(tourney_date,1,4)) || '-' || MAX(SUBSTR(tourney_date,1,4)) AS detail
            FROM matches m
            WHERE {where} AND round = 'F' AND winner_name != ''
            GROUP BY winner_name
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], r["detail"] or "") for r in rows]

    def _stat_most_titles_by_level(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, tourney_level, COUNT(*) AS value
            FROM matches m
            WHERE {where} AND round = 'F' AND winner_name != ''
            GROUP BY winner_name, tourney_level
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], LEVEL_LABELS.get(r["tourney_level"], r["tourney_level"] or "")) for r in rows]

    def _stat_most_titles_by_surface(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, surface, COUNT(*) AS value
            FROM matches m
            WHERE {where} AND round = 'F' AND winner_name != ''
            GROUP BY winner_name, surface
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], r["surface"] or "") for r in rows]

    def _stat_most_finals_overall(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            WITH finalists AS (
                SELECT winner_name AS player, 1 AS won FROM matches m
                WHERE {where} AND round='F' AND winner_name != ''
                UNION ALL
                SELECT loser_name AS player, 0 AS won FROM matches m
                WHERE {where} AND round='F' AND loser_name != ''
            )
            SELECT player, COUNT(*) AS value, SUM(won) AS titles
            FROM finalists
            GROUP BY player
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + params + [limit])
        return [(r["player"], r["value"], f"{r['titles']} titles") for r in rows]

    def _stat_finals_win_pct(self, filters, limit):
        min_matches = max(1, int(filters.get("min_matches") or 1))
        where, params = self._where(filters)
        rows = self._query(f"""
            WITH finalists AS (
                SELECT winner_name AS player, 1 AS won FROM matches m
                WHERE {where} AND round='F' AND winner_name != ''
                UNION ALL
                SELECT loser_name AS player, 0 AS won FROM matches m
                WHERE {where} AND round='F' AND loser_name != ''
            )
            SELECT player, COUNT(*) AS finals, SUM(won) AS titles,
                   100.0 * SUM(won) / COUNT(*) AS pct
            FROM finalists
            GROUP BY player
            HAVING finals >= ?
            ORDER BY pct DESC, finals DESC, player ASC
            LIMIT ?
        """, params + params + [min_matches, limit])
        return [(r["player"], self._fmt_pct(r["pct"]), f"{r['titles']}-{r['finals'] - r['titles']} finals") for r in rows]

    def _stat_most_sf_qf_overall(self, filters, limit):
        where, params = self._where(filters, include_round=False)
        rows = self._query(f"""
            WITH appearances AS (
                SELECT winner_name AS player, tourney_id, tourney_name, tourney_date FROM matches m
                WHERE {where} AND round IN ('QF','SF') AND winner_name != ''
                UNION
                SELECT loser_name AS player, tourney_id, tourney_name, tourney_date FROM matches m
                WHERE {where} AND round IN ('QF','SF') AND loser_name != ''
            )
            SELECT player, COUNT(*) AS value
            FROM appearances
            GROUP BY player
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + params + [limit])
        return [(r["player"], r["value"], "QF/SF event appearances") for r in rows]

    def _stat_slam_boxset(self, filters, limit):
        return self._title_boxset(filters, limit, {
            "Australian Open", "Roland Garros", "Wimbledon", "US Open"
        }, level="G")

    def _stat_golden_masters(self, filters, limit):
        where, params = self._where(filters, include_level=False)
        rows = self._query(f"""
            SELECT winner_name AS player,
                   COUNT(DISTINCT tourney_name) AS value,
                   GROUP_CONCAT(DISTINCT tourney_name) AS detail
            FROM matches m
            WHERE {where} AND round='F' AND tourney_level='M' AND winner_name != ''
            GROUP BY winner_name
            HAVING value >= 9
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], r["detail"] or "") for r in rows]

    def _title_boxset(self, filters, limit, names, level=None):
        where, params = self._where(filters, include_level=False)
        placeholders = ",".join("?" for _ in names)
        extra = f"AND tourney_name IN ({placeholders})"
        if level:
            extra += " AND tourney_level = ?"
        rows = self._query(f"""
            SELECT winner_name AS player,
                   COUNT(DISTINCT tourney_name) AS value,
                   GROUP_CONCAT(DISTINCT tourney_name) AS detail
            FROM matches m
            WHERE {where} AND round='F' AND winner_name != '' {extra}
            GROUP BY winner_name
            HAVING value = ?
            ORDER BY player ASC
            LIMIT ?
        """, params + list(names) + ([level] if level else []) + [len(names), limit])
        return [(r["player"], "Complete", r["detail"] or "") for r in rows]

    # ------------------------------------------------------------------
    # Match records / rates
    # ------------------------------------------------------------------

    def _stat_most_entries_by_level(self, filters, limit):
        cte, params = self._player_match_cte(filters)
        rows = self._query(cte + """
            SELECT player, COUNT(DISTINCT tourney_name || '|' || tourney_date) AS value
            FROM player_matches
            GROUP BY player
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], "events played") for r in rows]

    def _stat_most_matches_won_by_level(self, filters, limit):
        return self._winner_count(filters, limit, "wins")

    def _stat_career_win_pct_overall(self, filters, limit):
        return self._win_pct(filters, limit)

    def _stat_best_win_pct_by_level(self, filters, limit):
        return self._win_pct(filters, limit)

    def _winner_count(self, filters, limit, detail):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, COUNT(*) AS value
            FROM matches m
            WHERE {where} AND winner_name != ''
            GROUP BY winner_name
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], detail) for r in rows]

    def _win_pct(self, filters, limit):
        min_matches = max(1, int(filters.get("min_matches") or 1))
        cte, params = self._player_match_cte(filters)
        rows = self._query(cte + """
            SELECT player, COUNT(*) AS matches, SUM(won) AS wins,
                   100.0 * SUM(won) / COUNT(*) AS pct
            FROM player_matches
            GROUP BY player
            HAVING matches >= ?
            ORDER BY pct DESC, matches DESC, player ASC
            LIMIT ?
        """, params + [min_matches, limit])
        return [(r["player"], self._fmt_pct(r["pct"]), f"{r['wins']}-{r['matches'] - r['wins']} ({r['matches']} matches)") for r in rows]

    def _stat_most_wins_at_single_tournament(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, tourney_name, COUNT(*) AS value
            FROM matches m
            WHERE {where} AND winner_name != ''
            GROUP BY winner_name, tourney_name
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], r["tourney_name"] or "") for r in rows]

    # ------------------------------------------------------------------
    # Seasons / titles consistency
    # ------------------------------------------------------------------

    def _stat_seasons_with_title(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player,
                   COUNT(DISTINCT SUBSTR(tourney_date,1,4)) AS value,
                   MIN(SUBSTR(tourney_date,1,4)) || '-' || MAX(SUBSTR(tourney_date,1,4)) AS detail
            FROM matches m
            WHERE {where} AND round='F' AND winner_name != ''
            GROUP BY winner_name
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], r["detail"] or "") for r in rows]

    def _stat_most_titles_single_season(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, SUBSTR(tourney_date,1,4) AS season,
                   COUNT(*) AS value
            FROM matches m
            WHERE {where} AND round='F' AND winner_name != ''
            GROUP BY winner_name, season
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], r["season"] or "") for r in rows]

    def _stat_most_finals_single_season(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            WITH finalists AS (
                SELECT winner_name AS player, SUBSTR(tourney_date,1,4) AS season FROM matches m
                WHERE {where} AND round='F' AND winner_name != ''
                UNION ALL
                SELECT loser_name AS player, SUBSTR(tourney_date,1,4) AS season FROM matches m
                WHERE {where} AND round='F' AND loser_name != ''
            )
            SELECT player, season, COUNT(*) AS value
            FROM finalists
            GROUP BY player, season
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + params + [limit])
        return [(r["player"], r["value"], r["season"] or "") for r in rows]

    def _stat_best_single_season_win_pct(self, filters, limit):
        min_matches = max(1, int(filters.get("min_matches") or 1))
        cte, params = self._player_match_cte(filters)
        rows = self._query(cte + """
            SELECT player, SUBSTR(tourney_date,1,4) AS season,
                   COUNT(*) AS matches, SUM(won) AS wins,
                   100.0 * SUM(won) / COUNT(*) AS pct
            FROM player_matches
            GROUP BY player, season
            HAVING matches >= ?
            ORDER BY pct DESC, matches DESC, player ASC
            LIMIT ?
        """, params + [min_matches, limit])
        return [(r["player"], self._fmt_pct(r["pct"]), f"{r['season']} - {r['wins']}-{r['matches'] - r['wins']}") for r in rows]

    def _stat_consecutive_seasons_with_title(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, CAST(SUBSTR(tourney_date,1,4) AS INTEGER) AS season
            FROM matches m
            WHERE {where} AND round='F' AND winner_name != ''
            GROUP BY winner_name, season
            ORDER BY player, season
        """, params)
        seasons = defaultdict(list)
        for row in rows:
            seasons[row["player"]].append(row["season"])
        results = []
        for player, years in seasons.items():
            best = cur = 0
            start = best_start = prev = None
            for year in years:
                if prev is None or year != prev + 1:
                    cur = 1
                    start = year
                else:
                    cur += 1
                if cur > best:
                    best = cur
                    best_start = start
                prev = year
            if best:
                results.append((player, best, f"{best_start}-{best_start + best - 1}"))
        return sorted(results, key=lambda r: (-r[1], r[0]))[:limit]

    # ------------------------------------------------------------------
    # Ages / career length
    # ------------------------------------------------------------------

    def _stat_youngest_title_winner(self, filters, limit):
        return self._title_age(filters, limit, "ASC")

    def _stat_oldest_title_winner(self, filters, limit):
        return self._title_age(filters, limit, "DESC")

    def _title_age(self, filters, limit, direction):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, winner_age AS age,
                   tourney_name || ' ' || SUBSTR(tourney_date,1,4) AS detail
            FROM matches m
            WHERE {where} AND round='F' AND winner_age IS NOT NULL AND winner_name != ''
            ORDER BY winner_age {direction}, tourney_date ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], f"{float(r['age']):.1f}", r["detail"] or "") for r in rows]

    def _stat_youngest_main_draw_player(self, filters, limit):
        return self._main_draw_age(filters, limit, "ASC")

    def _stat_oldest_main_draw_player(self, filters, limit):
        return self._main_draw_age(filters, limit, "DESC")

    def _main_draw_age(self, filters, limit, direction):
        where, params = self._where(filters, include_round=False)
        rows = self._query(f"""
            WITH ages AS (
                SELECT winner_name AS player, winner_age AS age, tourney_name, tourney_date FROM matches m
                WHERE {where} AND round NOT LIKE 'Q%' AND winner_age IS NOT NULL AND winner_name != ''
                UNION ALL
                SELECT loser_name AS player, loser_age AS age, tourney_name, tourney_date FROM matches m
                WHERE {where} AND round NOT LIKE 'Q%' AND loser_age IS NOT NULL AND loser_name != ''
            )
            SELECT player, age, tourney_name || ' ' || SUBSTR(tourney_date,1,4) AS detail
            FROM ages
            ORDER BY age {direction}, tourney_date ASC
            LIMIT ?
        """, params + params + [limit])
        return [(r["player"], f"{float(r['age']):.1f}", r["detail"] or "") for r in rows]

    def _stat_career_length(self, filters, limit):
        cte, params = self._player_match_cte(filters)
        rows = self._query(cte + """
            SELECT player, MIN(tourney_date) AS first_date, MAX(tourney_date) AS last_date,
                   COUNT(*) AS matches
            FROM player_matches
            GROUP BY player
            HAVING matches >= 2
        """, params)
        results = []
        for row in rows:
            y1, y2 = self._date_year(row["first_date"]), self._date_year(row["last_date"])
            if y1 is None or y2 is None:
                continue
            results.append((row["player"], y2 - y1 + 1, f"{y1}-{y2} - {row['matches']} matches"))
        return sorted(results, key=lambda r: (-r[1], r[0]))[:limit]

    # ------------------------------------------------------------------
    # Opponent / ranking
    # ------------------------------------------------------------------

    def _stat_wins_vs_top10(self, filters, limit):
        return self._wins_vs_rank(filters, limit, 10)

    def _stat_wins_vs_top5(self, filters, limit):
        return self._wins_vs_rank(filters, limit, 5)

    def _stat_wins_vs_top3(self, filters, limit):
        return self._wins_vs_rank(filters, limit, 3)

    def _wins_vs_rank(self, filters, limit, rank_limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, COUNT(*) AS value
            FROM matches m
            WHERE {where} AND loser_rank IS NOT NULL AND loser_rank <= ? AND winner_name != ''
            GROUP BY winner_name
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [rank_limit, limit])
        return [(r["player"], r["value"], f"vs Top {rank_limit}") for r in rows]

    def _stat_win_pct_vs_top10(self, filters, limit):
        return self._win_pct_vs_rank(filters, limit, 10)

    def _stat_best_win_pct_vs_top10(self, filters, limit):
        return self._win_pct_vs_rank(filters, limit, 10)

    def _win_pct_vs_rank(self, filters, limit, rank_limit):
        min_matches = max(1, int(filters.get("min_matches") or 1))
        cte, params = self._player_match_cte(filters, extra_where=(
            f"((winner_rank IS NOT NULL AND winner_rank <= {rank_limit}) OR "
            f"(loser_rank IS NOT NULL AND loser_rank <= {rank_limit}))"))
        rows = self._query(cte + """
            SELECT player, COUNT(*) AS matches, SUM(won) AS wins,
                   100.0 * SUM(won) / COUNT(*) AS pct
            FROM player_matches
            WHERE opponent_rank IS NOT NULL AND opponent_rank <= ?
            GROUP BY player
            HAVING matches >= ?
            ORDER BY pct DESC, matches DESC, player ASC
            LIMIT ?
        """, params + [rank_limit, min_matches, limit])
        return [(r["player"], self._fmt_pct(r["pct"]), f"{r['wins']}-{r['matches'] - r['wins']} vs Top {rank_limit}") for r in rows]

    def _stat_top10_wins_single_season(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, SUBSTR(tourney_date,1,4) AS season,
                   COUNT(*) AS value
            FROM matches m
            WHERE {where} AND loser_rank IS NOT NULL AND loser_rank <= 10 AND winner_name != ''
            GROUP BY winner_name, season
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], r["season"] or "") for r in rows]

    def _stat_best_record_as_top1(self, filters, limit):
        min_matches = max(1, int(filters.get("min_matches") or 1))
        cte, params = self._player_match_cte(filters, extra_where="(winner_rank = 1 OR loser_rank = 1)")
        rows = self._query(cte + """
            SELECT player, COUNT(*) AS matches, SUM(won) AS wins,
                   100.0 * SUM(won) / COUNT(*) AS pct
            FROM player_matches
            WHERE player_rank = 1
            GROUP BY player
            HAVING matches >= ?
            ORDER BY pct DESC, matches DESC, player ASC
            LIMIT ?
        """, params + [min_matches, limit])
        return [(r["player"], self._fmt_pct(r["pct"]), f"{r['wins']}-{r['matches'] - r['wins']} as No. 1") for r in rows]

    def _stat_upset_wins_by_rank_gap(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player,
                   CAST(winner_rank AS INTEGER) - CAST(loser_rank AS INTEGER) AS value,
                   loser_name || ' - ' || tourney_name || ' ' || SUBSTR(tourney_date,1,4) AS detail
            FROM matches m
            WHERE {where} AND winner_rank IS NOT NULL AND loser_rank IS NOT NULL
              AND winner_rank > loser_rank AND winner_name != ''
            ORDER BY value DESC, tourney_date DESC
            LIMIT ?
        """, params + [limit])
        return [(r["player"], r["value"], r["detail"] or "") for r in rows]

    def _stat_streak_weeks_at_no1(self, filters, limit):
        return self._ranking_streak(filters, limit, 1)

    def _stat_streak_weeks_top10(self, filters, limit):
        return self._ranking_streak(filters, limit, 10)

    def _ranking_streak(self, filters, limit, rank_limit):
        base_conditions = ["r.ranking_date GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'"]
        base_params = []
        tour = filters.get("tour")
        if tour and tour != "All":
            base_conditions.append("r.tour = ?")
            base_params.append(tour.lower())
        y1, y2 = self._era_years(filters.get("era"))
        if y1:
            base_conditions.append("SUBSTR(r.ranking_date,1,4) >= ?")
            base_params.append(str(y1))
        if y2:
            base_conditions.append("SUBSTR(r.ranking_date,1,4) <= ?")
            base_params.append(str(y2))
        base_where = " AND ".join(base_conditions)

        snapshot_rows = self._query(f"""
            SELECT DISTINCT r.tour, r.ranking_date
            FROM rankings r
            WHERE {base_where}
            ORDER BY r.tour, r.ranking_date
        """, base_params)
        dates_by_tour = defaultdict(list)
        for row in snapshot_rows:
            try:
                date = datetime.strptime(str(row["ranking_date"]), "%Y%m%d")
            except ValueError:
                continue
            dates_by_tour[row["tour"]].append(date)
        date_index = {
            tour_name: {date: index for index, date in enumerate(dates)}
            for tour_name, dates in dates_by_tour.items()
        }
        next_date = {
            tour_name: {
                date: (dates[index + 1] if index + 1 < len(dates) else date + timedelta(days=7))
                for index, date in enumerate(dates)
            }
            for tour_name, dates in dates_by_tour.items()
        }

        rows = self._query(f"""
            SELECT COALESCE(p.name_first || ' ' || p.name_last, r.player_id) AS player,
                   r.player_id, r.tour, r.ranking_date
            FROM rankings r
            LEFT JOIN players p ON p.player_id = r.player_id AND p.tour = r.tour
            WHERE {base_where} AND r.rank <= ?
            ORDER BY r.tour, r.player_id, r.ranking_date
        """, base_params + [rank_limit])
        dates_by_player = defaultdict(list)
        names_by_key = {}
        for row in rows:
            try:
                date = datetime.strptime(str(row["ranking_date"]), "%Y%m%d")
            except ValueError:
                continue
            key = (row["tour"], row["player_id"] or row["player"])
            names_by_key[key] = row["player"]
            dates_by_player[key].append(date)

        results = []
        for key, dates in dates_by_player.items():
            tour_name = key[0]
            indexes = date_index.get(tour_name, {})
            next_dates = next_date.get(tour_name, {})
            best_weeks = current_weeks = 0
            best_start = best_end = start = current_end = prev = None
            for date in dates:
                current_index = indexes.get(date)
                previous_index = indexes.get(prev) if prev is not None else None
                if prev is None or current_index is None or previous_index is None or current_index != previous_index + 1:
                    if current_weeks > best_weeks:
                        best_weeks = current_weeks
                        best_start = start
                        best_end = current_end
                    current_weeks = 0
                    start = date

                coverage_end = next_dates.get(date, date + timedelta(days=7))
                span_weeks = max(1, round((coverage_end - date).days / 7))
                current_weeks += span_weeks
                current_end = coverage_end - timedelta(days=1)
                prev = date

            if current_weeks > best_weeks:
                best_weeks = current_weeks
                best_start = start
                best_end = current_end
            if best_weeks:
                start_text = best_start.strftime("%Y-%m-%d") if best_start else ""
                end_text = best_end.strftime("%Y-%m-%d") if best_end else ""
                results.append((names_by_key.get(key, key[1]), best_weeks, f"{start_text} to {end_text}"))
        return sorted(results, key=lambda r: (-r[1], r[0]))[:limit]

    # ------------------------------------------------------------------
    # Performance / milestones
    # ------------------------------------------------------------------

    def _stat_surface_specialist_index(self, filters, limit):
        min_matches = max(1, int(filters.get("min_matches") or 1))
        cte, params = self._player_match_cte({**filters, "surface": "All"})
        rows = self._query(cte + """
            , overall AS (
                SELECT player, COUNT(*) AS matches, SUM(won) AS wins,
                       100.0 * SUM(won) / COUNT(*) AS pct
                FROM player_matches GROUP BY player
            ), surface_rates AS (
                SELECT player, surface, COUNT(*) AS matches, SUM(won) AS wins,
                       100.0 * SUM(won) / COUNT(*) AS pct
                FROM player_matches WHERE surface IS NOT NULL AND surface != ''
                GROUP BY player, surface
            )
            SELECT s.player, s.surface, s.matches, s.pct - o.pct AS value,
                   s.pct AS surface_pct, o.pct AS overall_pct
            FROM surface_rates s JOIN overall o ON o.player = s.player
            WHERE s.matches >= ?
            ORDER BY value DESC, s.matches DESC
            LIMIT ?
        """, params + [min_matches, limit])
        return [(r["player"], f"+{float(r['value']):.1f} pp", f"{r['surface']} {r['surface_pct']:.1f}% vs {r['overall_pct']:.1f}% overall") for r in rows]

    def _stat_fastest_to_50_wins(self, filters, limit):
        return self._fastest_to_wins(filters, limit, 50)

    def _stat_fastest_to_100_wins(self, filters, limit):
        return self._fastest_to_wins(filters, limit, 100)

    def _stat_fastest_to_150_wins(self, filters, limit):
        return self._fastest_to_wins(filters, limit, 150)

    def _fastest_to_wins(self, filters, limit, milestone):
        cte, params = self._player_match_cte(filters)
        rows = self._query(cte + """
            , ordered AS (
                SELECT player, won, tourney_date,
                       ROW_NUMBER() OVER (PARTITION BY player ORDER BY tourney_date, tourney_name, round) AS match_index,
                       SUM(won) OVER (PARTITION BY player ORDER BY tourney_date, tourney_name, round ROWS UNBOUNDED PRECEDING) AS cum_wins
                FROM player_matches
            )
            SELECT player, MIN(match_index) AS value, MIN(tourney_date) AS date_hit
            FROM ordered
            WHERE cum_wins >= ?
            GROUP BY player
            ORDER BY value ASC, player ASC
            LIMIT ?
        """, params + [milestone, limit])
        return [(r["player"], r["value"], f"hit {milestone} wins in {self._date_year(r['date_hit']) or ''}") for r in rows]

    def _stat_most_wins_after_n_matches(self, filters, limit):
        n = max(1, int(filters.get("milestone") or 150))
        cte, params = self._player_match_cte(filters)
        rows = self._query(cte + """
            , ordered AS (
                SELECT player, won,
                       ROW_NUMBER() OVER (PARTITION BY player ORDER BY tourney_date, tourney_name, round) AS match_index
                FROM player_matches
            )
            SELECT player, SUM(won) AS value, COUNT(*) AS matches
            FROM ordered
            WHERE match_index <= ?
            GROUP BY player
            HAVING matches = ?
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [n, n, limit])
        return [(r["player"], r["value"], f"after {n} matches") for r in rows]

    def _stat_aces_milestones(self, filters, limit):
        milestone = max(1, int(filters.get("milestone") or 5000))
        where, params = self._where(filters)
        rows = self._query(f"""
            WITH ace_rows AS (
                SELECT winner_name AS player, COALESCE(w_ace, 0) AS aces, tourney_date, tourney_name
                FROM matches m WHERE {where} AND winner_name != ''
                UNION ALL
                SELECT loser_name AS player, COALESCE(l_ace, 0) AS aces, tourney_date, tourney_name
                FROM matches m WHERE {where} AND loser_name != ''
            ), ordered AS (
                SELECT player, aces, tourney_date, tourney_name,
                       SUM(aces) OVER (PARTITION BY player ORDER BY tourney_date, tourney_name ROWS UNBOUNDED PRECEDING) AS cum_aces
                FROM ace_rows
            )
            SELECT player, MIN(tourney_date) AS date_hit, MIN(cum_aces) AS value
            FROM ordered
            WHERE cum_aces >= ?
            GROUP BY player
            ORDER BY date_hit ASC, player ASC
            LIMIT ?
        """, params + params + [milestone, limit])
        return [(r["player"], int(r["value"]), f"crossed {milestone} in {self._date_year(r['date_hit']) or ''}") for r in rows]

    def _stat_career_points_pct_big3_style(self, filters, limit):
        min_matches = max(1, int(filters.get("min_matches") or 1))
        where, params = self._where(filters)
        rows = self._query(f"""
            WITH points AS (
                SELECT winner_name AS player,
                       COALESCE(w_1stWon,0) + COALESCE(w_2ndWon,0)
                       + (COALESCE(l_svpt,0) - COALESCE(l_1stWon,0) - COALESCE(l_2ndWon,0)) AS pts_won,
                       COALESCE(w_svpt,0) + COALESCE(l_svpt,0) AS total_pts
                FROM matches m WHERE {where} AND winner_name != ''
                UNION ALL
                SELECT loser_name AS player,
                       COALESCE(l_1stWon,0) + COALESCE(l_2ndWon,0)
                       + (COALESCE(w_svpt,0) - COALESCE(w_1stWon,0) - COALESCE(w_2ndWon,0)) AS pts_won,
                       COALESCE(w_svpt,0) + COALESCE(l_svpt,0) AS total_pts
                FROM matches m WHERE {where} AND loser_name != ''
            )
            SELECT player, COUNT(*) AS matches, SUM(pts_won) AS won, SUM(total_pts) AS total,
                   100.0 * SUM(pts_won) / SUM(total_pts) AS pct
            FROM points
            WHERE total_pts > 0
            GROUP BY player
            HAVING matches >= ? AND total > 0
            ORDER BY pct DESC, matches DESC, player ASC
            LIMIT ?
        """, params + params + [min_matches, limit])
        return [(r["player"], self._fmt_pct(r["pct"]), f"{int(r['won'])}/{int(r['total'])} points") for r in rows]

    # ------------------------------------------------------------------
    # Streaks / event runs
    # ------------------------------------------------------------------

    def _stat_title_streak_overall(self, filters, limit):
        events = self._event_results(filters)
        results = self._streak_from_boolean_events(
            events,
            label_func=lambda event: event["player"],
            success_func=lambda event: event["best_round"] == "W",
        )
        return sorted(results, key=lambda row: (-row[1], row[0]))[:limit]

    def _stat_title_streak_by_level(self, filters, limit):
        events = self._event_results(filters)
        results = self._streak_from_boolean_events(
            events,
            label_func=lambda event: (
                event["player"],
                LEVEL_LABELS.get(event["tourney_level"], event["tourney_level"] or ""),
            ),
            success_func=lambda event: event["best_round"] == "W",
        )
        return sorted(results, key=lambda row: (-row[1], row[0]))[:limit]

    def _stat_win_streak_overall(self, filters, limit):
        return self._win_streak(filters, limit)

    def _stat_win_streak_by_level(self, filters, limit):
        return self._win_streak(filters, limit, group_attr="level")

    def _stat_win_streak_by_surface(self, filters, limit):
        return self._win_streak(filters, limit, group_attr="surface")

    def _stat_round_streak_slam_sf_f(self, filters, limit):
        return self._round_reached_streak(filters, limit, "Grand Slam", default_round="SF")

    def _stat_round_streak_m1000_sf_f(self, filters, limit):
        return self._round_reached_streak(filters, limit, "Masters 1000", default_round="SF")

    def _stat_deep_run_streak(self, filters, limit):
        selected_round = filters.get("round") if filters.get("round") != "All" else "QF"
        return self._round_reached_streak(filters, limit, None, default_round=selected_round)

    def _round_reached_streak(self, filters, limit, forced_level, default_round="SF"):
        threshold = filters.get("round") if filters.get("round") != "All" else default_round
        threshold_rank = self._round_rank(threshold)
        events = self._event_results(filters, forced_level=forced_level)
        results = self._streak_from_boolean_events(
            events,
            label_func=lambda event: event["player"],
            success_func=lambda event: event["best_rank"] >= threshold_rank,
        )
        detail_suffix = forced_level or f"at least {threshold}"
        return sorted(
            [(player, value, f"{detail} - {detail_suffix}" if detail else detail_suffix)
             for player, value, detail in results],
            key=lambda row: (-row[1], row[0]),
        )[:limit]

    def _stat_longest_gap_between_titles(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, tourney_name, tourney_date
            FROM matches m
            WHERE {where} AND round='F' AND winner_name != ''
            ORDER BY winner_name, tourney_date
        """, params)
        by_player = defaultdict(list)
        for row in rows:
            date_value = self._parse_match_date(row["tourney_date"])
            if date_value:
                by_player[row["player"]].append((date_value, row["tourney_name"] or ""))
        results = []
        for player, titles in by_player.items():
            best_gap = None
            best_pair = None
            for previous, current in zip(titles, titles[1:]):
                gap_days = (current[0] - previous[0]).days
                if best_gap is None or gap_days > best_gap:
                    best_gap = gap_days
                    best_pair = (previous, current)
            if best_gap and best_pair:
                years = best_gap / 365.25
                detail = f"{best_pair[0][1]} {best_pair[0][0].year} to {best_pair[1][1]} {best_pair[1][0].year}"
                results.append((player, f"{years:.1f} yrs", detail, best_gap))
        ordered = sorted(results, key=lambda row: (-row[3], row[0]))[:limit]
        return [(player, value, detail) for player, value, detail, _ in ordered]

    def _stat_same_tournament_title_streak(self, filters, limit):
        where, params = self._where(filters)
        rows = self._query(f"""
            SELECT winner_name AS player, tourney_name,
                   CAST(SUBSTR(tourney_date,1,4) AS INTEGER) AS season
            FROM matches m
            WHERE {where} AND round='F' AND winner_name != ''
            GROUP BY winner_name, tourney_name, season
            ORDER BY winner_name, tourney_name, season
        """, params)
        titles = defaultdict(list)
        for row in rows:
            titles[(row["player"], row["tourney_name"] or "")].append(row["season"])
        results = []
        for (player, tournament), seasons in titles.items():
            best = current = 0
            start = best_start = previous = None
            for season in seasons:
                if previous is None or season != previous + 1:
                    current = 1
                    start = season
                else:
                    current += 1
                if current > best:
                    best = current
                    best_start = start
                previous = season
            if best:
                results.append((player, best, f"{tournament} {best_start}-{best_start + best - 1}"))
        return sorted(results, key=lambda row: (-row[1], row[0]))[:limit]

    # ------------------------------------------------------------------
    # Score-derived fun/performance records
    # ------------------------------------------------------------------

    def _stat_most_bagels_given(self, filters, limit):
        return self._bagel_count(filters, limit, received=False)

    def _stat_most_bagels_received(self, filters, limit):
        return self._bagel_count(filters, limit, received=True)

    def _bagel_count(self, filters, limit, received=False):
        counts = defaultdict(int)
        details = defaultdict(lambda: defaultdict(int))
        for row, parsed in self._score_match_rows(filters):
            for winner_games, loser_games, _ in parsed["set_scores"]:
                if winner_games == 6 and loser_games == 0:
                    giver, receiver = row["winner_name"], row["loser_name"]
                elif winner_games == 0 and loser_games == 6:
                    giver, receiver = row["loser_name"], row["winner_name"]
                else:
                    continue
                player = receiver if received else giver
                counts[player] += 1
                details[player][row.get("surface") or "Unknown"] += 1
        results = []
        for player, value in counts.items():
            surface, surface_count = max(details[player].items(), key=lambda item: item[1])
            label = "received" if received else "given"
            results.append((player, value, f"{label}; top surface {surface} ({surface_count})"))
        return sorted(results, key=lambda row: (-row[1], row[0]))[:limit]

    def _stat_five_set_win_pct(self, filters, limit):
        return self._score_match_win_pct(filters, limit, mode="five_set")

    def _stat_deciding_set_win_pct(self, filters, limit):
        return self._score_match_win_pct(filters, limit, mode="deciding_set")

    def _score_match_win_pct(self, filters, limit, mode):
        min_matches = max(1, int(filters.get("min_matches") or 1))
        records = defaultdict(lambda: {"wins": 0, "matches": 0})
        for row, parsed in self._score_match_rows(filters):
            best_of = int(row.get("best_of") or parsed.get("best_of") or 3)
            total_sets = parsed["total_sets"]
            if mode == "five_set":
                include = total_sets == 5
                label = "five-set matches"
            else:
                include = total_sets == best_of and parsed["sets_lost"] > 0
                label = "deciding-set matches"
            if not include:
                continue
            records[row["winner_name"]]["wins"] += 1
            records[row["winner_name"]]["matches"] += 1
            records[row["loser_name"]]["matches"] += 1
        results = []
        for player, record in records.items():
            if record["matches"] < min_matches:
                continue
            pct = 100.0 * record["wins"] / record["matches"]
            losses = record["matches"] - record["wins"]
            results.append((player, pct, f"{record['wins']}-{losses} {label}"))
        ordered = sorted(results, key=lambda row: (-row[1], row[0]))[:limit]
        return [(player, self._fmt_pct(value), detail) for player, value, detail in ordered]

    def _stat_tie_break_win_pct(self, filters, limit):
        min_tiebreaks = max(1, int(filters.get("min_matches") or 1))
        records = defaultdict(lambda: {"won": 0, "played": 0})
        for row, parsed in self._score_match_rows(filters):
            winner_won = parsed["tiebreaks_won"]
            winner_lost = parsed["tiebreaks_lost"]
            total_tiebreaks = winner_won + winner_lost
            if total_tiebreaks == 0:
                continue
            records[row["winner_name"]]["won"] += winner_won
            records[row["winner_name"]]["played"] += total_tiebreaks
            records[row["loser_name"]]["won"] += winner_lost
            records[row["loser_name"]]["played"] += total_tiebreaks
        results = []
        for player, record in records.items():
            if record["played"] < min_tiebreaks:
                continue
            pct = 100.0 * record["won"] / record["played"]
            results.append((player, pct, f"{record['won']}-{record['played'] - record['won']} tiebreaks"))
        ordered = sorted(results, key=lambda row: (-row[1], row[0]))[:limit]
        return [(player, self._fmt_pct(value), detail) for player, value, detail in ordered]

    def _stat_straight_set_title_runs(self, filters, limit):
        return self._title_run_count(filters, limit, mode="straight")

    def _stat_no_tiebreak_title_runs(self, filters, limit):
        return self._title_run_count(filters, limit, mode="no_tiebreak")

    def _title_run_count(self, filters, limit, mode):
        rows = self._ordered_match_rows(filters)
        event_matches = defaultdict(list)
        champions = {}
        for row in rows:
            event_key = self._event_key(row)
            event_matches[event_key].append(row)
            if row.get("round") == "F":
                champions[event_key] = row.get("winner_name")
        counts = defaultdict(int)
        details = defaultdict(list)
        for event_key, champion in champions.items():
            champion_matches = [row for row in event_matches[event_key]
                                if row.get("winner_name") == champion]
            if not champion_matches:
                continue
            parsed_scores = [parse_score(row.get("score") or "") for row in champion_matches]
            if any(parsed is None for parsed in parsed_scores):
                continue
            if mode == "straight":
                success = all(parsed["sets_lost"] == 0 for parsed in parsed_scores)
                label = "straight-set title runs"
            else:
                success = all(parsed["tiebreaks_won"] + parsed["tiebreaks_lost"] == 0
                              for parsed in parsed_scores)
                label = "no-tiebreak title runs"
            if success:
                counts[champion] += 1
                sample = event_matches[event_key][0]
                details[champion].append(
                    f"{sample.get('tourney_name') or ''} {self._date_year(sample.get('tourney_date')) or ''}")
        results = []
        for player, value in counts.items():
            samples = "; ".join(details[player][:3])
            results.append((player, value, f"{label}; {samples}"))
        return sorted(results, key=lambda row: (-row[1], row[0]))[:limit]

    # ------------------------------------------------------------------
    # Entry / Slam contextual records
    # ------------------------------------------------------------------

    def _stat_qualifying_to_title_runs(self, filters, limit):
        where, params = self._where(filters, include_round=False)
        rows = self._query(f"""
            SELECT final.winner_name AS player,
                   COUNT(DISTINCT final.tourney_name || '|' || SUBSTR(final.tourney_date,1,4)) AS value,
                   GROUP_CONCAT(DISTINCT final.tourney_name || ' ' || SUBSTR(final.tourney_date,1,4)) AS detail
            FROM matches final
            WHERE {where.replace('m.', 'final.')} AND final.round='F'
              AND final.winner_name != ''
              AND (
                  UPPER(COALESCE(final.winner_entry,'')) = 'Q'
                  OR EXISTS (
                      SELECT 1 FROM matches qual
                      WHERE (qual.is_upcoming = 0 OR qual.is_upcoming IS NULL)
                        AND qual.round IN ('Q1', 'Q2', 'Q3')
                        AND qual.winner_name = final.winner_name
                        AND COALESCE(qual.tourney_id, '') = COALESCE(final.tourney_id, '')
                        AND qual.tourney_name = final.tourney_name
                        AND SUBSTR(qual.tourney_date,1,4) = SUBSTR(final.tourney_date,1,4)
                  )
              )
            GROUP BY final.winner_name
            ORDER BY value DESC, player ASC
            LIMIT ?
        """, params + [limit])
        return [(row["player"], row["value"], row["detail"] or "") for row in rows]

    def _stat_lucky_loser_deep_runs(self, filters, limit):
        threshold = filters.get("round") if filters.get("round") != "All" else "QF"
        threshold_rank = self._round_rank(threshold)
        counts = defaultdict(int)
        samples = defaultdict(list)
        for event in self._event_results(filters):
            if (event.get("entry") or "").upper() != "LL":
                continue
            if event["best_rank"] < threshold_rank:
                continue
            counts[event["player"]] += 1
            samples[event["player"]].append(
                f"{event['tourney_name']} {self._date_year(event['tourney_date']) or ''} ({event['best_round']})")
        results = []
        for player, value in counts.items():
            results.append((player, value, "; ".join(samples[player][:3])))
        return sorted(results, key=lambda row: (-row[1], row[0]))[:limit]

    def _stat_home_slam_performance(self, filters, limit):
        min_matches = max(1, int(filters.get("min_matches") or 1))
        local_filters = dict(filters)
        local_filters["level"] = "Grand Slam"
        rows = self._ordered_match_rows(local_filters, include_round=True)
        records = defaultdict(lambda: {"home_w": 0, "home_l": 0, "other_w": 0, "other_l": 0, "slam": ""})

        def add_result(player, ioc, tourney_name, won):
            home_slam = self.HOME_SLAMS.get((ioc or "").upper())
            if not home_slam:
                return
            record = records[player]
            record["slam"] = home_slam
            normalized_name = (tourney_name or "").lower()
            is_home = home_slam.lower() in normalized_name
            key = "home_w" if is_home and won else "home_l" if is_home else "other_w" if won else "other_l"
            record[key] += 1

        for row in rows:
            add_result(row["winner_name"], row.get("winner_ioc"), row.get("tourney_name"), True)
            add_result(row["loser_name"], row.get("loser_ioc"), row.get("tourney_name"), False)

        results = []
        for player, record in records.items():
            home_matches = record["home_w"] + record["home_l"]
            other_matches = record["other_w"] + record["other_l"]
            if home_matches < min_matches or other_matches < min_matches:
                continue
            home_pct = 100.0 * record["home_w"] / home_matches
            other_pct = 100.0 * record["other_w"] / other_matches
            diff = home_pct - other_pct
            detail = (f"{record['slam']}: {record['home_w']}-{record['home_l']} "
                      f"vs other Slams {record['other_w']}-{record['other_l']}")
            results.append((player, diff, detail))
        ordered = sorted(results, key=lambda row: (-row[1], row[0]))[:limit]
        return [(player, f"{value:+.1f} pp", detail) for player, value, detail in ordered]

    def _stat_slam_dominance_index(self, filters, limit):
        local_filters = dict(filters)
        local_filters["level"] = "Grand Slam"
        cte, params = self._player_match_cte(local_filters)
        rows = self._query(cte + """
            SELECT player, tourney_name, COUNT(*) AS matches, SUM(won) AS wins,
                   100.0 * SUM(won) / COUNT(*) AS win_pct
            FROM player_matches
            GROUP BY player, tourney_name
            HAVING matches >= ?
        """, params + [max(1, int(filters.get("min_matches") or 1))])
        title_where, title_params = self._where(local_filters, include_round=False)
        title_rows = self._query(f"""
            WITH slam_finishes AS (
                SELECT winner_name AS player, tourney_name, 1 AS title, 1 AS final
                FROM matches m WHERE {title_where} AND round='F' AND winner_name != ''
                UNION ALL
                SELECT loser_name AS player, tourney_name, 0 AS title, 1 AS final
                FROM matches m WHERE {title_where} AND round='F' AND loser_name != ''
            )
            SELECT player, tourney_name, SUM(title) AS titles, SUM(final) AS finals
            FROM slam_finishes
            GROUP BY player, tourney_name
        """, title_params + title_params)
        finishes = {(row["player"], row["tourney_name"]): row for row in title_rows}
        results = []
        for row in rows:
            finish = finishes.get((row["player"], row["tourney_name"]), {})
            titles = int(finish.get("titles") or 0)
            finals = int(finish.get("finals") or 0)
            score = titles * 10 + finals * 3 + float(row["win_pct"] or 0) / 10
            detail = (f"{row['tourney_name']}: {titles} titles, {finals} finals, "
                      f"{row['wins']}-{row['matches'] - row['wins']}")
            results.append((row["player"], score, detail))
        ordered = sorted(results, key=lambda row: (-row[1], row[0]))[:limit]
        return [(player, f"{value:.1f}", detail) for player, value, detail in ordered]

    def _stat_losses_from_match_points(self, filters, limit):
        return {
            "columns": ["Rank", "Player", "Value", "Detail"],
            "rows": [],
            "note": "This needs point-by-point sequence data with match-point state; the local match_pbp_stats table only stores aggregate PBP metrics.",
        }
