"""
Main floating window: camera feed + minimal control panel + collapsible drawer.
"""

import os
import json
import subprocess

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSizePolicy, QGridLayout,
    QFrame, QLineEdit, QAbstractButton,
)
from PyQt6.QtCore import Qt, pyqtSlot, QTimer, QPointF
from PyQt6.QtGui import QPixmap, QImage, QIcon, QCursor, QPainter, QPen, QColor


class ChevronButton(QAbstractButton):
    """A button that draws a bold downward (or upward) chevron."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._open = False
        self.setFixedSize(36, 28)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def set_open(self, val: bool):
        self._open = val
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        col = QColor('#a366ff') if self.underMouse() else QColor('#7c3aed')
        pen = QPen(col, 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        w, h = self.width(), self.height()
        cx = w / 2
        # Chevron points: left tip, centre bottom, right tip
        arm = w * 0.28          # half-width of the chevron
        drop = h * 0.22         # vertical drop from tips to centre
        mid_y = h / 2 - drop / 2 + 1

        if self._open:          # pointing up
            p.drawLine(QPointF(cx - arm, mid_y + drop),
                       QPointF(cx,       mid_y))
            p.drawLine(QPointF(cx,       mid_y),
                       QPointF(cx + arm, mid_y + drop))
        else:                   # pointing down
            p.drawLine(QPointF(cx - arm, mid_y),
                       QPointF(cx,       mid_y + drop))
            p.drawLine(QPointF(cx,       mid_y + drop),
                       QPointF(cx + arm, mid_y))
        p.end()

from .toggle import ToggleSwitch
from gesture_engine import GestureEngine

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOGO         = os.path.join(_PROJECT_ROOT, 'Sideswipe-Logo.png')
_RIFF         = os.path.join(_PROJECT_ROOT, 'IronManRiff.mp3')
_BOOKMARKS_PATH = os.path.expanduser('~/.sideswipe_bookmarks.json')

# ── Stylesheet ────────────────────────────────────────────────────────────────
_QSS = """
QMainWindow { background: #111113; }
QWidget#root  { background: #111113; }
QWidget#panel {
    background: #17171a;
    border-top: 1px solid #252529;
}
QWidget#drawer {
    background: #111113;
    border-top: 1px solid #1e1e22;
}
QFrame#vsep {
    background: #2a2a30;
    min-width: 1px; max-width: 1px;
    min-height: 20px; max-height: 20px;
}
QFrame#hsep {
    background: #1e1e24;
    min-height: 1px; max-height: 1px;
}
QLabel { color: #e2e2e5; }
QLabel#brand {
    font-size: 15px; font-weight: 600;
    letter-spacing: -0.3px; color: #e8e8ec;
}
QLabel#status-lbl { color: #484852; font-size: 11px; }
QLabel#section-hdr {
    color: #606068; font-size: 10px; font-weight: 600;
    letter-spacing: 0.6px;
}
QLabel#gesture-name  { color: #c0c0c8; font-size: 12px; font-weight: 500; }
QLabel#gesture-action { color: #505058; font-size: 12px; }
QLabel#bm-label { color: #9090a0; font-size: 12px; }
QLineEdit#url-input {
    background: #1c1c20;
    border: 1px solid #2e2e36;
    border-radius: 5px;
    color: #c8c8d8;
    font-size: 11px;
    padding: 5px 8px;
    selection-background-color: #7c3aed;
}
QLineEdit#url-input:focus { border-color: #5a3a9e; }
QLineEdit#url-input::placeholder { color: #404048; }
"""

_GESTURES = [
    ("Fist",              "Rest / no action"),
    ("1–4 fingers",       "Jump to tab 1–4"),
    ("Pinch + move",      "Scroll page"),
    ("Both hands L",      "New tab"),
    ("Left hand L",       "Close tab"),
    ("Left Spiderman",    "Open bookmark 1"),
    ("Right Spiderman",   "Open bookmark 2"),
    ("Double clap",       "Toggle on / off"),
]


class SideswipeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sideswipe")
        self.setMinimumWidth(300)
        self.resize(390, 400)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)

        if os.path.exists(_LOGO):
            self.setWindowIcon(QIcon(_LOGO))

        self._expanded = False
        self._drawer_h = 0
        self._syncing  = False
        self._bookmarks  = self._load_bookmarks()
        self._panel_h    = 56   # fixed height of the control bar

        self._engine = GestureEngine(self)
        self._engine.active_changed.connect(self._on_active_changed)
        self._engine.status_msg.connect(self._on_status)
        self._engine.gesture_event.connect(self._on_gesture)

        self._build_ui()
        self.setStyleSheet(_QSS)

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._poll_frame)
        self._frame_timer.start(33)

        self._engine.start()

    # ── Bookmark persistence ──────────────────────────────────────────────────

    def _load_bookmarks(self) -> dict:
        if os.path.exists(_BOOKMARKS_PATH):
            try:
                with open(_BOOKMARKS_PATH) as f:
                    return json.load(f)
            except Exception:
                pass
        return {'spiderman_left': '', 'spiderman_right': '', 'riff_enabled': True}

    def _save_bookmarks(self):
        try:
            with open(_BOOKMARKS_PATH, 'w') as f:
                json.dump(self._bookmarks, f, indent=2)
        except Exception:
            pass

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget(); root.setObjectName('root')
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # ── Camera feed ───────────────────────────────────────────────────────
        self._cam = QLabel()
        self._cam.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._cam.setMinimumHeight(200)
        self._cam.setStyleSheet('background: #090910;')
        vbox.addWidget(self._cam, 1)

        # ── Control panel ─────────────────────────────────────────────────────
        panel = QWidget(); panel.setObjectName('panel'); panel.setFixedHeight(56)
        row = QHBoxLayout(panel)
        row.setContentsMargins(14, 0, 12, 0)
        row.setSpacing(10)

        logo_lbl = QLabel(); logo_lbl.setFixedSize(26, 26)
        if os.path.exists(_LOGO):
            logo_lbl.setPixmap(
                QPixmap(_LOGO).scaled(26, 26,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
        else:
            logo_lbl.setStyleSheet(
                'border:1.5px solid #30303a; border-radius:5px; background:transparent;')

        brand = QLabel("Sideswipe"); brand.setObjectName('brand')
        row.addWidget(logo_lbl)
        row.addWidget(brand)
        row.addStretch()

        self._toggle = ToggleSwitch()
        self._toggle.toggled.connect(self._on_toggle)
        row.addWidget(self._toggle)

        sep = QFrame(); sep.setObjectName('vsep'); sep.setFrameShape(QFrame.Shape.VLine)
        row.addSpacing(6); row.addWidget(sep); row.addSpacing(2)

        self._expand_btn = ChevronButton()
        self._expand_btn.clicked.connect(self._toggle_drawer)
        row.addWidget(self._expand_btn)
        vbox.addWidget(panel)

        # ── Collapsible drawer ────────────────────────────────────────────────
        self._drawer = QWidget(); self._drawer.setObjectName('drawer'); self._drawer.hide()
        d = QVBoxLayout(self._drawer)
        d.setContentsMargins(18, 14, 18, 16)
        d.setSpacing(0)

        # Gesture reference
        hdr1 = QLabel("GESTURES"); hdr1.setObjectName('section-hdr')
        d.addWidget(hdr1)
        d.addSpacing(8)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20); grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for r, (g, a) in enumerate(_GESTURES):
            n = QLabel(g); n.setObjectName('gesture-name')
            al = QLabel(a); al.setObjectName('gesture-action')
            grid.addWidget(n, r, 0); grid.addWidget(al, r, 1)
        d.addLayout(grid)
        d.addSpacing(14)

        # Divider
        sep2 = QFrame(); sep2.setObjectName('hsep'); sep2.setFrameShape(QFrame.Shape.HLine)
        d.addWidget(sep2)
        d.addSpacing(14)

        # Custom bookmarks
        hdr2 = QLabel("CUSTOM BOOKMARKS"); hdr2.setObjectName('section-hdr')
        d.addWidget(hdr2)
        d.addSpacing(10)

        # Spiderman — left hand
        lbl_sp_l = QLabel('Spiderman — Left hand 🤘')
        lbl_sp_l.setObjectName('bm-label')
        d.addWidget(lbl_sp_l)
        d.addSpacing(4)
        self._url_spider_l = QLineEdit()
        self._url_spider_l.setObjectName('url-input')
        self._url_spider_l.setPlaceholderText("Paste URL — opens when gesture fires")
        self._url_spider_l.setText(self._bookmarks.get('spiderman_left', ''))
        self._url_spider_l.editingFinished.connect(self._save_spider_left_url)
        d.addWidget(self._url_spider_l)
        d.addSpacing(10)

        # Spiderman — right hand
        lbl_sp_r = QLabel('Spiderman — Right hand 🤘')
        lbl_sp_r.setObjectName('bm-label')
        d.addWidget(lbl_sp_r)
        d.addSpacing(4)
        self._url_spider_r = QLineEdit()
        self._url_spider_r.setObjectName('url-input')
        self._url_spider_r.setPlaceholderText("Paste URL — opens when gesture fires")
        self._url_spider_r.setText(self._bookmarks.get('spiderman_right', ''))
        self._url_spider_r.editingFinished.connect(self._save_spider_right_url)
        d.addWidget(self._url_spider_r)
        d.addSpacing(10)

        # Both-L note (no URL needed)
        lbl_l = QLabel("Both hands L  →  New tab (Cmd+T)")
        lbl_l.setObjectName('bm-label')
        d.addWidget(lbl_l)
        d.addSpacing(14)

        # Divider
        sep3 = QFrame(); sep3.setObjectName('hsep'); sep3.setFrameShape(QFrame.Shape.HLine)
        d.addWidget(sep3)
        d.addSpacing(14)

        # Sound settings
        hdr3 = QLabel("SOUND"); hdr3.setObjectName('section-hdr')
        d.addWidget(hdr3)
        d.addSpacing(10)

        riff_row = QHBoxLayout()
        riff_lbl = QLabel("Iron Man Riff")
        riff_lbl.setObjectName('bm-label')
        self._riff_toggle = ToggleSwitch()
        self._riff_toggle.setChecked(self._bookmarks.get('riff_enabled', True))
        self._riff_toggle.toggled.connect(self._on_riff_toggled)
        riff_row.addWidget(riff_lbl)
        riff_row.addStretch()
        riff_row.addWidget(self._riff_toggle)
        d.addLayout(riff_row)
        d.addSpacing(14)

        # Status
        self._status_lbl = QLabel("Double clap to activate")
        self._status_lbl.setObjectName('status-lbl')
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d.addWidget(self._status_lbl)

        vbox.addWidget(self._drawer)

    # ── Bookmark save helpers ─────────────────────────────────────────────────

    def _save_spider_left_url(self):
        self._bookmarks['spiderman_left'] = self._url_spider_l.text().strip()
        self._save_bookmarks()

    def _save_spider_right_url(self):
        self._bookmarks['spiderman_right'] = self._url_spider_r.text().strip()
        self._save_bookmarks()

    def _on_riff_toggled(self, val: bool):
        self._bookmarks['riff_enabled'] = val
        self._save_bookmarks()

    def _play_riff(self):
        if os.path.exists(_RIFF):
            subprocess.Popen(['afplay', _RIFF])

    # ── Frame polling ─────────────────────────────────────────────────────────

    @pyqtSlot()
    def _poll_frame(self):
        rgb = self._engine.get_latest_frame()
        if rgb is None:
            return
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888)
        pm = QPixmap.fromImage(qimg).scaled(
            self._cam.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._cam.setPixmap(pm)

    # ── Slots ─────────────────────────────────────────────────────────────────

    @pyqtSlot(str)
    def _on_gesture(self, name: str):
        ctrl = self._engine._controller
        if name == 'spiderman_left':
            url = self._bookmarks.get('spiderman_left', '').strip()
            if url:
                ctrl.open_url(url)
        elif name == 'spiderman_right':
            url = self._bookmarks.get('spiderman_right', '').strip()
            if url:
                ctrl.open_url(url)
        # 'both_l' is handled entirely inside the engine (new_tab() already called)

    @pyqtSlot(bool)
    def _on_active_changed(self, active: bool):
        self._syncing = True
        self._toggle.setChecked(active)
        self._syncing = False
        self._status_lbl.setText(
            "Active — gestures on" if active else "Inactive — double clap to activate"
        )
        if active and self._bookmarks.get('riff_enabled', True):
            self._play_riff()

    @pyqtSlot(str)
    def _on_status(self, msg: str):
        self._status_lbl.setText(msg)

    @pyqtSlot(bool)
    def _on_toggle(self, val: bool):
        if self._syncing:
            return
        self._engine.set_active(val)

    def _toggle_drawer(self):
        # Snapshot the current camera height before any resize
        cam_h = self._cam.height()
        self._expanded = not self._expanded
        if self._expanded:
            self._drawer.show()
            drawer_h = self._drawer.sizeHint().height()
            # Window = camera + panel + drawer; camera stays identical
            self.resize(self.width(), cam_h + self._panel_h + drawer_h)
        else:
            self._drawer.hide()
            # Window shrinks back to exactly camera + panel — no leftover space
            self.resize(self.width(), cam_h + self._panel_h)
        self._expand_btn.set_open(self._expanded)

    def closeEvent(self, event):
        self._frame_timer.stop()
        self._engine.stop()
        super().closeEvent(event)
