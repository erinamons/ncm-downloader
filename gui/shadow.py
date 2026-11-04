"""窗口阴影：先生成一张高斯模糊位图，绘制时直接贴图。

早前用多层圆角矩形叠加模拟阴影，在四角会叠成一块近黑的楔形；真正做一次
模糊既均匀又便宜——只在窗口尺寸变化时重算，平时只贴图。
"""
from __future__ import annotations

from PIL import Image, ImageFilter
from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QPainterPath
from PyQt5.QtGui import QColor, QImage, QPainter, QPixmap

_cache: dict[tuple[int, int, int, int], QPixmap] = {}
_MAX_CACHE = 6


def render_shadow(
    width: int,
    height: int,
    margin: int,
    radius: int,
    strength: float = 0.30,
    offset_y: int = 3,
) -> QPixmap:
    """生成与窗口同尺寸的阴影位图，中间（窗口本体所在处）不透明区域会被窗口盖住。"""
    if width <= 0 or height <= 0:
        return QPixmap()

    key = (width, height, margin, radius, offset_y)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    image = QImage(width, height, QImage.Format_RGBA8888)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 0, 0, 255))
    painter.drawRoundedRect(
        QRectF(
            margin,
            margin + offset_y,
            width - margin * 2,
            height - margin * 2,
        ),
        radius,
        radius,
    )
    painter.end()

    ptr = image.bits()
    ptr.setsize(image.byteCount())
    mask = Image.frombytes("RGBA", (width, height), bytes(ptr)).getchannel("A")
    mask = mask.filter(ImageFilter.GaussianBlur(margin * 0.55))
    mask = mask.point(lambda v: int(v * strength))

    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    shadow.putalpha(mask)
    result = QImage(shadow.tobytes(), width, height, QImage.Format_RGBA8888).copy()
    pixmap = QPixmap.fromImage(result)

    if len(_cache) >= _MAX_CACHE:
        _cache.pop(next(iter(_cache)))
    _cache[key] = pixmap
    return pixmap


def clip_outside_window(painter: QPainter, rect, margin: int, radius: int) -> None:
    """把绘制区域限制在窗口圆角轮廓之外。

    阴影位图在窗口所在的矩形里也是不透明的，若直接整张贴上去，圆角切掉的
    那四个小三角就会露出阴影本体，看起来像一块黑楔。裁掉轮廓内部即可。
    """
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    path.addRect(QRectF(rect))
    inner = QRectF(rect).adjusted(margin, margin, -margin, -margin)
    path.addRoundedRect(inner, radius, radius)
    painter.setClipPath(path)
