"""播放条里的曲名文字：超长时跑马灯滚动。

曲名和歌手是同一条线上的两段色文字，所以不能拆成两个 QLabel——那样两段
各自省略会很难看。这里自己排版：先量总宽，放得下就静止，放不下就整体
横向滚动，回到起点时停顿一下再继续。
"""
from __future__ import annotations

from PyQt5.QtCore import QElapsedTimer, QPointF, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt5.QtWidgets import QSizePolicy, QWidget

SPEED = 26.0          # 滚动速度 px/s
PAUSE = 1.4           # 每轮回起点后停顿秒数
GAP = 26              # 曲名与歌手之间的间距
TAIL = 44             # 尾部留白，避免两轮之间粘连
TICK = 16             # ~60fps

TITLE_COLOR = "#e8e8ea"
SUB_COLOR = "#8b8b95"
IDLE_COLOR = "#6f6f78"


class MarqueeText(QWidget):
    """两段式单行文字，溢出时整体滚动。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._title = ""
        self._sub = ""
        self._offset = 0.0
        self._pause_left = PAUSE
        self._scrolling_allowed = True

        self._font_title = QFont()
        self._font_title.setPixelSize(12)
        self._font_sub = QFont()
        self._font_sub.setPixelSize(11)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumWidth(90)
        self.setAttribute(Qt.WA_Hover, True)

        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(TICK)
        self._timer.timeout.connect(self._tick)

    # ---------------- 内容 ----------------

    def set_text(self, title: str, sub: str = "") -> None:
        title = title or ""
        sub = sub or ""
        if (title, sub) == (self._title, self._sub):
            return
        self._title, self._sub = title, sub
        self._offset = 0.0
        self._pause_left = PAUSE
        tip = title if not sub else f"{title}\n{sub}"
        self.setToolTip(tip)
        self._sync()
        self.update()

    def clear(self) -> None:
        self.set_text("", "")
        self.setToolTip("")

    def set_scrolling_allowed(self, allowed: bool) -> None:
        """暂停播放时停止滚动，减少干扰。"""
        self._scrolling_allowed = allowed
        self._sync()

    # ---------------- 尺寸与滚动 ----------------

    def _text_width(self) -> float:
        fm_t = QFontMetrics(self._font_title)
        width = fm_t.horizontalAdvance(self._title)
        if self._sub:
            width += GAP + QFontMetrics(self._font_sub).horizontalAdvance(self._sub)
        return float(width)

    def _needs_scroll(self) -> bool:
        return self._text_width() > self.width() - 2

    def _sync(self) -> None:
        should = self._scrolling_allowed and self._needs_scroll()
        if should and not self._timer.isActive():
            self._clock.restart()
            self._timer.start()
        elif not should:
            self._timer.stop()
            if self._offset:
                self._offset = 0.0
                self.update()

    def _tick(self) -> None:
        # 按真实经过时间推进，帧率低时速度也不会变慢
        elapsed = self._clock.restart() / 1000.0
        if elapsed <= 0:
            return
        if self._pause_left > 0:
            self._pause_left -= elapsed
            return
        self._offset += SPEED * elapsed
        if self._offset >= self._text_width() + TAIL:
            self._offset = 0.0
            self._pause_left = PAUSE
        self.update()

    # ---------------- 绘制 ----------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setClipRect(self.rect())

        if not self._title:
            painter.setFont(self._font_title)
            painter.setPen(QColor(IDLE_COLOR))
            fm = QFontMetrics(self._font_title)
            y = (self.height() + fm.ascent() - fm.descent()) / 2
            painter.drawText(QPointF(0, y), "未在播放")
            return

        fm_t = QFontMetrics(self._font_title)
        baseline = (self.height() + fm_t.ascent() - fm_t.descent()) / 2
        x = -self._offset

        painter.setFont(self._font_title)
        painter.setPen(QColor(TITLE_COLOR))
        painter.drawText(QPointF(x, baseline), self._title)

        if self._sub:
            x += fm_t.horizontalAdvance(self._title) + GAP
            painter.setFont(self._font_sub)
            painter.setPen(QColor(SUB_COLOR))
            painter.drawText(QPointF(x, baseline), self._sub)

    # ---------------- 事件 ----------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync()
