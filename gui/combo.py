"""下拉选择器：与搜索历史浮层保持同一套外观。

QSS 无法控制下拉项的行高，用委托给每项 28px；弹窗窗口再设成透明，
否则圆角外会被默认白底/黑底填成直角。
"""
from __future__ import annotations

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import QComboBox, QStyledItemDelegate

ROW_H = 30


class _RowDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        size = super().sizeHint(option, index)
        return QSize(size.width() + 18, ROW_H)


def style_combo(combo: QComboBox) -> None:
    combo.setItemDelegate(_RowDelegate(combo))
    popup = combo.view().window()
    # 系统会给弹窗画一圈方形投影，圆角之外会露出深色三角，必须关掉；
    # 同时保持半透明，圆角才能透出背后的界面
    popup.setWindowFlags(
        Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
    )
    popup.setAttribute(Qt.WA_TranslucentBackground)
