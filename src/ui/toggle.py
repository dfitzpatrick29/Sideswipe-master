"""iOS-style animated toggle switch."""

from PyQt6.QtWidgets import QAbstractButton
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPainter, QColor, QBrush

_W, _H       = 46, 26    # widget dimensions
_TRACK_Y     = 3          # track top offset
_TRACK_H     = 20         # track height
_THUMB_W     = 18         # thumb diameter
_THUMB_Y     = _TRACK_Y + (_TRACK_H - _THUMB_W) // 2   # vertically centred in track
_THUMB_OFF   = 3          # thumb x when off  (3 px left margin)
_THUMB_ON    = _W - _THUMB_W - _THUMB_OFF               # thumb x when on   (3 px right margin)


class ToggleSwitch(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(_W, _H)
        self._thumb = float(_THUMB_OFF)

        self._anim = QPropertyAnimation(self, b'thumb', self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.toggled.connect(self._animate)

    @pyqtProperty(float)
    def thumb(self):
        return self._thumb

    @thumb.setter
    def thumb(self, val):
        self._thumb = val
        self.update()

    def _animate(self, checked):
        self._anim.setStartValue(self._thumb)
        self._anim.setEndValue(float(_THUMB_ON if checked else _THUMB_OFF))
        self._anim.start()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        # Track
        p.setBrush(QBrush(QColor('#7c3aed') if self.isChecked() else QColor('#38383e')))
        p.drawRoundedRect(0, _TRACK_Y, _W, _TRACK_H, _TRACK_H // 2, _TRACK_H // 2)

        # Thumb (white circle, centred in track)
        p.setBrush(QBrush(QColor('#ffffff')))
        p.drawEllipse(int(self._thumb), _THUMB_Y, _THUMB_W, _THUMB_W)
        p.end()

    def sizeHint(self):
        return self.size()
