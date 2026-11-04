"""生成应用图标：红色圆角底 + 白色音符与下载箭头。

输出 gui/assets/app.png（256）与 app.ico（多尺寸）。图形沿用界面里的
Lucide 图标，保证和顶部播放条、按钮的视觉语言一致。

用法：python tools/make_icon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QByteArray, QRectF, Qt
from PyQt5.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
)
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QApplication

ASSETS = ROOT / "gui" / "assets"
ICON_DIR = ASSETS / "icons"
SIZE = 256
RADIUS = 58

# 与界面主色一致，上浅下深做出一点体积感
TOP = QColor("#f2705f")
BOTTOM = QColor("#d8403a")


def _svg(name: str, color: str) -> QSvgRenderer:
    text = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    return QSvgRenderer(QByteArray(text.replace("currentColor", color).encode("utf-8")))


def render(size: int = SIZE) -> QImage:
    scale = size / SIZE
    image = QImage(size, size, QImage.Format_RGBA8888)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.scale(scale, scale)

    # 底板：圆角方形 + 竖向渐变
    plate = QPainterPath()
    plate.addRoundedRect(QRectF(6, 6, SIZE - 12, SIZE - 12), RADIUS, RADIUS)
    gradient = QLinearGradient(0, 0, 0, SIZE)
    gradient.setColorAt(0.0, TOP)
    gradient.setColorAt(1.0, BOTTOM)
    painter.fillPath(plate, gradient)

    # 主图形：音符居中偏上，箭头压在右下方，形成「音乐 + 下载」的组合
    music = _svg("music", "#ffffff")
    music.render(painter, QRectF(52, 50, 112, 112))

    # 反色徽章：白圆 + 红箭头，既和音符分开，小尺寸下也认得出是"下载"
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(QRectF(148, 144, 80, 80))
    arrow = _svg("download", BOTTOM.name())
    arrow.render(painter, QRectF(163, 159, 50, 50))

    painter.end()
    return image


def main() -> int:
    app = QApplication(sys.argv)  # QPainter 与字体渲染需要 GUI 环境
    ASSETS.mkdir(parents=True, exist_ok=True)

    big = render(SIZE)
    app.processEvents()
    png = ASSETS / "app.png"
    big.save(str(png))
    print("saved", png)

    # 各尺寸单独渲染再交给 Pillow 打包，比只缩一张 256 清晰
    from PIL import Image

    frames = {}
    for px in (16, 24, 32, 48, 64, 128, 256):
        img = render(px)
        buf = img.bits()
        buf.setsize(img.byteCount())
        # Format_RGBA8888 本身就是字节序 R,G,B,A，这里再按 BGRA 解会把红蓝互换
        frames[px] = Image.frombytes("RGBA", (px, px), bytes(buf))

    ico = ASSETS / "app.ico"
    frames[256].save(
        str(ico),
        format="ICO",
        sizes=[(px, px) for px in sorted(frames)],
        append_images=[frames[px] for px in sorted(frames) if px != 256],
    )
    print("saved", ico)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
