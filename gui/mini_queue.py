"""紧凑模式用的轻量下载队列控件。

紧凑窗口只有 400px 宽，完整队列表格（曲名/状态/进度/大小 四列）放不下。
这里给两种形态，由设置里的「紧凑队列」切换：

- ``MiniQueueDock``：一行停靠条，显示当前曲目、总进度、完成数；点一下弹出浮层。
- ``QueueSheet``：浮层明细，最多 8 行，点外面自动收起。

第三种「折叠面板」直接用主窗口自己的队列表格，不在这里实现。
"""
from __future__ import annotations

from PyQt5.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui import icons

MAX_ROWS = 8
ROW_H = 26
SHEET_GAP = 6
CHEVRON_SIZE = 16
CHECK_SIZE = 14
# 卡片顶栏高度：和列表行共用一条左基线，行高 28 更透气
HEAD_H = 32
CHEVRON_COLOR = "#9a9aa4"


def _chevron_pixmap(opened: bool):
    """展开/收起箭头。用 Lucide 的 chevron，比 ▾ 这种字符边角干净得多。"""
    return icons.pixmap(
        "chevron-up" if opened else "chevron-down", CHEVRON_COLOR, CHEVRON_SIZE
    )


class MiniQueueDock(QWidget):
    """紧凑模式底部的一行队列条。"""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("miniQueueDock")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        self._full_title = "下载队列"
        self._open = False

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(8)

        self.title = QLabel("下载队列")
        self.title.setStyleSheet("color:#b6b6c0;font-size:11px;")
        # 宽度让给进度条，长曲名自己截断（见 _elide）
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.title, 1)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedSize(64, 4)
        self.bar.setStyleSheet(
            "QProgressBar{background:#2e2e34;border:none;border-radius:2px;}"
            "QProgressBar::chunk{background:#e8554a;border-radius:2px;}"
        )
        row.addWidget(self.bar)

        self.percent = QLabel("")
        self.percent.setStyleSheet("color:#96969e;font-size:10px;")
        self.percent.setFixedWidth(30)
        self.percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.percent)

        self.counts = QLabel("")
        self.counts.setStyleSheet("color:#7a7a84;font-size:10px;")
        row.addWidget(self.counts)

        self.arrow = QLabel()
        self.arrow.setFixedSize(CHEVRON_SIZE, CHEVRON_SIZE)
        self.arrow.setAlignment(Qt.AlignCenter)
        self.arrow.setPixmap(_chevron_pixmap(False))
        row.addWidget(self.arrow)

    # ---------------- 状态 ----------------

    def set_open(self, opened: bool) -> None:
        self._open = opened
        self.arrow.setPixmap(_chevron_pixmap(not opened))

    def update_state(
        self,
        current: str,
        percent: int,
        done: int,
        total: int,
        busy: bool,
    ) -> None:
        if total:
            if busy and current:
                self._full_title = f"下载中 {current}"
            elif busy:
                self._full_title = f"下载中 {done}/{total}"
            else:
                self._full_title = f"下载队列 · {total} 首"
        else:
            self._full_title = "下载队列 · 空"

        self.bar.setValue(max(0, min(1000, percent * 10)))
        self.percent.setText(f"{percent}%" if total else "")
        self.counts.setText(f"{done}/{total}" if total else "")
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = max(20, self.title.width() - 2)
        self.title.setText(
            self.title.fontMetrics().elidedText(
                self._full_title, Qt.ElideRight, width
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_elide()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class QueueHeader(QWidget):
    """紧凑模式队列的卡片顶栏：徽标 + 名称 + 进度 + 清空 + 小三角。

    整行都能点，点一下折叠/展开。徽标里是「正在下载的条数」，
    右侧数字是「已完成/总数 · 总百分比」。

    全部完成之后切成汇总态：绿勾 + 「N 首已完成」+ 清空，不再逐行重复「完成」。
    """

    clicked = pyqtSignal()
    cleared = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("queueHeader")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(HEAD_H)
        self.setCursor(Qt.PointingHandCursor)
        self._summary = 0

        row = QHBoxLayout(self)
        # 左右留 10px：顶栏和列表行用同一条左基线（卡片的 1px 边框在布局外边）
        row.setContentsMargins(10, 0, 8, 0)
        row.setSpacing(7)

        self.badge = QLabel("0")
        self.badge.setFixedSize(16, 16)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setStyleSheet(
            "background:#e8554a; color:#ffffff; border-radius:8px;"
            " font-size:10px; font-weight:600;"
        )
        self.badge.hide()
        row.addWidget(self.badge)

        self.check = QLabel()
        self.check.setFixedSize(CHECK_SIZE, CHECK_SIZE)
        self.check.setPixmap(icons.pixmap("check", "#4caf7d", CHECK_SIZE))
        self.check.hide()
        row.addWidget(self.check)

        self.title = QLabel("下载队列")
        self.title.setStyleSheet("color:#c3c3cc; font-size:11.5px;")
        row.addWidget(self.title)

        self.counts = QLabel("")
        self.counts.setStyleSheet("color:#96969e; font-size:11px;")
        row.addWidget(self.counts)

        row.addStretch(1)

        # 清空挂在顶栏右侧：完成态才有，点这一小块只清空，不折叠
        self.clear_link = QLabel("清空")
        self.clear_link.setStyleSheet("color:#8b8b95; font-size:11px; padding:2px 6px;")
        self.clear_link.setCursor(Qt.PointingHandCursor)
        # 交给父级判断点击位置，免得 QLabel 吃掉鼠标事件
        self.clear_link.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.clear_link.hide()
        row.addWidget(self.clear_link)

        self.chevron = QLabel()
        self.chevron.setFixedSize(CHEVRON_SIZE, CHEVRON_SIZE)
        self.chevron.setAlignment(Qt.AlignCenter)
        self.chevron.setPixmap(_chevron_pixmap(False))
        row.addWidget(self.chevron)

    # ---------------- 状态 ----------------

    def update_state(
        self, done: int, total: int, percent: int, active: int, opened: bool
    ) -> None:
        self._set_summary_mode(False)
        if active > 0:
            self.badge.setText(str(active))
            self.badge.show()
        else:
            # 文字也要清掉：只 hide 的话队列清空后再显示会闪一下上一批的数字
            self.badge.setText("")
            self.badge.hide()
        self.title.setText("下载队列")
        if total:
            tail = f"{done}/{total}"
            if percent:
                tail += f" · {percent}%"
            self.counts.setText(tail)
            self.counts.show()
        else:
            self.counts.setText("")
            self.counts.hide()
        # 空队列没有可展开的内容，箭头留着点了也没反应
        self.chevron.setVisible(bool(total))
        self.set_open(opened)

    def set_summary(self, done: int) -> None:
        """全部完成：绿勾 + 「N 首已完成」+ 清空，箭头还在（点开能看明细）。"""
        self._set_summary_mode(True)
        self.title.setText(f"{done} 首已完成")
        self.counts.setText("")
        self.counts.hide()
        self.badge.hide()
        self.chevron.setVisible(True)

    def _set_summary_mode(self, on: bool) -> None:
        self._summary = 1 if on else 0
        self.check.setVisible(on)
        self.clear_link.setVisible(on)

    def set_open(self, opened: bool) -> None:
        # 卡片顶栏：收起时向下指（点开），展开时向上指（收起）
        self.chevron.setPixmap(_chevron_pixmap(opened))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            if (
                self.clear_link.isVisible()
                and self.clear_link.geometry().contains(event.pos())
            ):
                self.cleared.emit()
            else:
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class QueueSheet(QFrame):
    """点停靠条弹出的队列明细；点窗口别处自动收起。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("queueSheet")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(2)

        self._rows: list[dict] = []
        for _ in range(MAX_ROWS):
            self._rows.append(self._make_row(outer))

        self.more = QLabel("")
        self.more.setStyleSheet("color:#7a7a84;font-size:10px;padding-left:4px;")
        outer.addWidget(self.more)

        # 这里不能用 QGraphicsDropShadowEffect：主窗口是 WA_TranslucentBackground，
        # 效果会先把控件渲染到一张不透明的离屏图上，圆角外面就多出一圈黑色矩形
        # （用户看到的就是「浮层里有个长方形黑底」）。阴影改由边框和更深一档的
        # 底色来暗示，视觉上够用，也没有那个黑框。
        self._anchor: QWidget | None = None

    def _make_row(self, layout: QVBoxLayout) -> dict:
        row = QWidget()
        row.setFixedHeight(ROW_H)
        h = QHBoxLayout(row)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(8)

        name = QLabel("")
        name.setStyleSheet("color:#d8d8dc;font-size:11px;")
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        h.addWidget(name, 1)

        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setTextVisible(False)
        bar.setFixedSize(56, 4)
        h.addWidget(bar)

        state = QLabel("")
        state.setFixedWidth(56)
        state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        state.setStyleSheet("color:#96969e;font-size:10px;")
        h.addWidget(state)

        layout.addWidget(row)
        row.hide()
        return {"row": row, "name": name, "bar": bar, "state": state, "tip": ""}

    # ---------------- 展示 ----------------

    def show_for(self, anchor: QWidget, rows: list[dict]) -> None:
        host = self.parentWidget()
        if host is None:
            return
        self._anchor = anchor
        self.refresh(rows)
        top_left = anchor.mapTo(host, QPoint(0, 0))
        x = 8
        y = max(0, top_left.y() - self.height() - SHEET_GAP)
        self.move(x, y)
        self.raise_()
        self.show()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def refresh(self, rows: list[dict]) -> None:
        visible = rows[:MAX_ROWS]
        for index, slot in enumerate(self._rows):
            if index >= len(visible):
                slot["row"].hide()
                continue
            data = visible[index]
            slot["row"].show()
            slot["name"].setText(
                slot["name"].fontMetrics().elidedText(
                    data["name"], Qt.ElideRight, max(40, slot["name"].width() - 2)
                )
            )
            slot["name"].setToolTip(data["name"])
            slot["bar"].setValue(max(0, min(1000, (data.get("pct") or 0) * 10)))
            slot["bar"].setStyleSheet(_bar_qss(data.get("tone", "muted")))
            state = data.get("status", "")
            if slot["tip"] != state:
                slot["tip"] = state
                slot["state"].setText(state)
                slot["state"].setStyleSheet(
                    f"color:{_tone_color(data.get('tone', 'muted'))};font-size:10px;"
                )

        extra = len(rows) - MAX_ROWS
        if extra > 0:
            self.more.setText(f"另有 {extra} 首，完整队列见标准窗口")
            self.more.show()
        else:
            self.more.hide()

        host = self.parentWidget()
        width = (host.width() - 16) if host is not None else 360
        height = min(len(rows), MAX_ROWS) * ROW_H + 12 + (16 if extra > 0 else 0)
        self.setFixedSize(max(240, width), height)

    def hideEvent(self, event) -> None:  # noqa: N802
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # 点停靠条本身由它的 clicked 处理，这里只判「点到外面了」
        if event.type() == QEvent.MouseButtonPress:
            target = QApplication.widgetAt(event.globalPos())
            inside = target is not None and (
                target is self or self.isAncestorOf(target) or target is self._anchor
            )
            if not inside:
                self.hide()
                anchor = self._anchor
                if isinstance(anchor, MiniQueueDock):
                    anchor.set_open(False)
        return False


def _tone_color(tone: str) -> str:
    return {
        "ok": "#4caf7d",
        "fail": "#e8554a",
        "warn": "#d9a13b",
    }.get(tone, "#96969e")


def _bar_qss(tone: str) -> str:
    color = _tone_color(tone) if tone != "muted" else "#55555f"
    return (
        "QProgressBar{background:#2e2e34;border:none;border-radius:2px;}"
        f"QProgressBar::chunk{{background:{color};border-radius:2px;}}"
    )
