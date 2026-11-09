"""PyQt5 图形界面。"""
import os

# Qt5 的 Windows 多媒体后端默认是 DirectShow，它没有 AAC / FLAC 解码器：
# 缓存解出来是 m4a、无损是 flac，默认后端下必然「播放失败」，只有 mp3/wav 能放。
# Media Foundation（插件键 "windows"）走系统自带解码器，四种格式实测全通。
# 放在包入口：run_gui.py、测试脚本、任何 import gui.* 的入口都会先生效。
os.environ.setdefault("QT_MULTIMEDIA_PREFERRED_PLUGINS", "windows,dsengine")

# 版本号只在 ncm/__init__.py 定义，这里转发，避免两处写死后对不上
from ncm import __version__

__all__ = ["__version__"]
