"""顶部播放条与播放引擎。

条内是单行 46px：图标 / 上一首·播放·下一首 / 封面 / 曲名跑马灯 / 时间 /
音量 / 循环模式 / 窗口按钮，底部 3px 是进度线。
"""
from __future__ import annotations

import os
import logging
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
from ncm.trace import trace

BAR_H = 46
ROW_H = 43
TEMP_KEEP = 24 * 3600
log = logging.getLogger("ncm.player")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

PLAYING = "playing"
PAUSED = "paused"
LOADING = "loading"
STOPPED = "stopped"
# 已起播但数据未就绪（联网补齐下载完后的加载、网络流缓冲）。
# QMediaPlayer 的 state 只代表「用户意图」，play() 一调就是 Playing，
# 真正出声要看 mediaStatus 到没到 Loaded/Buffered——之前把两者混为一谈，
# 表现就是「状态栏写着正在播放，实际没声，像卡住了」
BUFFERING = "buffering"

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
    failed = pyqtSignal(str, str)       # 给用户看的文案, 类型(format/file/convert/network)
    notice = pyqtSignal(str)            # 给状态栏用的一句话提示
    # 工作线程准备好文件后经此信号封送回主线程：QMediaPlayer 只能在
    # 创建它的线程里 setMedia/play，跨线程直接调会随机崩溃
    _start_requested = pyqtSignal(str, int)  # 本地文件路径, token

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
        self._start_requested.connect(self._on_start_requested)

        # 和其他缓存一样放在程序目录：系统 TEMP 可能被别的软件改成无写权限的
        # 公共目录，那样网络播放会直接失败
        from ncm import cache as cache_mod

        self._temp = cache_mod.player_cache_dir()
        self._temp.mkdir(parents=True, exist_ok=True)
        self._clean_temp()
        # 这一轮是否用了本地文件、是否已经试过「本地坏了改联网」
        self._used_local = False
        self._net_fallback = False
        # 兼容播放：系统放不了的格式先用 ffmpeg 转成 WAV 再播。
        # 默认关闭，由设置项控制；关闭时失败只提示，不擅自转换
        self.compat_play = False
        # 本进程里已经确认放不了的扩展名，下次直接转换，不再先失败一次
        self._bad_formats: set[str] = set()
        # 当前交给播放器的文件，失败时据此判断格式
        self._current_path: Path | None = None
        # 结束信号的瞬间 position 有时会被清零，用它兜一下
        self._last_pos = 0
        # 数据就绪度：对外状态（正在播放/缓冲中）由它和 state 共同决定
        self._media_status = QMediaPlayer.NoMedia

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
        self._current_path = None
        self.started.emit(song)
        self.state_changed.emit(LOADING)
        threading.Thread(
            target=self._prepare, args=(song, token, force_remote), daemon=True
        ).start()

    def play_local(self, song: Song, path: Path) -> None:
        """队列里已有落盘文件时直接播，不再走取地址。"""
        self._token += 1
        self._song = song
        # 和 play_song 一样重置回退状态：上一首残留的 _last_pos 会让
        # 「提前结束」检测虚高，_net_fallback 残留会吞掉该有的网络回退
        # 这一轮播的确实是磁盘上的文件，_used_local 要为真：
        # 文件被截断时才能按「播到一半就结束」改走网络，状态页也才显示得对
        self._used_local = True
        self._net_fallback = False
        self._last_pos = 0
        self._current_path = None
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
        self._current_path = None
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
            if path is not None:
                trace("播放", "选到文件", "ok",
                      detail={"src": "本地" if self._used_local else "网络", "file": path.name})
            if path is not None and self.compat_play:
                # 这种格式本进程里已经确认放不了：直接转换，不再先失败一次
                path = self._maybe_convert(path, song.id, token) or path
            if token != self._token:
                return
            if path is None:
                self._safe(self.failed, "拿不到可播放的音频", "network")
                self._safe(self.state_changed, STOPPED)
                return
            # 不直接碰 QMediaPlayer：emit 让主线程去 setMedia/play
            self._safe(self._start_requested, str(path), token)
        except Exception as exc:  # noqa: BLE001
            if token == self._token:
                log.warning("准备播放失败：%s", exc)
                self._safe(self.failed, "拿不到可播放的音频", "network")
                self._safe(self.state_changed, STOPPED)

    def _on_start_requested(self, path: str, token: int) -> None:
        """主线程槽：token 仍有效才真正起播（期间可能已经切歌/停止）。"""
        if token != self._token:
            return
        self._start_file(Path(path))

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
            # remote_provider 只给 (url, ext)，没有 size；总长从响应头拿，
            # 缺失（chunked 传输）时走按字节数报进度的分支
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            written = 0
            last_milestone = 0
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(64 * 1024):
                    if token != self._token:
                        fh.close()
                        part.unlink(missing_ok=True)
                        return None
                    fh.write(chunk)
                    # 下载期间给状态栏一个进度感：整首要下几十秒时
                    # 「没有反馈」看起来就像卡死
                    written += len(chunk)
                    if total:
                        pct = int(written / total * 100)
                        if pct >= last_milestone + 5 and pct < 100:
                            last_milestone = pct
                            self._safe(self.notice, f"正在获取音频 {pct}%")
                    elif written // 524288 > last_milestone:
                        last_milestone = written // 524288  # 每 512KB 报一次
                        self._safe(
                            self.notice,
                            f"正在获取音频 {written / 1048576:.1f} MB",
                        )
        os.replace(part, dest)
        return dest

    # ---------------- 兼容播放（系统放不了的格式 → WAV） ----------------

    def _maybe_convert(self, path: Path, song_id: int, token: int) -> Path | None:
        """已知放不了的格式直接转 WAV；不需要转换或失败时返回 None。

        在准备阶段调用（工作线程），避免用户每次都要先失败一次再转。
        """
        ext = path.suffix.lower()
        if ext in (".mp3", ".wav") or ext not in self._bad_formats:
            return None
        dst = self._temp / f"{song_id or path.stem}.pcm.wav"
        try:
            if (
                dst.exists()
                and dst.stat().st_size > 0
                and dst.stat().st_mtime >= path.stat().st_mtime
            ):
                return dst
        except OSError:
            pass
        self._safe(self.notice, "正在转换播放格式，首次需要几秒…")
        try:
            from ncm import transcode

            transcode.to_wav(path, dst)
        except Exception as exc:  # noqa: BLE001
            log.warning("兼容播放转换失败 %s：%s", path.name, exc)
            return None
        if token != self._token:
            return None
        self._prune_converted(dst)
        return dst

    def _convert_then_play(self, path: Path, song_id: int, token: int) -> None:
        """播放失败后的补救：转成 WAV 再交给播放器。"""
        dst = self._temp / f"{song_id or path.stem}.pcm.wav"
        started = time.perf_counter()
        try:
            from ncm import transcode

            transcode.to_wav(path, dst)
        except Exception as exc:  # noqa: BLE001
            log.warning("兼容播放转换失败 %s：%s", path.name, exc)
            trace("播放", "兼容转换", "fail", (time.perf_counter() - started) * 1000,
                  {"file": path.name, "error": type(exc).__name__})
            if token == self._token:
                self._safe(self.failed, "转换失败", "convert")
                self._safe(self.state_changed, STOPPED)
            return
        trace("播放", "兼容转换", "ok", (time.perf_counter() - started) * 1000,
              {"file": path.name, "out": dst.name})
        if token != self._token:
            return
        self._prune_converted(dst)
        self._safe(self._start_requested, str(dst), token)

    def _prune_converted(self, keep: Path, limit_mb: int = 1200) -> None:
        """转出来的 WAV 很占地方（约 10 MB/分钟），超上限按最旧的删。"""
        limit = limit_mb * 1048576
        items: list[tuple[float, int, Path]] = []
        try:
            total = keep.stat().st_size if keep.exists() else 0
        except OSError:
            total = 0
        for path in self._temp.glob("*.pcm.wav"):
            if path == keep:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size
        if total <= limit:
            return
        for _, size, path in sorted(items):
            if total <= limit:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                continue

    def _start_file(self, path: Path) -> None:
        self._current_path = Path(path)
        trace("播放", "起播", "run", detail={"file": path.name, "size": path.stat().st_size if path.exists() else 0})
        self._player.setMedia(QMediaContent(QUrl.fromLocalFile(str(path))))
        self._player.play()
        if self._song is not None and self._song.cover_url:
            token = self._token
            threading.Thread(
                target=self._load_cover, args=(self._song.cover_url, token), daemon=True
            ).start()

    def _load_cover(self, url: str, token: int = 0) -> None:
        try:
            resp = requests.get(
                url + "?param=120y120",
                headers={"User-Agent": _UA, "Referer": "https://music.163.com/"},
                timeout=8,
            )
            # 快速切歌时旧请求可能后返回，别拿上一首的封面盖掉这一首
            if token != self._token:
                return
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
        self._emit_state()

    def _on_media_status(self, status) -> None:
        self._media_status = status
        if status == QMediaPlayer.InvalidMedia:
            # 数据坏掉：error 信号随后必到，失败路径由 _on_error 负责
            return
        if status == QMediaPlayer.EndOfMedia:
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
            return
        self._emit_state()

    # 对外状态以 mediaStatus（数据就绪度）为准：
    # 只有 Playing 且媒体已 Loaded/Buffered 才算真正在播
    _READY_STATUS = (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia)

    def _emit_state(self) -> None:
        state = self._player.state()
        if state == QMediaPlayer.PlayingState:
            if self._media_status in self._READY_STATUS:
                self.state_changed.emit(PLAYING)
            else:
                self.state_changed.emit(BUFFERING)
        elif state == QMediaPlayer.PausedState:
            self.state_changed.emit(PAUSED)

    def _on_error(self, *_args) -> None:
        raw = self._player.errorString().strip()
        path = self._current_path
        ext = path.suffix.lower() if path is not None else ""
        try:
            size = path.stat().st_size if path is not None and path.exists() else 0
        except OSError:
            size = 0
        # 技术细节只进日志：用户看到的是上层按类型给的文案
        log.warning(
            "播放失败 backend=%s format=%s err=%s errstr=%r file=%s size=%s compat=%s",
            os.environ.get("QT_MULTIMEDIA_PREFERRED_PLUGINS", ""),
            ext.lstrip(".") or "-",
            int(self._player.error()),
            raw,
            path.name if path else "-",
            size or "-",
            self.compat_play,
        )
        self.state_changed.emit(STOPPED)
        trace("播放", "起播失败", "fail",
              detail={"format": ext.lstrip(".") or "-", "error": int(self._player.error()),
                      "file": path.name if path else "-"})

        if path is None:
            self.failed.emit(raw or "没能拿到可播放的音频", "network")
            return
        if ext in (".mp3", ".wav"):
            # 这两种系统本来就能放，失败说明文件坏了，转换也救不回来
            self.failed.emit(raw or "音频文件无法读取", "file")
            return
        self._bad_formats.add(ext)
        if not self.compat_play:
            # 开关关着就只提示，绝不擅自转码
            self.failed.emit(raw or "音频格式不支持", "format")
            return
        song = self._song
        token = self._token
        self.notice.emit("正在转换播放格式，首次需要几秒…")
        threading.Thread(
            target=self._convert_then_play,
            args=(path, song.id if song is not None else 0, token),
            daemon=True,
        ).start()


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
        # 缓冲中也显示暂停键：起播意图已发生，点一下就是暂停
        playing = self._state in (PLAYING, BUFFERING)
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
