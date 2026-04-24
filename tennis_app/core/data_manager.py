"""
Data manager for downloading and caching tennis CSV data
from Jeff Sackmann's GitHub repositories, plus live scraping
from tennisabstract.com for recent data.
"""

import datetime
import os
import re
import time
import logging
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import pandas as pd

from .scraper import TennisAbstractScraper, convert_scraped_to_db_format

logger = logging.getLogger(__name__)

BASE_URL_ATP = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
BASE_URL_WTA = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master"

CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600  # 1 week


def get_data_dir():
    """Return the path to the local data cache directory."""
    data_dir = Path.home() / ".tennis_analytics" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _download_file(url, dest_path):
    """Download a file from a URL to a local path."""
    logger.info("Downloading %s", url)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
    logger.info("Saved %s (%d bytes)", dest_path.name, len(resp.content))


def _is_fresh(path):
    """Check if a cached file is still fresh."""
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < CACHE_MAX_AGE_SECONDS


def _parallel_download(files_to_download, data_dir, force=False,
                       progress_callback=None):
    """Download files in parallel using a thread pool."""
    total = len(files_to_download)
    to_fetch = []
    for fname, url in files_to_download:
        dest = data_dir / fname
        if force or not _is_fresh(dest):
            to_fetch.append((fname, url, dest))
        else:
            logger.debug("Using cached %s", fname)

    done = total - len(to_fetch)
    if progress_callback:
        progress_callback(done, total, "Downloading...")

    def _do_download(item):
        fname, url, dest = item
        try:
            _download_file(url, dest)
        except requests.HTTPError as exc:
            logger.warning("Could not download %s: %s", fname, exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_do_download, item): item for item in to_fetch}
        for future in as_completed(futures):
            done += 1
            if progress_callback:
                fname = futures[future][0]
                progress_callback(done, total, f"Downloaded {fname}...")

    if progress_callback:
        progress_callback(total, total, "Download complete!")


def download_atp_data(year_start=1968, year_end=None, force=False,
                      progress_callback=None):
    """
    Download ATP match data, player list, and rankings from GitHub.

    Parameters
    ----------
    year_start : int
        First year of match data to download.
    year_end : int
        Last year of match data to download (inclusive).
    force : bool
        If True, re-download even if cache is fresh.
    progress_callback : callable, optional
        Called with (current_step, total_steps, message) for progress updates.
    """
    if year_end is None:
        year_end = datetime.date.today().year
    data_dir = get_data_dir() / "atp"
    data_dir.mkdir(parents=True, exist_ok=True)

    files_to_download = []

    # Players file
    files_to_download.append(("atp_players.csv", f"{BASE_URL_ATP}/atp_players.csv"))

    # Rankings — current + historical decades
    files_to_download.append((
        "atp_rankings_current.csv",
        f"{BASE_URL_ATP}/atp_rankings_current.csv",
    ))
    for decade in ("70s", "80s", "90s", "00s", "10s", "20s"):
        fname = f"atp_rankings_{decade}.csv"
        files_to_download.append((fname, f"{BASE_URL_ATP}/{fname}"))

    # Tour-level match files per year
    for year in range(year_start, year_end + 1):
        fname = f"atp_matches_{year}.csv"
        files_to_download.append((fname, f"{BASE_URL_ATP}/{fname}"))

    # Qualifying / challenger match files (available from 1978)
    for year in range(max(year_start, 1978), year_end + 1):
        fname = f"atp_matches_qual_chall_{year}.csv"
        files_to_download.append((fname, f"{BASE_URL_ATP}/{fname}"))

    # Futures match files (available from 1991)
    for year in range(max(year_start, 1991), year_end + 1):
        fname = f"atp_matches_futures_{year}.csv"
        files_to_download.append((fname, f"{BASE_URL_ATP}/{fname}"))

    # Doubles match files (available 2000–2020)
    for year in range(max(year_start, 2000), min(year_end, 2020) + 1):
        fname = f"atp_matches_doubles_{year}.csv"
        files_to_download.append((fname, f"{BASE_URL_ATP}/{fname}"))

    _parallel_download(files_to_download, data_dir, force, progress_callback)


