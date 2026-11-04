"""扫码登录对话框。"""
from __future__ import annotations

import io

import qrcode
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton

from gui.dialog import RoundDialog
from ncm import auth

QR_SIZE = 230


class LoginDialog(RoundDialog):
    """显示二维码并轮询扫码状态，成功后把 Cookie 落盘。"""

    def __init__(self, parent=None):
        super().__init__(parent, "扫码登录", width=340)
        self.cookie = ""
        self.account = auth.Account()
        self.qr: auth.QrLogin | None = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)

        layout = self.body_layout
        layout.setSpacing(10)

        self.qr_label = QLabel("正在获取二维码…")
        self.qr_label.setObjectName("muted")
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setFixedSize(QR_SIZE, QR_SIZE)
        layout.addWidget(self.qr_label, 0, Qt.AlignHCenter)

        self.status = QLabel("请用网易云音乐 App 扫码")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新二维码")
        self.btn_refresh.clicked.connect(self.start)
        row.addWidget(self.btn_refresh)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        row.addWidget(btn_close)
        layout.addLayout(row)

        self.finished.connect(lambda _: self.timer.stop())
        self.start()

    def start(self) -> None:
        self.timer.stop()
        self.status.setText("正在获取二维码…")
        self.btn_refresh.setEnabled(False)
        try:
            self.qr = auth.QrLogin()
            self.qr.start()
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"获取二维码失败：{exc}")
            self.btn_refresh.setEnabled(True)
            return
        self._show_qr(self.qr.url)
        self.status.setText("请用网易云音乐 App 扫码")
        self.btn_refresh.setEnabled(True)
        self.timer.start(2000)

    def _show_qr(self, url: str) -> None:
        image = qrcode.make(url)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        pix = QPixmap()
        pix.loadFromData(buf.getvalue())
        self.qr_label.setPixmap(
            pix.scaled(QR_SIZE, QR_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _poll(self) -> None:
        if self.qr is None:
            return
        try:
            code = self.qr.poll()
        except Exception:  # noqa: BLE001
            return
        if code == auth.SCANNED:
            self.status.setText("已扫描，请在手机上点确认")
        elif code == auth.EXPIRED:
            self.timer.stop()
            self.status.setText("二维码已过期，点「刷新二维码」重来")
        elif code == auth.CONFIRMED:
            self.timer.stop()
            cookie = auth.cookie_string(self.qr.session)
            if "MUSIC_U" not in cookie:
                self.status.setText("登录失败：没有拿到 MUSIC_U")
                return
            self.account = auth.fetch_account(cookie)
            auth.save_login(cookie, self.account)
            self.cookie = cookie
            self.accept()
