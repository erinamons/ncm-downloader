"""命令行入口：ncm search / ncm dl / ncm url。"""
from __future__ import annotations

import os
import threading
import time
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from . import downloader as dl
from .backend import BackendError, LocalBackend
from .models import QUALITY_LABEL, QUALITY_ORDER, Song, normalize_level
from .store import DEFAULT_TEMPLATE, TEMPLATE_HELP, Store, render_template
from .targets import parse_target

app = typer.Typer(
    add_completion=False,
    help="网易云音乐下载工具",
    no_args_is_help=True,
)
console = Console()


def _backend(cookie: str = "", source: str = "client") -> LocalBackend:
    """未显式传 Cookie 时用本地保存的登录态；web 音源退回网页身份。"""
    from . import auth

    return LocalBackend(
        cookie=cookie or auth.load_cookie(), client_mode=(source != "web")
    )


def _probe_sources(cookie: str) -> None:
    """对比两种接口身份与本地缓存，告诉用户每种音源能拿到什么。"""
    free_id, vip_id = 501468000, 1945894789
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("音源", width=11, no_wrap=True)
    table.add_column("免费曲目", width=11, no_wrap=True)
    table.add_column("VIP 曲目", width=11, no_wrap=True)
    table.add_column("说明", style="dim")

    for label, mode, desc in (
        ("客户端接口", True, "伪装客户端，可用账号的客户端权益"),
        ("网页接口", False, "网页访客身份，VIP 曲目只有试听"),
    ):
        backend = LocalBackend(cookie=cookie, client_mode=mode)
        cells: list[str] = []
        for sid in (free_id, vip_id):
            try:
                src = backend.song_url(sid, "exhigh")
            except Exception:  # noqa: BLE001
                cells.append("失败")
                continue
            if not src.url:
                cells.append("无地址")
            elif src.trial:
                span = src.free_trial or {}
                span_len = int(span.get("end") or 0) - int(span.get("start") or 0)
                cells.append(f"试听 {span_len}s")
            else:
                cells.append(f"{src.br // 1000}k 完整")
        while len(cells) < 2:
            cells.append("-")
        table.add_row(label, cells[0], cells[1], desc)

    from . import cache as cm

    root = cm.default_cache_dir()
    cached = 0
    if root:
        cached = sum(1 for e in cm.scan(root) if e.complete and e.size > 200_000)
    table.add_row("自动", "缓存优先", "缓存优先", "命中缓存零请求，否则回退客户端接口")
    table.add_row(
        "仅本地缓存",
        f"{cached} 首" if cached else "无",
        "-",
        "只处理客户端缓存里已有的曲目",
    )
    console.print(table)
    console.print("[dim]转 mp3 加 --to-mp3，需要系统装有 ffmpeg[/dim]")


@app.command("login")
def cmd_login(
    timeout: int = typer.Option(180, "--timeout", help="等待扫码的秒数"),
    png: bool = typer.Option(False, "--png", help="同时把二维码存成图片"),
) -> None:
    """扫码登录，登录态保存在本地。"""
    from . import auth

    qr = auth.QrLogin()
    with console.status("申请二维码…"):
        try:
            qr.start()
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

    url = qr.url
    if png:
        try:
            import qrcode

            path = Path.cwd() / "login-qrcode.png"
            qrcode.make(url).save(path)
            console.print(f"[dim]二维码图片：{path}[/dim]")
        except Exception:  # noqa: BLE001
            pass

    console.print("用网易云音乐 App 扫码登录：\n")
    try:
        import qrcode

        box = qrcode.QRCode(border=1)
        box.add_data(url)
        box.make(fit=True)
        box.print_ascii(invert=True)
    except Exception:  # noqa: BLE001
        pass
    console.print(f"[dim]扫码地址：{url}[/dim]\n")

    deadline = time.time() + timeout
    last = -1
    ok = False
    while time.time() < deadline:
        try:
            code = qr.poll()
        except Exception:  # noqa: BLE001
            code = last
        if code != last:
            if code == auth.SCANNED:
                console.print("[cyan]已扫描，请在手机上点确认[/cyan]")
            elif code == auth.EXPIRED:
                console.print("[red]二维码已过期，请重新执行 login[/red]")
                break
            last = code
        if code == auth.CONFIRMED:
            ok = True
            break
        time.sleep(2)

    if not ok:
        console.print("[yellow]未完成登录[/yellow]")
        raise typer.Exit(1)

    cookie = auth.cookie_string(qr.session)
    if "MUSIC_U" not in cookie:
        console.print("[red]登录返回里没有 MUSIC_U，可能被风控[/red]")
        raise typer.Exit(1)

    account = auth.fetch_account(cookie)
    auth.save_login(cookie, account)
    console.print(
        f"\n[green]已登录[/green] {account.nickname or '(未取到昵称)'} "
        f"· {account.vip_text} · uid {account.user_id}"
    )
    console.print(f"[dim]登录态已保存到 {auth.config_path()}[/dim]")
    console.print("\n[dim]各音源实测…[/dim]")
    _probe_sources(cookie)