def download_wta_data(year_start=1968, year_end=None, force=False,
                      progress_callback=None):
    """Download WTA match data, player list, and rankings from GitHub."""
    if year_end is None:
        year_end = datetime.date.today().year
    data_dir = get_data_dir() / "wta"
    data_dir.mkdir(parents=True, exist_ok=True)

    files_to_download = []
    files_to_download.append(("wta_players.csv", f"{BASE_URL_WTA}/wta_players.csv"))

    # Rankings — current + historical decades
    files_to_download.append((
        "wta_rankings_current.csv",
        f"{BASE_URL_WTA}/wta_rankings_current.csv",
    ))
    for decade in ("80s", "90s", "00s", "10s", "20s"):
        fname = f"wta_rankings_{decade}.csv"
        files_to_download.append((fname, f"{BASE_URL_WTA}/{fname}"))

    # Tour-level match files per year
    for year in range(year_start, year_end + 1):
        fname = f"wta_matches_{year}.csv"
        files_to_download.append((fname, f"{BASE_URL_WTA}/{fname}"))

    # Qualifying / ITF match files (available from 1968)
    for year in range(year_start, year_end + 1):
        fname = f"wta_matches_qual_itf_{year}.csv"
        files_to_download.append((fname, f"{BASE_URL_WTA}/{fname}"))

    _parallel_download(files_to_download, data_dir, force, progress_callback)


def load_players(tour="atp"):
    """Load player data from cached CSV."""
    data_dir = get_data_dir() / tour
    path = data_dir / f"{tour}_players.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(
        path,
        dtype={"player_id": str, "dob": str, "wikidata_id": str},
        low_memory=False,
    )


def load_matches(tour="atp", year_start=1968, year_end=None,
                 include_qual=False):
    """Load match data from cached CSVs and concatenate into one DataFrame."""
    if year_end is None:
        year_end = datetime.date.today().year
    data_dir = get_data_dir() / tour
    dtype_map = {
        "winner_id": str, "loser_id": str,
        "winner_seed": str, "loser_seed": str,
    }
    frames = []

    def _read(path):
        if path.exists():
            frames.append(pd.read_csv(path, dtype=dtype_map, low_memory=False))

    for year in range(year_start, year_end + 1):
        # Tour-level singles
        _read(data_dir / f"{tour}_matches_{year}.csv")

        if include_qual:
            # ATP: qual_chall;  WTA: qual_itf
            _read(data_dir / f"{tour}_matches_qual_chall_{year}.csv")
            _read(data_dir / f"{tour}_matches_qual_itf_{year}.csv")
            # ATP futures
            _read(data_dir / f"{tour}_matches_futures_{year}.csv")

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_doubles(tour="atp", year_start=2000, year_end=2020):
    """Load doubles match data from cached CSVs."""
    data_dir = get_data_dir() / tour
    dtype_map = {
        "winner1_id": str, "winner2_id": str,
        "loser1_id": str, "loser2_id": str,
        "winner_seed": str, "loser_seed": str,
    }
    frames = []
    for year in range(year_start, year_end + 1):
        path = data_dir / f"{tour}_matches_doubles_{year}.csv"
        if path.exists():
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                frames.append(pd.read_csv(path, dtype=dtype_map, low_memory=False, index_col=False))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_rankings(tour="atp"):
    """Load rankings from cached CSVs (current + historical decades)."""
    data_dir = get_data_dir() / tour
    frames = []
    dtype_map = {"player": str}

    # Historical decade files
    decades = ("70s", "80s", "90s", "00s", "10s", "20s") if tour == "atp" \
        else ("80s", "90s", "00s", "10s", "20s")
    for decade in decades:
        path = data_dir / f"{tour}_rankings_{decade}.csv"
        if path.exists():
            frames.append(pd.read_csv(path, dtype=dtype_map, low_memory=False))

    # Current rankings
    path = data_dir / f"{tour}_rankings_current.csv"
    if path.exists():
        frames.append(pd.read_csv(path, dtype=dtype_map, low_memory=False))

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Live scraping from tennisabstract.com
# ---------------------------------------------------------------------------

_scraper_instance = None


def _get_scraper():
    global _scraper_instance
    if _scraper_instance is None:
        _scraper_instance = TennisAbstractScraper()
    return _scraper_instance


