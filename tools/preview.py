"""生成界面预览图，仅用于开发校验。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtWidgets import QApplication

from gui.main import MainWindow
from ncm import auth
from ncm.downloader import FAILED, OK, SKIPPED, TRIAL, TaskResult
from ncm.models import Song


class DemoAccount:
    """截图里绝不出现真实账号，统一用演示身份。"""

    nickname = "演示账号"
    user_id = 0
    vip_type = 0

    @property
    def vip_text(self) -> str:
        return "非会员"


auth.load_cookie = lambda *a, **k: "MUSIC_U=demo"
auth.saved_account = lambda *a, **k: DemoAccount()

SONGS = [
    Song(501468000, "Sacred Play Secret Place", ["Matryoshka"],
         "Virgin Babylon Records Sampler 20161126", 317547, 11),
    Song(26093064, "Sacred Play Secret Place", ["Matryoshka"],
         "Laideronnette", 317000, 0),
    Song(1480055408, "Sacred Play Secret Place Remixed by Negative Headphone",
         ["Matryoshka"], "Eight Scenery Of The Burning Piano", 323000, 0),
    Song(366867, "Tyrant's Miniature Garden", ["Matryoshka"],
         "zatracenie", 439000, 6),
    Song(306675, "明年依然好时节", ["许飞"], "许飞", 161000, 1),
    Song(3448738711, "无 意", ["加木", "Demxntia"], "无 意", 176000, 1),
]

app = QApplication(sys.argv)
win = MainWindow()
win.songs = SONGS
win._playing_id = SONGS[0].id
win._fill_result_table()
win.result_info.setText("搜索结果 · 共 6 首")
win._prepare_queue(SONGS)

states = [
    (OK, 12_700_000, "完成"),
    (OK, 8_900_000, "完成"),
    (TRIAL, 0, "仅试听"),
    (SKIPPED, 17_500_000, "已存在"),
    (FAILED, 0, "失败"),
    (OK, 11_200_000, "完成"),
]
for song, (status, size, _) in zip(SONGS, states):
    res = TaskResult(song, status, path=None, br=320000, quality="极高")
    if size:
        res.path = Path("dummy.mp3")
    win._on_song_result(res)
    if size:
        win.queue_table.item(win._row_of(song.id), 3).setText(f"{size/1048576:.1f} MB")

win.summary_label.setText(
    '<a href="open" style="color:#4caf7d;text-decoration:none">完成 3</a>'
    " · 跳过 1 · 试听 1 · "
    '<a href="retry" style="color:#e8554a;text-decoration:none">失败 1，点此重试</a>'
    " · 38.6 MB"
)
win.status_label.setText("本批 6 首处理完毕")
win.queue_info.setText("下载队列 · 已完成 6 首")
win.result_table.selectRow(2)

# 顶栏播放态：正在播放第一首
win.topbar.set_song(SONGS[0])
win.topbar.set_position(83000, 317000)
win.topbar.set_state("playing")
# 模拟按时长降序，展示表头箭头与加粗
win.sort_key = "duration"
win.sort_desc = True
win._update_header_sort_style()
win._update_download_button()

win.resize(1000, 740)
win.show()
app.processEvents()

out_dir = Path(__file__).resolve().parent.parent / "docs"
out_dir.mkdir(exist_ok=True)

# 圆角窗口带透明边距，直接 grab 会得到透明边框；合成到桌面色上才看得清
shot = win.grab()
from PyQt5.QtGui import QColor, QPainter, QPixmap

canvas = QPixmap(shot.size())
canvas.fill(QColor("#0f0f12"))
painter = QPainter(canvas)
painter.drawPixmap(0, 0, shot)
painter.end()
canvas.save(str(out_dir / "preview.png"))
print("saved", out_dir / "preview.png", win.size())

# 紧凑模式单独一张
win.toggle_mini()
app.processEvents()
win._rebuild_shadow()
app.processEvents()
# 紧凑模式下汇总不带「点此重试」与体积，预览要与实际一致
win.summary_label.setText("完成 3 · 跳过 1 · 试听 1 · 失败 1")
compact = win.grab()
canvas2 = QPixmap(compact.size())
canvas2.fill(QColor("#101014"))
q = QPainter(canvas2)
q.drawPixmap(0, 0, compact)
q.end()
canvas2.save(str(out_dir / "mini.png"))
print("saved", out_dir / "mini.png", compact.size())
win.toggle_mini()
app.processEvents()

from gui.main import SettingsDialog

dlg = SettingsDialog(win.settings, win.out_dir, win.cache_dir, win)
dlg.show()
dlg.to_mp3.setChecked(True)
app.processEvents()
dlg.grab().save(str(out_dir / "settings.png"))
print("saved", out_dir / "settings.png", dlg.size())
dlg.close()

from gui.login_dialog import QR_SIZE, LoginDialog

login = LoginDialog(win)
login.show()
app.processEvents()

# 真实登录二维码绝不上传：换成指向项目网站的二维码，扫码不会泄露任何凭证
import qrcode
from PyQt5.QtGui import QImage

_qr_img = qrcode.make("https://mochizuki.top").convert("RGB").resize((QR_SIZE, QR_SIZE))
_qr_data = _qr_img.tobytes("raw", "RGB")
_qimage = QImage(_qr_data, QR_SIZE, QR_SIZE, QR_SIZE * 3, QImage.Format_RGB888).copy()
login.qr_label.setPixmap(QPixmap.fromImage(_qimage))
login.status.setText("请用网易云音乐 App 扫码（示意）")
app.processEvents()

login.grab().save(str(out_dir / "login.png"))
print("saved", out_dir / "login.png", login.size())
login.close()

# 搜索历史浮层：现在画在窗口内，连窗口一起截
win.input.set_history(["周杰伦", "Matryoshka", "陈奕迅", "宇多田光", "许飞"])
win.input.show_history()
app.processEvents()
if win.input._popup is not None and win.input._popup.isVisible():
    shot = win.grab()
    canvas = QPixmap(shot.size())
    canvas.fill(QColor("#0f0f12"))
    p2 = QPainter(canvas)
    p2.drawPixmap(0, 0, shot)
    p2.end()
    canvas.save(str(out_dir / "history.png"))
    print("saved", out_dir / "history.png", win.input._popup.size())
    win.input._popup.hide()
    app.processEvents()

if win.songs:
    menu = win._build_context_menu(win.songs[0])
    menu.show()
    app.processEvents()
    menu.grab().save(str(out_dir / "context-menu.png"))
    print("saved", out_dir / "context-menu.png", menu.size())
    menu.close()
