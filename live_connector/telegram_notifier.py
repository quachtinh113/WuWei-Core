"""
WuWei Core - Telegram Live Alert & Telemetry Notifier
Non-blocking institutional notifications for trades, risk events, and trailing stops
"""
from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional


class TelegramNotifier:
    """
    Module gửi thông báo tự động tới Telegram:
    - Chạy bất đồng bộ (Non-blocking daemon thread) không làm chậm vòng lặp giao dịch
    - Tự động nhận diện token & chat_id từ .env
    - Định dạng tin nhắn chuẩn Markdown chuyên nghiệp
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.is_enabled = bool(self.bot_token and self.chat_id)

        if not self.is_enabled:
            # Silent mode nếu chưa cấu hình token
            pass

    def _send_async(self, message: str) -> None:
        """Gửi HTTP request trong thread riêng biệt với timeout ngắn"""
        if not self.is_enabled:
            return

        def worker():
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=4) as response:
                    pass
            except Exception as e:
                # Không chặn tiến trình giao dịch nếu lỗi mạng
                pass

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def send_order_opened(
        self,
        symbol: str,
        side: str,
        volume: float,
        price: float,
        stop_loss: float,
        forecast: float,
        ticket: Optional[int] = None,
    ) -> None:
        """Thông báo mở lệnh mới"""
        side_icon = "🟢 <b>BUY</b>" if side.upper() == "BUY" else "🔴 <b>SELL</b>"
        ticket_str = f"#{ticket}" if ticket else "Market"
        msg = (
            f"⚡ <b>WUWEI CORE — NEW ORDER EXECUTED</b>\n\n"
            f"• <b>Asset:</b> <code>{symbol}</code>\n"
            f"• <b>Action:</b> {side_icon} | <b>Lot:</b> <code>{volume:.2f}</code>\n"
            f"• <b>Entry Price:</b> <code>{price:.4f}</code>\n"
            f"• <b>Stop Loss:</b> <code>{stop_loss:.4f}</code>\n"
            f"• <b>Carver Forecast:</b> <code>{forecast:+.1f}</code> / 20.0\n"
            f"• <b>Ticket:</b> <code>{ticket_str}</code>\n"
            f"• <b>Time:</b> <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
            f"<i>Triết lý Vô Vi — Thuận theo xu hướng tự nhiên.</i>"
        )
        self._send_async(msg)

    def send_trailing_stop_updated(
        self,
        symbol: str,
        old_sl: float,
        new_sl: float,
        reason: str,
        ticket: Optional[int] = None,
    ) -> None:
        """Thông báo dời SL (Break-even hoặc Trailing Stop)"""
        ticket_str = f"#{ticket}" if ticket else ""
        msg = (
            f"🛡️ <b>WUWEI CORE — SL MODIFIED ({reason.upper()})</b>\n\n"
            f"• <b>Asset:</b> <code>{symbol}</code> {ticket_str}\n"
            f"• <b>Old SL:</b> <code>{old_sl:.4f}</code> ➔ <b>New SL:</b> <code>{new_sl:.4f}</code>\n"
            f"• <b>Status:</b> Lợi nhuận được bảo toàn tự động trên sàn.\n"
            f"• <b>Time:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>"
        )
        self._send_async(msg)

    def send_position_closed(
        self,
        symbol: str,
        side: str,
        exit_price: float,
        pnl: float,
        reason: str,
        ticket: Optional[int] = None,
    ) -> None:
        """Thông báo đóng vị thế"""
        pnl_icon = "💰" if pnl >= 0 else "🛑"
        ticket_str = f"#{ticket}" if ticket else ""
        msg = (
            f"{pnl_icon} <b>WUWEI CORE — POSITION CLOSED</b>\n\n"
            f"• <b>Asset:</b> <code>{symbol}</code> ({side}) {ticket_str}\n"
            f"• <b>Exit Price:</b> <code>{exit_price:.4f}</code>\n"
            f"• <b>Realized PnL:</b> <b>{pnl:+.2f} USD</b>\n"
            f"• <b>Reason:</b> <code>{reason}</code>\n"
            f"• <b>Time:</b> <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
        )
        self._send_async(msg)

    def send_circuit_breaker_alert(
        self,
        daily_dd_pct: float,
        threshold_pct: float,
    ) -> None:
        """Báo động khẩn khi chạm ngưỡng cắt lỗ tài khoản trong ngày"""
        msg = (
            f"🚨 <b>WUWEI CORE — CIRCUIT BREAKER TRIGGERED!</b>\n\n"
            f"• <b>Daily Drawdown:</b> <code>{daily_dd_pct*100:.2f}%</code>\n"
            f"• <b>Daily Threshold:</b> <code>{threshold_pct*100:.1f}%</code>\n"
            f"• <b>Action:</b> Toàn bộ hệ thống TẠM KHÓA mở lệnh mới trong 24 giờ.\n"
            f"• <b>Time:</b> <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
            f"<i>Bảo toàn vốn tuyệt đối là ưu tiên cao nhất!</i>"
        )
        self._send_async(msg)

    def send_system_alert(
        self,
        title: str,
        details: str,
        alert_type: str = "WARNING",
    ) -> None:
        """Gửi cảnh báo trạng thái hệ thống / Watchdog"""
        icon = "⚠️" if alert_type == "WARNING" else ("🚨" if alert_type == "CRITICAL" else "ℹ️")
        msg = (
            f"{icon} <b>WUWEI WATCHDOG — {title.upper()}</b>\n\n"
            f"{details}\n\n"
            f"• <b>Time:</b> <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
        )
        self._send_async(msg)

