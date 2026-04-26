"""
Player Search & Profile page.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSizePolicy, QPushButton,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..widgets import (
    ScrollablePage, SearchBar, StatGrid, Separator, PlayerHeader, DataTable,
    SectionHeader, PillButtonGroup,
)
from ..charts import (
    spider_chart, bar_chart_yearly, surface_donut,
    rank_yearly_chart, ranking_history_chart,
    get_chart_base_url,
)
from ..theme import COLORS, FONTS


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

class _ProfileWorker(QThread):
    """Fetch all DB data and build chart HTML strings off the UI thread."""

    data_ready = Signal(dict)   # emits payload dict on success
    error = Signal(str)

    def __init__(self, db, player_id, tour, parent=None):
        super().__init__(parent)
        self._db = db
        self._player_id = player_id
        self._tour = tour

    def run(self):
        try:
            player = self._db.get_player(self._player_id, tour=self._tour)
            if not player:
                self.error.emit("Player not found")
                return

            stats = self._db.get_player_career_stats(self._player_id, tour=self._tour)
            rank_history = []
            try:
                rank_history = self._db.get_player_ranking_history(
                    self._player_id, self._tour or player.get("tour"))
            except Exception:
                pass

            # Pre-compute all chart HTML in the background thread
            html_donut = surface_donut(stats.get("surfaces", {})) or ""
            html_yearly = bar_chart_yearly(stats.get("yearly", {})) if stats.get("yearly") else ""
            html_rank_yr = rank_yearly_chart(rank_history) if rank_history else ""
            html_rank_wk = ranking_history_chart(rank_history) if rank_history else ""

            self.data_ready.emit({
                "player": dict(player),
                "stats": stats,
                "rank_history": rank_history,
                "html_donut": html_donut,
                "html_yearly": html_yearly,
                "html_rank_yr": html_rank_yr,
                "html_rank_wk": html_rank_wk,
            })
        except Exception as exc:
            self.error.emit(str(exc))


class PlayerPage(QWidget):
    """Page for searching players and viewing their profile dashboard."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.current_player = None
        self._profile_worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Search bar at top
        search_container = QWidget()
        search_container.setStyleSheet(
            f"background-color: {COLORS['bg_secondary']}; "
            f"padding: 12px 24px;")
        sc_layout = QHBoxLayout(search_container)
        sc_layout.setContentsMargins(24, 12, 24, 12)

        self.search_bar = SearchBar(
            placeholder="Search player name...",
            button_text="🔍 Search",
        )
        self.search_bar.searched.connect(self._on_search)
        sc_layout.addWidget(self.search_bar)

        layout.addWidget(search_container)

        # Splitter: left = results, right = profile
        splitter = QSplitter(Qt.Horizontal)

        # Left panel — search results table
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 4, 8)

        lbl = QLabel("Results")
        lbl.setObjectName("subHeaderLabel")
        left_layout.addWidget(lbl)

        self.results_table = DataTable([
            ("Name", 180), ("Country", 60), ("Hand", 55), ("Born", 90),
        ])
        self.results_table.cellClicked.connect(self._on_select)
        left_layout.addWidget(self.results_table)

        splitter.addWidget(left)

        # Right panel — profile
        self.profile_area = ScrollablePage()
        splitter.addWidget(self.profile_area)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter, 1)

        # Placeholder
        placeholder = QLabel("Search and select a player to view their profile.")
        placeholder.setObjectName("dimLabel")
        placeholder.setAlignment(Qt.AlignCenter)
        self.profile_area.content_layout.addWidget(placeholder)

        # Store mapping from row → (tour, player_id)
        self._row_map = []

    def _on_search(self, query: str):
        if len(query) < 2:
            return
        results = self.db.search_players(query)
        self._row_map.clear()
        rows = []
        for p in results:
            dob = p.get("dob", "")
            if dob and len(str(dob)) >= 8:
                dob = f"{str(dob)[:4]}-{str(dob)[4:6]}-{str(dob)[6:8]}"
            hand_map = {"R": "Right", "L": "Left", "U": "Unknown"}
            tour = p.get("tour", "atp")
            rows.append([
                f"{p['name_first']} {p['name_last']}",
                p.get("ioc", ""),
                hand_map.get(p.get("hand", ""), p.get("hand", "")),
                str(dob),
            ])
            self._row_map.append((tour, p["player_id"]))
        self.results_table.populate(rows)

    def _on_select(self, row, col):
        if row < 0 or row >= len(self._row_map):
            return
        tour, player_id = self._row_map[row]
        self._show_profile(player_id, tour)

    def _show_profile(self, player_id, tour=None):
        # Show spinner immediately so the UI stays responsive
        self.profile_area.clear_content()
        spinner = QLabel("Loading player profile…")
        spinner.setObjectName("dimLabel")
        spinner.setAlignment(Qt.AlignCenter)
        self.profile_area.content_layout.addWidget(spinner)

        # Stop any previous worker
        if self._profile_worker and self._profile_worker.isRunning():
            self._profile_worker.quit()
            self._profile_worker.wait(2000)

        worker = _ProfileWorker(self.db, player_id, tour, parent=self)
        worker.data_ready.connect(self._on_profile_data)
        worker.error.connect(self._on_profile_error)
        self._profile_worker = worker
        worker.start()

    def _on_profile_error(self, msg):
        self.profile_area.clear_content()
        lbl = QLabel(f"Could not load profile: {msg}")
        lbl.setObjectName("dimLabel")
        lbl.setAlignment(Qt.AlignCenter)
        self.profile_area.content_layout.addWidget(lbl)

    def _on_profile_data(self, payload):
        player = payload["player"]
        stats = payload["stats"]
        rank_history = payload["rank_history"]
        html_donut = payload["html_donut"]
        html_yearly = payload["html_yearly"]
        html_rank_yr = payload["html_rank_yr"]
        html_rank_wk = payload["html_rank_wk"]

        self.current_player = player
        self.profile_area.clear_content()
        layout = self.profile_area.content_layout

        # --- Header ---
        name = f"{player['name_first']} {player['name_last']}"
        info_parts = []
        if player.get("ioc"):
            info_parts.append(f"🏳 {player['ioc']}")
        hand_map = {"R": "Right-handed", "L": "Left-handed"}
        if player.get("hand"):
            info_parts.append(
                f"✋ {hand_map.get(player['hand'], player['hand'])}")
        if player.get("height"):
            info_parts.append(f"📏 {int(player['height'])} cm")
        dob = player.get("dob", "")
        if dob and len(str(dob)) >= 8:
            d = str(dob)
            info_parts.append(f"🎂 {d[:4]}-{d[4:6]}-{d[6:8]}")

        header = PlayerHeader(name, info_parts)

        # Header row with Stats button
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(header)
        header_row.addStretch()

        stats_btn = QPushButton("📈 View Stats")
        stats_btn.setObjectName("accentBtn")
        stats_btn.setCursor(Qt.PointingHandCursor)
        stats_btn.clicked.connect(lambda: self._go_to_stats(name))
        header_row.addWidget(stats_btn)

        header_widget = QWidget()
        header_widget.setLayout(header_row)
        layout.addWidget(header_widget)
        layout.addWidget(Separator())

        # --- Career record cards ---
        grid = StatGrid(columns=4)
        grid.add_stat("Career W-L",
                       f"{stats['wins']}-{stats['losses']}", icon="🎾")
        pct = (stats['wins'] / (stats['wins'] + stats['losses']) * 100
               if (stats['wins'] + stats['losses']) else 0)
        grid.add_stat("Win %", f"{pct:.1f}%", icon="📈")
        grid.add_stat("Titles", str(stats['titles']), icon="🏆")

        if stats.get("serve"):
            sv = stats["serve"]
            grid.add_stat("Aces", str(sv.get("aces", 0)), icon="🎯")
            grid.add_stat("1st Serve %", f"{sv.get('first_serve_pct', 0)}%")
            grid.add_stat("1st Serve Won",
                          f"{sv.get('first_serve_won_pct', 0)}%")
            grid.add_stat("2nd Serve Won",
                          f"{sv.get('second_serve_won_pct', 0)}%")
            grid.add_stat("BP Saved",
                          f"{sv.get('bp_saved_pct', 0)}%", icon="🛡️")

        layout.addWidget(grid)
        layout.addWidget(Separator())

        # --- Surface breakdown — donut + stat cards ---
        layout.addWidget(SectionHeader("Record by Surface"))

        surfaces = stats.get("surfaces", {})
        if surfaces:
            row = QHBoxLayout()

            # Donut chart (HTML pre-built in background thread)
            if html_donut:
                chart = QWebEngineView()
                chart.setFixedHeight(280)
                chart.setHtml(html_donut, get_chart_base_url())
                row.addWidget(chart, 2)

            # Stat cards
            surface_grid = StatGrid(columns=2)
            for surface, rec in surfaces.items():
                w, l = rec["wins"], rec["losses"]
                p = round(w / (w + l) * 100, 1) if (w + l) else 0
                surface_grid.add_stat(surface, f"{w}-{l} ({p}%)")
            row.addWidget(surface_grid, 1)

            row_widget = QWidget()
            row_widget.setLayout(row)
            layout.addWidget(row_widget)
        layout.addWidget(Separator())

        # --- Tournament level ---
        layout.addWidget(SectionHeader("Record by Tournament Level"))

        level_grid = StatGrid(columns=4)
        for level, rec in stats.get("levels", {}).items():
            w, l = rec["wins"], rec["losses"]
            p = round(w / (w + l) * 100, 1) if (w + l) else 0
            level_grid.add_stat(level, f"{w}-{l} ({p}%)")
        layout.addWidget(level_grid)
        layout.addWidget(Separator())

        # --- Yearly chart ---
        if html_yearly:
            layout.addWidget(SectionHeader("Year-by-Year Record"))
            chart = QWebEngineView()
            chart.setFixedHeight(300)
            chart.setHtml(html_yearly, get_chart_base_url())
            layout.addWidget(chart)
            layout.addWidget(Separator())

        # --- Ranking career (yearly best/year-end + week-by-week) ---
        if rank_history:
            layout.addWidget(SectionHeader("Ranking Career"))

            if html_rank_yr:
                chart_yr = QWebEngineView()
                chart_yr.setFixedHeight(320)
                chart_yr.setHtml(html_rank_yr, get_chart_base_url())
                layout.addWidget(chart_yr)

            if html_rank_wk:
                chart_wk = QWebEngineView()
                chart_wk.setFixedHeight(340)
                chart_wk.setHtml(html_rank_wk, get_chart_base_url())
                layout.addWidget(chart_wk)

            layout.addWidget(Separator())

        # --- By round ---
        layout.addWidget(SectionHeader("Record by Round"))

        round_names = {
            "F": "Final", "SF": "Semi-Final", "QF": "Quarter-Final",
            "R16": "Round of 16", "R32": "Round of 32",
            "R64": "Round of 64", "R128": "Round of 128", "RR": "Round Robin",
        }
        round_table = DataTable([
            ("Round", 120), ("Wins", 80), ("Losses", 80), ("Win %", 80),
        ])
        round_rows = []
        for rnd, rec in stats.get("rounds", {}).items():
            w, l = rec["wins"], rec["losses"]
            p = round(w / (w + l) * 100, 1) if (w + l) else 0
            round_rows.append([
                round_names.get(rnd, rnd), str(w), str(l), f"{p}%"
            ])
        round_table.populate(round_rows)
        round_table.setMinimumHeight(200)
        round_table.setMaximumHeight(300)
        layout.addWidget(round_table)

    def _go_to_stats(self, player_name: str):
        """Navigate to the Stats tab and load this player."""
        main_win = self.window()
        if hasattr(main_win, 'switch_to_stats'):
            main_win.switch_to_stats(player_name)
