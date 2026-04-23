"""
Match History page.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QSizePolicy,
)
from PySide6.QtCore import Qt

from ..widgets import SearchBar, DataTable, Separator, PillButtonGroup, PlayerSearchEdit
from ..theme import COLORS


class MatchesPage(QWidget):
    """Page for browsing match history with filters and pagination."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._all_matches = []
        self._current_player = None
        self._current_page = 1
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # --- Header row with search ---
        header_row = QHBoxLayout()
        header = QLabel("Match History")
        header.setObjectName("headerLabel")
        header_row.addWidget(header)

        self.player_search = PlayerSearchEdit(self.db, placeholder="Player name...")
        self.player_search.setMinimumWidth(320)
        self.player_search.player_selected.connect(self._on_player_selected)
        header_row.addWidget(self.player_search)

        header_row.addStretch()
        layout.addLayout(header_row)

        # --- Filters ---
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        self.surface_pills = PillButtonGroup(
            ["All", "Hard", "Clay", "Grass", "Carpet"])
        filter_row.addWidget(self.surface_pills)

        filter_row.addWidget(QLabel("Year:"))
        self.year_combo = QComboBox()
        self.year_combo.addItems(
            ["All"] + [str(y) for y in range(2026, 1967, -1)])
        filter_row.addWidget(self.year_combo)

        filter_row.addWidget(QLabel("Round:"))
        self.round_combo = QComboBox()
        self.round_combo.addItems(
            ["All", "F", "SF", "QF", "R16", "R32", "R64", "R128", "RR"])
        filter_row.addWidget(self.round_combo)

        self.level_pills = PillButtonGroup(
            ["All", "GS", "M", "ATP", "CH",
             "DC", "Fin"])
        filter_row.addWidget(self.level_pills)

        filter_row.addWidget(QLabel("Rows:"))
        self.rows_combo = QComboBox()
        self.rows_combo.addItems(["50", "100", "200", "500", "All"])
        self.rows_combo.currentIndexChanged.connect(
            lambda _: self._display_page())
        filter_row.addWidget(self.rows_combo)

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # --- Table ---
        self.table = DataTable([
            ("Date", 90), ("Tournament", 170), ("Level", 80),
            ("Surface", 65), ("Round", 55), ("Winner", 180),
            ("Loser", 180), ("Score", 140), ("Min", 45),
        ])
        layout.addWidget(self.table, 1)

        # --- Pagination ---
        page_row = QHBoxLayout()
        self.status_label = QLabel(
            "Enter a player name and click Search")
        self.status_label.setObjectName("dimLabel")
        page_row.addWidget(self.status_label)
        page_row.addStretch()

        self.prev_btn = QPushButton("◀ Prev")
        self.prev_btn.setObjectName("accentBtn")
        self.prev_btn.setCursor(Qt.PointingHandCursor)
        self.prev_btn.clicked.connect(self._prev_page)
        page_row.addWidget(self.prev_btn)

        self.page_label = QLabel("Page 1")
        self.page_label.setObjectName("dimLabel")
        page_row.addWidget(self.page_label)

        self.next_btn = QPushButton("Next ▶")
        self.next_btn.setObjectName("accentBtn")
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.clicked.connect(self._next_page)
        page_row.addWidget(self.next_btn)

        layout.addLayout(page_row)

    def _on_player_selected(self, player):
        """Called when user selects a player from autocomplete."""
        self._search_player(player)

    def _search_player(self, player):
        player_id = player["player_id"]
        player_tour = player.get("tour")
        self._current_player = f"{player['name_first']} {player['name_last']}"

        surface = self.surface_pills.value()
        surface = None if surface == "All" else surface
        year = self.year_combo.currentText()
        year = None if year == "All" else int(year)
        round_ = self.round_combo.currentText()
        round_ = None if round_ == "All" else round_

        _level_map = {
            "All": None, "GS": "G", "M": "M",
            "ATP": "A", "CH": "C", "DC": "D", "Fin": "F",
        }
        tourney_level = _level_map.get(self.level_pills.value())

        self._all_matches = self.db.get_player_matches(
            player_id, surface=surface,
            tourney_level=tourney_level,
            year=year, round_=round_,
            tour=player_tour,
        )
        self._current_page = 1
        self._display_page()

    def _rows_per_page(self):
        text = self.rows_combo.currentText()
        return None if text == "All" else int(text)

    def _total_pages(self):
        rpp = self._rows_per_page()
        if not rpp or not self._all_matches:
            return 1
        return max(1, (len(self._all_matches) + rpp - 1) // rpp)

    def _display_page(self):
        if not self._all_matches:
            self.table.setRowCount(0)
            self.status_label.setText("No matches found")
            self.page_label.setText("")
            return

        rpp = self._rows_per_page()
        total_pages = self._total_pages()
        self._current_page = max(1, min(self._current_page, total_pages))

        if rpp:
            start = (self._current_page - 1) * rpp
            page = self._all_matches[start:start + rpp]
        else:
            page = self._all_matches

        level_map = {
            "G": "Grand Slam", "M": "Masters", "A": "ATP",
            "D": "Davis Cup", "F": "Finals", "C": "Challenger",
        }

        rows = []
        for m in page:
            date = str(m.get("tourney_date", ""))
            if len(date) == 8:
                date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
            level = level_map.get(
                m.get("tourney_level", ""), m.get("tourney_level", ""))
            rows.append([
                date,
                m.get("tourney_name", ""),
                level,
                m.get("surface", ""),
                m.get("round", ""),
                self._format_player(m, "winner"),
                self._format_player(m, "loser"),
                m.get("score", ""),
                str(int(m["minutes"])) if m.get("minutes") else "",
            ])

        self.table.populate(rows)

        page_text = (f"Page {self._current_page} of {total_pages}"
                     if rpp else "All")
        self.page_label.setText(page_text)
        self.status_label.setText(
            f"Showing {len(page)} of {len(self._all_matches)} matches "
            f"for {self._current_player}")

    def _prev_page(self):
        if self._current_page > 1:
            self._current_page -= 1
            self._display_page()

    def _next_page(self):
        if self._current_page < self._total_pages():
            self._current_page += 1
            self._display_page()

    @staticmethod
    def _format_player(match, role):
        name = match.get(f"{role}_name", "")
        seed = match.get(f"{role}_seed", "")
        entry = match.get(f"{role}_entry", "")
        ioc = match.get(f"{role}_ioc", "")

        parts = [name]
        tag = ""
        if seed and str(seed).strip():
            try:
                tag = str(int(float(seed)))
            except (ValueError, TypeError):
                tag = str(seed)
        if entry and str(entry).strip():
            tag = f"{tag}/{entry}" if tag else str(entry)
        if tag:
            parts.append(f"({tag})")
        if ioc and str(ioc).strip():
            parts.append(str(ioc))
        return " ".join(parts)
