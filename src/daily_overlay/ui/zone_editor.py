from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class ZoneEditorOverlay(QWidget):
    """Interactive translucent canvas over an app window to draw Allowed (green) & Forbidden (red) zones."""

    zones_saved = Signal(list, list)
    editor_closed = Signal()

    def __init__(
        self,
        app_name: str,
        target_rect: tuple[int, int, int, int],
        wireframe: dict | None = None,
        allowed_zones: list[list[float]] | None = None,
        forbidden_zones: list[list[float]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.app_name = app_name
        self.wireframe = wireframe
        self._draw_mode: str = "allow"  # "allow" | "forbid"
        self._drag_start: QPointF | None = None
        self._drag_end: QPointF | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        screen = QGuiApplication.primaryScreen()
        geo = screen.geometry() if screen else None
        if geo:
            self.setGeometry(geo)
            ox, oy = geo.x(), geo.y()
            sw, sh = geo.width(), geo.height()
        else:
            ox, oy, sw, sh = 0, 0, 1920, 1080
            self.resize(sw, sh)

        tl, tt, tr, tb = target_rect
        rx = max(0.0, float(tl - ox))
        ry = max(0.0, float(tt - oy))
        rw = max(120.0, min(float(sw) - rx, float(tr - tl)))
        rh = max(120.0, min(float(sh) - ry, float(tb - tt)))
        self._target_rect = QRectF(rx, ry, rw, rh)

        # Stored in chronological order so Undo removes the most recent box
        self._boxes: list[tuple[str, QRectF]] = []
        for kind, rel_list in (("allow", allowed_zones or []), ("forbid", forbidden_zones or [])):
            for box in rel_list:
                if len(box) != 4:
                    continue
                bx, by, bw, bh = box
                self._boxes.append(
                    (
                        kind,
                        QRectF(
                            self._target_rect.x() + bx * self._target_rect.width(),
                            self._target_rect.y() + by * self._target_rect.height(),
                            bw * self._target_rect.width(),
                            bh * self._target_rect.height(),
                        ),
                    )
                )

        self._build_toolbar(sw)

    def _build_toolbar(self, screen_w: int) -> None:
        self.toolbar = QWidget(self)
        self.toolbar.setObjectName("toolbar")
        self.toolbar.setCursor(Qt.CursorShape.ArrowCursor)
        self.toolbar.setStyleSheet(
            """
            #toolbar {
                background-color: rgba(20, 24, 32, 232);
                border: 1px solid rgba(255, 255, 255, 55);
                border-radius: 12px;
            }
            QLabel {
                color: #E2E8F0;
                font-size: 12px;
                font-weight: 600;
                padding: 0 6px;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 18);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 28);
                border-radius: 8px;
                padding: 5px 10px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 38);
            }
            QPushButton:checked {
                border: 1.5px solid #38BDF8;
                background-color: rgba(56, 189, 248, 55);
            }
            """
        )

        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(6)

        title = QLabel(f"🎨 Vẽ vùng cho {self.app_name} (Kéo chuột trái để vẽ · Chuột phải vào ô để xóa)")
        row.addWidget(title)

        self.allow_btn = QPushButton("🟢 Vẽ vùng Cho phép")
        self.allow_btn.setCheckable(True)
        self.allow_btn.setChecked(True)
        self.allow_btn.clicked.connect(lambda: self._set_mode("allow"))
        row.addWidget(self.allow_btn)

        self.forbid_btn = QPushButton("🔴 Vẽ vùng Cấm")
        self.forbid_btn.setCheckable(True)
        self.forbid_btn.clicked.connect(lambda: self._set_mode("forbid"))
        row.addWidget(self.forbid_btn)

        undo_btn = QPushButton("↩ Hoàn tác")
        undo_btn.clicked.connect(self.undo_last)
        row.addWidget(undo_btn)

        clear_btn = QPushButton("🗑 Xóa hết")
        clear_btn.clicked.connect(self.clear_all)
        row.addWidget(clear_btn)

        save_btn = QPushButton("✓ Lưu vùng")
        save_btn.setStyleSheet(
            "QPushButton { background-color: rgba(39, 174, 96, 200); border-color: #2ECC71; }"
        )
        save_btn.clicked.connect(self.save_and_close)
        row.addWidget(save_btn)

        cancel_btn = QPushButton("✕ Hủy")
        cancel_btn.clicked.connect(self.close)
        row.addWidget(cancel_btn)

        self.toolbar.adjustSize()
        tx = max(12, min(screen_w - self.toolbar.width() - 12, int(self._target_rect.center().x() - self.toolbar.width() / 2)))
        ty = max(12, int(self._target_rect.top()) + 10)
        self.toolbar.move(tx, ty)

    def _set_mode(self, mode: str) -> None:
        self._draw_mode = mode
        self.allow_btn.setChecked(mode == "allow")
        self.forbid_btn.setChecked(mode == "forbid")

    def undo_last(self) -> None:
        if self._boxes:
            self._boxes.pop()
            self.update()

    def clear_all(self) -> None:
        self._boxes.clear()
        self.update()

    def export_normalized_boxes(self) -> tuple[list[list[float]], list[list[float]]]:
        tw = max(1.0, self._target_rect.width())
        th = max(1.0, self._target_rect.height())
        allowed: list[list[float]] = []
        forbidden: list[list[float]] = []

        for kind, rect in self._boxes:
            rx = max(0.0, min(1.0, (rect.x() - self._target_rect.x()) / tw))
            ry = max(0.0, min(1.0, (rect.y() - self._target_rect.y()) / th))
            rw = max(0.0, min(1.0 - rx, rect.width() / tw))
            rh = max(0.0, min(1.0 - ry, rect.height() / th))
            item = [round(rx, 4), round(ry, 4), round(rw, 4), round(rh, 4)]
            if kind == "allow":
                allowed.append(item)
            else:
                forbidden.append(item)
        return allowed, forbidden

    def save_and_close(self) -> None:
        allowed, forbidden = self.export_normalized_boxes()
        self.zones_saved.emit(allowed, forbidden)
        self.close()

    def _clamp_to_target(self, pt: QPointF) -> QPointF:
        return QPointF(
            max(self._target_rect.left(), min(self._target_rect.right(), pt.x())),
            max(self._target_rect.top(), min(self._target_rect.bottom(), pt.y())),
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            for idx in range(len(self._boxes) - 1, -1, -1):
                if self._boxes[idx][1].contains(pos):
                    self._boxes.pop(idx)
                    self.update()
                    return
        elif event.button() == Qt.MouseButton.LeftButton:
            if self._target_rect.contains(pos):
                clamped = self._clamp_to_target(pos)
                self._drag_start = clamped
                self._drag_end = clamped
                self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_start is not None:
            self._drag_end = self._clamp_to_target(event.position())
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            end = self._clamp_to_target(event.position())
            rect = QRectF(self._drag_start, end).normalized().intersected(self._target_rect)
            if rect.width() >= 24.0 and rect.height() >= 24.0:
                self._boxes.append((self._draw_mode, rect))
            self._drag_start = None
            self._drag_end = None
            self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        elif event.key() == Qt.Key.Key_Z and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.undo_last()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.editor_closed.emit()
        super().closeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Gentle dark scrim across screen
        painter.fillRect(self.rect(), QColor(10, 14, 22, 76))

        # Highlight target app window outline
        target_pen = QPen(QColor(255, 255, 255, 200), 2.0, Qt.PenStyle.DashLine)
        painter.setPen(target_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._target_rect)

        # Render existing Zero-PII wireframe blocks as subtle blue guides
        if self.wireframe and self.wireframe.get("blocks"):
            vp = self.wireframe.get("viewport") or [self._target_rect.width(), self._target_rect.height()]
            vw = max(1.0, float(vp[0]))
            vh = max(1.0, float(vp[1]))
            wf_pen = QPen(QColor(93, 173, 226, 150), 1.3, Qt.PenStyle.DotLine)
            painter.setPen(wf_pen)
            painter.setBrush(QColor(93, 173, 226, 22))
            for blk in self.wireframe.get("blocks", []):
                box = blk.get("box")
                if not box or len(box) != 4:
                    continue
                bx, by, bw, bh = box
                r = QRectF(
                    self._target_rect.x() + (bx / vw) * self._target_rect.width(),
                    self._target_rect.y() + (by / vh) * self._target_rect.height(),
                    (bw / vw) * self._target_rect.width(),
                    (bh / vh) * self._target_rect.height(),
                )
                painter.drawRect(r)
                painter.drawText(r.adjusted(4, 14, -4, -4), str(blk.get("role", "pane")))

        # Render saved Allowed / Forbidden zones + live drag preview
        draw_list = list(self._boxes)
        if self._drag_start is not None and self._drag_end is not None:
            preview = QRectF(self._drag_start, self._drag_end).normalized()
            if preview.width() >= 4 and preview.height() >= 4:
                draw_list.append((self._draw_mode, preview))

        for kind, rect in draw_list:
            if kind == "allow":
                painter.setPen(QPen(QColor("#2ECC71"), 2.2))
                painter.setBrush(QColor(46, 204, 113, 68))
                painter.drawRect(rect)
                painter.drawText(rect.adjusted(6, 16, -4, -4), "✓ Cho phép")
            else:
                painter.setPen(QPen(QColor("#E74C3C"), 2.2))
                painter.setBrush(QBrush(QColor(231, 76, 60, 82), Qt.BrushStyle.BDiagPattern))
                painter.drawRect(rect)
                painter.drawText(rect.adjusted(6, 16, -4, -4), "✕ Vùng cấm")

        painter.end()
