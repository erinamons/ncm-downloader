"""顶部播放条与播放引擎。

条内是单行 46px：图标 / 上一首·播放·下一首 / 封面 / 曲名跑马灯 / 时间 /
音量 / 循环模式 / 窗口按钮，底部 3px 是进度线。
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

import requests
from PyQt5.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gui import icons
from gui.marquee import MarqueeText
from ncm.models import Song

BAR_H = 46
ROW_H = 43
TEMP_KEEP = 24 * 3600
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

PLAYING = "playing"
PAUSED = "paused"
LOADING = "loading"
STOPPED = "stopped"

REPEAT_MODES = ("list", "one", "off")
REPEAT_ICON = {"list": "repeat", "one": "repeat-1", "off": "repeat"}
REPEAT_TIP = {"list": "列表循环", "one": "单曲循环", "off": "顺序播放"}


def placeholder_cover(size: int = 24) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#34343a"))
    painter.drawRoundedRect(0, 0, size, size, 4, 4)
    note = icons.pixmap("music", "#9a9aa4", 13)
    if not note.isNull():
        side = note.width() / note.devicePixelRatio()
        offset = (size - side) / 2
        painter.drawPixmap(
            QRectF(offset, offset, side, side), note, QRectF(note.rect())
        )
    painter.end()
    return pixmap


class SeekBar(QWidget):
    """贴在播放条底边的细进度线，可拖动。"""

    seeked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._duration = 0
        self._value = 0
        self._hover = False

    def set_duration(self, ms: int) -> None:
        self._duration = max(0, ms)
        self.update()

    def set_value(self, ms: int) -> None:
        if self._duration <= 0:
            return
        self._value = max(0, min(ms, self._duration))
        self.update()

    def _ratio_at(self, x: float) -> float:
        return max(0.0, min(1.0, x / max(1, self.width())))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(Qt.NoPen)
        painter.setRenderHint(QPainter.Antialiasing, True)
        width = self.width()
        painter.fillRect(0, 0, width, self.height(), QColor("#25252b"))
        if self._duration <= 0:
            return
        ratio = self._value / self._duration
        painter.fillRect(0, 0, int(width * ratio), self.height(), QColor("#e8554a"))
        if self._hover:
            x = width * ratio
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(QPointF(x, 1.5), 3.5, 3.5)

    def mousePressEvent(self, event) -> None:
        if self._duration > 0 and event.button() == Qt.LeftButton:
            self.seeked.emit(int(self._ratio_at(event.x()) * self._duration))

    def mouseMoveEvent(self, event) -> None:
        if not self._hover:
            self._hover = True
            self.update()
        if event.buttons() & Qt.LeftButton and self._duration > 0:
            self.seeked.emit(int(self._ratio_at(event.x()) * self._duration))

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()


class PlayerEngine(QObject):
    """把「歌曲」变成「可播放的本地文件」再交给 QMediaPlayer。"""

    started = pyqtSignal(object)        # Song
    state_changed = pyqtSignal(str)
    position = pyqtSignal(int, int)     # 当前 ms, 总 ms
    cover_ready = pyqtSignal(bytes)
    finished = pyqtSignal()
    failed = pyqtSignal(str)
    notice = pyqtSignal(str)            # 给状态栏用的一句话提示

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.stateChanged.connect(self._on_state)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.error.connect(self._on_error)

        self._song: Song | None = None
        self._token = 0
        self._volume = 70
        self._player.setVolume(self._volume)

        self.local_provider: Callable[[Song], Path | None] | None = None
        self.remote_provider: Callable[[Song], tuple[str, str] | None] | None = None

        # 和其他缓存一样放在程序目录：系统 TEMP 可能被别的软件改成无写权限的
        # 公共目录，那样网络播放会直接失败
        from ncm import cache as cache_mod

        self._temp = cache_mod.player_cache_dir()
        self._temp.mkdir(parents=True, exist_ok=True)
        self._clean_temp()
        # 这一轮是否用了本地文件、是否已经试过「本地坏了改联网」
        self._used_local = False
        self._net_fallback = False
        # 结束信号的瞬间 position 有时会被清零，用它兜一下
        self._last_pos = 0

    # ---------------- 对外 ----------------

    @property
    def song(self) -> Song | None:
        return self._song

    def play_song(self, song: Song, force_remote: bool = False) -> None:
        """force_remote=True 用于「重试」：跳过本地文件，直接重新联网取。"""
        self._token += 1
        token = self._token
        self._song = song
        self._used_local = False
        self._net_fallback = False
        self._last_pos = 0
        self.started.emit(song)
        self.state_changed.emit(LOADING)
        threading.Thread(
            target=self._prepare, args=(song, token, force_remote), daemon=True
        ).start()

    def play_local(self, song: Song, path: Path) -> None:
        """队列里已有落盘文件时直接播，不再走取地址。"""
        self._token += 1
        self._song = song
        self.started.emit(song)
        self._start_file(Path(path))

    def toggle(self) -> None:
        if self._player.state() == QMediaPlayer.PlayingState:
            self._player.pause()
        elif self._song is not None:
            self._player.play()

    def stop(self) -> None:
        self._token += 1
        self._player.stop()
        self._player.setMedia(QMediaContent())
        self._song = None
        self.state_changed.emit(STOPPED)
        self.position.emit(0, 0)

    def seek(self, ms: int) -> None:
        if self._player.duration() > 0:
            self._player.setPosition(ms)

    def set_volume(self, value: int) -> None:
        self._volume = max(0, min(100, value))
        self._player.setVolume(self._volume)

    @property
    def volume(self) -> int:
        return self._volume

    # ---------------- 内部 ----------------

    def _safe(self, signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            pass  # 窗口已销毁，忽略

    def shutdown(self) -> None:
        """关窗前调用：停线程、断信号。"""
        self._token += 1
        try:
            self._player.stop()
            self._player.setMedia(QMediaContent())
        except RuntimeError:
            pass

    def _clean_temp(self) -> None:
        for path in self._temp.glob("*"):
            try:
                if time.time() - path.stat().st_mtime > TEMP_KEEP:
                    path.unlink(missing_ok=True)
            except OSError:
                pass

    def _prepare(self, song: Song, token: int, force_remote: bool = False) -> None:
        try:
            path = None
            # 重试时先绕开本地：播放失败往往就是这份本地文件本身解不开，
            # 再按同样的顺序走一遍，只会得到同样的错误
            if not force_remote:
                path = self.local_provider(song) if self.local_provider else None
                self._used_local = path is not None
            if path is None:
                path = self._download(song, token, fresh=force_remote)
            if path is None and force_remote and self.local_provider:
                # 网络也没取到，退回本地：残片总比什么都没有强
                path = self.local_provider(song)
                self._used_local = path is not None
            if token != self._token:
                return
            if path is None:
                self._safe(self.failed, "拿不到可播放的音频")
                self._safe(self.state_changed, STOPPED)
                return
            self._start_file(path)
        except Exception as exc:  # noqa: BLE001
            if token == self._token:
                self._safe(self.failed, str(exc))
                self._safe(self.state_changed, STOPPED)

    def _download(self, song: Song, token: int, fresh: bool = False) -> Path | None:
        if self.remote_provider is None:
            return None
        resolved = self.remote_provider(song)
        if not resolved:
            return None
        url, ext = resolved
        dest = self._temp / f"{song.id}.{ext or 'mp3'}"
        if fresh:
            # 上次缓存的临时文件可能就是解不开的那份，重试时丢掉重下
            dest.unlink(missing_ok=True)
            dest.with_name(dest.name + ".part").unlink(missing_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        part = dest.with_suffix(dest.suffix + ".part")
        with requests.get(
            url,
            headers={"User-Agent": _UA, "Referer": "https://music.163.com/"},
            stream=True,
            timeout=(10, 40),
        ) as resp:
            resp.raise_for_status()
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(64 * 1024):
                    if token != self._token:
                        fh.close()
                        part.unlink(missing_ok=True)
                        return None
                    fh.write(chunk)
        os.replace(part, dest)
        return dest

    def _start_file(self, path: Path) -> None:
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(str(path))))
        self._player.play()
        if self._song is not None and self._song.cover_url:
            threading.Thread(
                target=self._load_cover, args=(self._song.cover_url,), daemon=True
            ).start()

    def _load_cover(self, url: str) -> None:
        try:
            resp = requests.get(
                url + "?param=120y120",
                headers={"User-Agent": _UA, "Referer": "https://music.163.com/"},
                timeout=8,
            )
            if resp.ok and resp.content:
                self._safe(self.cover_ready, resp.content)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 播放器信号 ----------------

    def _on_position(self, pos: int) -> None:
        self._last_pos = pos
        self.position.emit(pos, self._player.duration())

    def _on_duration(self, dur: int) -> None:
        self.position.emit(self._player.position(), dur)

    def _on_state(self, state) -> None:
        if state == QMediaPlayer.PlayingState:
            self.state_changed.emit(PLAYING)
        elif state == QMediaPlayer.PausedState:
            self.state_changed.emit(PAUSED)

    def _on_media_status(self, status) -> None:
        if status != QMediaPlayer.EndOfMedia:
            return
        # 本地文件坏了（缓存没写完、下载中断）时，Qt 常常播几秒就当成「正常结束」，
        # 既不报错也不提示。按已播时长判断，明显不够就联网重放一次。
        expected = self._song.duration_ms if self._song else 0
        played = max(self._player.position(), self._last_pos)
        if (
            self._used_local
            and not self._net_fallback
            and expected
            and played < expected * 0.8
        ):
            self._net_fallback = True
            self._used_local = False
            self.notice.emit("本地文件不完整，已改用网络播放")
            song = self._song
            # 别在信号里直接重开播放器，等这次回调走完
            QTimer.singleShot(0, lambda: self.play_song(song, force_remote=True))
            return
        self.finished.emit()

    def _on_error(self, *_args) -> None:
        message = self._player.errorString() or "播放失败"
        self.failed.emit(message)
        self.state_changed.emit(STOPPED)


class TopBar(QWidget):
    """标题栏与播放条合并的一行。"""

    play_pause = pyqtSignal()
    prev = pyqtSignal()
    next = pyqtSignal()
    toggle_mini = pyqtSignal()
    stop_requested = pyqtSignal()
    seeked = pyqtSignal(int)
    volume_changed = pyqtSignal(int)
    repeat_changed = pyqtSignal(str)

    def __init__(self, window, title: str, parent=None):
        super().__init__(parent)
        self._win = window
        self._drag_from: QPoint | None = None
        self._repeat = "list"
        self._state = STOPPED
        self._compact = False
        self._muted = False
        self._last_volume = 70
        # 标题显隐按宽度阈值走，拖动时防抖，避免反复重排
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(120)
        self._layout_timer.timeout.connect(self._sync_title_visibility)

        self.setObjectName("topBar")
        self.setFixedHeight(BAR_H)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        row_widget = QWidget()
        row_widget.setObjectName("topBarRow")
        row_widget.setFixedHeight(ROW_H)
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(14, 0, 6, 0)
        row.setSpacing(6)
        outer.addWidget(row_widget)

        self._logo = QLabel()
        self._logo.setPixmap(icons.pixmap("music", "#e8554a", 14))
        self._logo.setFixedSize(14, 14)
        self._logo.setScaledContents(True)
        row.addWidget(self._logo)
        self._row = row

        self.name = QLabel(title)
        self.name.setObjectName("titleText")
        row.addWidget(self.name)
        row.addSpacing(4)

        self.btn_prev = self._icon_button("skip-back", "上一首", self.prev.emit, 22)
        self.btn_play = QPushButton()
        self.btn_play.setObjectName("playButton")
        self.btn_play.setFixedSize(26, 26)
        self.btn_play.setIconSize(QSize(12, 12))
        self.btn_play.setToolTip("播放 / 暂停（空格）")
        self.btn_play.setFocusPolicy(Qt.NoFocus)
        self.btn_play.clicked.connect(self.play_pause.emit)
        self.btn_next = self._icon_button("skip-forward", "下一首", self.next.emit, 22)
        # 停止单独给个按钮：以前只在右键菜单里，右键某一首看到「停止播放」
        # 很容易被理解成停这一首
        self.btn_stop = self._icon_button("stop", "停止播放", self.stop_requested.emit, 22)
        self.btn_stop.setEnabled(False)
        for w in (self.btn_prev, self.btn_play, self.btn_next, self.btn_stop):
            row.addWidget(w)

        row.addSpacing(6)
        self.cover = QLabel()
        self.cover.setObjectName("cover")
        self.cover.setFixedSize(24, 24)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setPixmap(placeholder_cover())
        row.addWidget(self.cover)

        self.text = MarqueeText()
        row.addWidget(self.text, 1)

        self.time = QLabel("")
        self.time.setObjectName("timeText")
        self.time.setFixedWidth(88)
        self.time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        font = QFont(self.time.font())
        font.setPixelSize(11)
        self.time.setFont(font)
        row.addWidget(self.time)

        row.addSpacing(4)
        self.btn_volume = self._icon_button("volume-2", "静音", self._toggle_mute, 18)
        row.addWidget(self.btn_volume)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setObjectName("volumeSlider")
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setFixedWidth(34)
        self.volume.setFocusPolicy(Qt.NoFocus)
        self.volume.valueChanged.connect(self._on_volume)
        row.addWidget(self.volume)

        self.btn_repeat = self._icon_button("repeat", REPEAT_TIP["list"], self._cycle_repeat, 18)
        row.addWidget(self.btn_repeat)

        row.addSpacing(8)
        self.btn_mini = self._icon_button("minimize-2", "紧凑模式（Ctrl+M）", self.toggle_mini.emit, 20)
        row.addWidget(self.btn_mini)

        self.btn_min = self._win_button("minus", "最小化", self._win.showMinimized)
        self.btn_max = self._win_button("square", "最大化", self._toggle_max)
        self.btn_close = self._win_button("x", "关闭", self._win.close, close=True)
        for w in (self.btn_min, self.btn_max, self.btn_close):
            row.addWidget(w)

        self.seek = SeekBar()
        self.seek.seeked.connect(self.seeked.emit)
        outer.addWidget(self.seek)

        self._sync_play_icon()

    # ---------------- 子控件工厂 ----------------

    def _icon_button(self, name: str, tip: str, slot, size: int) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("barButton")
        btn.setIcon(icons.stateful(name, "#a8a8b2", "#ffffff", size - 6))
        btn.setIconSize(QSize(size - 6, size - 6))
        btn.setFixedSize(26, 24)
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(slot)
        return btn

    def _win_button(self, name: str, tip: str, slot, close: bool = False) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("closeBtn" if close else "winBtn")
        btn.setIcon(icons.stateful(name, "#c0c0c8", "#ffffff", 13))
        btn.setIconSize(QSize(13, 13))
        btn.setFixedSize(28, 24)
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(slot)
        return btn

    # ---------------- 状态 ----------------

    def set_song(self, song: Song | None) -> None:
        if song is None:
            self.text.clear()
            self.cover.setPixmap(placeholder_cover())
            self.time.setText("")
            self.seek.set_duration(0)
            return
        sub = " · ".join(x for x in (song.artist_text, song.album) if x)
        self.text.set_text(song.name, sub)
        self.cover.setPixmap(placeholder_cover())
        self.seek.set_duration(0)
        self.time.setText("")

    def set_cover(self, data: bytes) -> None:
        pix = QPixmap()
        if pix.loadFromData(data):
            self.cover.setPixmap(
                pix.scaled(24, 24, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            )

    def set_state(self, state: str) -> None:
        self._state = state
        self._sync_play_icon()
        self.btn_stop.setEnabled(state != STOPPED)
        self.text.set_scrolling_allowed(state == PLAYING)

    def set_position(self, pos: int, duration: int) -> None:
        self.seek.set_duration(duration)
        self.seek.set_value(pos)
        self._refresh_time()

    def set_volume(self, value: int) -> None:
        self.volume.blockSignals(True)
        self.volume.setValue(value)
        self.volume.blockSignals(False)
        self._sync_volume_icon()

    def set_repeat(self, mode: str) -> None:
        self._repeat = mode if mode in REPEAT_MODES else "list"
        self.btn_repeat.setIcon(
            icons.stateful(REPEAT_ICON[self._repeat], "#a8a8b2", "#ffffff", 12)
        )
        self.btn_repeat.setToolTip(REPEAT_TIP[self._repeat])

    def _sync_play_icon(self) -> None:
        playing = self._state == PLAYING
        name = "pause" if playing else "play"
        self.btn_play.setIcon(icons.icon(name, "#ffffff", 14))
        self.btn_play.setToolTip("暂停（空格）" if playing else "播放（空格）")

    def _sync_volume_icon(self) -> None:
        name = "volume-x" if self.volume.value() == 0 else "volume-2"
        self.btn_volume.setIcon(icons.stateful(name, "#a8a8b2", "#ffffff", 12))
        self.btn_volume.setToolTip("取消静音" if self.volume.value() == 0 else "静音")

    def _on_volume(self, value: int) -> None:
        if value > 0:
            self._last_volume = value
        self._sync_volume_icon()
        self.volume_changed.emit(value)

    def _toggle_mute(self) -> None:
        if self.volume.value() > 0:
            self.volume.setValue(0)
        else:
            self.volume.setValue(self._last_volume or 70)

    def _cycle_repeat(self) -> None:
        index = (REPEAT_MODES.index(self._repeat) + 1) % len(REPEAT_MODES)
        self.set_repeat(REPEAT_MODES[index])
        self.repeat_changed.emit(self._repeat)

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self.btn_mini.setIcon(
            icons.stateful("maximize-2" if compact else "minimize-2", "#a8a8b2", "#ffffff", 14)
        )
        self.btn_mini.setToolTip("还原窗口（Ctrl+M）" if compact else "紧凑模式（Ctrl+M）")
        self._sync_title_visibility()
        # 400px 宽时每个像素都要省：藏掉 logo、收紧间距、曲名至少留 70px
        self._logo.setVisible(not compact)
        self._row.setSpacing(4 if compact else 6)
        self.text.setMinimumWidth(70 if compact else 90)
        self.btn_volume.setVisible(not compact)
        self.volume.setVisible(not compact)
        self.btn_repeat.setVisible(not compact)
        self.btn_max.setVisible(not compact)
        self.time.setFixedWidth(38 if compact else 88)
        self._refresh_time()

    def _refresh_time(self) -> None:
        duration = self.seek._duration
        pos = self.seek._value
        if duration <= 0:
            self.time.setText("")
        elif self._compact:
            self.time.setText(_clock(pos))
        else:
            self.time.setText(f"{_clock(pos)} / {_clock(duration)}")

    # ---------------- 窗口动作 ----------------

    def _toggle_max(self) -> None:
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()
        self.sync_window()

    def sync_window(self) -> None:
        maxed = self._win.isMaximized()
        self.btn_max.setIcon(
            icons.stateful("copy" if maxed else "square", "#c0c0c8", "#ffffff", 13)
        )
        self.btn_max.setToolTip("向下还原" if maxed else "最大化")

    # ---------------- 拖动 ----------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # 顶部边缘是缩放区，判定在窗口那边。这里先问一句，否则
            # startSystemMove 会抢在缩放之前接管，表现为「指针是拉伸，一拉却在移动窗口」
            win_pos = self.mapTo(self._win, event.pos())
            edges_at = getattr(self._win, "_edges_at", None)
            if edges_at is not None and edges_at(win_pos):
                event.ignore()
                return
            handle = self._win.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove"):
                if handle.startSystemMove():
                    event.accept()
                    return
            self._drag_from = event.globalPos() - self._win.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            if self._win.isMaximized():
                self._win.showNormal()
                self.sync_window()
                self._drag_from = QPoint(self._win.width() // 2, BAR_H // 2)
            self._win.move(event.globalPos() - self._drag_from)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # 紧凑模式下双击还原窗口，普通模式双击最大化
            if self._compact:
                self.toggle_mini.emit()
            else:
                self._toggle_max()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 窗口窄时先让位给播放控件，标题文字自动收起。
        # 拖动改宽度时每帧都判一次 940 阈值会让标题反复显隐、整条重排，
        # 看起来就是左右拉伸在闪——攒到停手后再切。
        self._layout_timer.start()

    def _sync_title_visibility(self) -> None:
        self.name.setVisible(not self._compact and self.width() >= 940)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        act_mini = menu.addAction("还原窗口" if self._compact else "紧凑模式")
        menu.addSeparator()
        act_close = menu.addAction("关闭")
        chosen = menu.exec_(event.globalPos())
        if chosen is act_mini:
            self.toggle_mini.emit()
        elif chosen is act_close:
            self._win.close()


def _clock(ms: int) -> str:
    seconds = max(0, ms // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"