def scrape_player_matches(player_name, min_year=None, tour="atp",
                          max_matches=None):
    """
    Scrape all matches for a player from tennisabstract.com and return
    a DataFrame in the same format as the database 'matches' table.

    If *min_year* is set, only matches from that year onward are kept.
    If *max_matches* is set, only the N most recent matches are kept
    (after the min_year filter).

    Returns (DataFrame, last_match_date_str).
    ``last_match_date_str`` is the YYYYMMDD string of the most recent
    match across ALL years (before the min_year filter), or None.
    """
    scraper = _get_scraper()
    raw = scraper.fetch_player_matches(player_name, tour=tour)
    if raw is None:
        return pd.DataFrame(), None

    # Extract last match date from raw data BEFORE filtering
    last_match_date = None
    for match in raw:
        if match and len(match) > 0 and match[0] is not None:
            try:
                d = str(int(match[0]))
                if last_match_date is None or d > last_match_date:
                    last_match_date = d
            except (ValueError, TypeError):
                pass

    df = convert_scraped_to_db_format(raw, player_name, min_year=min_year,
                                      max_matches=max_matches)
    return df, last_match_date


def scrape_current_rankings(tour="atp", discipline="singles", source="LIVE"):
    """
    Scrape rankings with points from live-tennis.eu.

    Falls back to tennisabstract.com (without points) only for singles LIVE.
    Returns a list of dicts: {rank, name, country, points, ...}
    """
    scraper = _get_scraper()
    # Primary source: live-tennis.eu (has points, age, diffs, next tournament)
    rankings = scraper.scrape_rankings_live_tennis(
        tour=tour, discipline=discipline, source=source
    )
    if rankings:
        return rankings
    # Fallback: tennisabstract (no points) for singles LIVE only
    if discipline == "singles" and source.upper() == "LIVE":
        html = scraper.fetch_rankings_html(tour)
        if not html:
            return []
        return scraper.parse_rankings(html, tour)
    return []


