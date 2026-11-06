"""图标：把 Lucide 的线性 SVG 渲染成指定颜色的 QIcon。

Lucide 用 stroke="currentColor" 占位，这里替换成实际颜色再渲染，
所以同一套图标能按主题/状态换色，不需要准备多份资源。

按 2 倍像素渲染并设置 devicePixelRatio，高分屏不糊，普通屏缩小后
边缘也更干净。
"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QByteArray, Qt
from PyQt5.QtGui import QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer

ASSETS = Path(__file__).parent / "assets" / "icons"
_cache: dict[tuple[str, str, int], QPixmap] = {}
_SCALE = 2


def pixmap(name: str, color: str = "#d8d8dc", size: int = 20) -> QPixmap:
    """渲染成指定颜色和逻辑尺寸的位图。"""
    key = (name, color, size)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    path = ASSETS / f"{name}.svg"
    if not path.exists():
        return QPixmap()

    svg = path.read_text(encoding="utf-8").replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QPixmap()

    px = size * _SCALE
    canvas = QPixmap(px, px)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    canvas.setDevicePixelRatio(_SCALE)

    _cache[key] = canvas
    return canvas


def icon(name: str, color: str = "#d8d8dc", size: int = 20) -> QIcon:
    pm = pixmap(name, color, size)
    return QIcon(pm) if not pm.isNull() else QIcon()


def stateful(
    name: str,
    normal: str = "#c8c8d0",
    hover: str = "#ffffff",
    size: int = 15,
) -> QIcon:
    """带悬停/选中态的图标：普通一色，悬停或勾选（checked）提亮。

    checkable 按钮 checked 时 Qt 取 QIcon 的 On 状态；不补的话会回退到
    Normal/Off 的灰色，出现「红底上灰图标」的违和组合。
    """
    result = QIcon()
    result.addPixmap(pixmap(name, normal, size), QIcon.Normal, QIcon.Off)
    result.addPixmap(pixmap(name, hover, size), QIcon.Active, QIcon.Off)
    result.addPixmap(pixmap(name, hover, size), QIcon.Normal, QIcon.On)
    result.addPixmap(pixmap(name, hover, size), QIcon.Active, QIcon.On)
    return result