@app.command("logout")
def cmd_logout() -> None:
    """清除本地登录态。"""
    from . import auth

    if not auth.load_cookie():
        console.print("[dim]当前未登录[/dim]")
        raise typer.Exit()
    auth.clear_login()
    console.print("已清除本地登录态")


@app.command("whoami")
def cmd_whoami() -> None:
    """查看当前登录账号与音质权限。"""
    from . import auth

    cookie = auth.load_cookie()
    if not cookie:
        console.print("[yellow]尚未登录[/yellow]，执行 [bold]ncm login[/bold] 扫码")
        raise typer.Exit(1)

    account = auth.fetch_account(cookie)
    if not account.user_id:
        console.print("[red]登录态已失效[/red]，请重新执行 [bold]ncm login[/bold]")
        raise typer.Exit(1)

    console.print(
        f"[bold]{account.nickname}[/bold] · {account.vip_text} · uid {account.user_id}"
    )
    console.print()
    _probe_sources(cookie)


def _print_songs(songs: list[Song]) -> None:
    # 窄终端下丢掉专辑列，优先保证曲名、艺人、id 不被压缩
    wide = console.width >= 100
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("#", justify="right", width=3, style="dim", no_wrap=True)
    table.add_column(
        "曲名", overflow="ellipsis", max_width=40 if wide else 26, no_wrap=True
    )
    table.add_column(
        "艺人", overflow="ellipsis", max_width=18, style="cyan", no_wrap=True
    )
    if wide:
        table.add_column(
            "专辑", overflow="ellipsis", max_width=22, style="dim", no_wrap=True
        )
    table.add_column("时长", justify="right", width=5, style="dim", no_wrap=True)
    table.add_column("id", justify="right", width=10, style="dim", no_wrap=True)

    for i, song in enumerate(songs, 1):
        cells = [str(i), song.name, song.artist_text]
        if wide:
            cells.append(song.album)
        cells.extend([song.duration_text, str(song.id)])
        table.add_row(*cells)
    console.print(table)


def _add_unique(target: list[Song], seen: set[int], batch: list[Song]) -> None:
    for song in batch:
        if song.id not in seen:
            seen.add(song.id)
            target.append(song)


@app.command("search")
def cmd_search(
    keyword: str = typer.Argument(..., help="关键词"),
    limit: int = typer.Option(20, "--limit", "-n", help="返回条数"),
    cookie: str = typer.Option("", "--cookie", help="登录 Cookie（可提升音质）"),
) -> None:
    """按关键词搜索歌曲。"""
    backend = _backend(cookie)
    with console.status("搜索中…"):
        songs = backend.search(keyword, limit=limit)
    if not songs:
        console.print("[yellow]没有找到结果[/yellow]")
        raise typer.Exit(1)
    _print_songs(songs)
    console.print(
        f"[dim]共 {len(songs)} 条。下载单曲：ncm dl <id>[/dim]", highlight=False
    )


