from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from typing import Callable

from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
)

from daily_overlay.ai.director import (
    append_scenario_to_deck,
    cycle_next_scenario,
    dislike_active_scenario,
    generate_pinned_style_for_profile,
    load_or_create_daily_deck,
    save_daily_deck,
)
from daily_overlay.ai.schema import DailyDeck, DailyScenario, PinnedAppStyle
from daily_overlay.assets.manager import (
    bulk_download_public_pack,
    list_available_catalog_count,
    list_local_assets,
)
from daily_overlay.autostart import (
    get_autostart_command,
    get_launch_command,
    is_autostart_enabled,
    set_autostart,
)
from daily_overlay.config import (
    ASSETS_DIR,
    COMMON_BROWSERS,
    DAILY_DECK_SIZE,
    EXTENSION_DIR,
    LOG_PATH,
    SCENARIOS_DIR,
    AppProfile,
    ZoneType,
    current_cycle_slot,
    cycle_key,
    ensure_data_dirs,
    load_app_profiles,
    match_profile_for_context,
    save_app_profiles,
    slugify_profile_id,
    upsert_app_profile,
)
from daily_overlay.context.browser_bridge import BrowserBridgeServer
from daily_overlay.context.layout_scanner import (
    scan_win32_window_wireframe,
    summarize_wireframe_for_prompt,
)
from daily_overlay.overlay.engine import OverlayEngine
from daily_overlay.ui.quick_bar import QuickControlDock
from daily_overlay.ui.zone_editor import ZoneEditorOverlay

logger = logging.getLogger(__name__)

ZONE_OPTIONS: list[ZoneType] = [
    "left_right_margins",
    "bottom_edge",
    "right_edge",
    "top_edge",
    "corners",
]


def _make_tray_icon(primary_hex: str = "#6FB1D6", accent_hex: str = "#E08E67") -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QColor(0, 0, 0, 0))
    p.setBrush(QColor(primary_hex))
    p.drawEllipse(6, 6, 52, 52)
    p.setBrush(QColor(accent_hex))
    p.drawEllipse(22, 18, 24, 24)
    p.end()
    return QIcon(pix)


class _GenerateScenarioWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, deck: DailyDeck) -> None:
        super().__init__()
        self._deck = deck

    def run(self) -> None:
        try:
            profiles = load_app_profiles()
            sc = append_scenario_to_deck(self._deck, profiles=profiles)
            self.finished.emit(sc)
        except Exception as exc:
            self.failed.emit(str(exc))


class _PinStyleWorker(QObject):
    finished = Signal(str, object)
    failed = Signal(str)

    def __init__(self, profile: AppProfile) -> None:
        super().__init__()
        self._profile = profile

    def run(self) -> None:
        try:
            style = generate_pinned_style_for_profile(self._profile)
            self.finished.emit(self._profile.id, style)
        except Exception as exc:
            self.failed.emit(str(exc))


class _CycleRolloverWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, profiles: list[AppProfile]) -> None:
        super().__init__()
        self._profiles = [p.model_copy(deep=True) for p in profiles]

    def run(self) -> None:
        try:
            deck = load_or_create_daily_deck(force_new=False, profiles=self._profiles)
            self.finished.emit(deck)
        except Exception as exc:
            self.failed.emit(str(exc))


