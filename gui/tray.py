"""托盘图标与媒体键。

媒体键走 Win32 的 RegisterHotKey：Qt 本身不暴露全局热键，而这里只需要
播放/上一首/下一首三个，够用且不引额外依赖。

注册可能失败（别的播放器正占着媒体键），失败就静默放弃——托盘菜单里
照样能控制播放。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt5.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QMenu, QSystemTrayIcon

WM_HOTKEY = 0x0312
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3

_HOTKEY_ACTIONS = {
    1: "play_pause",
    2: "previous",
    3: "next",
}


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if event_type == b"windows_generic_MSG":
            try:
                msg = wintypes.MSG.from_address(int(message))
            except (TypeError, ValueError):
                return False, 0
            if msg.message == WM_HOTKEY:
                self._callback(int(msg.wParam))
        return False, 0


class Tray(QObject):
    """托盘图标 + 媒体键。信号由主窗口接到播放器上。"""

    play_pause = pyqtSignal()
    previous = pyqtSignal()
    next = pyqtSignal()
    show_window = pyqtSignal()
    console = pyqtSignal()
    quit_app = pyqtSignal()

    def __init__(self, window, icon: QIcon):
        super().__init__(window)
        self._window = window
        self._registered: list[int] = []
        self._last_error = 0
        self.available = QSystemTrayIcon.isSystemTrayAvailable()

        self.icon = QSystemTrayIcon(icon, window)
        self.icon.setToolTip("网易云音乐下载器")
        if self.available:
            self.icon.setContextMenu(self._build_menu())
            self.icon.activated.connect(self._on_activated)
            self.icon.show()

        self._filter = _HotkeyFilter(self._on_hotkey)
        from PyQt5.QtWidgets import QApplication

        QApplication.instance().installNativeEventFilter(self._filter)
        self._register_hotkeys()

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.addAction("显示主窗口", self.show_window.emit)
        menu.addSeparator()
        menu.addAction("播放 / 暂停", self.play_pause.emit)
        menu.addAction("上一首", self.previous.emit)
        menu.addAction("下一首", self.next.emit)
        menu.addSeparator()
        menu.addAction("命令行…", self.console.emit)
        menu.addAction("退出", self.quit_app.emit)
        return menu

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window.emit()

    def _register_hotkeys(self) -> None:
        hwnd = int(self._window.winId())
        # 必须用 WinDLL + argtypes：默认 restype 是 c_int，64 位下 HWND 会被截断，
        # 而且拿不到 GetLastError，失败时只能看到 error 0
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.RegisterHotKey.restype = wintypes.BOOL
        for hotkey_id, vk in (
            (1, VK_MEDIA_PLAY_PAUSE),
            (2, VK_MEDIA_PREV_TRACK),
            (3, VK_MEDIA_NEXT_TRACK),
        ):
            # MOD_NOREPEAT = 0x4000，按住不放时只触发一次
            if user32.RegisterHotKey(wintypes.HWND(hwnd), hotkey_id, 0x4000, vk):
                self._registered.append(hotkey_id)
            else:
                # 1409 = 已被占用，通常是网易云客户端开着，它自己也占了媒体键
                self._last_error = ctypes.get_last_error()

    def _on_hotkey(self, hotkey_id: int) -> None:
        action = _HOTKEY_ACTIONS.get(hotkey_id)
        if action == "play_pause":
            self.play_pause.emit()
        elif action == "previous":
            self.previous.emit()
        elif action == "next":
            self.next.emit()

    def set_now_playing(self, text: str) -> None:
        """托盘悬停提示带上当前曲目，最小化时也能确认在放什么。"""
        if not self.available:
            return
        self.icon.setToolTip(
            f"网易云音乐下载器\n{text}" if text else "网易云音乐下载器"
        )

    def notify(self, title: str, message: str) -> None:
        if self.available:
            self.icon.showMessage(title, message, QSystemTrayIcon.Information, 4000)

    def detach(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = int(self._window.winId())
        for hotkey_id in self._registered:
            user32.UnregisterHotKey(wintypes.HWND(hwnd), hotkey_id)
        self._registered.clear()
        self.icon.hide()

    @property
    def hotkey_hint(self) -> str:
        """给设置页用的一句话说明。"""
        if self._registered:
            return "媒体键已接管"
        if self._last_error == 1409:
            return "媒体键被其它播放器占用"
        return "媒体键不可用"