def _normalize_name(name):
    """Normalize a player name for matching across sources."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.replace("-", " ")
    return re.sub(r"\s+", " ", name).strip().lower()


def _activity_changed(old_fp, new_fp):
    """Pure-in-memory equivalent of ``has_(extended_)new_activity``.

    Returns True if *new_fp* contains tournament text not already
    present in *old_fp*.  Used by the bulk activity-check path so we
    don't issue 1000+ Turso round-trips per loop.
    """
    if old_fp is None:
        return True
    if old_fp == new_fp:
        return False
    old_combined = old_fp.replace("|", "")
    new_cur, new_prev = (new_fp.split("|", 1) + [""])[:2]
    cur_is_new = bool(new_cur) and new_cur not in old_combined
    prev_is_new = bool(new_prev) and new_prev not in old_combined
    return cur_is_new or prev_is_new


def _cache_row_is_fresh(cache_row, expire_hours):
    """In-memory equivalent of ``is_(player|extended)_cache_valid``."""
    if not cache_row or expire_hours == 0:
        return False
    last_iso = cache_row[0]
    if not last_iso:
        return False
    try:
        last = datetime.datetime.fromisoformat(last_iso)
    except (ValueError, TypeError):
        return False
    return (datetime.datetime.now() - last
            < datetime.timedelta(hours=expire_hours))


def _resolve_ranking_name(ranking_name, db, tour="atp"):
    """Resolve a live-tennis.eu ranking name to the full DB player name.

    Rankings often carry truncated names (e.g. "Daniel Mérida" instead of
    "Daniel Merida Aguilar").  We look up the ``players`` table for a row
    whose normalized ``name_first + ' ' + name_last`` starts with (or
    equals) the normalized ranking name.

    Returns the canonical ``name_first + ' ' + name_last`` if found,
    otherwise the original *ranking_name* unchanged.
    """
    if db is None:
        return ranking_name
    norm = _normalize_name(ranking_name)
    try:
        rows = db.conn.execute(
            "SELECT name_first, name_last FROM players WHERE tour = ?",
            (tour,),
        ).fetchall()
        for r in rows:
            first = r[0] or ""
            last = r[1] or ""
            full = f"{first} {last}".strip()
            nfull = _normalize_name(full)
            if nfull == norm:
                return full          # exact match
            if nfull.startswith(norm + " "):
                return full          # ranking name is a prefix
        # Second pass: ranking first matches, ranking last is prefix of DB last
        parts = norm.split()
        if len(parts) >= 2:
            r_first = parts[0]
            r_last = " ".join(parts[1:])
            for r in rows:
                first = r[0] or ""
                last = r[1] or ""
                nf = _normalize_name(first)
                nl = _normalize_name(last)
                if nf == r_first and nl.startswith(r_last):
                    return f"{first} {last}".strip()
    except Exception:
        pass
    return ranking_name


def _build_ranking_name_map(ranking_names, db, tour="atp"):
    """Batch-resolve a list of ranking display names to full DB names.

    Returns a dict mapping each original ranking name to its resolved
    full name (unchanged if no DB match is found).
    """
    if db is None:
        return {n: n for n in ranking_names}

    # Build normalized lookup from players table (once)
    try:
        rows = db.conn.execute(
            "SELECT name_first, name_last FROM players WHERE tour = ?",
            (tour,),
        ).fetchall()
    except Exception:
        return {n: n for n in ranking_names}

    # Two indexes: exact normalized name → full, and prefix-friendly list
    exact_map = {}   # norm_full → canonical full name
    prefix_list = [] # (norm_full, norm_first, norm_last, canonical)
    for r in rows:
        first = r[0] or ""
        last = r[1] or ""
        full = f"{first} {last}".strip()
        if not full:
            continue
        nfull = _normalize_name(full)
        exact_map[nfull] = full
        prefix_list.append((
            nfull,
            _normalize_name(first),
            _normalize_name(last),
            full,
        ))

    result = {}
    for rname in ranking_names:
        norm = _normalize_name(rname)
        # 1. Exact normalized match — keep the original ranking name
        #    (_clean_name already handles accent differences)
        if norm in exact_map:
            result[rname] = rname
            continue
        # 2. Full normalized DB name starts with ranking name
        #    → ranking name is truncated, use the longer DB name
        found = None
        for nfull, _, _, canonical in prefix_list:
            if nfull.startswith(norm + " "):
                found = canonical
                break
        if found:
            result[rname] = found
            continue
        # 3. First name matches, last-name is a prefix of DB last
        parts = norm.split()
        if len(parts) >= 2:
            r_first = parts[0]
            r_last = " ".join(parts[1:])
            for _, nf, nl, canonical in prefix_list:
                if nf == r_first and nl.startswith(r_last):
                    found = canonical
                    break
        result[rname] = found if found else rname
    return result


def scrape_top_players_matches(top_n=50, tour="atp", progress_callback=None,
                               db=None, cache_expire_hours=6, min_year=None,
                               max_matches_per_player=20):
    """
    Scrape matches for the top N ranked players.

    Uses OFFICIAL rankings to detect activity (event-driven scraping):
    - Changed activity fingerprint → scrape (new results available)
    - Same fingerprint → skip (no new data)
    - Both current/previous empty → inactive, 7-day cache fallback

    If *db* is provided, uses the scrape cache to skip players whose
    data was scraped less than *cache_expire_hours* ago.
    If *min_year* is set, only matches from that year onward are kept.
    If *max_matches_per_player* is set, only the N most recent matches
    per player are kept (default 20). Reduces import work since older
    scraped matches are already in the DB. Set to None for full history.

    Returns (combined_DataFrame, rankings_list, scraped_names_list).
    """
    # Use OFFICIAL rankings as primary source (stable ordering by official rank)
    # Fall back to LIVE only if OFFICIAL fails.
    used_official = False
    rankings = scrape_current_rankings(tour, discipline="singles",
                                       source="OFFICIAL")
    if rankings:
        used_official = True
    else:
        logger.info("OFFICIAL rankings unavailable, falling back to LIVE")
        rankings = scrape_current_rankings(tour)
    if not rankings:
        logger.warning("Could not fetch rankings for scraping")
        return pd.DataFrame(), [], []

    # Sort by rank to ensure correct top-N selection, then hard-filter
    # by rank value to handle cases where the rank field doesn't match
    # list position (e.g. LIVE ranking projections).
    rankings.sort(key=lambda e: e.get("rank", 9999))
    rankings = [e for e in rankings if e.get("rank", 9999) <= top_n]

    # Resolve ranking display names to full DB names
    name_map = _build_ranking_name_map(
        [e["name"] for e in rankings], db, tour=tour)
    for entry in rankings:
        entry["name"] = name_map.get(entry["name"], entry["name"])

    player_names = [e["name"] for e in rankings]
    logger.info("Selected %d players for scraping (ranks %s\u2013%s: %s \u2026 %s, source=%s)",
                len(rankings),
                rankings[0].get("rank") if rankings else "?",
                rankings[-1].get("rank") if rankings else "?",
                rankings[0]["name"] if rankings else "?",
                rankings[-1]["name"] if rankings else "?",
                "OFFICIAL" if used_official else "LIVE")

    # ---- Event-driven activity detection via OFFICIAL rankings ----
    # Only build fingerprints from OFFICIAL data (has current/previous tournament).
    # If we fell back to LIVE, fetch OFFICIAL separately for fingerprinting.
    activity_map = {}  # normalized_name → fingerprint string
    if db is not None:
        official_entries = rankings if used_official else []
        if not used_official:
            try:
                scraper = _get_scraper()
                official_entries = scraper.scrape_rankings_live_tennis(
                    tour=tour, discipline="singles", source="OFFICIAL"
                )
                logger.info("Fetched OFFICIAL rankings (%d) for activity detection",
                            len(official_entries))
            except Exception as exc:
                logger.warning("Could not fetch OFFICIAL rankings for activity "
                               "detection: %s", exc)
        for entry in official_entries:
            cur_t = entry.get("current_tournament", "")
            prev_t = entry.get("previous_tournament", "")
            fp = f"{cur_t}|{prev_t}"
            norm = _normalize_name(entry["name"])
            activity_map[norm] = fp
        logger.info("Built activity map for %d players", len(activity_map))

    # Determine which players need re-scraping
    stale = set()
    fingerprints = {}  # name → fingerprint (for players we'll scrape)

    # Bulk-load the entire scrape_cache in ONE round-trip instead of
    # issuing two queries per player (1000+ remote calls for top-1000).
    cache_snapshot = db.get_all_scrape_cache() if db is not None else {}

    for name in player_names:
        norm = _normalize_name(name)
        fp = activity_map.get(norm)

        if db is None:
            stale.add(name)
            continue

        cache_row = cache_snapshot.get(name)

        if fp is not None:
            # We have activity data from OFFICIAL
            cur_t, prev_t = fp.split("|", 1)
            if not cur_t and not prev_t:
                # Inactive player: use 7-day cache
                if not _cache_row_is_fresh(cache_row, 168):
                    stale.add(name)
                    fingerprints[name] = fp
            elif _activity_changed(cache_row[1] if cache_row else None, fp):
                # Fingerprint changed to new non-empty value: scrape
                stale.add(name)
                fingerprints[name] = fp
            else:
                # Fingerprint unchanged or only cleared: keep stored
                # fingerprint in sync (update without re-scraping)
                fingerprints[name] = fp
                logger.debug("Skipping %s (activity unchanged)", name)
        else:
            # Player not found in OFFICIAL: fall back to time-based cache
            if not _cache_row_is_fresh(cache_row, cache_expire_hours):
                stale.add(name)

    logger.info("Event-driven check: %d/%d players (top %d) need refresh",
                len(stale), len(player_names), top_n)

    all_frames = []
    scraped_names = []  # only the players we actually re-scraped
    scrape_idx = 0  # counter for actually scraped players

    # ---- Sync fingerprints for skipped (still-fresh) players ----
    skipped_updates = []
    actual_targets = []  # (idx, entry) tuples that need scraping
    for idx, entry in enumerate(rankings):
        name = entry["name"]
        if name not in stale:
            fp = fingerprints.get(name)
            if db is not None and fp is not None:
                skipped_updates.append((fp, name))
            logger.info("Skipping %s (cache still valid)", name)
            continue
        actual_targets.append((idx, entry))

    if db is not None and skipped_updates:
        # Filter to players that already exist in the cache (UPDATE only)
        existing = {row[0] for row in db.conn.execute(
            "SELECT player_name FROM scrape_cache").fetchall()}
        with db._write_lock:
            db.conn.executemany(
                "UPDATE scrape_cache SET activity_fingerprint = ? "
                "WHERE player_name = ?",
                [(fp, n) for fp, n in skipped_updates if n in existing])
            db.conn.commit()

    # ---- Parallel HTTP fetches for stale players ----
    # cloudscraper sessions are thread-safe for concurrent GETs; we keep
    # the worker count modest to avoid hammering tennisabstract.com.
    total_to_scrape = len(actual_targets)
    max_workers = min(8, max(1, total_to_scrape))

    def _fetch_one(entry):
        name = entry["name"]
        try:
            df, last_match_date = scrape_player_matches(
                name, min_year=min_year, tour=tour,
                max_matches=max_matches_per_player)
            return name, df, last_match_date, None
        except Exception as exc:
            return name, None, None, exc

    if total_to_scrape > 0:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, entry): entry
                       for _, entry in actual_targets}
            for future in as_completed(futures):
                scrape_idx += 1
                name, df, last_match_date, exc = future.result()

                if progress_callback:
                    progress_callback(scrape_idx, total_to_scrape,
                                      f"Scraped {name} "
                                      f"({scrape_idx}/{total_to_scrape})")

                if exc is not None:
                    logger.warning("Failed to scrape %s: %s", name, exc)
                    continue

                # Update cache (decorator on update_scrape_cache handles locking)
                if db is not None:
                    db.update_scrape_cache(
                        name,
                        len(df) if df is not None else 0,
                        last_match_date=last_match_date,
                        activity_fingerprint=fingerprints.get(name),
                    )
                if df is not None and not df.empty:
                    all_frames.append(df)
                    scraped_names.append(name)
                    logger.info("Scraped %d matches for %s", len(df), name)
                else:
                    logger.info("No recent matches for %s", name)

    if progress_callback:
        progress_callback(len(stale), len(stale), "Scraping complete!")

    # Commit any fingerprint updates for skipped players
    if db is not None:
        db.conn.commit()

    combined = pd.concat(all_frames, ignore_index=True) if all_frames else pd.DataFrame()
    return combined, rankings, scraped_names


# ---------------------------------------------------------------------------
# Extended stats scraping (player-more.cgi)
# ---------------------------------------------------------------------------

def scrape_top_players_extended_stats(top_n=150, tour="atp",
                                      progress_callback=None, db=None,
                                      stop_event=None):
    """
    Scrape extended stats for the top N ranked players, using the same
    activity-fingerprint logic as match scraping to skip unchanged players.

    Parameters
    ----------
    top_n : int
        Number of top-ranked players to process.
    tour : str
        "atp" or "wta"
    progress_callback : callable, optional
        Called with (current, total, message)
    db : TennisDatabase, optional
        Database instance for caching and storage.
    stop_event : threading.Event, optional
        If set, the loop will abort early (for graceful shutdown).

    Returns list of player names that were actually scraped.
    """
    rankings = scrape_current_rankings(tour, discipline="singles",
                                       source="OFFICIAL")
    if not rankings:
        rankings = scrape_current_rankings(tour)
    if not rankings:
        return []

    rankings.sort(key=lambda e: e.get("rank", 9999))
    rankings = [e for e in rankings if e.get("rank", 9999) <= top_n]

    # Resolve ranking display names to full DB names
    name_map = _build_ranking_name_map(
        [e["name"] for e in rankings], db, tour=tour)
    for entry in rankings:
        entry["name"] = name_map.get(entry["name"], entry["name"])

    # Build activity fingerprint map
    activity_map = {}
    for entry in rankings:
        cur_t = entry.get("current_tournament", "")
        prev_t = entry.get("previous_tournament", "")
        fp = f"{cur_t}|{prev_t}"
        norm = _normalize_name(entry["name"])
        activity_map[norm] = fp

    # Determine which players need scraping
    stale = []
    fingerprints = {}

    # Bulk-load extended_stats_cache in ONE round-trip (avoids 1000+
    # remote calls for top-1000 against Turso).
    cache_snapshot = (db.get_all_extended_stats_cache()
                      if db is not None else {})

    for entry in rankings:
        name = entry["name"]
        norm = _normalize_name(name)
        fp = activity_map.get(norm)

        if db is None:
            stale.append(name)
            fingerprints[name] = fp
            continue

        cache_row = cache_snapshot.get(name)

        if fp is not None:
            cur_t, prev_t = fp.split("|", 1)
            if not cur_t and not prev_t:
                # Inactive: use time-based cache (1 week)
                if not _cache_row_is_fresh(cache_row, 168):
                    stale.append(name)
                    fingerprints[name] = fp
            elif _activity_changed(cache_row[1] if cache_row else None, fp):
                stale.append(name)
                fingerprints[name] = fp
            else:
                # Fingerprint unchanged — sync it silently
                fingerprints[name] = fp
        else:
            if not _cache_row_is_fresh(cache_row, 168):
                stale.append(name)

    scraped_names = []
    for idx, name in enumerate(stale):
        if stop_event and stop_event.is_set():
            logger.info("Extended stats background scrape stopped early")
            break

        if progress_callback:
            progress_callback(idx, len(stale),
                              f"Extended stats: {name} "
                              f"({idx + 1}/{len(stale)})...")
        try:
            scrape_player_extended_stats(
                name, db=db, tour=tour, force=True,
                activity_fingerprint=fingerprints.get(name),
            )
            scraped_names.append(name)
        except Exception as exc:
            logger.warning("Failed extended stats for %s: %s", name, exc)

    logger.info("Extended stats scrape done: %d/%d players scraped",
                len(scraped_names), len(stale))
    return scraped_names

def scrape_player_extended_stats(player_name, db=None, tables=None,
                                 tour="atp", progress_callback=None,
                                 force=False, activity_fingerprint=None):
    """
    Scrape extended statistics for a player from tennisabstract.com
    player-more.cgi endpoint.

    Parameters
    ----------
    player_name : str
        Player name (e.g. "Jannik Sinner")
    db : TennisDatabase, optional
        Database to store results. If None, returns raw data only.
    tables : list[str], optional
        Specific tables to scrape. None = all available.
    tour : str
        "atp" or "wta"
    progress_callback : callable, optional
        Called with (current, total, message)
    force : bool
        If True, scrape even if cache is fresh.
    activity_fingerprint : str, optional
        Current activity fingerprint for smart cache invalidation.

    Returns dict of table_name -> record_count
    """
    from .scraper import EXTENDED_CONVERTERS, EXTENDED_DB_TABLES

    # Check cache using fingerprint if available, else time-based
    if db and not force:
        if activity_fingerprint:
            if not db.has_extended_new_activity(player_name,
                                               activity_fingerprint):
                logger.info("Extended stats fingerprint unchanged for %s",
                            player_name)
                return {}
        elif db.is_extended_cache_valid(player_name):
            logger.info("Extended stats cache still valid for %s",
                        player_name)
            return {}

    scraper = _get_scraper()
    raw_data = scraper.fetch_all_extended_tables(
        player_name, tables=tables, progress_callback=progress_callback,
        tour=tour)

    if not raw_data:
        logger.warning("No extended stats found for %s", player_name)
        # Cache the negative result so we don't retry every run.
        # The fingerprint guards against staleness: when the player gets
        # new activity, has_extended_new_activity() will return True.
        if db:
            try:
                db.update_extended_stats_cache(
                    player_name, [],
                    activity_fingerprint=activity_fingerprint)
            except Exception:
                logger.exception(
                    "Failed to cache empty extended stats for %s",
                    player_name)
        return {}

    result = {}
    tables_scraped = []

    for table_name, rows in raw_data.items():
        converter = EXTENDED_CONVERTERS.get(table_name)
        db_table = EXTENDED_DB_TABLES.get(table_name)
        if not converter or not db_table:
            continue

        records = converter(rows, player_name, tour=tour)
        if records and db:
            count = db.import_extended_stats(db_table, records, player_name)
            result[table_name] = count
            tables_scraped.append(table_name)
        elif records:
            result[table_name] = len(records)
            tables_scraped.append(table_name)

    # Update cache with fingerprint (even if no tables produced records,
    # to avoid retrying every run for players with no extended data).
    if db:
        db.update_extended_stats_cache(player_name, tables_scraped,
                                       activity_fingerprint=activity_fingerprint)

    logger.info("Extended stats for %s: %s", player_name, result)
    return result
