"""
Nations & Fun Facts page.

Nationality-based tournament statistics:
  • Breakdown of how many times players of a country appeared at each
    round / tournament-level combination.
  • Concentration finder: tournaments where at least N players of a
    chosen nationality were present at the same stage.
"""

from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QSizePolicy, QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush

from ..widgets import (
    DataTable, Separator, SectionHeader, StatGrid,
    PillButtonGroup, MultiPillButtonGroup, ScrollablePage,
)
from ..theme import COLORS

# ---- Constants ---------------------------------------------------------------

ROUND_ORDER = {
    "F": 0, "SF": 1, "QF": 2, "R16": 3, "R32": 4,
    "R64": 5, "R128": 6, "RR": 7,
}
LEVEL_LABEL = {
    "G": "Grand Slam", "M": "Masters", "A": "ATP", "C": "Challenger",
    "D": "Davis Cup", "F": "Finals",
}
LEVEL_CODE = {v: k for k, v in LEVEL_LABEL.items()}

# Colour gradient helpers for count cells
_GREEN = QColor("#2e7d32")
_YELLOW = QColor("#f9a825")
_RED = QColor("#c62828")


def _count_color(val: int, max_val: int) -> QColor:
    """Return a colour between dim-grey (0) and bright-green (max)."""
    if max_val <= 0 or val <= 0:
        return QColor(COLORS["text_muted"])
    t = min(val / max_val, 1.0)
    r = int(_RED.red()   * (1 - t) + _GREEN.red()   * t)
    g = int(_RED.green() * (1 - t) + _GREEN.green() * t)
    b = int(_RED.blue()  * (1 - t) + _GREEN.blue()  * t)
    return QColor(r, g, b)


# ---- Page --------------------------------------------------------------------

