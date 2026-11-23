from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class QuickControlDock(QWidget):
    """Minimal translucent floating pill at bottom-right with 1-click Pause / Switch / Pin / Dislike / Zen."""

    def __init__(
        self,
        on_toggle_pause: Callable[[], None],
        on_reroll: Callable[[], None],
        on_toggle_pin: Callable[[], None],
        on_dislike_reroll: Callable[[], None],
        on_toggle_zen: Callable[[], None],
    ) -> None:
        super().__init__(None)
        self._expanded = False
        self._user_visible = True
        self._fullscreen_hidden = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.container = QWidget(self)
        self.container.setObjectName("pill")
        self.setStyleSheet(
            """
            #pill {
                background-color: rgba(24, 28, 36, 185);
                border: 1px solid rgba(255, 255, 255, 42);
                border-radius: 17px;
            }
            QPushButton {
                background-color: transparent;
                color: #F0F4F8;
                border: none;
                border-radius: 11px;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 36);
            }
            QPushButton:disabled {
                color: rgba(240, 244, 248, 95);
            }
            QLabel {
                color: #B8C4D0;
                font-size: 11px;
                padding: 0 5px;
            }
            """
        )

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.container)

        layout = QHBoxLayout(self.container)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)

        self.theme_lbl = QLabel("")
        self.theme_lbl.setVisible(False)

        self.pause_btn = QPushButton("⏸ Tắt")
        self.pause_btn.setToolTip("Tạm dừng / Bật lại overlay")
        self.pause_btn.clicked.connect(on_toggle_pause)

        self.reroll_btn = QPushButton("🔀 Đổi cảnh")
        self.reroll_btn.setToolTip("Chuyển sang cảnh tiếp theo trong bộ cảnh hôm nay")
        self.reroll_btn.clicked.connect(on_reroll)

        self.pin_btn = QPushButton("📌 Ghim app")
        self.pin_btn.setToolTip("Ghim / Bỏ ghim style riêng cho ứng dụng đang mở")
        self.pin_btn.clicked.connect(on_toggle_pin)

        self.dislike_btn = QPushButton("🗑 Bỏ cảnh")
        self.dislike_btn.setToolTip("Không thích cảnh này: chặn & đổi cảnh khác ngay")
        self.dislike_btn.clicked.connect(on_dislike_reroll)

        self.zen_btn = QPushButton("🧘 Zen")
        self.zen_btn.setToolTip("Ẩn 30 phút để tập trung")
        self.zen_btn.clicked.connect(on_toggle_zen)

        self.toggle_btn = QPushButton("✦")
        self.toggle_btn.setToolTip("DailyOverlay — Điều khiển nhanh")
        self.toggle_btn.clicked.connect(self.toggle_expanded)

        for w in (
            self.theme_lbl,
            self.pause_btn,
            self.reroll_btn,
            self.pin_btn,
            self.dislike_btn,
            self.zen_btn,
        ):
            w.setVisible(False)
            layout.addWidget(w)
        layout.addWidget(self.toggle_btn)

        self._reposition()

    @property
    def user_visible(self) -> bool:
        return self._user_visible

    def set_user_visible(self, visible: bool) -> None:
        self._user_visible = visible
        self._apply_visibility()

    def set_fullscreen_hidden(self, hidden: bool) -> None:
        self._fullscreen_hidden = hidden
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        should_show = self._user_visible and not self._fullscreen_hidden
        if should_show:
            self.show()
            self._reposition()
        else:
            self.hide()

    def set_status_label(self, text: str) -> None:
        short = text if len(text) <= 28 else text[:27] + "…"
        self.theme_lbl.setText(short)
        self._reposition()

    def set_pin_state(self, is_pinned: bool, can_pin: bool = True) -> None:
        self.pin_btn.setEnabled(can_pin)
        self.pin_btn.setText("🔓 Bỏ ghim" if is_pinned else "📌 Ghim app")
        self._reposition()

    def set_paused(self, paused: bool) -> None:
        self.pause_btn.setText("▶ Bật" if paused else "⏸ Tắt")

    def set_busy(self, busy: bool) -> None:
        self.reroll_btn.setEnabled(not busy)
        self.dislike_btn.setEnabled(not busy)
        self.reroll_btn.setText("⏳ Đang tạo..." if busy else "🔀 Đổi cảnh")
        self._reposition()

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        for w in (
            self.theme_lbl,
            self.pause_btn,
            self.reroll_btn,
            self.pin_btn,
            self.dislike_btn,
            self.zen_btn,
        ):
            w.setVisible(self._expanded)
        self.toggle_btn.setText("✕" if self._expanded else "✦")
        self._reposition()

    def _reposition(self) -> None:
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        x = avail.right() - self.width() - 14
        y = avail.bottom() - self.height() - 12
        self.move(x, y)