class AppProfilesDialog(QDialog):
    """Unified setup dialog for 2-mode App Profiles, Zero-PII Layout Scanner, and Interactive Zone Painter."""

    def __init__(
        self,
        parent=None,
        select_profile_id: str = "",
        scan_fn: Callable[[], dict | None] | None = None,
        draw_zones_fn: Callable[[AppProfile], tuple[list[list[float]], list[list[float]]] | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("DailyOverlay — Quản lý Ứng dụng & Trang web")
        self.resize(710, 470)
        self.profiles = load_app_profiles()
        self._scan_fn = scan_fn
        self._draw_zones_fn = draw_zones_fn
        self.ai_pin_requested_for: str | None = None

        layout = QHBoxLayout(self)

        # Left list
        left_col = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_select)
        left_col.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Thêm mới")
        del_btn = QPushButton("Xóa")
        add_btn.clicked.connect(self._on_add)
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        left_col.addLayout(btn_row)
        layout.addLayout(left_col, 2)

        # Right form
        right_col = QVBoxLayout()
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.proc_edit = QLineEdit()
        self.proc_edit.setPlaceholderText("vd: chrome.exe, code.exe")
        self.hosts_edit = QLineEdit()
        self.hosts_edit.setPlaceholderText("vd: youtube.com, github.com (nếu là web)")
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("vd: Google Docs (trống = toàn bộ app)")
        self.zone_combo = QComboBox()
        self.zone_combo.addItems(ZONE_OPTIONS)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("🔄 Theo chủ đề mỗi ngày (mặc định)", "follow_daily")
        self.mode_combo.addItem("📌 Ghim style riêng cố định", "pinned")

        self.vibe_edit = QLineEdit()
        self.vibe_edit.setPlaceholderText("Gợi ý phong cách hoặc vùng trống cho AI")

        self.wireframe_lbl = QLabel("Chưa quét bố cục")
        self.wireframe_lbl.setWordWrap(True)
        self.custom_zones_lbl = QLabel("Chưa khoanh vùng thủ công")
        self.custom_zones_lbl.setWordWrap(True)
        self.pinned_lbl = QLabel("Chưa ghim style riêng")
        self.pinned_lbl.setWordWrap(True)

        form.addRow("Tên hiển thị:", self.name_edit)
        form.addRow("Ứng dụng (.exe):", self.proc_edit)
        form.addRow("Tên miền Web:", self.hosts_edit)
        form.addRow("Từ khóa tiêu đề:", self.title_edit)
        form.addRow("Vùng ưu tiên:", self.zone_combo)
        form.addRow("Chế độ:", self.mode_combo)
        form.addRow("Ghi chú cho AI:", self.vibe_edit)
        form.addRow("Bố cục ẩn danh:", self.wireframe_lbl)
        form.addRow("Vùng vẽ tay:", self.custom_zones_lbl)
        form.addRow("Style đang ghim:", self.pinned_lbl)
        right_col.addLayout(form)

        action_row = QHBoxLayout()
        if self._scan_fn is not None:
            scan_btn = QPushButton("🔍 Quét bố cục app đang mở")
            scan_btn.clicked.connect(self._on_scan_current)
            action_row.addWidget(scan_btn)

        if self._draw_zones_fn is not None:
            draw_btn = QPushButton("🎨 Vẽ vùng")
            draw_btn.setToolTip("Khoanh trực tiếp vùng Cho phép (xanh) & Vùng cấm (đỏ) trên app")
            draw_btn.clicked.connect(self._on_draw_zones)
            action_row.addWidget(draw_btn)

        self.ai_pin_btn = QPushButton("✨ Tạo style riêng bằng AI")
        self.ai_pin_btn.clicked.connect(self._on_request_ai_pin)
        action_row.addWidget(self.ai_pin_btn)

        self.unpin_btn = QPushButton("🔓 Bỏ ghim")
        self.unpin_btn.clicked.connect(self._on_unpin)
        action_row.addWidget(self.unpin_btn)
        right_col.addLayout(action_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save_all)
        buttons.rejected.connect(self.reject)
        right_col.addWidget(buttons)
        layout.addLayout(right_col, 3)

        start_idx = 0
        if select_profile_id:
            for i, p in enumerate(self.profiles):
                if p.id == select_profile_id:
                    start_idx = i
                    break
        self._refresh_list(start_idx)

    def _refresh_list(self, select_idx: int = 0) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for p in self.profiles:
            badge = "📌" if p.mode == "pinned" else "🔄"
            if p.custom_allowed_zones or p.custom_forbidden_zones:
                extra = "🎨"
            elif p.layout_wireframe and p.layout_wireframe.get("blocks"):
                extra = "✓"
            else:
                extra = ""
            self.list_widget.addItem(f"{badge} {p.name} {extra}".strip())
        self.list_widget.blockSignals(False)
        if self.profiles:
            idx = max(0, min(select_idx, len(self.profiles) - 1))
            self.list_widget.setCurrentRow(idx)
            self._on_select(idx)

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self.profiles):
            return
        p = self.profiles[row]
        self.name_edit.setText(p.name)
        self.proc_edit.setText(", ".join(p.match_process))
        self.hosts_edit.setText(", ".join(p.match_hosts))
        self.title_edit.setText(", ".join(p.match_title_contains))
        self.zone_combo.setCurrentText(p.preferred_zone)
        self.mode_combo.setCurrentIndex(1 if p.mode == "pinned" else 0)
        self.vibe_edit.setText(p.preferred_vibe)

        wf = p.layout_wireframe
        if wf and wf.get("blocks"):
            self.wireframe_lbl.setText(summarize_wireframe_for_prompt(wf))
        elif wf is not None:
            self.wireframe_lbl.setText(
                "Không đọc được bố cục con — dùng nút '🎨 Vẽ vùng' để khoanh trực tiếp"
            )
        else:
            self.wireframe_lbl.setText(
                "Chưa quét (bấm Quét hoặc click icon Extension trên trình duyệt)"
            )

        if p.custom_allowed_zones or p.custom_forbidden_zones:
            self.custom_zones_lbl.setText(
                f"🟢 {len(p.custom_allowed_zones)} vùng cho phép · 🔴 {len(p.custom_forbidden_zones)} vùng cấm"
            )
        else:
            self.custom_zones_lbl.setText("Chưa khoanh vùng thủ công (dùng tự động)")

        if p.mode == "pinned" and p.pinned_theme_name:
            since = f" (từ {p.pinned_since})" if p.pinned_since else ""
            self.pinned_lbl.setText(f"📌 {p.pinned_theme_name}{since}")
            self.unpin_btn.setEnabled(True)
        else:
            self.pinned_lbl.setText("Đang chạy theo chủ đề ngày")
            self.unpin_btn.setEnabled(False)

    def _apply_current_fields(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.profiles):
            return
        old = self.profiles[row]
        name = self.name_edit.text().strip() or "Custom App"
        chosen_mode = self.mode_combo.currentData() or "follow_daily"
        self.profiles[row] = AppProfile(
            id=old.id or slugify_profile_id(name),
            name=name,
            match_process=[s.strip() for s in self.proc_edit.text().split(",") if s.strip()],
            match_hosts=[s.strip().lower() for s in self.hosts_edit.text().split(",") if s.strip()],
            match_title_contains=[s.strip() for s in self.title_edit.text().split(",") if s.strip()],
            preferred_zone=self.zone_combo.currentText(),  # type: ignore[arg-type]
            preferred_vibe=self.vibe_edit.text().strip() or "calm and unobtrusive",
            mode=chosen_mode,  # type: ignore[arg-type]
            layout_wireframe=old.layout_wireframe,
            custom_allowed_zones=list(old.custom_allowed_zones),
            custom_forbidden_zones=list(old.custom_forbidden_zones),
            pinned_theme_name=old.pinned_theme_name if chosen_mode == "pinned" else "",
            pinned_since=old.pinned_since if chosen_mode == "pinned" else "",
            pinned_components=old.pinned_components if chosen_mode == "pinned" else [],
            enabled=old.enabled,
        )
        self._refresh_list(row)

    def _on_scan_current(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.profiles) or self._scan_fn is None:
            return
        wf = self._scan_fn()
        if wf is None:
            QMessageBox.information(
                self,
                "Không tìm thấy cửa sổ",
                "Hãy mở ứng dụng hoặc trang web bạn muốn quét trước rồi bấm Quét lại.",
            )
            return

        self.profiles[row].layout_wireframe = wf
        self._refresh_list(row)
        if not wf.get("blocks") and self._draw_zones_fn is not None:
            reply = QMessageBox.question(
                self,
                "Không đọc được bố cục con",
                "Ứng dụng này không lộ cấu trúc UI cho Windows UIA (thường gặp ở app Electron/GPU).\n"
                "Bạn có muốn mở khung 🎨 Vẽ vùng để tự khoanh vùng Cho phép / Vùng cấm ngay không?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._on_draw_zones()

    def _on_draw_zones(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.profiles) or self._draw_zones_fn is None:
            return
        self._apply_current_fields()
        self.hide()
        try:
            res = self._draw_zones_fn(self.profiles[row])
        finally:
            self.show()
            self.raise_()
            self.activateWindow()
        if res is not None:
            allowed, forbidden = res
            self.profiles[row].custom_allowed_zones = allowed
            self.profiles[row].custom_forbidden_zones = forbidden
            self._refresh_list(row)

    def _on_unpin(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.profiles):
            return
        self.profiles[row].mode = "follow_daily"
        self.profiles[row].pinned_theme_name = ""
        self.profiles[row].pinned_since = ""
        self.profiles[row].pinned_components = []
        self._refresh_list(row)

    def _on_request_ai_pin(self) -> None:
        self._apply_current_fields()
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.profiles):
            self.ai_pin_requested_for = self.profiles[row].id
            save_app_profiles(self.profiles)
            self.accept()

    def _on_add(self) -> None:
        self.profiles.append(
            AppProfile(
                id=f"app_{len(self.profiles) + 1}",
                name="Ứng dụng mới",
                match_process=["chrome.exe"],
                match_hosts=[],
                match_title_contains=[],
                preferred_zone="bottom_edge",
                preferred_vibe="calm and unobtrusive",
            )
        )
        self._refresh_list(len(self.profiles) - 1)

    def _on_delete(self) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.profiles):
            self.profiles.pop(row)
            self._refresh_list(max(0, row - 1))

    def _on_save_all(self) -> None:
        self._apply_current_fields()
        save_app_profiles(self.profiles)
        self.accept()