class InsightsPage(QWidget):
    """Nations & Fun Facts page."""

    _BREAKDOWN_COLS = [
        ("Level",          140),
        ("Round",           60),
        ("Won",             70),
        ("Lost",            70),
        ("Total",           70),
        ("Unique Players",  90),
    ]
    _INSTANCE_COLS = [
        ("Year",        55),
        ("Tournament", 200),
        ("Level",      120),
        ("Surface",     75),
        ("Round",       60),
        ("Players",     70),
    ]

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._ioc_list: list[str] = []
        self._breakdown_data: list[dict] = []
        self._instance_data: list[dict] = []
        self._build_ui()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ---- Header ----
        header = QLabel("🌍 Nations & Fun Facts")
        header.setObjectName("headerLabel")
        root.addWidget(header)

        # ---- Filter row A ----
        fa = QHBoxLayout()
        fa.setSpacing(10)

        fa.addWidget(QLabel("Tour:"))
        self.tour_pills = PillButtonGroup(["ATP", "WTA"])
        self.tour_pills.changed.connect(self._on_tour_changed)
        fa.addWidget(self.tour_pills)

        fa.addWidget(QLabel("Nation:"))
        self.ioc_combo = QComboBox()
        self.ioc_combo.setMinimumWidth(90)
        fa.addWidget(self.ioc_combo)

        fa.addWidget(QLabel("Level:"))
        self.level_pills = MultiPillButtonGroup(
            ["All", "GS", "M", "ATP", "CH", "DC", "Fin"])
        fa.addWidget(self.level_pills)

        fa.addWidget(QLabel("From:"))
        self.year_from = QSpinBox()
        self.year_from.setRange(1968, datetime.now().year)
        self.year_from.setValue(1968)
        self.year_from.setFixedWidth(72)
        fa.addWidget(self.year_from)

        fa.addWidget(QLabel("To:"))
        self.year_to = QSpinBox()
        self.year_to.setRange(1968, datetime.now().year)
        self.year_to.setValue(datetime.now().year)
        self.year_to.setFixedWidth(72)
        fa.addWidget(self.year_to)

        self.search_btn = QPushButton("🔍 Search")
        self.search_btn.setObjectName("accentBtn")
        self.search_btn.setCursor(Qt.PointingHandCursor)
        self.search_btn.clicked.connect(self._run_search)
        fa.addWidget(self.search_btn)

        fa.addStretch()
        root.addLayout(fa)

        # ---- Summary StatGrid ----
        self.summary_grid = StatGrid(columns=5)
        self.summary_widget = QWidget()
        sg_layout = QVBoxLayout(self.summary_widget)
        sg_layout.setContentsMargins(0, 0, 0, 0)
        sg_layout.addWidget(SectionHeader("Summary"))
        sg_layout.addWidget(self.summary_grid)
        self.summary_widget.setVisible(False)
        root.addWidget(self.summary_widget)

        # ---- Section B: Round breakdown table ----
        root.addWidget(SectionHeader("Round Breakdown"))

        self.breakdown_table = DataTable(self._BREAKDOWN_COLS)
        self.breakdown_table.setMinimumHeight(220)
        root.addWidget(self.breakdown_table)

        root.addWidget(Separator())

        # ---- Filter row B: Concentration finder ----
        fb = QHBoxLayout()
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(10)

        fb.addWidget(QLabel("At least"))
        self.min_count_spin = QSpinBox()
        self.min_count_spin.setRange(1, 32)
        self.min_count_spin.setValue(2)
        self.min_count_spin.setFixedWidth(60)
        fb.addWidget(self.min_count_spin)

        fb.addWidget(QLabel("players at round:"))
        self.conc_round_pills = MultiPillButtonGroup(
            ["All", "F", "SF", "QF", "R16", "R32", "R64", "R128", "RR"])
        fb.addWidget(self.conc_round_pills)

        self.find_btn = QPushButton("🎯 Find Instances")
        self.find_btn.setObjectName("accentBtn")
        self.find_btn.setCursor(Qt.PointingHandCursor)
        self.find_btn.clicked.connect(self._run_concentration)
        fb.addWidget(self.find_btn)

        fb.addStretch()
        root.addLayout(fb)

        # ---- Section C: Instances table ----
        root.addWidget(SectionHeader("Instances Found"))
        self.instance_table = DataTable(self._INSTANCE_COLS)
        self.instance_table.setMinimumHeight(220)
        root.addWidget(self.instance_table, 1)

        # ---- Status labels ----
        self.status_label = QLabel("Select a nation and click Search")
        self.status_label.setObjectName("dimLabel")
        root.addWidget(self.status_label)

        self.instance_status_label = QLabel("Configure the finder above and click 🎯 Find Instances")
        self.instance_status_label.setObjectName("dimLabel")
        root.addWidget(self.instance_status_label)

        # Populate IOC dropdown (ATP is default)
        self._reload_ioc_list("atp")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current_tour(self) -> str:
        return self.tour_pills.value().lower()

    def _active_levels(self) -> list[str] | None:
        """Return list of tourney_level codes, or None if All."""
        _map = {"GS": "G", "M": "M", "ATP": "A", "CH": "C", "DC": "D", "Fin": "F"}
        selected = self.level_pills.values()
        if not selected:
            return None
        return [_map[v] for v in selected if v in _map] or None

    def _reload_ioc_list(self, tour: str):
        self.ioc_combo.blockSignals(True)
        prev = self.ioc_combo.currentText()
        self.ioc_combo.clear()
        try:
            codes = self.db.get_distinct_ioc_codes(tour=tour)
        except Exception:
            codes = []
        self._ioc_list = codes
        self.ioc_combo.addItems(codes)
        # Restore previous selection if still available
        idx = self.ioc_combo.findText(prev)
        if idx >= 0:
            self.ioc_combo.setCurrentIndex(idx)
        self.ioc_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_tour_changed(self, _value: str):
        self._reload_ioc_list(self._current_tour())

    def _run_search(self):
        ioc = self.ioc_combo.currentText().strip()
        if not ioc:
            self.status_label.setText("Please select a nation.")
            return

        tour = self._current_tour()
        year_from = self.year_from.value()
        year_to = self.year_to.value()
        if year_from > year_to:
            year_from, year_to = year_to, year_from
        levels = self._active_levels()

        try:
            data = self.db.get_nationality_round_breakdown(
                ioc=ioc, tour=tour,
                year_from=year_from, year_to=year_to,
                tourney_levels=levels,
            )
        except Exception as exc:
            self.status_label.setText(f"Query error: {exc}")
            return

        self._breakdown_data = data
        self._populate_breakdown(data, ioc, year_from, year_to)

    def _run_concentration(self):
        ioc = self.ioc_combo.currentText().strip()
        if not ioc:
            self.status_label.setText("Please select a nation.")
            return

        tour = self._current_tour()
        year_from = self.year_from.value()
        year_to = self.year_to.value()
        if year_from > year_to:
            year_from, year_to = year_to, year_from
        levels = self._active_levels()
        rounds = self.conc_round_pills.values() or None
        min_count = self.min_count_spin.value()

        try:
            data = self.db.get_ioc_concentration_instances(
                ioc=ioc, min_count=min_count, tour=tour,
                year_from=year_from, year_to=year_to,
                tourney_levels=levels, rounds=rounds,
            )
        except Exception as exc:
            self.status_label.setText(f"Query error: {exc}")
            return

        self._instance_data = data
        self._populate_instances(data, ioc, min_count, rounds, levels)

    # ------------------------------------------------------------------
    # Data display helpers
    # ------------------------------------------------------------------

    def _populate_breakdown(
        self, data: list[dict], ioc: str, year_from: int, year_to: int
    ):
        if not data:
            self.breakdown_table.setRowCount(0)
            self.summary_widget.setVisible(False)
            self.status_label.setText(
                f"No data found for {ioc} ({year_from}–{year_to})")
            return

        # --- Summary tiles ---
        total_appearances = sum(d["appearances"] for d in data)
        all_player_ids: set = set()
        # We can't get unique IDs from aggregated data, so use sum of unique_players
        # as an upper bound (some players appear in multiple rounds)
        # For the real unique count we'd need a separate query — use the breakdown max
        best_round = min(
            (ROUND_ORDER.get(d["round"], 99) for d in data),
            default=99,
        )
        best_round_label = next(
            (d["round"] for d in data
             if ROUND_ORDER.get(d["round"], 99) == best_round),
            "—"
        )
        gs_apps = sum(d["appearances"] for d in data if d["tourney_level"] == "G")
        m_apps  = sum(d["appearances"] for d in data if d["tourney_level"] == "M")
        titles  = sum(d["appearances"] for d in data
                      if d["tourney_level"] != "D"
                      and d["round"] == "F")
        titles_won = next(
            (d.get("wins", 0) for d in data
             if d["round"] == "F" and d["tourney_level"] != "D"), 0)

        self.summary_grid.clear()
        self.summary_grid.add_stat("Total Match Appearances", str(total_appearances), icon="🎾")
        self.summary_grid.add_stat("Grand Slam Apperances",   str(gs_apps),           icon="🏆")
        self.summary_grid.add_stat("Masters Appearances",     str(m_apps),            icon="🥇")
        self.summary_grid.add_stat("Finals Reached",          str(titles),            icon="🏅")
        self.summary_grid.add_stat("Best Round",              best_round_label,       icon="📈")
        self.summary_widget.setVisible(True)

        # --- Breakdown table ---
        # Sort by level group then round order
        sorted_data = sorted(
            data,
            key=lambda d: (
                d.get("tourney_level", ""),
                ROUND_ORDER.get(d["round"], 99),
            ),
        )

        max_wins = max((d.get("wins", 0) for d in sorted_data), default=1)
        max_losses = max((d.get("losses", 0) for d in sorted_data), default=1)
        rows = []
        for d in sorted_data:
            level_label = LEVEL_LABEL.get(d.get("tourney_level", ""), d.get("tourney_level", ""))
            rows.append([
                level_label,
                d.get("round", ""),
                str(d.get("wins", 0)),
                str(d.get("losses", 0)),
                str(d.get("appearances", 0)),
                str(d["unique_players"]),
            ])
        self.breakdown_table.populate(rows)

        # Colour Won (col 2) green, Lost (col 3) red
        _win_max_color = QColor("#2e7d32")
        _loss_max_color = QColor("#c62828")
        for r, d in enumerate(sorted_data):
            w_item = self.breakdown_table.item(r, 2)
            l_item = self.breakdown_table.item(r, 3)
            w = d.get("wins", 0)
            lo = d.get("losses", 0)
            if w_item and max_wins > 0:
                t = min(w / max_wins, 1.0)
                col = QColor(
                    int(80 + t * (46 - 80)),
                    int(80 + t * (125 - 80)),
                    int(80 + t * (50 - 80)),
                )
                w_item.setForeground(QBrush(_win_max_color if t > 0.6 else col))
            if l_item and max_losses > 0:
                t = min(lo / max_losses, 1.0)
                l_item.setForeground(QBrush(_loss_max_color if t > 0.6 else QColor(COLORS["text_dim"])))

        self.status_label.setText(
            f"Showing breakdown for {ioc} · {year_from}–{year_to} "
            f"· {len(data)} combinations"
        )

    def _populate_instances(
        self,
        data: list[dict],
        ioc: str,
        min_count: int,
        rounds: list | None = None,
        levels: list | None = None,
    ):
        round_str = "/".join(rounds) if rounds else "any round"
        level_str = "/".join(
            LEVEL_LABEL.get(lv, lv) for lv in (levels or [])
        ) or "any level"

        if not data:
            self.instance_table.setRowCount(0)
            self.instance_status_label.setText(
                f"No instances found: ≥{min_count} {ioc} players "
                f"at {round_str} · {level_str}")
            return

        rows = []
        for d in data:
            rows.append([
                str(d.get("year", "")),
                d.get("tourney_name", ""),
                LEVEL_LABEL.get(d.get("tourney_level", ""), d.get("tourney_level", "")),
                d.get("surface", ""),
                d.get("round", ""),
                str(d.get("ioc_count", "")),
            ])
        self.instance_table.populate(rows)

        # Colour the "Players" column (index 5)
        max_cnt = max(d.get("ioc_count", 0) for d in data) if data else 1
        for r, d in enumerate(data):
            item = self.instance_table.item(r, 5)
            if item:
                item.setForeground(QBrush(_count_color(d.get("ioc_count", 0), max_cnt)))

        self.instance_status_label.setText(
            f"{len(data)} instances where ≥{min_count} {ioc} players "
            f"reached the same round · {round_str} · {level_str}")
