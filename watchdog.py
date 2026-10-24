"""
WuWei Core - Institutional Watchdog & Self-Healing Daemon
--------------------------------------------------------
Giám sát liên tục trạng thái tiến trình MT5 (terminal64.exe) và Python Bot (live_trade.py).
Tự động phục hồi, giải phóng tiến trình treo (deadlock/freeze) và gửi cảnh báo Telegram.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import psutil
from dotenv import load_dotenv

# Thiết lập đường dẫn gốc
CURRENT_DIR = Path(__file__).resolve().parent
load_dotenv(CURRENT_DIR / ".env")

# Thêm đường dẫn vào PYTHONPATH
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

try:
    from live_connector.telegram_notifier import TelegramNotifier
except ImportError:
    TelegramNotifier = None

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

# Thiết lập Logger
LOG_FILE = CURRENT_DIR / "watchdog.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("WuWeiWatchdog")


class WuWeiWatchdog:
    def __init__(
        self,
        check_interval: int = 30,
        heartbeat_timeout: int = 180,
        max_memory_mb: float = 1500.0,
    ):
        self.check_interval = check_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.max_memory_mb = max_memory_mb

        self.telemetry_path = CURRENT_DIR / "dashboard" / "telemetry_state.json"
        self.start_bot_script = CURRENT_DIR / "start_bot.bat"
        self.mt5_path = Path(
            os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")
        )

        self.notifier = TelegramNotifier() if TelegramNotifier else None
        self.bot_process: Optional[subprocess.Popen] = None

    def find_mt5_processes(self) -> List[psutil.Process]:
        """Tìm các tiến trình MT5 terminal64.exe đang chạy"""
        res = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["name"] and p.info["name"].lower() == "terminal64.exe":
                    res.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return res

    def find_bot_processes(self) -> List[psutil.Process]:
        """Tìm các tiến trình Python đang chạy live_trade.py"""
        res = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = p.info.get("cmdline") or []
                cmd_str = " ".join(cmdline).lower()
                if "live_trade.py" in cmd_str and p.pid != os.getpid():
                    res.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return res

    def ensure_mt5_running(self) -> bool:
        """Đảm bảo MT5 Terminal đang chạy; tự động kích hoạt nếu bị sập"""
        mt5_procs = self.find_mt5_processes()
        if mt5_procs:
            return True

        logger.warning("🚨 Phát hiện MetaTrader 5 (terminal64.exe) KHÔNG hoạt động!")
        if self.notifier:
            self.notifier.send_system_alert(
                title="MT5 Terminal Sập",
                details="Tiến trình terminal64.exe đã dừng. Watchdog đang tự động khởi động lại...",
                alert_type="CRITICAL",
            )

        if self.mt5_path.exists():
            logger.info(f"Đang khởi động MT5 Terminal từ: {self.mt5_path}")
            try:
                subprocess.Popen([str(self.mt5_path)], shell=False)
                logger.info("Chờ 25 giây để MT5 hoàn tất kết nối máy chủ Exness...")
                time.sleep(25)
                logger.info("MT5 Terminal đã sẵn sàng.")
                return True
            except Exception as e:
                logger.error(f"Lỗi khởi động MT5: {e}")
                return False
        else:
            logger.error(f"Không tìm thấy file thực thi MT5 tại: {self.mt5_path}")
            return False

    def check_heartbeat_alive(self) -> bool:
        """
        Kiểm tra thời gian cập nhật của dashboard/telemetry_state.json.
        Nếu quá thời gian heartbeat_timeout, coi như bot bị treo (Freeze/Deadlock).
        """
        if not self.telemetry_path.exists():
            # Nếu chưa có file telemetry, có thể bot vừa khởi động
            return True

        try:
            mtime = self.telemetry_path.stat().st_mtime
            age_sec = time.time() - mtime
            if age_sec > self.heartbeat_timeout:
                logger.warning(
                    f"⚠️ Telemetry stale! Lần cập nhật cuối cách đây {age_sec:.1f}s (Ngưỡng tối đa: {self.heartbeat_timeout}s)"
                )
                return False
            return True
        except Exception as e:
            logger.error(f"Lỗi đọc telemetry heartbeat: {e}")
            return True

    def kill_hung_bot(self, reason: str = "Treo tiến trình (Freeze)") -> None:
        """Buộc dừng toàn bộ tiến trình live_trade.py bị treo hoặc rò rỉ RAM"""
        procs = self.find_bot_processes()
        if not procs:
            return

        logger.critical(f"🛑 Đang ép tắt {len(procs)} tiến trình Bot lỗi. Lý do: {reason}")
        if self.notifier:
            self.notifier.send_system_alert(
                title="Cưỡng Chế Khởi Động Lại Bot",
                details=f"Phát hiện bot bị: {reason}. Watchdog tiến hành terminate và restart.",
                alert_type="CRITICAL",
            )

        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def restart_bot(self) -> None:
        """Khởi động lại bot thông qua start_bot.bat trong cửa sổ nền"""
        logger.info("🚀 Đang khởi động lại WuWei Bot...")
        try:
            if sys.platform == "win32":
                # Khởi động dưới tiến trình độc lập không phụ thuộc watchdog
                subprocess.Popen(
                    [str(self.start_bot_script)],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    shell=True,
                    cwd=str(CURRENT_DIR),
                )
            else:
                subprocess.Popen(["bash", str(self.start_bot_script)], cwd=str(CURRENT_DIR))
            logger.info("Lệnh khởi động Bot đã được phát đi thành công.")
        except Exception as e:
            logger.error(f"Không thể khởi động bot: {e}")

    def check_memory_usage(self, procs: List[psutil.Process]) -> bool:
        """Kiểm tra xem bot có bị rò rỉ bộ nhớ (Memory Leak) vượt ngưỡng không"""
        for p in procs:
            try:
                mem_mb = p.memory_info().rss / (1024 * 1024)
                if mem_mb > self.max_memory_mb:
                    logger.warning(
                        f"⚠️ Cảnh báo RAM: Tiến trình {p.pid} đang chiếm {mem_mb:.1f}MB (Vượt mức {self.max_memory_mb}MB)"
                    )
                    return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return True

    def scan_cycle(self) -> None:
        """Thực hiện một chu kỳ kiểm tra sức khỏe hệ thống toàn diện"""
        # 1. Đảm bảo MT5 sống
        mt5_ok = self.ensure_mt5_running()
        if not mt5_ok:
            return

        # 2. Tìm tiến trình Bot
        bot_procs = self.find_bot_processes()

        if not bot_procs:
            logger.warning("⚠️ Không phát hiện tiến trình live_trade.py đang chạy!")
            self.restart_bot()
            return

        # 3. Kiểm tra rò rỉ RAM
        if not self.check_memory_usage(bot_procs):
            self.kill_hung_bot(reason=f"Bộ nhớ vượt ngưỡng an toàn ({self.max_memory_mb}MB)")
            time.sleep(3)
            self.restart_bot()
            return

        # 4. Kiểm tra Heartbeat Telemetry (phát hiện deadlock / socket freeze)
        if not self.check_heartbeat_alive():
            self.kill_hung_bot(reason=f"Mất tín hiệu Heartbeat > {self.heartbeat_timeout} giây")
            time.sleep(3)
            self.restart_bot()
            return

        logger.info(
            f"✔ [Sức Khỏe Hệ Thống: BÌNH THƯỜNG] MT5: {len(self.find_mt5_processes())} | "
            f"Bot PIDs: {[p.pid for p in bot_procs]} | Heartbeat: OK"
        )

    def run_forever(self) -> None:
        """Vòng lặp giám sát liên tục 24/7"""
        logger.info("=" * 70)
        logger.info("   WUWEI CORE - WATCHDOG & SELF-HEALING MONITOR INITIALIZED")
        logger.info(f"   Chu kỳ quét: {self.check_interval}s | Heartbeat Timeout: {self.heartbeat_timeout}s")
        logger.info("=" * 70)

        while True:
            try:
                self.scan_cycle()
            except Exception as e:
                logger.error(f"Lỗi không xác định trong vòng lặp Watchdog: {e}")
            time.sleep(self.check_interval)


def parse_args():
    parser = argparse.ArgumentParser(description="WuWei Core Watchdog & Self-Healing Monitor")
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Khoảng cách giữa các chu kỳ kiểm tra (giây)",
    )
    parser.add_argument(
        "--heartbeat-timeout",
        type=int,
        default=180,
        help="Thời gian chờ tối đa kể từ lần ghi telemetry cuối (giây)",
    )
    parser.add_argument(
        "--max-mem",
        type=float,
        default=1500.0,
        help="Ngưỡng bộ nhớ tối đa cho phép (MB)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Chỉ quét 1 lần kiểm tra sức khỏe rồi thoát",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    watchdog = WuWeiWatchdog(
        check_interval=args.interval,
        heartbeat_timeout=args.heartbeat_timeout,
        max_memory_mb=args.max_mem,
    )
    if args.once:
        logger.info("Thực hiện 1 chu kỳ kiểm tra sức khỏe chẩn đoán (--once)...")
        watchdog.scan_cycle()
    else:
        watchdog.run_forever()