class TrayController(QObject):
    """Organized, non-redundant Vietnamese System Tray menu + QuickControlDock + Deck Refiller."""

    _browser_scan_signal = Signal(str, object)

    def __init__(
        self,
        engine: OverlayEngine,
        browser_bridge: BrowserBridgeServer | None,
        on_quit: Callable[[], None],
    ) -> None:
        super().__init__()
        self.engine = engine
        self.browser_bridge = browser_bridge
        self._on_quit = on_quit
        self._worker_thread: QThread | None = None
        self._worker: QObject | None = None
        self._refill_thread: QThread | None = None
        self._refill_worker: QObject | None = None
        self._rollover_thread: QThread | None = None
        self._rollover_worker: QObject | None = None
        self._refill_cycle_key: str = ""
        self._pack_thread: QThread | None = None
        self._pack_worker: QObject | None = None
        self._zone_editor: ZoneEditorOverlay | None = None

        self._browser_scan_signal.connect(self._handle_browser_layout_scan)
        if self.browser_bridge is not None:
            self.browser_bridge.set_on_layout_scan(
                lambda host, wf: self._browser_scan_signal.emit(host, wf)
            )

        self.tray = QSystemTrayIcon(
            _make_tray_icon(
                engine.scenario.palette.primary,
                engine.scenario.palette.accent,
            )
        )
        self.tray.setToolTip(f"DailyOverlay — {engine.scenario.theme_name}")

        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._refresh_live_status)

        # === Header (Status) ===
        self.theme_action = QAction(self._theme_label(), self.menu)
        self.theme_action.setEnabled(False)
        self.menu.addAction(self.theme_action)

        self.status_action = QAction("📍 Đang xem: Desktop · Theo ngày", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        # === Quick State Controls ===
        self.pause_action = QAction("⏸ Tạm dừng overlay", self.menu)
        self.pause_action.triggered.connect(self._toggle_pause)
        self.menu.addAction(self.pause_action)

        self.zen_action = QAction("🧘 Chế độ Tập trung (Ẩn 30 phút)", self.menu)
        self.zen_action.triggered.connect(self._toggle_zen)
        self.menu.addAction(self.zen_action)
        self.menu.addSeparator()

        # === Submenu 1: Cảnh hôm nay ===
        scene_menu = self.menu.addMenu("🎨 Cảnh hôm nay")
        self.next_scene_action = QAction("🔀 Chuyển cảnh tiếp theo (tức thì)", scene_menu)
        self.next_scene_action.triggered.connect(self.switch_next_scenario)
        scene_menu.addAction(self.next_scene_action)

        self.regen_action = QAction("✨ Tạo thêm cảnh mới bằng AI", scene_menu)
        self.regen_action.triggered.connect(self.generate_fresh_scenario)
        scene_menu.addAction(self.regen_action)

        self.dislike_action = QAction("🗑 Không thích cảnh này (chặn & đổi ngay)", scene_menu)
        self.dislike_action.triggered.connect(self.dislike_and_switch)
        scene_menu.addAction(self.dislike_action)

        # === Submenu 2: Ứng dụng đang mở ===
        app_menu = self.menu.addMenu("📌 Ứng dụng đang mở")
        self.scan_setup_action = QAction("🔍 Quét bố cục & Thiết lập cho app này...", app_menu)
        self.scan_setup_action.triggered.connect(self._scan_and_setup_current_app)
        app_menu.addAction(self.scan_setup_action)

        self.draw_zones_action = QAction("🎨 Vẽ vùng cho phép / vùng cấm cho app này...", app_menu)
        self.draw_zones_action.triggered.connect(self._open_zone_editor_for_current_app)
        app_menu.addAction(self.draw_zones_action)

        self.pin_toggle_action = QAction("📌 Ghim cảnh hiện tại cho app này", app_menu)
        self.pin_toggle_action.triggered.connect(self.toggle_pin_current_app)
        app_menu.addAction(self.pin_toggle_action)

        self.ai_pin_action = QAction("✨ Tạo style riêng bằng AI cho app này", app_menu)
        self.ai_pin_action.triggered.connect(self._ai_pin_current_app)
        app_menu.addAction(self.ai_pin_action)

        app_menu.addSeparator()
        manage_apps_action = QAction("📋 Quản lý tất cả App đã thiết lập...", app_menu)
        manage_apps_action.triggered.connect(lambda: self._open_profiles_dialog())
        app_menu.addAction(manage_apps_action)

        # === Submenu 3: Cài đặt ===
        settings_menu = self.menu.addMenu("⚙ Cài đặt")

        opacity_menu = settings_menu.addMenu("💡 Độ rõ hiển thị")
        opacity_group = QActionGroup(self)
        opacity_group.setExclusive(True)
        for label, mult in [
            ("Dịu nhẹ (65%)", 0.65),
            ("Vừa mắt (100% - Mặc định)", 1.0),
            ("Rõ nét (125%)", 1.25),
        ]:
            act = QAction(label, opacity_menu, checkable=True)
            if abs(mult - self.engine.user_opacity_mult) < 0.05:
                act.setChecked(True)
            act.triggered.connect(lambda _checked, m=mult: self._set_opacity(m))
            opacity_group.addAction(act)
            opacity_menu.addAction(act)

        self.quickbar_action = QAction("🎛 Hiện nút nổi góc màn hình (✦)", settings_menu, checkable=True)
        self.quickbar_action.setChecked(True)
        self.quickbar_action.triggered.connect(self._toggle_quick_bar)
        settings_menu.addAction(self.quickbar_action)

        if getattr(sys, "frozen", False) and is_autostart_enabled():
            if get_autostart_command() != get_launch_command():
                set_autostart(True)

        self.autostart_action = QAction("🚀 Khởi động cùng Windows", settings_menu, checkable=True)
        self.autostart_action.setChecked(is_autostart_enabled())
        self.autostart_action.triggered.connect(self._toggle_autostart)
        settings_menu.addAction(self.autostart_action)

        self.download_pack_action = QAction(self._pack_label(), settings_menu)
        self.download_pack_action.triggered.connect(self._download_public_pack)
        settings_menu.addAction(self.download_pack_action)

        folders_menu = settings_menu.addMenu("📁 Mở thư mục dữ liệu")
        open_assets = QAction("Kho hình động (Assets)", folders_menu)
        open_assets.triggered.connect(self._open_assets_dir)
        folders_menu.addAction(open_assets)

        open_ext = QAction("Tiện ích trình duyệt (Extension)", folders_menu)
        open_ext.triggered.connect(self._open_extension_dir)
        folders_menu.addAction(open_ext)

        open_scen = QAction("Kịch bản theo ngày (Scenarios)", folders_menu)
        open_scen.triggered.connect(self._open_scenarios_dir)
        folders_menu.addAction(open_scen)

        open_log = QAction("Nhật ký chạy (Log)", folders_menu)
        open_log.triggered.connect(self._open_log_file)
        folders_menu.addAction(open_log)

        self.menu.addSeparator()
        quit_action = QAction("✕ Thoát DailyOverlay", self.menu)
        quit_action.triggered.connect(self._quit)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        # Floating quick control pill
        self.quick_bar = QuickControlDock(
            on_toggle_pause=self._toggle_pause,
            on_reroll=self.switch_next_scenario,
            on_toggle_pin=self.toggle_pin_current_app,
            on_dislike_reroll=self.dislike_and_switch,
            on_toggle_zen=self._toggle_zen,
        )
        self._sync_ui_labels()
        self.quick_bar.show()

        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(1000)
        self._sync_timer.timeout.connect(self._sync_quick_bar_context)
        self._sync_timer.start()

        QTimer.singleShot(15000, self._schedule_deck_refill)

        period_tag = "Ca sáng (06:00–18:00)" if engine.deck.period == "day" else "Ca tối (18:00–06:00)"
        self.tray.showMessage(
            f"DailyOverlay — {engine.scenario.theme_name}",
            f"{period_tag} · {engine.scenario.tagline}",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def _pack_label(self) -> str:
        local_n = len(list_local_assets())
        total_n = max(local_n, list_available_catalog_count())
        return f"📦 Tải kho hình động offline ({local_n}/{total_n})"

    def _theme_label(self) -> str:
        deck = self.engine.deck
        total = max(1, len(deck.scenarios))
        idx = (deck.active_index % total) + 1
        period_badge = "🌅 Sáng" if deck.period == "day" else "🌙 Tối"
        return f"{period_badge}: {self.engine.scenario.theme_name} (Cảnh {idx}/{total})"

    def _target_app_label_and_profile(self) -> tuple[str, AppProfile | None]:
        ctx = self.engine.last_real_app_ctx
        bctx = self.engine.last_real_browser_ctx
        if ctx is None:
            return "Chưa xác định", None

        prof = match_profile_for_context(
            self.engine.profiles,
            ctx.process_name,
            ctx.title,
            bctx.host if bctx else "",
        )
        if prof is not None:
            return prof.name, prof
        if bctx and bctx.host:
            return bctx.host, None
        return ctx.process_name, None

    def _sync_quick_bar_context(self) -> None:
        label, prof = self._target_app_label_and_profile()
        is_pinned = bool(prof and prof.mode == "pinned" and prof.pinned_components)
        can_pin = self.engine.last_real_app_ctx is not None
        self.quick_bar.set_pin_state(is_pinned=is_pinned, can_pin=can_pin)
        if is_pinned and prof:
            self.quick_bar.set_status_label(f"📌 {prof.name}: {prof.pinned_theme_name}")
        else:
            deck = self.engine.deck
            total = max(1, len(deck.scenarios))
            idx = (deck.active_index % total) + 1
            period_icon = "🌅" if deck.period == "day" else "🌙"
            self.quick_bar.set_status_label(
                f"{period_icon} {self.engine.scenario.theme_name} ({idx}/{total})"
            )

    def _sync_ui_labels(self) -> None:
        self.theme_action.setText(self._theme_label())
        self.tray.setToolTip(f"DailyOverlay — {self.engine.scenario.theme_name}")
        self.tray.setIcon(
            _make_tray_icon(
                self.engine.scenario.palette.primary,
                self.engine.scenario.palette.accent,
            )
        )
        self._sync_quick_bar_context()

    def _refresh_live_status(self) -> None:
        self.theme_action.setText(self._theme_label())
        label, prof = self._target_app_label_and_profile()

        mode_txt = "Theo ngày"
        if prof and prof.mode == "pinned":
            days_note = ""
            if prof.pinned_since:
                try:
                    days = (current_cycle_slot()[0] - dt.date.fromisoformat(prof.pinned_since)).days
                    if days >= 4:
                        days_note = f" ({days} ngày)"
                except Exception:
                    pass
            mode_txt = f"📌 Đã ghim{days_note}"

        ctx_state_map = {
            "music": " · 🎵 Nhạc",
            "deep_focus": " · 🧘 Tập trung",
            "idle_break": " · ☕ Nghỉ tay",
            "normal": "",
        }
        state_badge = ctx_state_map.get(self.engine.active_context_state, "")
        empty_badge = ""
        if self.engine.active_empty_status == "realtime_ok":
            empty_badge = f" · 🟢 {self.engine.active_empty_count} vùng trống"
        elif self.engine.active_empty_status == "realtime_packed":
            empty_badge = " · 🙈 Kín chữ (tự ẩn)"
        self.status_action.setText(f"📍 Đang xem: {label} · {mode_txt}{state_badge}{empty_badge}")

        has_target = self.engine.last_real_app_ctx is not None
        self.scan_setup_action.setEnabled(has_target)
        self.draw_zones_action.setEnabled(has_target)
        self.pin_toggle_action.setEnabled(has_target)
        self.ai_pin_action.setEnabled(has_target)
        if has_target:
            self.scan_setup_action.setText(f'🔍 Quét bố cục & Thiết lập cho "{label}"...')
            self.draw_zones_action.setText(f'🎨 Vẽ vùng cho phép / vùng cấm cho "{label}"...')
            if prof and prof.mode == "pinned":
                self.pin_toggle_action.setText(f'🔓 Bỏ ghim "{label}" (quay về theo ngày)')
            else:
                self.pin_toggle_action.setText(f'📌 Ghim cảnh hiện tại cho "{label}"')
            self.ai_pin_action.setText(f'✨ Tạo style riêng bằng AI cho "{label}"')

        self.download_pack_action.setText(self._pack_label())
        self.autostart_action.setChecked(is_autostart_enabled())

    def set_fullscreen_hidden(self, hidden: bool) -> None:
        self.quick_bar.set_fullscreen_hidden(hidden)

    def handle_new_day(self) -> None:
        """Async auto-rollover to fresh deck at 06:00 (day) and 18:00 (night)."""
        slot_day, slot_period = current_cycle_slot()
        target_key = cycle_key(slot_day, slot_period)
        if target_key == self.engine.deck.cycle_key:
            return
        if self._rollover_thread is not None and self._rollover_thread.isRunning():
            return
        if self._worker_thread is not None and self._worker_thread.isRunning():
            return

        thread = QThread()
        worker = _CycleRolloverWorker(self.engine.profiles)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_rollover_ready)
        worker.failed.connect(self._on_rollover_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)

        self._rollover_thread = thread
        self._rollover_worker = worker
        thread.start()

    def _on_rollover_ready(self, deck: DailyDeck) -> None:
        self.engine.set_deck(deck)
        self._sync_ui_labels()
        if deck.period == "day":
            title = f"🌅 Ca sáng mới (06:00) — {deck.active_scenario.theme_name}"
        else:
            title = f"🌙 Ca tối mới (18:00) — {deck.active_scenario.theme_name}"
        self.tray.showMessage(
            title,
            deck.active_scenario.tagline,
            QSystemTrayIcon.MessageIcon.Information,
            4500,
        )
        self._schedule_deck_refill()

    def _on_rollover_failed(self, err: str) -> None:
        logger.warning("Auto-rollover cycle failed: %s", err)
        self.tray.showMessage(
            "Không chuyển ca tự động được",
            err[:180],
            QSystemTrayIcon.MessageIcon.Warning,
            4000,
        )

    def _set_opacity(self, mult: float) -> None:
        self.engine.user_opacity_mult = mult

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if not self.quick_bar.user_visible:
                self.quick_bar.set_user_visible(True)
                self.quickbar_action.setChecked(True)
            self.quick_bar.toggle_expanded()

    def _toggle_quick_bar(self, checked: bool) -> None:
        self.quick_bar.set_user_visible(checked)

    def _toggle_autostart(self, checked: bool) -> None:
        ok, info = set_autostart(checked)
        if not ok:
            self.autostart_action.setChecked(not checked)
            self.tray.showMessage(
                "Không thể đổi cài đặt khởi động",
                info[:180],
                QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )
            return
        if checked:
            self.tray.showMessage(
                "🚀 Đã bật khởi động cùng Windows",
                "DailyOverlay sẽ tự chạy ngầm (không hiện cửa sổ terminal) khi bạn đăng nhập.",
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )
        else:
            self.tray.showMessage(
                "Đã tắt khởi động cùng Windows",
                "Bạn vẫn có thể mở lại DailyOverlay bất cứ lúc nào.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _toggle_pause(self) -> None:
        self.engine.paused = not self.engine.paused
        self.pause_action.setText("▶ Bật lại overlay" if self.engine.paused else "⏸ Tạm dừng overlay")
        self.quick_bar.set_paused(self.engine.paused)

    def _toggle_zen(self) -> None:
        if self.engine.is_zen_active:
            self.engine.set_zen_minutes(0)
            self.zen_action.setText("🧘 Chế độ Tập trung (Ẩn 30 phút)")
        else:
            self.engine.set_zen_minutes(30)
            self.zen_action.setText("Tắt chế độ Tập trung")

    # === Daily Deck Instant Switching & Background Refill ===

    def switch_next_scenario(self) -> None:
        if len(self.engine.deck.scenarios) > 1:
            _sc, needs_refill = cycle_next_scenario(self.engine.deck)
            self.engine.set_deck(self.engine.deck)
            self._sync_ui_labels()
            if needs_refill:
                self._schedule_deck_refill()
        else:
            self.generate_fresh_scenario()

    def dislike_and_switch(self) -> None:
        next_sc, deck = dislike_active_scenario(self.engine.deck)
        if next_sc is not None:
            self.engine.set_deck(deck)
            self._sync_ui_labels()
            self._schedule_deck_refill()
        else:
            self.generate_fresh_scenario()

    def generate_fresh_scenario(self) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            return
        self._set_ai_busy(True)

        thread = QThread()
        worker = _GenerateScenarioWorker(deck=self.engine.deck)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_fresh_scenario_ready)
        worker.failed.connect(self._on_ai_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)

        self._worker_thread = thread
        self._worker = worker
        thread.start()

    def _on_fresh_scenario_ready(self, scenario: DailyScenario) -> None:
        self.engine.deck.active_index = max(0, len(self.engine.deck.scenarios) - 1)
        save_daily_deck(self.engine.deck)
        self.engine.set_deck(self.engine.deck)
        self._set_ai_busy(False)
        self._sync_ui_labels()
        self.tray.showMessage(
            f"Cảnh mới: {scenario.theme_name}",
            scenario.tagline,
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )
        if len(self.engine.deck.scenarios) < DAILY_DECK_SIZE:
            self._schedule_deck_refill()

    def _on_ai_failed(self, err: str) -> None:
        self._set_ai_busy(False)
        self.tray.showMessage(
            "Không tạo được cảnh mới",
            err[:180],
            QSystemTrayIcon.MessageIcon.Warning,
            4500,
        )

    def _set_ai_busy(self, busy: bool) -> None:
        self.regen_action.setEnabled(not busy)
        self.dislike_action.setEnabled(not busy)
        self.regen_action.setText(
            "⏳ Đang nhờ Gemini sáng tác..." if busy else "✨ Tạo thêm cảnh mới bằng AI"
        )
        self.quick_bar.set_busy(busy)

    def _schedule_deck_refill(self) -> None:
        if len(self.engine.deck.scenarios) >= DAILY_DECK_SIZE:
            return
        if self._rollover_thread is not None and self._rollover_thread.isRunning():
            return
        if self._refill_thread is not None and self._refill_thread.isRunning():
            return
        if self._worker_thread is not None and self._worker_thread.isRunning():
            return

        self._refill_cycle_key = self.engine.deck.cycle_key
        thread = QThread()
        worker = _GenerateScenarioWorker(deck=self.engine.deck)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_refill_done)
        worker.failed.connect(lambda _err: None)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)

        self._refill_thread = thread
        self._refill_worker = worker
        thread.start()

    def _on_refill_done(self, _sc: DailyScenario) -> None:
        if self._refill_cycle_key != self.engine.deck.cycle_key:
            return
        self._sync_ui_labels()
        if len(self.engine.deck.scenarios) < DAILY_DECK_SIZE:
            QTimer.singleShot(4000, self._schedule_deck_refill)

    # === 2-Mode App Profile Actions, Zero-PII Layout Scanner & Interactive Zone Painter ===

    def _scan_real_window_wireframe(self) -> dict | None:
        ctx = self.engine.last_real_app_ctx
        bctx = self.engine.last_real_browser_ctx
        if (
            ctx is not None
            and ctx.process_name.lower() in COMMON_BROWSERS
            and bctx is not None
            and bctx.wireframe is not None
        ):
            return bctx.wireframe
        if ctx is not None and ctx.hwnd:
            screen = QGuiApplication.primaryScreen()
            dpr = screen.devicePixelRatio() if screen else 1.0
            return scan_win32_window_wireframe(ctx.hwnd, dpr=dpr)
        return None

    def _target_window_rect(self) -> tuple[int, int, int, int]:
        ctx = self.engine.last_real_app_ctx
        if ctx is not None and (ctx.rect[2] - ctx.rect[0]) >= 120 and (ctx.rect[3] - ctx.rect[1]) >= 120:
            return ctx.rect
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        if avail:
            return (avail.left(), avail.top(), avail.right(), avail.bottom())
        return (0, 0, 1920, 1040)

    def _run_zone_editor_modal(
        self,
        prof: AppProfile,
    ) -> tuple[list[list[float]], list[list[float]]] | None:
        saved_result: list[tuple[list[list[float]], list[list[float]]]] = []
        loop = QEventLoop(self)
        editor = ZoneEditorOverlay(
            app_name=prof.name,
            target_rect=self._target_window_rect(),
            wireframe=prof.layout_wireframe,
            allowed_zones=prof.custom_allowed_zones,
            forbidden_zones=prof.custom_forbidden_zones,
        )
        self._zone_editor = editor
        editor.zones_saved.connect(lambda a, f: saved_result.append((a, f)))
        editor.editor_closed.connect(loop.quit)
        editor.show()
        editor.activateWindow()
        loop.exec()
        self._zone_editor = None
        return saved_result[0] if saved_result else None

    def _open_zone_editor_for_current_app(self) -> None:
        prof = self._ensure_profile_for_current_app(with_scan=True)
        if prof is None:
            return
        res = self._run_zone_editor_modal(prof)
        if res is not None:
            allowed, forbidden = res
            prof.custom_allowed_zones = allowed
            prof.custom_forbidden_zones = forbidden
            self.engine.reload_profiles(upsert_app_profile(prof))
            self._sync_ui_labels()
            self.tray.showMessage(
                f"✓ Đã lưu vùng cho {prof.name}",
                f"🟢 {len(allowed)} vùng cho phép · 🔴 {len(forbidden)} vùng cấm",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )

    def _ensure_profile_for_current_app(self, with_scan: bool = True) -> AppProfile | None:
        ctx = self.engine.last_real_app_ctx
        bctx = self.engine.last_real_browser_ctx
        if ctx is None:
            return None

        prof = match_profile_for_context(
            self.engine.profiles,
            ctx.process_name,
            ctx.title,
            bctx.host if bctx else "",
        )
        wf = self._scan_real_window_wireframe() if with_scan else None

        if prof is not None:
            if wf:
                prof.layout_wireframe = wf
                upsert_app_profile(prof)
                self.engine.reload_profiles()
            return prof

        is_browser = ctx.process_name.lower() in COMMON_BROWSERS
        if is_browser and bctx and bctx.host:
            name = bctx.host
            new_prof = AppProfile(
                id=slugify_profile_id(name),
                name=name,
                match_process=list(COMMON_BROWSERS),
                match_hosts=[bctx.host],
                match_title_contains=[],
                preferred_zone=(
                    "left_right_margins"
                    if (wf and any(b.get("role") == "empty_space" for b in wf.get("blocks", [])))
                    else "bottom_edge"
                ),
                layout_wireframe=wf,
            )
        else:
            name = ctx.process_name.removesuffix(".exe").title()
            new_prof = AppProfile(
                id=slugify_profile_id(ctx.process_name),
                name=name,
                match_process=[ctx.process_name.lower()],
                match_hosts=[],
                match_title_contains=[],
                preferred_zone="right_edge",
                layout_wireframe=wf,
            )

        self.engine.reload_profiles(upsert_app_profile(new_prof))
        return new_prof

    def toggle_pin_current_app(self) -> None:
        prof = self._ensure_profile_for_current_app(with_scan=True)
        if prof is None:
            return

        if prof.mode == "pinned":
            prof.mode = "follow_daily"
            prof.pinned_theme_name = ""
            prof.pinned_since = ""
            prof.pinned_components = []
            self.engine.reload_profiles(upsert_app_profile(prof))
            self._sync_ui_labels()
            self.tray.showMessage(
                f"Đã bỏ ghim {prof.name}",
                "Ứng dụng này sẽ tự đổi theo chủ đề mỗi ngày.",
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )
            return

        active_configs = [c.config.model_dump() for c in self.engine._components]
        prof.mode = "pinned"
        prof.pinned_theme_name = self.engine.scenario.theme_name
        prof.pinned_since = current_cycle_slot()[0].isoformat()
        prof.pinned_components = active_configs
        self.engine.reload_profiles(upsert_app_profile(prof))
        self._sync_ui_labels()
        self.tray.showMessage(
            f"📌 Đã ghim style cho {prof.name}",
            f"Giữ cố định \"{prof.pinned_theme_name}\" cho tới khi bạn bấm Bỏ ghim.",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def _ai_pin_current_app(self) -> None:
        prof = self._ensure_profile_for_current_app(with_scan=True)
        if prof is None:
            return
        self._start_ai_pin_worker(prof)

    def _start_ai_pin_worker(self, prof: AppProfile) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            return
        self._set_ai_busy(True)

        thread = QThread()
        worker = _PinStyleWorker(prof)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_ai_pin_ready)
        worker.failed.connect(self._on_ai_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)

        self._worker_thread = thread
        self._worker = worker
        thread.start()

    def _on_ai_pin_ready(self, profile_id: str, style: PinnedAppStyle) -> None:
        self._set_ai_busy(False)
        profiles = load_app_profiles()
        target = next((p for p in profiles if p.id == profile_id), None)
        if target is None:
            return
        target.mode = "pinned"
        target.pinned_theme_name = style.style_name
        target.pinned_since = current_cycle_slot()[0].isoformat()
        target.pinned_components = [c.model_dump() for c in style.components]
        self.engine.reload_profiles(upsert_app_profile(target))
        self._sync_ui_labels()
        self.tray.showMessage(
            f"📌 Style riêng cho {target.name}: {style.style_name}",
            style.tagline,
            QSystemTrayIcon.MessageIcon.Information,
            4500,
        )

    def _scan_and_setup_current_app(self) -> None:
        prof = self._ensure_profile_for_current_app(with_scan=True)
        self._open_profiles_dialog(select_profile_id=prof.id if prof else "")

    def _handle_browser_layout_scan(self, host: str, wireframe: dict) -> None:
        profiles = load_app_profiles()
        existing = next(
            (p for p in profiles if any(h.lower() in host.lower() for h in p.match_hosts)),
            None,
        )
        if existing is not None:
            existing.layout_wireframe = wireframe
            self.engine.reload_profiles(upsert_app_profile(existing))
        else:
            has_margins = any(b.get("role") == "empty_space" for b in wireframe.get("blocks", []))
            new_prof = AppProfile(
                id=slugify_profile_id(host),
                name=host,
                match_process=list(COMMON_BROWSERS),
                match_hosts=[host.lower()],
                match_title_contains=[],
                preferred_zone="left_right_margins" if has_margins else "bottom_edge",
                layout_wireframe=wireframe,
            )
            self.engine.reload_profiles(upsert_app_profile(new_prof))

        self.tray.showMessage(
            f"Đã quét bố cục ẩn danh: {host}",
            "Đã lưu bản đồ vùng trống (Zero-PII). Bạn có thể vẽ thêm vùng hoặc giữ theo chủ đề ngày.",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def _open_profiles_dialog(self, select_profile_id: str = "") -> None:
        dlg = AppProfilesDialog(
            select_profile_id=select_profile_id,
            scan_fn=self._scan_real_window_wireframe,
            draw_zones_fn=self._run_zone_editor_modal,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.engine.reload_profiles(load_app_profiles())
            self._sync_ui_labels()
            if dlg.ai_pin_requested_for:
                prof = next(
                    (p for p in self.engine.profiles if p.id == dlg.ai_pin_requested_for),
                    None,
                )
                if prof is not None:
                    self._start_ai_pin_worker(prof)
                    return
            self.tray.showMessage(
                "✓ Đã cập nhật bố cục App",
                "Các cảnh sẽ tự động di chuyển mượt theo vùng trống mới.",
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )

    @staticmethod
    def _open_scenarios_dir() -> None:
        ensure_data_dirs()
        os.startfile(str(SCENARIOS_DIR))

    @staticmethod
    def _open_assets_dir() -> None:
        ensure_data_dirs()
        os.startfile(str(ASSETS_DIR))

    def _download_public_pack(self) -> None:
        self.download_pack_action.setEnabled(False)
        self.download_pack_action.setText("⏳ Đang tải kho hình động offline...")

        class _PackWorker(QObject):
            done = Signal(int, int)

            def run(self) -> None:
                newly, total = bulk_download_public_pack(limit=248)
                self.done.emit(newly, total)

        thread = QThread()
        worker = _PackWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _on_done(newly: int, total: int) -> None:
            self.download_pack_action.setEnabled(True)
            self.download_pack_action.setText(self._pack_label())
            self.tray.showMessage(
                "Đã tải xong kho hình động offline!",
                f"Vừa tải thêm {newly} hình — tổng cộng {total} asset sẵn sàng dùng nhiều tháng.",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

        worker.done.connect(_on_done)
        worker.done.connect(thread.quit)
        self._pack_thread = thread
        self._pack_worker = worker
        thread.start()

    @staticmethod
    def _open_extension_dir() -> None:
        EXTENSION_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(EXTENSION_DIR))

    @staticmethod
    def _open_log_file() -> None:
        ensure_data_dirs()
        target = LOG_PATH if LOG_PATH.is_file() else LOG_PATH.parent
        os.startfile(str(target))

    def _quit(self) -> None:
        self._sync_timer.stop()
        if self._zone_editor is not None:
            self._zone_editor.close()
        self.quick_bar.close()
        self.tray.hide()
        self._on_quit()
        app = QApplication.instance()
        if app is not None:
            app.quit()