@app.command("url")
def cmd_url(
    target: str = typer.Argument(..., help="歌曲链接或 id"),
    level: str = typer.Option("exhigh", "--level", "-q", help="standard/higher/exhigh/lossless/hires"),
    cookie: str = typer.Option("", "--cookie"),
) -> None:
    """只解析播放地址，供排查用。"""
    _, song_id = parse_target(target)
    backend = _backend(cookie)
    level = normalize_level(level)
    src = backend.song_url(song_id, level)
    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column(style="dim", width=12)
    table.add_column()
    table.add_row("url", src.url or "[red]空[/red]")
    table.add_row("实际码率", f"{src.br} ({src.br // 1000}k)")
    table.add_row("容器", src.ext or "-")
    table.add_row("大小", f"{src.size / 1048576:.2f} MB" if src.size else "-")
    table.add_row("音质判定", src.quality_label + ("（已降级）" if src.degraded else ""))
    table.add_row("试听片段", "是" if src.trial else "否")
    table.add_row("有效期", f"{src.expi}s")
    console.print(table)


@app.command("dl")
def cmd_download(
    targets: list[str] = typer.Argument(..., help="歌曲/歌单链接或 id，可多个"),
    out: Path = typer.Option(Path("downloads"), "--out", "-o", help="输出目录"),
    level: str = typer.Option("exhigh", "--level", "-q", help="目标音质"),
    template: str = typer.Option(DEFAULT_TEMPLATE, "--template", "-t", help="命名模板，--template-help 查看变量"),
    workers: int = typer.Option(3, "--workers", "-w", help="并发数，建议不超过 5"),
    limit: int = typer.Option(0, "--limit", "-n", help="只下前 N 首，0 为全部"),
    cookie: str = typer.Option("", "--cookie", help="登录 Cookie，可解锁更高音质"),
    no_cover: bool = typer.Option(False, "--no-cover", help="不写入封面"),
    no_lyric: bool = typer.Option(False, "--no-lyric", help="不写入歌词"),
    force: bool = typer.Option(False, "--force", "-f", help="忽略已下载记录，强制重下"),
    search_mode: bool = typer.Option(
        False, "--search", "-s", help="把参数当作关键词，搜索后批量下载"
    ),
    source: str = typer.Option(
        "auto", "--source", help="音源：auto(缓存优先) / client / web / cache"
    ),
    to_mp3: bool = typer.Option(
        False, "--to-mp3", help="下载后把 m4a/flac 转成 mp3（需要 ffmpeg）"
    ),
    mp3_bitrate: str = typer.Option("auto", "--mp3-bitrate", help="转 mp3 码率，auto 表示跟随源码率"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只列出待下载曲目"),
    template_help: bool = typer.Option(False, "--template-help", help="打印命名模板变量说明"),
) -> None:
    """下载歌曲或整个歌单。"""
    if template_help:
        console.print(TEMPLATE_HELP)
        raise typer.Exit()

    level = normalize_level(level)
    source = (source or "auto").lower()
    if source not in dl.SOURCES:
        console.print(f"[red]未知音源 {source}[/red]，可选：{' / '.join(dl.SOURCES)}")
        raise typer.Exit(2)
    backend = _backend(cookie, source)
    songs: list[Song] = []
    seen: set[int] = set()

    for raw in targets:
        if search_mode:
            with console.status(f"搜索「{raw}」…"):
                found = backend.search(raw, limit=limit or 20)
            console.print(f"[dim]「{raw}」命中 {len(found)} 首[/dim]")
            for song in found:
                if song.id not in seen:
                    seen.add(song.id)
                    songs.append(song)
            continue

        try:
            kind, ident = parse_target(raw)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc

        if kind == "song":
            with console.status("读取歌曲信息…"):
                song = backend.song_detail(ident)
            if song is not None:
                _add_unique(songs, seen, [song])
                continue
            # 纯数字无法从链接判断类型，单曲取不到时按歌单再试一次
            with console.status(f"按歌单读取 {ident}…"):
                try:
                    playlist = backend.playlist(ident)
                except BackendError:
                    console.print(f"[red]{ident} 既不是歌曲也不是歌单[/red]")
                    continue
            console.print(
                f"[bold]{playlist.name}[/bold] "
                f"[dim]{playlist.creator} · {playlist.track_count} 首[/dim]"
            )
            _add_unique(songs, seen, playlist.songs)
        else:
            with console.status(f"读取歌单 {ident}…"):
                try:
                    playlist = backend.playlist(ident)
                except BackendError as exc:
                    console.print(f"[red]{exc}[/red]")
                    continue
            console.print(
                f"[bold]{playlist.name}[/bold] "
                f"[dim]{playlist.creator} · {playlist.track_count} 首[/dim]"
            )
            _add_unique(songs, seen, playlist.songs)

    if not songs:
        console.print("[yellow]没有可下载的曲目[/yellow]")
        raise typer.Exit(1)

    if limit > 0 and not search_mode:
        songs = songs[:limit]

    console.print(
        f"目标 [bold]{len(songs)}[/bold] 首 · 音质 "
        f"[bold]{QUALITY_LABEL[level]}[/bold]({level}) · "
        f"音源 [bold]{dl.SOURCE_LABEL[source]}[/bold] · 并发 {workers}"
    )

    if dry_run:
        _print_songs(songs)
        raise typer.Exit()

    store = Store(out / ".ncm-index.db")
    engine = dl.Downloader(
        backend=backend,
        store=store,
        out_dir=out,
        template=template,
        level=level,
        workers=workers,
        with_cover=not no_cover,
        with_lyric=not no_lyric,
        force=force,
        source=source,
        to_mp3=to_mp3,
        mp3_bitrate=mp3_bitrate,
    )

    lock = threading.Lock()
    progress = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("{task.description}", justify="left"),
        BarColumn(bar_width=None),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
        transient=True,
    )
    tasks: dict[int, int] = {}
    finished = [0]

    with progress:

        def on_start(song: Song) -> None:
            with lock:
                tasks[song.id] = progress.add_task(
                    song.name[:26], total=None, start=False
                )

        def on_progress(song: Song, done: int, total: int) -> None:
            with lock:
                tid = tasks.get(song.id)
                if tid is None:
                    return
                progress.update(tid, completed=done, total=total or None)

        def on_result(res: dl.TaskResult) -> None:
            with lock:
                tid = tasks.get(res.song.id)
                if tid is not None:
                    progress.update(tid, visible=False)
                finished[0] += 1

                color = {
                    dl.OK: "green",
                    dl.SKIPPED: "dim",
                    dl.NO_COPYRIGHT: "yellow",
                    dl.TRIAL: "yellow",
                    dl.CACHE_MISS: "yellow",
                    dl.FAILED: "red",
                }[res.status]
                suffix = f" [dim]{res.message}[/dim]" if res.message else ""
                line = (
                    f"[dim]{finished[0]:>3}/{len(songs)}[/dim] "
                    f"[{color}]{dl.STATUS_TEXT[res.status]:<4}[/{color}] "
                    f"{res.song.name} [dim]- {res.song.artist_text}[/dim]"
                )
                if res.br:
                    line += f" [dim]{res.br // 1000}k {res.quality}[/dim]"
                console.print(line + suffix, highlight=False)

        results = engine.run(
            songs, on_result=on_result, on_start=on_start, on_progress=on_progress
        )

    store.close()

    stats = Counter(r.status for r in results)
    total_bytes = sum(
        r.path.stat().st_size for r in results if r.ok and r.path and r.path.exists()
    )
    table = Table(box=None, show_header=False, pad_edge=False, title="汇总")
    table.add_column(style="dim", width=10)
    table.add_column(justify="right")
    for status, text in dl.STATUS_TEXT.items():
        table.add_row(text, str(stats.get(status, 0)))
    table.add_row("合计", f"{len(results)} 首 · {total_bytes / 1048576:.1f} MB")
    console.print(table)

    failed = stats.get(dl.FAILED, 0)
    if failed:
        console.print(f"[red]{failed} 首失败[/red]，可重复执行，已完成的会自动跳过")
        raise typer.Exit(1)


