"""音频转码：m4a / flac 等转成 mp3。

依赖系统 ffmpeg（PATH 里能找到即可）。
注意：转码不会提升音质，128k 的 AAC 转成 320k 的 mp3 只是文件变大。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class TranscodeError(RuntimeError):
    pass


# mp3 的常用码率阶梯，转码时取「比源略高一档」以补偿 AAC 的编码效率
_LADDER = (64, 96, 112, 128, 160, 192, 224, 256, 320)


def _auto_bitrate(src_br: int) -> str:
    """AAC 编码效率比 MP3 高约三成，直接按源码率转会二次损失，这里上浮后取档。"""
    if not src_br:
        return "192k"
    kbps = int(src_br / 1000 * 1.35)
    for value in _LADDER:
        if kbps <= value:
            return f"{value}k"
    return "320k"


def _bundled_ffmpeg() -> str | None:
    """找 exe 同级的 ffmpeg 目录。

    文件夹版把 ffmpeg 摆在明处（`<程序目录>\\ffmpeg\\binaries\\ffmpeg*.exe`），
    用户能直接看见、替换、删除；单文件版则退回包内自带的那份。
    """
    import sys

    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else None
    if root is None:
        return None
    for candidate in (
        root / "ffmpeg" / "binaries",
        root / "ffmpeg",
    ):
        if not candidate.is_dir():
            continue
        for path in sorted(candidate.glob("ffmpeg*.exe")):
            return str(path)
    return None


def ffmpeg_path() -> str | None:
    # 顺序：程序自带 → PATH → Python 包自带
    bundled = _bundled_ffmpeg()
    if bundled:
        return bundled
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def to_mp3(src: Path, bitrate: str = "auto", keep_source: bool = False) -> Path:
    """把 src 转成同名 .mp3，返回新路径。失败抛 TranscodeError。

    bitrate 默认 auto：按源码率取略高一档，避免把 128k 硬转成 320k 白白放大体积。
    """
    if src.suffix.lower() == ".mp3":
        return src

    exe = ffmpeg_path()
    if not exe:
        raise TranscodeError("未找到 ffmpeg，请先安装并加入 PATH")

    if not bitrate or bitrate == "auto":
        bitrate = _auto_bitrate(probe_bitrate(src))

    dst = src.with_suffix(".mp3")
    if dst.exists():
        dst.unlink()
    cmd = [
        exe,
        "-y",
        "-loglevel", "error",
        "-i", str(src),
        "-vn",
        "-codec:a", "libmp3lame",
        "-b:a", bitrate,
        str(dst),
    ]
    # timeout：坏输入可能让 ffmpeg 挂住，卡死线程池的一个槽位；
    # CREATE_NO_WINDOW：GUI（pythonw）下每个 ffmpeg 会弹黑控制台
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        raise TranscodeError("ffmpeg 超时（10 分钟），已中止")
    if proc.returncode != 0 or not dst.exists():
        detail = (proc.stderr or "").strip().splitlines()
        dst.unlink(missing_ok=True)  # 别留半截 mp3 占空间/被误当成品
        raise TranscodeError(detail[-1] if detail else "ffmpeg 执行失败")

    if not keep_source:
        src.unlink(missing_ok=True)
    return dst


def probe_bitrate(path: Path) -> int:
    """读音频码率（bps），失败返回 0。"""
    try:
        from mutagen import File

        audio = File(path)
        return int(getattr(audio.info, "bitrate", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0
