"""下拉选择器：与搜索历史浮层保持同一套外观。

QSS 无法控制下拉项的行高，用委托给每项 28px；弹窗窗口再设成透明，
否则圆角外会被默认白底/黑底填成直角。
"""
from __future__ import annotations

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QComboBox, QFrame, QStyledItemDelegate

from gui.style import PANEL

ROW_H = 30


class _RowDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width() + 18, ROW_H)


def style_combo(combo: QComboBox) -> None:
    """统一收拾下拉框：行高、浮层圆角、首帧底色。

    浮层容器在第一次 showPopup 之前就建好了（是 combo 的子控件），
    所以这里改窗口标志是安全的；等弹出来再改会把浮层闪一下。
    """
    combo.setItemDelegate(_RowDelegate(combo))
    view = combo.view()
    popup = view.window()
    # 还没建浮层时 window() 会返回主窗口，别把主窗口改成半透明
    if popup is combo or popup is combo.window() or not isinstance(popup, QFrame):
        return
    # 系统会给弹窗画一圈方形投影，圆角之外会露出深色三角，必须关掉；
    # 同时保持半透明，圆角才能透出背后的界面
    popup.setWindowFlags(
        Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
    )
    popup.setAttribute(Qt.WA_TranslucentBackground, True)
    popup.setAttribute(Qt.WA_NoSystemBackground, True)
    # 容器默认是系统浅色底，列表画出来之前会闪一帧浅灰；先压成面板色
    palette = popup.palette()
    palette.setColor(QPalette.Window, QColor(PANEL))
    popup.setPalette(palette)
    # 列表自己带圆角和 1px 边框，原生边框会让里面多一个直角
    view.setFrameShape(QFrame.NoFrame)
