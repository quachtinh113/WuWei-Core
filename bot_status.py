"""
WuWei Core - Institutional Bot & System Status Inspector
--------------------------------------------------------
Kiểm tra toàn diện trạng thái hệ thống:
1. Tiến trình MetaTrader 5 (terminal64.exe)
2. Tiến trình Live Trading Daemon (live_trade.py)
3. Tiến trình Self-Healing Watchdog (watchdog.py)
4. Telemetry Heartbeat & Dữ liệu tài khoản thời gian thực
5. Cấu hình OS Institutional 24/7 (Power Plan, Sleep, Standby)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Đảm bảo mã hóa UTF-8 trên Windows Console
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CURRENT_DIR = Path(__file__).resolve().parent
load_dotenv(CURRENT_DIR / ".env")

console = Console(force_terminal=True, legacy_windows=False)


def find_processes_by_cmd(keyword: str, exclude_pid: Optional[int] = None, exact_name: Optional[str] = None) -> List[Dict[str, Any]]:
    results = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time", "cpu_percent", "memory_info"]):
        try:
            if exclude_pid and p.pid == exclude_pid:
                continue
            name = (p.info.get("name") or "").lower()
            cmdline = p.info.get("cmdline") or []
            cmd_str = " ".join(cmdline).lower()

            if exact_name:
                match = (name == exact_name.lower())
            else:
                match = ("python" in name and keyword.lower() in cmd_str)

            if match:
                mem_mb = (p.info["memory_info"].rss / (1024 * 1024)) if p.info.get("memory_info") else 0.0
                uptime_sec = time.time() - p.info["create_time"] if p.info.get("create_time") else 0
                uptime_str = str(datetime.utcfromtimestamp(uptime_sec).strftime("%H:%M:%S"))
                if uptime_sec >= 86400:
                    days = int(uptime_sec // 86400)
                    uptime_str = f"{days}d {uptime_str}"

                results.append({
                    "pid": p.pid,
                    "name": p.info.get("name"),
                    "cmd": " ".join(cmdline[:4]),
                    "uptime": uptime_str,
                    "memory_mb": round(mem_mb, 1),
                    "cpu_pct": p.info.get("cpu_percent") or 0.0,
                    "started": datetime.fromtimestamp(p.info["create_time"]).strftime("%Y-%m-%d %H:%M:%S")
                    if p.info.get("create_time")
                    else "N/A",
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Lọc bỏ tiến trình cha (wrapper/launcher) nếu tiến trình con đang chạy cùng mục tiêu
    final_results = []
    for r in results:
        is_parent = False
        for other in results:
            if other["pid"] != r["pid"]:
                try:
                    proc = psutil.Process(other["pid"])
                    if proc.ppid() == r["pid"]:
                        is_parent = True
                        break
                except Exception:
                    pass
        if not is_parent:
            final_results.append(r)

    return final_results if final_results else results


def check_telemetry(telemetry_file: Path) -> Dict[str, Any]:
    if not telemetry_file.exists():
        return {"exists": False, "status": "NOT_FOUND"}

    try:
        mtime = telemetry_file.stat().st_mtime
        age_sec = time.time() - mtime
        with open(telemetry_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        return {
            "exists": True,
            "age_sec": round(age_sec, 1),
            "status": "HEALTHY" if age_sec <= 90 else ("STALE" if age_sec <= 240 else "DEAD"),
            "data": data,
        }
    except Exception as e:
        return {"exists": True, "status": "ERROR", "error": str(e)}


def check_os_tuning() -> Dict[str, Any]:
    os_info = {
        "high_performance": False,
        "sleep_disabled": False,
        "is_admin": False,
    }

    # Kiểm tra quyền Admin
    try:
        import ctypes
        os_info["is_admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        pass

    # Kiểm tra Power Scheme
    try:
        out = subprocess.check_output("powercfg /getactivescheme", shell=True, text=True)
        if "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c" in out or "High performance" in out or "Hiệu năng cao" in out:
            os_info["high_performance"] = True
    except Exception:
        pass

    return os_info


def inspect_all() -> Dict[str, Any]:
    current_pid = os.getpid()

    # 1. MT5 processes (terminal64.exe)
    mt5_list = find_processes_by_cmd("terminal64.exe", exclude_pid=current_pid, exact_name="terminal64.exe")

    # 2. Live Bot processes (live_trade.py)
    bot_list = find_processes_by_cmd("live_trade.py", exclude_pid=current_pid)

    # 3. Watchdog processes (watchdog.py)
    watchdog_list = find_processes_by_cmd("watchdog.py", exclude_pid=current_pid)

    # 4. Telemetry check
    telemetry = check_telemetry(CURRENT_DIR / "dashboard" / "telemetry_state.json")

    # 5. OS tuning check
    os_tuning = check_os_tuning()

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mt5": mt5_list,
        "bot": bot_list,
        "watchdog": watchdog_list,
        "telemetry": telemetry,
        "os_tuning": os_tuning,
    }


def render_dashboard(state: Dict[str, Any]) -> None:
    console.print()
    header_panel = Panel(
        "[bold cyan]WUWEI CORE - INSTITUTIONAL PROCESS & TELEMETRY AUDIT[/]\n"
        "[dim]System Architecture: Robert Carver Systematic Portfolio x WuWei Live Engine[/]\n"
        f"Snapshot Time: [bold yellow]{state['timestamp']}[/]",
        border_style="cyan",
        expand=True,
    )
    console.print(header_panel)

    # Table 1: Process Telemetry
    p_table = Table(title="[bold white]1. TIẾN TRÌNH HỆ THỐNG (SYSTEM PROCESS TELEMETRY)[/]", expand=True)
    p_table.add_column("Thành Phần", style="bold cyan", width=22)
    p_table.add_column("Trạng Thái", justify="center", width=16)
    p_table.add_column("PID", justify="right", width=9)
    p_table.add_column("RAM (MB)", justify="right", width=12)
    p_table.add_column("Thời Gian Chạy", justify="center", width=16)
    p_table.add_column("Khởi Động Lúc", justify="center", width=20)

    # MT5
    if state["mt5"]:
        for p in state["mt5"]:
            p_table.add_row("MetaTrader 5 (MT5)", "[bold green]✔ RUNNING[/]", str(p["pid"]), f"{p['memory_mb']:.1f}", p["uptime"], p["started"])
    else:
        p_table.add_row("MetaTrader 5 (MT5)", "[bold red]❌ STOPPED[/]", "-", "-", "-", "-")

    # Bot
    if state["bot"]:
        for p in state["bot"]:
            p_table.add_row("WuWei Bot (live_trade.py)", "[bold green]✔ ACTIVE[/]", str(p["pid"]), f"{p['memory_mb']:.1f}", p["uptime"], p["started"])
    else:
        p_table.add_row("WuWei Bot (live_trade.py)", "[bold red]❌ STOPPED[/]", "-", "-", "-", "-")

    # Watchdog
    if state["watchdog"]:
        for p in state["watchdog"]:
            p_table.add_row("Watchdog (watchdog.py)", "[bold green]✔ PROTECTING[/]", str(p["pid"]), f"{p['memory_mb']:.1f}", p["uptime"], p["started"])
    else:
        p_table.add_row("Watchdog (watchdog.py)", "[dim]OFF (Chế độ 1 tiến trình)[/]", "-", "-", "-", "-")

    console.print(p_table)

    # Table 2: Financial Telemetry & Heartbeat
    t_data = state["telemetry"]
    if t_data.get("exists") and "data" in t_data:
        acc = t_data["data"].get("account", {})
        assets = t_data["data"].get("assets", [])

        status_str = {
            "HEALTHY": "[bold green]✔ FRESH (Bình thường)[/]",
            "STALE": "[bold yellow]⚠ STALE (Cảnh báo trễ)[/]",
            "DEAD": "[bold red]❌ DEAD (Mất tín hiệu)[/]",
        }.get(t_data.get("status"), "[dim]UNKNOWN[/]")

        f_table = Table(title="[bold white]2. TELEMETRY TÀI KHOẢN & TÍN HIỆU ĐỊNH LƯỢNG (LIVE TELEMETRY)[/]", expand=True)
        f_table.add_column("Chỉ Số", style="bold yellow", width=24)
        f_table.add_column("Giá Trị Thực Tế", style="bold white")

        f_table.add_row("Trạng Thái Heartbeat", f"{status_str} (Cập nhật cách đây {t_data.get('age_sec', 0)}s)")
        f_table.add_row("Broker Server / Login", f"{acc.get('server', 'N/A')} | #{acc.get('login', 'N/A')}")
        f_table.add_row("Số Dư (Balance) / Equity", f"${acc.get('balance', 0):,.2f} USD | Equity: ${acc.get('equity', 0):,.2f} USD")
        f_table.add_row("Ký Quỹ Khả Dụng (Free Margin)", f"${acc.get('free_margin', 0):,.2f} USD")
        f_table.add_row("Daily Drawdown Pct", f"{acc.get('daily_drawdown_pct', 0) * 100:.3f}%")
        cb_str = "[bold red]ACTIVATED[/]" if acc.get("circuit_breaker_active") else "[bold green]OFF (Bình thường)[/]"
        f_table.add_row("Institutional Circuit Breaker", cb_str)
        f_table.add_row("Tổng Số Tài Sản Đang Quét", f"{len(assets)} Cặp (Kim loại, FX, Chỉ số, Năng lượng, Crypto)")

        console.print(f_table)

    # Table 3: OS Environment Tuning
    os_info = state["os_tuning"]
    os_table = Table(title="[bold white]3. TỐI ƯU HÓA HỆ ĐIỀU HÀNH CHO VẬN HÀNH 24/7 (OS TUNING)[/]", expand=True)
    os_table.add_column("Hạng Mục Tối Ưu", style="bold cyan", width=30)
    os_table.add_column("Trạng Thái Hiện Tại", justify="center", width=20)
    os_table.add_column("Ghi Chú Đánh Giá", style="dim")

    hp_str = "[bold green]✔ High Performance[/]" if os_info["high_performance"] else "[bold yellow]Default / Balanced[/]"
    os_table.add_row("Windows Power Plan", hp_str, "Cần High Performance để CPU không throttle khi chạy nền")

    adm_str = "[bold green]Administrator[/]" if os_info["is_admin"] else "[bold yellow]Standard User[/]"
    os_table.add_row("Quyền Thực Thi Phiên", adm_str, "Cần Admin để cấu hình chống tắt Card mạng & Task Scheduler")

    console.print(os_table)
    console.print()


def kill_processes(keyword: str) -> int:
    killed = 0
    current_pid = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.pid == current_pid:
                continue
            name = (p.info.get("name") or "").lower()
            cmdline = p.info.get("cmdline") or []
            cmd_str = " ".join(cmdline).lower()
            if keyword.lower() in name or keyword.lower() in cmd_str:
                # Nếu tiến trình cha là cmd.exe đang chạy start_bot.bat, dừng luôn cha để tránh vòng lặp cũ
                try:
                    parent = p.parent()
                    if parent and "cmd" in parent.name().lower():
                        parent_cmd = " ".join(parent.cmdline() or []).lower()
                        if "start_bot" in parent_cmd or "start_wuwei" in parent_cmd:
                            parent.terminate()
                except Exception:
                    pass

                p.terminate()
                try:
                    p.wait(timeout=3)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


def main():
    parser = argparse.ArgumentParser(description="WuWei Core Bot Status Inspector")
    parser.add_argument("--json", action="store_true", help="Xuất dữ liệu dạng JSON thô")
    parser.add_argument("--check-bot-running", action="store_true", help="Trả về exit code 0 nếu bot đang chạy, 1 nếu không")
    parser.add_argument("--check-watchdog-running", action="store_true", help="Trả về exit code 0 nếu watchdog đang chạy, 1 nếu không")
    parser.add_argument("--kill-bot", action="store_true", help="Buộc dừng toàn bộ tiến trình live_trade.py")
    parser.add_argument("--kill-watchdog", action="store_true", help="Buộc dừng toàn bộ tiến trình watchdog.py")
    args = parser.parse_args()

    if args.kill_bot:
        n = kill_processes("live_trade.py")
        console.print(f"[bold yellow]Đã đóng {n} tiến trình live_trade.py.[/]")
        return

    if args.kill_watchdog:
        n = kill_processes("watchdog.py")
        console.print(f"[bold yellow]Đã đóng {n} tiến trình watchdog.py.[/]")
        return

    state = inspect_all()

    if args.json:
        print(json.dumps(state, indent=2))
        return

    if args.check_bot_running:
        sys.exit(0 if state["bot"] else 1)

    if args.check_watchdog_running:
        sys.exit(0 if state["watchdog"] else 1)

    render_dashboard(state)


if __name__ == "__main__":
    main()
