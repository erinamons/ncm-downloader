"""把打包目录压成分发用的 zip。

排除运行时生成的东西（登录态、设置、日志、缓存），否则会把账号 cookie 发给别人。
"""
from __future__ import annotations

import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SRC = Path("pack/网易云下载器")
OUT_DIR = Path("dist")
EXCLUDE_NAMES = {"config.json", "settings.ini", "app.log", "app.log.1", "app.log.2"}
# crash.log 是 windowed 模式的崩溃栈，随包发出去既没用又难看
EXCLUDE_SUFFIXES = {".log"}
EXCLUDE_DIRS = {"play-cache", "downloads"}


def main() -> int:
    if not SRC.is_dir():
        print(f"找不到 {SRC}，先跑 build.bat")
        return 1

    from ncm import __version__

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"网易云下载器-v{__version__}-win64.zip"

    files = []
    for path in SRC.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(SRC)
        if rel.parts[0] in EXCLUDE_DIRS:
            continue
        if path.name in EXCLUDE_NAMES or path.name.startswith("app.log."):
            continue
        # 只排顶层日志：_internal 里带的第三方资源不动
        if len(rel.parts) == 1 and path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        files.append((path, rel))

    total = sum(p.stat().st_size for p, _ in files)
    print(f"待压缩 {len(files)} 个文件，共 {total / 1048576:.0f} MB")
    print(f"输出 {out}")

    start = time.time()
    done = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for index, (path, rel) in enumerate(files, 1):
            # 顶层再套一层文件夹名，解压后不会散落一地
            zf.write(path, arcname=str(Path(SRC.name) / rel))
            done += path.stat().st_size
            if index % 200 == 0 or index == len(files):
                pct = done / total * 100 if total else 100
                print(f"  {index}/{len(files)}  {pct:.0f}%", flush=True)

    size = out.stat().st_size
    print(
        f"完成：{out}  {size / 1048576:.0f} MB"
        f"（压缩率 {size / total * 100:.0f}%，用时 {time.time() - start:.0f}s）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