@app.command("cache")
def cmd_cache(
    cache_dir: str = typer.Option("", "--dir", help="客户端缓存目录，留空自动定位"),
    out: Path = typer.Option(Path("downloads"), "--out", "-o", help="输出目录"),
    min_rate: int = typer.Option(
        128, "--min-rate", help="只处理不低于该档位的缓存（128/320/999）"
    ),
    template: str = typer.Option(DEFAULT_TEMPLATE, "--template", "-t", help="命名模板"),
    cookie: str = typer.Option("", "--cookie", help="登录 Cookie，用于补齐元数据与歌词"),
    no_cover: bool = typer.Option(False, "--no-cover", help="不写入封面"),
    no_lyric: bool = typer.Option(False, "--no-lyric", help="不写入歌词"),
    force: bool = typer.Option(False, "--force", "-f", help="覆盖已存在的文件"),
    to_mp3: bool = typer.Option(
        False, "--to-mp3", help="解码后转成 mp3（需要 ffmpeg）"
    ),
    mp3_bitrate: str = typer.Option("auto", "--mp3-bitrate", help="转 mp3 码率，auto 表示跟随源码率"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只列出，不解码"),
    limit: int = typer.Option(0, "--limit", "-n", help="只处理前 N 首"),
) -> None:
    """把客户端播放缓存解码成音频文件。"""
    from . import cache as cache_mod
    from . import transcode
    from .downloader import fetch_cover, fetch_lyrics
    from .tagger import write_tags

    root = Path(cache_dir).expanduser() if cache_dir else cache_mod.default_cache_dir()
    if root is None or not root.is_dir():
        console.print("[red]没找到客户端缓存目录[/red]，请用 --dir 指定具体路径")
        raise typer.Exit(2)
    console.print(f"[dim]缓存目录 {root}[/dim]")

    entries = cache_mod.scan(root)
    if not entries:
        console.print("[yellow]目录里没有可解析的 .uc 缓存文件[/yellow]")
        raise typer.Exit(1)

    # 小于 200KB 的多半是试听片段或没写开的残留
    usable = [
        e for e in entries if e.complete and e.rate >= min_rate and e.size >= 200_000
    ]
    best: dict[int, cache_mod.CacheEntry] = {}
    for entry in usable:
        current = best.get(entry.song_id)
        if current is None or entry.rate > current.rate:
            best[entry.song_id] = entry

    partial = sum(1 for e in entries if not e.complete)
    low = sum(1 for e in entries if e.complete and e.rate < min_rate)
    console.print(
        f"扫描 {len(entries)} 个 · 完整 {len(entries) - partial} · "
        f"写到一半 {partial} · 低于 {min_rate} 档 {low} · 去重后 {len(best)} 首"
    )

    items = sorted(best.values(), key=lambda e: e.song_id)
    if limit > 0:
        items = items[:limit]
    if not items:
        console.print("[yellow]没有符合条件的目标[/yellow]")
        raise typer.Exit(1)

    backend = _backend(cookie)
    details: dict[int, Song] = {}
    ids = [e.song_id for e in items]
    with console.status("查询歌曲信息…"):
        for start in range(0, len(ids), 50):
            try:
                for song in backend.song_details(ids[start : start + 50]):
                    details[song.id] = song
            except BackendError:
                continue

    if dry_run:
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("曲名", overflow="ellipsis", max_width=36, no_wrap=True)
        table.add_column("艺人", overflow="ellipsis", max_width=20, style="cyan", no_wrap=True)
        table.add_column("档位", width=6, style="dim")
        table.add_column("大小", justify="right", width=9, style="dim")
        for entry in items:
            song = details.get(entry.song_id)
            table.add_row(
                song.name if song else f"未识别 {entry.song_id}",
                song.artist_text if song else "-",
                entry.rate_label,
                f"{entry.size / 1048576:.1f} MB",
            )
        console.print(table)
        console.print("[dim]以上为待解码清单，去掉 --dry-run 开始转换[/dim]")
        raise typer.Exit()

    out.mkdir(parents=True, exist_ok=True)
    done = skipped = failed = trial = 0
    written = 0

    from mutagen import File as MutagenFile

    for index, entry in enumerate(items, 1):
        song = details.get(entry.song_id) or Song(
            entry.song_id, f"未识别曲目 {entry.song_id}", [], ""
        )
        tag = f"[dim]{index:>3}/{len(items)}[/dim]"
        try:
            scratch = out / f".ncm-cache-{entry.song_id}.bin"
            ext = cache_mod.decode(entry.path, scratch)
            rel = render_template(template, song, entry.rate_label)
            target = out / f"{rel}.{ext}"
            if target.exists() and not force:
                scratch.unlink(missing_ok=True)
                skipped += 1
                console.print(f"{tag} [dim]跳过 {song.name}[/dim]", highlight=False)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(scratch, target)

            # 试听片段的缓存也会完整落盘，只能靠时长识别
            try:
                probe = MutagenFile(target)
                duration = probe.info.length if probe and probe.info else 0
            except Exception:  # noqa: BLE001
                duration = 0
            if 0 < duration < 30:
                target.unlink(missing_ok=True)
                trial += 1
                console.print(
                    f"{tag} [yellow]仅试听[/yellow] {song.name} "
                    f"[dim]{duration:.0f}s，已丢弃[/dim]",
                    highlight=False,
                )
                continue

            converted = ""
            if to_mp3 and target.suffix.lower() != ".mp3":
                try:
                    target = transcode.to_mp3(target, bitrate=mp3_bitrate)
                    out_br = transcode.probe_bitrate(target) // 1000
                    converted = f" → mp3 {out_br}k"
                except Exception as exc:  # noqa: BLE001
                    converted = f"（转码失败：{exc}）"

            lyrics = "" if no_lyric else fetch_lyrics(backend, song)
            cover = b"" if no_cover else fetch_cover(song)
            try:
                write_tags(target, song, lyrics, cover)
            except Exception:  # noqa: BLE001
                pass

            done += 1
            written += target.stat().st_size
            console.print(
                f"{tag} [green]解码[/green] {song.name} "
                f"[dim]- {song.artist_text} · {entry.rate_label}{converted} · "
                f"{target.stat().st_size / 1048576:.1f} MB[/dim]",
                highlight=False,
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            console.print(f"{tag} [red]失败[/red] {song.name}: {exc}", highlight=False)

    table = Table(box=None, show_header=False, pad_edge=False, title="汇总")
    table.add_column(style="dim", width=10)
    table.add_column(justify="right")
    table.add_row("解码完成", str(done))
    table.add_row("已存在", str(skipped))
    if trial:
        table.add_row("仅试听", str(trial))
    table.add_row("失败", str(failed))
    table.add_row("合计", f"{done + skipped + failed + trial} 首 · {written / 1048576:.1f} MB")
    console.print(table)


@app.command("qualities")
def cmd_qualities() -> None:
    """列出可选音质档位。"""
    table = Table(box=None, show_header=True, header_style="bold")
    table.add_column("档位")
    table.add_column("名称")
    table.add_column("说明")
    notes = {
        "standard": "96k-128k，匿名可用",
        "higher": "160k-192k，匿名可用",
        "exhigh": "256k-320k，匿名可用",
        "lossless": "FLAC，需账号权限",
        "hires": "高采样率 FLAC，需账号权限",
    }
    for level in QUALITY_ORDER:
        table.add_row(level, QUALITY_LABEL[level], notes[level])
    console.print(table)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
