"""Streamlit web front-end for TennisStatsV2.

This file is intentionally isolated from the PySide6 desktop app. It reuses
the existing read-only query layer through a Turso snapshot, so user clicks hit
local SQLite on the Streamlit server instead of making one Turso request per
query.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SNAPSHOT_TTL_SECONDS = int(os.environ.get("TENNIS_WEB_SNAPSHOT_TTL", "14400"))

LEVEL_LABELS = {
    "G": "Grand Slam",
    "M": "Masters 1000 / WTA 1000",
    "A": "ATP 250/500",
    "P": "Premier / WTA",
    "D": "Davis/BJK Cup",
    "I": "International",
    "F": "Tour Finals",
    "E": "Elite Trophy",
}

GLOBAL_STATS = {
    "Most titles": "most_titles_overall",
    "Most finals": "most_finals_overall",
    "Career win percentage": "career_win_pct_overall",
    "Longest win streak": "win_streak_overall",
    "Most wins vs Top 10": "wins_vs_top10",
    "Weeks at No. 1 streak": "streak_weeks_at_no1",
    "Most bagels given": "most_bagels_given",
    "Deciding-set win percentage": "deciding_set_win_pct",
}

EXTENDED_TABLES = {
    "Winners / Errors": ("match_winners_errors", "get_player_winners_errors"),
    "Serve Speed": ("match_serve_speed", "get_player_serve_speed"),
    "Point-by-Point": ("match_pbp_stats", "get_player_pbp_stats"),
    "MCP Serve": ("match_mcp_serve", "get_player_mcp_serve"),
    "MCP Return": ("match_mcp_return", "get_player_mcp_return"),
    "MCP Rally": ("match_mcp_rally", "get_player_mcp_rally"),
    "MCP Tactics": ("match_mcp_tactics", "get_player_mcp_tactics"),
}


def _apply_streamlit_secrets() -> None:
    """Expose Streamlit secrets as env vars consumed by cloud.db."""
    for key in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "TURSO_LOCAL_PATH"):
        if key in os.environ:
            continue
        try:
            value = st.secrets.get(key)
        except Exception:
            value = None
        if value:
            os.environ[key] = str(value)

    if "TURSO_LOCAL_PATH" not in os.environ:
        cache_dir = Path(tempfile.gettempdir()) / "tennisstatsv2-web"
        cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["TURSO_LOCAL_PATH"] = str(cache_dir / "tennis.db")


@st.cache_resource(ttl=SNAPSHOT_TTL_SECONDS, show_spinner="Refreshing local data snapshot...")
def get_db(_refresh_token: int = 0):
    _apply_streamlit_secrets()
    from cloud.db import SnapshotTennisDatabase

    return SnapshotTennisDatabase(refresh_on_open=True)


def run_query(db, sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    cur = db.conn.execute(sql, params)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return pd.DataFrame([tuple(row) for row in rows], columns=cols)


def rows_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def format_date(value: Any) -> str:
    text = str(value or "")
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def player_name(player: dict[str, Any] | None) -> str:
    if not player:
        return ""
    return f"{player.get('name_first') or ''} {player.get('name_last') or ''}".strip()


def clean_player_name_for_db(name: str) -> str:
    try:
        from tennis_app.core.scraper import clean_player_name

        return clean_player_name(name) or name
    except Exception:
        return name


def pick_player(db, label: str, key: str, default: str = "") -> dict[str, Any] | None:
    query = st.text_input(label, value=default, key=f"{key}_query")
    if len(query.strip()) < 2:
        return None
    players = db.search_players(query.strip(), limit=25)
    if not players:
        st.warning("No players found.")
        return None

    options = {
        f"{player_name(p)} ({p.get('tour', '').upper()})": p for p in players
    }
    selected = st.selectbox("Select player", list(options), key=f"{key}_select")
    return options[selected]


def add_download(df: pd.DataFrame, name: str) -> None:
    if df.empty:
        return
    st.download_button(
        "Download CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=name,
        mime="text/csv",
        width="stretch",
    )


def display_matches(df: pd.DataFrame, limit: int | None = None) -> None:
    if df.empty:
        st.info("No matches found.")
        return
    show = df.copy()
    if limit:
        show = show.head(limit)
    columns = [
        "tourney_date", "tourney_name", "surface", "tourney_level", "round",
        "winner_name", "loser_name", "score", "winner_rank", "loser_rank", "tour",
    ]
    columns = [c for c in columns if c in show.columns]
    show = show[columns]
    if "tourney_date" in show.columns:
        show["tourney_date"] = show["tourney_date"].map(format_date)
    if "tourney_level" in show.columns:
        show["tourney_level"] = show["tourney_level"].map(lambda x: LEVEL_LABELS.get(x, x))
    st.dataframe(show, hide_index=True, width="stretch")


def render_header() -> None:
    st.set_page_config(page_title="TennisStatsV2 Web", layout="wide")
    st.markdown(
        """
        <style>
        :root { --accent: #0f766e; --ink: #17202a; --muted: #5b6472; }
        .block-container { padding-top: 1.4rem; padding-bottom: 2rem; }
        h1, h2, h3 { letter-spacing: 0; color: var(--ink); }
        div[data-testid="stMetric"] {
            background: #ffffff; border: 1px solid #d8dee8; border-radius: 8px;
            padding: 0.7rem 0.9rem; box-shadow: 0 1px 2px rgba(23,32,42,0.04);
        }
        div[data-testid="stTabs"] button { font-weight: 650; }
        .stButton > button, .stDownloadButton > button {
            border-radius: 8px; border-color: #b9c3d0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    left, right = st.columns([0.72, 0.28], vertical_alignment="center")
    with left:
        st.title("TennisStatsV2")
    with right:
        if st.button("Refresh snapshot", width="stretch"):
            get_db.clear()
            st.session_state["refresh_token"] = st.session_state.get("refresh_token", 0) + 1
            st.rerun()


def page_dashboard(db) -> None:
    counts = run_query(
        db,
        """
        SELECT
          (SELECT COUNT(*) FROM players) AS players,
          (SELECT COUNT(*) FROM matches WHERE is_upcoming = 0 OR is_upcoming IS NULL) AS matches,
          (SELECT COUNT(*) FROM matches WHERE tourney_id = 'SCRAPED') AS scraped_matches,
          (SELECT COUNT(*) FROM rankings WHERE ranking_date LIKE 'SCRAPED_%') AS ranking_rows,
          (SELECT COUNT(*) FROM match_winners_errors) AS extended_rows
        """,
    )
    if not counts.empty:
        row = counts.iloc[0]
        cols = st.columns(5)
        cols[0].metric("Players", f"{int(row.players):,}")
        cols[1].metric("Matches", f"{int(row.matches):,}")
        cols[2].metric("Scraped", f"{int(row.scraped_matches):,}")
        cols[3].metric("Rankings", f"{int(row.ranking_rows):,}")
        cols[4].metric("Ext rows", f"{int(row.extended_rows):,}")

    st.subheader("Latest completed matches")
    latest = run_query(
        db,
        """
        SELECT tourney_date, tourney_name, surface, tourney_level, round,
               winner_name, loser_name, score, winner_rank, loser_rank, tour
        FROM matches
        WHERE is_upcoming = 0 OR is_upcoming IS NULL
        ORDER BY tourney_date DESC, match_num DESC
        LIMIT 40
        """,
    )
    display_matches(latest)


def page_rankings(db) -> None:
    controls = st.columns([0.16, 0.16, 0.2, 0.48])
    tour = controls[0].segmented_control("Tour", ["atp", "wta"], default="atp", format_func=str.upper)
    top_n = controls[1].number_input("Top", min_value=10, max_value=1000, value=100, step=10)
    marker_rows = run_query(
        db,
        "SELECT DISTINCT ranking_date FROM rankings WHERE tour = ? ORDER BY ranking_date DESC LIMIT 30",
        (tour,),
    )
    dates = marker_rows["ranking_date"].tolist() if not marker_rows.empty else []
    preferred = ["SCRAPED_OFFICIAL_SINGLES", "SCRAPED_LIVE_SINGLES", "LIVE"]
    default_date = next((date for date in preferred if date in dates), dates[0] if dates else None)
    if not default_date:
        st.info("No ranking rows found.")
        return
    selected_date = controls[2].selectbox(
        "Snapshot",
        dates,
        index=dates.index(default_date),
    )

    rankings, ranking_date = db.get_rankings(tour=tour, date=selected_date, top_n=int(top_n))
    df = rows_to_frame(rankings)
    controls[3].metric("Ranking date", ranking_date or "-")
    if df.empty:
        st.info("No ranking rows found.")
        return

    df["player"] = (df["name_first"].fillna("") + " " + df["name_last"].fillna("")).str.strip()
    show = df[["rank", "player", "points", "age", "rank_diff", "pts_diff", "next_tournament", "ioc", "tour"]]
    st.dataframe(show, hide_index=True, width="stretch")
    add_download(show, f"rankings_{tour}_{ranking_date}.csv")


def page_player(db) -> None:
    player = pick_player(db, "Search player", "player_page")
    if not player:
        return
    name = player_name(player)
    tour = player.get("tour") or "atp"
    stats = db.get_player_career_stats(player["player_id"], tour=tour)
    streaks = db.get_player_streaks(player["player_id"], tour=tour)
    upcoming = db.get_player_upcoming_match(name)

    st.subheader(f"{name} ({tour.upper()})")
    cols = st.columns(5)
    wins = int(stats.get("wins", 0) or 0)
    losses = int(stats.get("losses", 0) or 0)
    total = wins + losses
    cols[0].metric("Record", f"{wins}-{losses}")
    cols[1].metric("Win %", f"{wins / total * 100:.1f}%" if total else "-")
    cols[2].metric("Titles", int(stats.get("titles", 0) or 0))
    cols[3].metric("Best streak", int(streaks.get("best_win_streak", 0) or 0))
    cols[4].metric("Current streak", int(streaks.get("current_win_streak", 0) or 0))

    if upcoming:
        st.info(
            f"Upcoming: {upcoming.get('tourney_name', '')} vs "
            f"{upcoming.get('opponent') or 'TBD'} on {format_date(upcoming.get('tourney_date'))}"
        )

    chart_cols = st.columns([0.42, 0.58])
    surface_rows = []
    for surface, values in (stats.get("surfaces") or {}).items():
        surface_rows.append({"surface": surface, "wins": values.get("wins", 0), "losses": values.get("losses", 0)})
    if surface_rows:
        surface_df = pd.DataFrame(surface_rows)
        surface_df["matches"] = surface_df["wins"] + surface_df["losses"]
        fig = px.bar(surface_df, x="surface", y=["wins", "losses"], barmode="stack", color_discrete_sequence=["#0f766e", "#c2410c"])
        chart_cols[0].plotly_chart(fig, width="stretch")

    rank_history = rows_to_frame(db.get_player_ranking_history(player["player_id"], tour))
    if not rank_history.empty:
        rank_history["date"] = pd.to_datetime(rank_history["ranking_date"], format="%Y%m%d", errors="coerce")
        fig = px.line(rank_history.dropna(subset=["date"]), x="date", y="rank", markers=False)
        fig.update_yaxes(autorange="reversed")
        chart_cols[1].plotly_chart(fig, width="stretch")

    matches = rows_to_frame(db.get_player_matches(player["player_id"], tour=tour))
    st.subheader("Matches")
    display_matches(matches, limit=100)
    add_download(matches, f"matches_{name.replace(' ', '_')}.csv")


def page_matches(db) -> None:
    controls = st.columns(5)
    tour = controls[0].segmented_control("Tour", ["atp", "wta"], default="atp", format_func=str.upper, key="matches_tour")
    year = controls[1].number_input("Year", min_value=1968, max_value=datetime.now().year + 1, value=datetime.now().year, step=1)
    surface = controls[2].selectbox("Surface", [None, "Hard", "Clay", "Grass", "Carpet"], format_func=lambda x: "All" if x is None else x)
    level = controls[3].selectbox("Level", [None] + list(LEVEL_LABELS), format_func=lambda x: "All" if x is None else LEVEL_LABELS.get(x, x))
    text = controls[4].text_input("Player / tournament")

    params: list[Any] = [tour, f"{int(year)}0000", f"{int(year)}9999"]
    filters = ["tour = ?", "tourney_date BETWEEN ? AND ?", "(is_upcoming = 0 OR is_upcoming IS NULL)"]
    if surface:
        filters.append("surface = ?")
        params.append(surface)
    if level:
        filters.append("tourney_level = ?")
        params.append(level)
    if text.strip():
        filters.append("(winner_name LIKE ? OR loser_name LIKE ? OR tourney_name LIKE ?)")
        like = f"%{text.strip()}%"
        params.extend([like, like, like])
    where = " AND ".join(filters)
    df = run_query(
        db,
        f"""
        SELECT tourney_date, tourney_name, surface, tourney_level, round,
               winner_name, loser_name, score, winner_rank, loser_rank, tour
        FROM matches
        WHERE {where}
        ORDER BY tourney_date DESC, match_num DESC
        LIMIT 1000
        """,
        tuple(params),
    )
    display_matches(df)
    add_download(df, f"matches_{tour}_{int(year)}.csv")


def page_h2h(db) -> None:
    col1, col2 = st.columns(2)
    with col1:
        p1 = pick_player(db, "Player 1", "h2h_p1")
    with col2:
        p2 = pick_player(db, "Player 2", "h2h_p2")
    if not p1 or not p2:
        return
    if (p1.get("tour") or "") != (p2.get("tour") or ""):
        st.warning("Players are on different tours.")
        return
    result = db.get_head_to_head(p1["player_id"], p2["player_id"], tour=p1.get("tour"))
    c1, c2, c3 = st.columns(3)
    c1.metric(player_name(p1), result.get("p1_wins", 0))
    c2.metric("Matches", result.get("total_matches", 0))
    c3.metric(player_name(p2), result.get("p2_wins", 0))
    surface = result.get("by_surface") or {}
    if surface:
        surf_df = pd.DataFrame([
            {"surface": k, player_name(p1): v.get("p1_wins", 0), player_name(p2): v.get("p2_wins", 0)}
            for k, v in surface.items()
        ])
        fig = px.bar(surf_df, x="surface", y=[player_name(p1), player_name(p2)], barmode="group", color_discrete_sequence=["#0f766e", "#c2410c"])
        st.plotly_chart(fig, width="stretch")
    display_matches(rows_to_frame(result.get("matches") or []))


def page_tournaments(db) -> None:
    controls = st.columns([0.2, 0.2, 0.6])
    tour = controls[0].segmented_control("Tour", ["atp", "wta"], default="atp", format_func=str.upper, key="tournament_tour")
    year = controls[1].number_input("Year", min_value=1968, max_value=datetime.now().year + 1, value=datetime.now().year, step=1, key="tournament_year")
    query = controls[2].text_input("Tournament contains")

    tournaments = rows_to_frame(db.get_tournament_list(year=int(year), tour=tour))
    if query.strip() and not tournaments.empty:
        tournaments = tournaments[tournaments["tourney_name"].str.contains(query.strip(), case=False, na=False)]
    if tournaments.empty:
        st.info("No tournaments found.")
        return
    tournaments["label"] = tournaments["tourney_name"] + " - " + tournaments["tourney_date"].map(format_date)
    selected = st.selectbox("Tournament", tournaments["label"].tolist())
    selected_row = tournaments[tournaments["label"] == selected].iloc[0]
    results = run_query(
        db,
        """
        SELECT * FROM matches
        WHERE tour = ? AND tourney_name = ?
          AND tourney_date BETWEEN ? AND ?
          AND (is_upcoming = 0 OR is_upcoming IS NULL)
        ORDER BY tourney_date DESC, match_num DESC
        """,
        (tour, selected_row["tourney_name"], f"{int(year)}0000", f"{int(year)}9999"),
    )
    display_matches(results)


def page_extended(db) -> None:
    player = pick_player(db, "Search player", "extended_player")
    if not player:
        return
    name = clean_player_name_for_db(player_name(player))
    counts = db.get_extended_stats_count(name)
    cols = st.columns(len(EXTENDED_TABLES))
    for idx, (label, (table, _method)) in enumerate(EXTENDED_TABLES.items()):
        cols[idx].metric(label.split()[0], counts.get(table, 0))

    table_label = st.selectbox("Table", list(EXTENDED_TABLES))
    surface = st.selectbox("Surface", [None, "Hard", "Clay", "Grass", "Carpet"], key="ext_surface", format_func=lambda x: "All" if x is None else x)
    year = st.number_input("Year", min_value=0, max_value=datetime.now().year + 1, value=0, step=1, key="ext_year")
    _table, method_name = EXTENDED_TABLES[table_label]
    method = getattr(db, method_name)
    rows = method(name, surface=surface, year=int(year) if year else None)
    df = rows_to_frame(rows)
    if df.empty:
        st.info("No extended rows found for this selection.")
        return
    if "tourney_date" in df.columns:
        df["tourney_date"] = df["tourney_date"].map(format_date)
    st.dataframe(df, hide_index=True, width="stretch")
    add_download(df, f"extended_{name.replace(' ', '_')}_{_table}.csv")


def page_global(db) -> None:
    from tennis_app.core.global_stats_engine import GlobalStatsEngine

    controls = st.columns(5)
    stat_name = controls[0].selectbox("Leaderboard", list(GLOBAL_STATS))
    tour = controls[1].segmented_control("Tour", ["atp", "wta"], default="atp", format_func=str.upper, key="global_tour")
    surface = controls[2].selectbox("Surface", [None, "Hard", "Clay", "Grass", "Carpet"], format_func=lambda x: "All" if x is None else x, key="global_surface")
    level = controls[3].selectbox("Level", [None] + list(LEVEL_LABELS), format_func=lambda x: "All" if x is None else LEVEL_LABELS.get(x, x), key="global_level")
    limit = controls[4].number_input("Limit", min_value=10, max_value=100, value=50, step=10)

    filters = {"tour": tour, "surface": surface, "tourney_level": level, "min_matches": 10}
    result = GlobalStatsEngine(db).compute(GLOBAL_STATS[stat_name], filters, limit=int(limit))
    rows = result.get("rows") or []
    if rows and isinstance(rows[0], dict):
        df = pd.DataFrame(rows)
    else:
        df = pd.DataFrame(rows, columns=result.get("columns"))
    if df.empty:
        st.info(result.get("message") or "No rows found.")
        return
    st.dataframe(df, hide_index=True, width="stretch")
    numeric_cols = []
    for col in df.columns:
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().any():
            df[f"_{col}_numeric"] = converted
            numeric_cols.append(f"_{col}_numeric")
    name_col = next(
        (c for c in ("Player", "player", "name", "winner_name", "label") if c in df.columns),
        df.columns[0],
    )
    if numeric_cols:
        value_col = numeric_cols[-1]
        fig = px.bar(df.head(20), x=value_col, y=name_col, orientation="h", color_discrete_sequence=["#0f766e"])
        value_label = value_col[1:-8] if value_col.startswith("_") and value_col.endswith("_numeric") else value_col
        fig.update_layout(xaxis_title=value_label, yaxis_title=name_col)
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, width="stretch")


def main() -> None:
    render_header()
    refresh_token = st.session_state.get("refresh_token", 0)
    try:
        db = get_db(refresh_token)
    except Exception as exc:
        st.error("Turso connection is not configured or the snapshot could not be loaded.")
        st.code(str(exc))
        st.stop()

    page = st.segmented_control(
        "View",
        ["Dashboard", "Rankings", "Player", "Matches", "H2H", "Tournaments", "Extended", "Global"],
        default="Dashboard",
        label_visibility="collapsed",
    )
    if page == "Dashboard":
        page_dashboard(db)
    elif page == "Rankings":
        page_rankings(db)
    elif page == "Player":
        page_player(db)
    elif page == "Matches":
        page_matches(db)
    elif page == "H2H":
        page_h2h(db)
    elif page == "Tournaments":
        page_tournaments(db)
    elif page == "Extended":
        page_extended(db)
    elif page == "Global":
        page_global(db)


if __name__ == "__main__":
    main()
