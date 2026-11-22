from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
)

from daily_overlay.ai.director import (
    delete_today_scenario,
    get_or_create_daily_scenario,
)
from daily_overlay.ai.schema import DailyScenario
from daily_overlay.assets.manager import bulk_download_public_pack, list_local_assets
from daily_overlay.config import (
    ASSETS_DIR,
    EXTENSION_DIR,
    SCENARIOS_DIR,
    AppProfile,
    ZoneType,
    ensure_data_dirs,
    load_app_profiles,
    save_app_profiles,
)
from daily_overlay.context.browser_bridge import BrowserBridgeServer
from daily_overlay.overlay.engine import OverlayEngine
from daily_overlay.ui.quick_bar import QuickControlDock

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


class _ScenarioWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, force: bool = True) -> None:
        super().__init__()
        self._force = force

    def run(self) -> None:
        try:
            profiles = load_app_profiles()
            scenario = get_or_create_daily_scenario(
                force_regenerate=self._force,
                profiles=profiles,
            )
            self.finished.emit(scenario)
        except Exception as exc:
            self.failed.emit(str(exc))


class AppProfilesDialog(QDialog):
    """Lightweight one-time setup dialog for mapping apps/tabs to safe overlay zones."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DailyOverlay — Cấu hình App (Setup 1 lần)")
        self.resize(580, 360)
        self.profiles = load_app_profiles()

        layout = QHBoxLayout(self)

        left_col = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_select)
        left_col.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Thêm")
        del_btn = QPushButton("Xóa")
        add_btn.clicked.connect(self._on_add)
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        left_col.addLayout(btn_row)
        layout.addLayout(left_col, 2)

        right_col = QVBoxLayout()
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.proc_edit = QLineEdit()
        self.proc_edit.setPlaceholderText("vd: chrome.exe, msedge.exe")
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("vd: Google Docs, YouTube (trống = cả app)")
        self.zone_combo = QComboBox()
        self.zone_combo.addItems(ZONE_OPTIONS)
        self.vibe_edit = QLineEdit()
        self.vibe_edit.setPlaceholderText("vd: cat_pet đùa quanh thumbnail")

        form.addRow("Tên cấu hình:", self.name_edit)
        form.addRow("Process (.exe):", self.proc_edit)
        form.addRow("Từ khóa tiêu đề:", self.title_edit)
        form.addRow("Vùng an toàn:", self.zone_combo)
        form.addRow("Gợi ý cho AI:", self.vibe_edit)
        right_col.addLayout(form)

        save_item_btn = QPushButton("Cập nhật mục đang chọn")
        save_item_btn.clicked.connect(self._apply_current_fields)
        right_col.addWidget(save_item_btn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save_all)
        buttons.rejected.connect(self.reject)
        right_col.addWidget(buttons)
        layout.addLayout(right_col, 3)

        self._refresh_list(0)

    def _refresh_list(self, select_idx: int = 0) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for p in self.profiles:
            self.list_widget.addItem(f"{p.name}  [{p.preferred_zone}]")
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
        self.title_edit.setText(", ".join(p.match_title_contains))
        self.zone_combo.setCurrentText(p.preferred_zone)
        self.vibe_edit.setText(p.preferred_vibe)

    def _apply_current_fields(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.profiles):
            return
        self.profiles[row] = AppProfile(
            name=self.name_edit.text().strip() or "Custom App",
            match_process=[s.strip() for s in self.proc_edit.text().split(",") if s.strip()],
            match_title_contains=[s.strip() for s in self.title_edit.text().split(",") if s.strip()],
            preferred_zone=self.zone_combo.currentText(),  # type: ignore[arg-type]
            preferred_vibe=self.vibe_edit.text().strip() or "subtle ambient",
        )
        self._refresh_list(row)

    def _on_add(self) -> None:
        self.profiles.append(
            AppProfile(
                name="App mới",
                match_process=["chrome.exe"],
                match_title_contains=["YouTube"],
                preferred_zone="bottom_edge",
                preferred_vibe="playful cat_pet",
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
    """System Tray menu to control DailyOverlay without cluttering the desktop."""

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
        self._worker: _ScenarioWorker | None = None

        self.tray = QSystemTrayIcon(
            _make_tray_icon(
                engine.scenario.palette.primary,
                engine.scenario.palette.accent,
            )
        )
        self.tray.setToolTip(f"DailyOverlay — {engine.scenario.theme_name}")

        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._refresh_live_status)

        self.theme_action = QAction(self._theme_label(), self.menu)
        self.theme_action.setEnabled(False)
        self.menu.addAction(self.theme_action)

        self.status_action = QAction("Đang nhận: Desktop → Global", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        self.pause_action = QAction("Tạm dừng Overlay", self.menu)
        self.pause_action.triggered.connect(self._toggle_pause)
        self.menu.addAction(self.pause_action)

        self.zen_action = QAction("Zen Mode (Ẩn 30 phút)", self.menu)
        self.zen_action.triggered.connect(self._toggle_zen)
        self.menu.addAction(self.zen_action)

        # Opacity preset submenu
        opacity_menu = self.menu.addMenu("Độ rõ Overlay")
        opacity_group = QActionGroup(self)
        opacity_group.setExclusive(True)
        for label, mult in [("Nhẹ (65%)", 0.65), ("Vừa (100% - Mặc định)", 1.0), ("Rõ nét (125%)", 1.25)]:
            act = QAction(label, opacity_menu, checkable=True)
            if abs(mult - self.engine.user_opacity_mult) < 0.05:
                act.setChecked(True)
            act.triggered.connect(lambda checked, m=mult: self._set_opacity(m))
            opacity_group.addAction(act)
            opacity_menu.addAction(act)

        self.menu.addSeparator()

        self.regen_action = QAction("Đổi kịch bản hôm nay (Gemini)", self.menu)
        self.regen_action.triggered.connect(self.regenerate_scenario)
        self.menu.addAction(self.regen_action)

        self.dislike_action = QAction("Xóa cảnh này (không lặp lại) & Tạo mới", self.menu)
        self.dislike_action.triggered.connect(self.dislike_and_regenerate)
        self.menu.addAction(self.dislike_action)

        self.quickbar_action = QAction("Ẩn nút điều khiển nhanh (góc phải)", self.menu)
        self.quickbar_action.triggered.connect(self._toggle_quick_bar)
        self.menu.addAction(self.quickbar_action)

        self.profiles_action = QAction("Cấu hình App (Setup 1 lần)...", self.menu)
        self.profiles_action.triggered.connect(self._open_profiles_dialog)
        self.menu.addAction(self.profiles_action)

        open_assets_action = QAction("Mở thư mục Assets (thả GIF/WebP)...", self.menu)
        open_assets_action.triggered.connect(self._open_assets_dir)
        self.menu.addAction(open_assets_action)

        self.download_pack_action = QAction(
            f"Tải kho Public Assets (~120 asset động | hiện có {len(list_local_assets())})",
            self.menu,
        )
        self.download_pack_action.triggered.connect(self._download_public_pack)
        self.menu.addAction(self.download_pack_action)

        open_ext_action = QAction("Mở thư mục Browser Extension...", self.menu)
        open_ext_action.triggered.connect(self._open_extension_dir)
        self.menu.addAction(open_ext_action)

        open_dir_action = QAction("Mở thư mục kịch bản", self.menu)
        open_dir_action.triggered.connect(self._open_scenarios_dir)
        self.menu.addAction(open_dir_action)

        self.menu.addSeparator()
        quit_action = QAction("Thoát DailyOverlay", self.menu)
        quit_action.triggered.connect(self._quit)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        # Floating quick control pill at bottom-right corner
        self.quick_bar = QuickControlDock(
            on_toggle_pause=self._toggle_pause,
            on_reroll=self.regenerate_scenario,
            on_dislike_reroll=self.dislike_and_regenerate,
            on_toggle_zen=self._toggle_zen,
        )
        self.quick_bar.set_theme_name(engine.scenario.theme_name)
        self.quick_bar.show()

        self.tray.showMessage(
            f"DailyOverlay — {engine.scenario.theme_name}",
            engine.scenario.tagline,
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def _theme_label(self) -> str:
        s = self.engine.scenario
        return f"Hôm nay: {s.theme_name} ({s.mood})"

    def _refresh_live_status(self) -> None:
        ext_clients = self.browser_bridge.connected_clients if self.browser_bridge else 0
        bctx = self.browser_bridge.get_context() if self.browser_bridge else None
        if ext_clients > 0 or bctx is not None:
            dom_info = ""
            if bctx:
                if bctx.elements:
                    dom_info = f" ({len(bctx.elements)} thumbs)"
                elif bctx.margins:
                    dom_info = f" ({len(bctx.margins)} margins)"
            ext_str = f"Ext: OK{dom_info}"
        else:
            ext_str = "Ext: chưa nối"
        self.status_action.setText(
            f"App: {self.engine.last_process_name} → {self.engine.active_rule_name} | {ext_str}"
        )

    def _set_opacity(self, mult: float) -> None:
        self.engine.user_opacity_mult = mult

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if not self.quick_bar.isVisible():
                self.quick_bar.show()
            self.quick_bar.toggle_expanded()

    def _toggle_quick_bar(self) -> None:
        if self.quick_bar.isVisible():
            self.quick_bar.hide()
            self.quickbar_action.setText("Hiện nút điều khiển nhanh (góc phải)")
        else:
            self.quick_bar.show()
            self.quickbar_action.setText("Ẩn nút điều khiển nhanh (góc phải)")

    def _toggle_pause(self) -> None:
        self.engine.paused = not self.engine.paused
        self.pause_action.setText("Tiếp tục Overlay" if self.engine.paused else "Tạm dừng Overlay")
        self.quick_bar.set_paused(self.engine.paused)

    def _toggle_zen(self) -> None:
        if self.engine.is_zen_active:
            self.engine.set_zen_minutes(0)
            self.zen_action.setText("Zen Mode (Ẩn 30 phút)")
        else:
            self.engine.set_zen_minutes(30)
            self.zen_action.setText("Tắt Zen Mode")

    def dislike_and_regenerate(self) -> None:
        current_name = self.engine.scenario.theme_name
        delete_today_scenario(dislike_theme_name=current_name)
        self.regenerate_scenario()

    def regenerate_scenario(self) -> None:
        if self._worker_thread is not None and self._worker_thread.isRunning():
            return
        self.regen_action.setEnabled(False)
        self.dislike_action.setEnabled(False)
        self.regen_action.setText("Đang nhờ Gemini sáng tác...")
        self.quick_bar.set_busy(True)

        thread = QThread()
        worker = _ScenarioWorker(force=True)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_scenario_ready)
        worker.failed.connect(self._on_scenario_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)

        self._worker_thread = thread
        self._worker = worker
        thread.start()

    def _on_scenario_ready(self, scenario: DailyScenario) -> None:
        self.engine.set_scenario(scenario)
        self.theme_action.setText(self._theme_label())
        self.tray.setToolTip(f"DailyOverlay — {scenario.theme_name}")
        self.tray.setIcon(_make_tray_icon(scenario.palette.primary, scenario.palette.accent))
        self.regen_action.setEnabled(True)
        self.dislike_action.setEnabled(True)
        self.regen_action.setText("Đổi kịch bản hôm nay (Gemini)")
        self.quick_bar.set_theme_name(scenario.theme_name)
        self.quick_bar.set_busy(False)
        self.tray.showMessage(
            f"Kịch bản mới: {scenario.theme_name}",
            scenario.tagline,
            QSystemTrayIcon.MessageIcon.Information,
            4500,
        )

    def _on_scenario_failed(self, err: str) -> None:
        self.regen_action.setEnabled(True)
        self.dislike_action.setEnabled(True)
        self.regen_action.setText("Đổi kịch bản hôm nay (Gemini)")
        self.quick_bar.set_busy(False)
        self.tray.showMessage(
            "Không tạo được kịch bản mới",
            err[:180],
            QSystemTrayIcon.MessageIcon.Warning,
            4500,
        )

    def _open_profiles_dialog(self) -> None:
        dlg = AppProfilesDialog()
        if dlg.exec() == QDialog.DialogCode.Accepted:
            reply = QMessageBox.question(
                None,
                "Áp dụng cấu hình mới?",
                "Bạn có muốn nhờ Gemini tạo lại kịch bản hôm nay theo cấu hình App mới không?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.regenerate_scenario()

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
        self.download_pack_action.setText("Đang tải kho Public Assets...")

        class _PackWorker(QObject):
            done = Signal(int, int)

            def run(self) -> None:
                newly, total = bulk_download_public_pack(limit=120)
                self.done.emit(newly, total)

        thread = QThread()
        worker = _PackWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _on_done(newly: int, total: int) -> None:
            self.download_pack_action.setEnabled(True)
            self.download_pack_action.setText(
                f"Tải kho Public Assets (~120 asset động | hiện có {total})"
            )
            self.tray.showMessage(
                "Đã tải xong kho Public Assets!",
                f"Vừa tải thêm {newly} asset mới — tổng cộng {total} asset động sẵn sàng offline.",
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

    def _quit(self) -> None:
        self.quick_bar.close()
        self.tray.hide()
        self._on_quit()
        app = QApplication.instance()
        if app is not None:
            app.quit()
