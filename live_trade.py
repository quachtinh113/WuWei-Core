"""
WuWei Core - MetaTrader 5 Live Multi-Asset Trading Daemon
Institutional Systematic Trend-Following Platform (Robert Carver x WuWei)
Featuring:
- Phase 1: Real-Time Position Lifecycle, Break-Even & Dynamic Trailing Stop Sync
- Phase 2: Carver Correlation Matrix, De-correlation Filter & Sector Risk Caps
- Phase 3: Non-Blocking Telegram Live Alerts & Real-Time JSON/HTML Web Telemetry
- Phase 4: Auto-Recovery Watchdog & 24/7 Resilient Reconnection Architecture
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Đảm bảo mã hóa UTF-8 trên Windows Console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Nạp thông tin cấu hình từ .env
load_dotenv(CURRENT_DIR / ".env")

from config.settings import get_settings
from core.types import ExitReason, Position, PositionSide, RegimeState, SignalAction
from engines.execution_engine import ExecutionEngine
from engines.portfolio_allocator import PortfolioAllocator, SECTOR_MAP
from engines.regime_engine import RegimeEngine
from engines.risk_engine import RiskEngine
from engines.signal_engine import SignalEngine
from live_connector.mt5_connector import MT5Connector
from live_connector.telegram_notifier import TelegramNotifier


ASSET_CLASS_MAP = {
    "XAUUSD": "Precious Metals",
    "XAGUSD": "Precious Metals",
    "EURUSD": "FX Major",
    "GBPUSD": "FX Major",
    "USDJPY": "FX Major",
    "AUDUSD": "FX Major",
    "USDCAD": "FX Major",
    "USDCHF": "FX Major",
    "NZDUSD": "FX Major",
    "EURJPY": "FX Cross",
    "GBPJPY": "FX Cross",
    "EURGBP": "FX Cross",
    "AUDJPY": "FX Cross",
    "US30": "Equity Index",
    "US500": "Equity Index",
    "USTEC": "Equity Index",
    "DE30": "Equity Index",
    "USOIL": "Energy Commodity",
    "UKOIL": "Energy Commodity",
    "BTCUSD": "Cryptocurrency",
    "ETHUSD": "Cryptocurrency",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="WuWei Core - MT5 Institutional Live Execution Daemon (4-Stage Pipeline)"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="ALL",
        help="Danh sách cặp tài sản giao dịch, phân cách bằng dấu phẩy hoặc 'ALL' (Default: ALL)",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default="H1",
        choices=["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
        help="Khung thời gian nến (Default: H1)",
    )
    parser.add_argument(
        "--scan-interval",
        type=int,
        default=30,
        help="Thời gian chờ giữa các chu kỳ quét (giây, Default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chế độ giám sát tín hiệu thời gian thực (Không gửi lệnh thật vào MT5)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Chỉ quét 1 chu kỳ hiện tại rồi thoát",
    )
    return parser.parse_args()


def export_telemetry_state(
    account_data: dict,
    assets_data: list,
    output_path: Path,
) -> None:
    """Xuất trạng thái hệ thống ra file JSON để phục vụ Web Dashboard"""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "account": account_data,
            "assets": assets_data,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def main():
    args = parse_args()
    console = Console()

    settings = get_settings()
    tf = args.timeframe
    dashboard_json_path = CURRENT_DIR / "dashboard" / "telemetry_state.json"

    # Xác định danh sách symbols
    if args.symbols.strip().upper() == "ALL":
        target_symbols = list(settings.symbols.keys())
    else:
        raw_list = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        target_symbols = [s for s in raw_list if s in settings.symbols]
        if not target_symbols:
            target_symbols = list(settings.symbols.keys())

    login_val = os.getenv("MT5_LOGIN") or os.getenv("MT5_ACCOUNT") or "0"
    login = int(login_val)
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "Exness-MT5Trial6")
    path = os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")

    is_live_mode = not args.dry_run and os.getenv("LIVE_TRADING", "true").lower() in ("true", "1", "yes")

    # Khởi tạo Notifier
    notifier = TelegramNotifier()

    banner = (
        "[bold cyan]WUWEI CORE — FULL INSTITUTIONAL 4-STAGE PIPELINE[/bold cyan]\n"
        "[italic yellow]Triết lý Vô Vi x Định Lượng Robert Carver (pysystemtrade)[/italic yellow]\n\n"
        f"  • [bold white]Master Account:[/] [bold green]{login}[/] | [bold white]Server:[/] [bold green]{server}[/]\n"
        f"  • [bold white]Universe:[/] [bold yellow]{len(target_symbols)} Assets[/] ({', '.join(target_symbols[:6])}...)\n"
        f"  • [bold white]Stage 1 (Lifecycle):[/] [bold green]Active (Break-even, Trailing Stop, Reversal Exit)[/]\n"
        f"  • [bold white]Stage 2 (Allocation):[/] [bold green]Active (Correlation Filter + Sector Risk Caps)[/]\n"
        f"  • [bold white]Stage 3 (Telemetry):[/] [bold green]Active (Telegram Alerts + Real-Time Web UI)[/]\n"
        f"  • [bold white]Stage 4 (Resilience):[/] [bold green]Active (Auto-Recovery Heartbeat & Fail-Safe)[/]\n"
        f"  • [bold white]Execution Mode:[/] [bold {'green' if is_live_mode else 'red'}]{'LIVE BROKER EXECUTION' if is_live_mode else 'DRY-RUN (Simulated)'}[/]"
    )
    console.print(Panel(banner, border_style="cyan", title="Institutional Quantitative Suite"))

    # Khởi tạo MT5 Connector
    connector = MT5Connector(login=login, password=password, server=server, path=path)
    if not connector.connect():
        console.print("[bold red]❌ Failed to connect to MetaTrader 5. Retrying in 10s...[/]")
        time.sleep(10)
        if not connector.connect():
            return

    # Khởi tạo các Engine & Allocator
    fdm = settings.carver.fdm if settings.carver else 1.25
    fcap = settings.carver.forecast_cap if settings.carver else 20.0

    regime_engines: Dict[str, RegimeEngine] = {}
    signal_engines: Dict[str, SignalEngine] = {}
    active_symbols: List[str] = []

    console.print(f"[bold blue]ℹ[/] Resolving and warming up {len(target_symbols)} assets on Exness Broker...")
    for sym in target_symbols:
        broker_sym = connector.resolve_symbol(sym)
        if broker_sym:
            active_symbols.append(sym)
            regime_engines[sym] = RegimeEngine(settings.regime)
            signal_engines[sym] = SignalEngine(settings.signal, fdm=fdm, forecast_cap=fcap)

    if not active_symbols:
        console.print("[bold red]❌ No active tradeable symbols found on broker.[/]")
        connector.disconnect()
        return

    # Khởi tạo Portfolio Allocator
    allocator = PortfolioAllocator(active_symbols, correlation_lookback=40, max_correlation_threshold=0.75)

    # Warmup từng Symbol bằng 60 nến quá khứ
    for sym in active_symbols:
        warmup_bars = connector.fetch_historical_bars(sym, timeframe_str=tf, count=60)
        if warmup_bars:
            for b in warmup_bars:
                reg_ev = regime_engines[sym].update(b)
                signal_engines[sym].update(b, reg_ev, PositionSide.FLAT)
            # Cung cấp dữ liệu giá nến đóng cửa cho Allocator
            for b in warmup_bars[-30:]:
                allocator.update_prices({sym: b.close})

    console.print(f"[bold green]✔[/] Warmup completed for all {len(active_symbols)} assets!")

    # Khởi tạo Risk Engine & Execution Engine
    risk_engine = RiskEngine(settings.risk, settings.symbols, carver_config=settings.carver)
    execution_engine = ExecutionEngine(settings.execution, settings.symbols)

    # Theo dõi trạng thái dời Stop Loss theo từng vị thế mở
    peak_profit_prices: Dict[str, float] = {}
    be_locked_symbols: set = set()

    # Vòng lặp giám sát đa tài sản
    try:
        while True:
            # Stage 4: Heartbeat & Auto-reconnect kiểm tra kết nối MT5
            acc_state = connector.get_account_state()
            if not acc_state or acc_state.get("equity", 0) <= 0:
                console.print("[bold red]⚠ Lost MT5 connection. Reconnecting automatically...[/]")
                connector.connect()
                time.sleep(5)
                continue

            current_equity = acc_state["equity"]
            current_balance = acc_state["balance"]

            snapshot = risk_engine.update_account_state(
                timestamp=datetime.now(),
                current_equity=current_equity,
                current_balance=current_balance,
                margin_used=acc_state["margin"],
            )

            # Cảnh báo khẩn cấp nếu Circuit Breaker kích hoạt
            if snapshot.circuit_breaker_active:
                notifier.send_circuit_breaker_alert(snapshot.daily_drawdown_pct, settings.risk.max_daily_drawdown_pct)

            # Lấy danh sách vị thế mở thời gian thực từ MT5
            open_positions = connector.get_open_positions()

            # Stage 2: Tính toán trọng số danh mục động từ Carver Allocator
            diversified_weights = allocator.compute_diversified_weights(active_symbols, open_positions)

            dashboard_rows = []
            web_assets_data = []
            triggered_orders = []

            price_snapshot = {}

            # Quét từng tài sản trong danh mục
            for sym in active_symbols:
                latest_bars = connector.fetch_historical_bars(sym, timeframe_str=tf, count=2)
                if not latest_bars:
                    continue

                curr_bar = latest_bars[-1]
                price_snapshot[sym] = curr_bar.close

                regime_ev = regime_engines[sym].update(curr_bar)
                current_pos = open_positions.get(sym)
                pos_side = current_pos.side if current_pos else PositionSide.FLAT

                signal_ev = signal_engines[sym].update(curr_bar, regime_ev, pos_side)
                spec = settings.symbols.get(sym)
                pip_size = spec.pip_size if spec else 0.0001
                atr_val = max(signal_ev.atr, 1e-4)

                order_status_str = "FLAT"

                # =============================================================
                # STAGE 1: DYNAMIC POSITION LIFECYCLE (BE, TRAILING SL, REVERSAL EXIT)
                # =============================================================
                if current_pos and current_pos.side != PositionSide.FLAT:
                    order_status_str = f"[bold green]{current_pos.side.value} {current_pos.volume:.2f}L[/] (${current_pos.unrealized_pnl:+.2f})"

                    # Cập nhật giá cao/thấp nhất từ lúc mở vị thế
                    if sym not in peak_profit_prices:
                        peak_profit_prices[sym] = curr_bar.close
                    if current_pos.side == PositionSide.LONG:
                        peak_profit_prices[sym] = max(peak_profit_prices[sym], curr_bar.high)
                    else:
                        peak_profit_prices[sym] = min(peak_profit_prices[sym], curr_bar.low)

                    # 1. Tự động đóng lệnh khi SignalEngine phát hiện Đảo Chiều Xu Hướng (Reversal Exit)
                    if (current_pos.side == PositionSide.LONG and signal_ev.action in (SignalAction.CLOSE_BUY, SignalAction.SELL)) or \
                       (current_pos.side == PositionSide.SHORT and signal_ev.action in (SignalAction.CLOSE_SELL, SignalAction.BUY)):
                        console.print(f"[bold red]🛑 SIGNAL REVERSAL DETECTED ON {sym}:[/] Closing position #{current_pos.ticket} immediately!")
                        if is_live_mode and current_pos.ticket:
                            if connector.close_position(current_pos.ticket, comment="WuWei Reversal Exit"):
                                notifier.send_position_closed(sym, current_pos.side.value, curr_bar.close, current_pos.unrealized_pnl, "Signal Reversal", current_pos.ticket)
                        order_status_str = f"[bold red]CLOSED (Reversal)[/]"

                    # 2. Cơ chế Khóa Hòa Vốn Tự Động (Break-Even Lock tại >= 1.5 * ATR)
                    elif sym not in be_locked_symbols:
                        be_trigger = 1.5 * atr_val
                        be_buffer = 2.0 * pip_size
                        if current_pos.side == PositionSide.LONG and (peak_profit_prices[sym] >= current_pos.entry_price + be_trigger):
                            new_sl = current_pos.entry_price + be_buffer
                            if new_sl > current_pos.stop_loss:
                                console.print(f"[bold cyan]🛡️ BREAK-EVEN TRIGGERED ON {sym}:[/] Moving SL from {current_pos.stop_loss:.4f} -> {new_sl:.4f}")
                                if is_live_mode and current_pos.ticket:
                                    if connector.modify_stop_loss(current_pos.ticket, new_sl, symbol=sym):
                                        notifier.send_trailing_stop_updated(sym, current_pos.stop_loss, new_sl, "Break-even", current_pos.ticket)
                                        be_locked_symbols.add(sym)
                        elif current_pos.side == PositionSide.SHORT and (peak_profit_prices[sym] <= current_pos.entry_price - be_trigger):
                            new_sl = current_pos.entry_price - be_buffer
                            if new_sl < current_pos.stop_loss:
                                console.print(f"[bold cyan]🛡️ BREAK-EVEN TRIGGERED ON {sym}:[/] Moving SL from {current_pos.stop_loss:.4f} -> {new_sl:.4f}")
                                if is_live_mode and current_pos.ticket:
                                    if connector.modify_stop_loss(current_pos.ticket, new_sl, symbol=sym):
                                        notifier.send_trailing_stop_updated(sym, current_pos.stop_loss, new_sl, "Break-even", current_pos.ticket)
                                        be_locked_symbols.add(sym)

                    # 3. Cơ chế Trailing Stop Động (Chandelier / ATR Trailing)
                    else:
                        trailing_dist = 2.0 * atr_val
                        if current_pos.side == PositionSide.LONG:
                            trail_sl = peak_profit_prices[sym] - trailing_dist
                            if trail_sl > current_pos.stop_loss + (1.0 * pip_size):
                                console.print(f"[bold green]📈 TRAILING STOP ADVANCED ON {sym}:[/] Moving SL {current_pos.stop_loss:.4f} -> {trail_sl:.4f}")
                                if is_live_mode and current_pos.ticket:
                                    if connector.modify_stop_loss(current_pos.ticket, trail_sl, symbol=sym):
                                        notifier.send_trailing_stop_updated(sym, current_pos.stop_loss, trail_sl, "Trailing Stop", current_pos.ticket)
                        elif current_pos.side == PositionSide.SHORT:
                            trail_sl = peak_profit_prices[sym] + trailing_dist
                            if trail_sl < current_pos.stop_loss - (1.0 * pip_size):
                                console.print(f"[bold green]📈 TRAILING STOP ADVANCED ON {sym}:[/] Moving SL {current_pos.stop_loss:.4f} -> {trail_sl:.4f}")
                                if is_live_mode and current_pos.ticket:
                                    if connector.modify_stop_loss(current_pos.ticket, trail_sl, symbol=sym):
                                        notifier.send_trailing_stop_updated(sym, current_pos.stop_loss, trail_sl, "Trailing Stop", current_pos.ticket)

                # =============================================================
                # STAGE 2: CARVER ALLOCATION & CORRELATION RISK FILTER
                # =============================================================
                elif regime_ev.is_trading_allowed and signal_ev.action in (SignalAction.BUY, SignalAction.SELL):
                    target_side_str = "BUY" if signal_ev.action == SignalAction.BUY else "SELL"

                    # 1. Kiểm tra hạn mức ngành (Sector Risk Cap)
                    sec_exceeded, sec_reason = allocator.check_sector_risk_limit(sym, open_positions, max_positions_per_sector=2)
                    # 2. Kiểm tra xung đột tương quan cao (De-correlation Filter)
                    corr_conflict, corr_reason = allocator.check_correlation_conflict(sym, target_side_str, open_positions)

                    if sec_exceeded:
                        order_status_str = f"[dim red]BLOCKED: {sec_reason}[/]"
                    elif corr_conflict:
                        order_status_str = f"[dim yellow]BLOCKED: {corr_reason}[/]"
                    else:
                        inst_weight = diversified_weights.get(sym, 1.0 / len(active_symbols))
                        order_ev, _ = risk_engine.evaluate_order(
                            signal=signal_ev,
                            current_price=curr_bar.close,
                            account=snapshot,
                            open_positions=open_positions,
                            instrument_weight=inst_weight,
                        )
                        if order_ev is not None:
                            triggered_orders.append((sym, order_ev, curr_bar.close, signal_ev.carver_forecast))
                            order_status_str = f"[bold yellow]SIGNAL: {order_ev.side.value} {order_ev.volume:.2f}L[/]"

                regime_badge = (
                    f"[green]{regime_ev.state.value}[/]"
                    if regime_ev.is_trading_allowed
                    else f"[dim]{regime_ev.state.value}[/]"
                )
                action_badge = (
                    f"[bold green]BUY[/]" if signal_ev.action == SignalAction.BUY
                    else (f"[bold red]SELL[/]" if signal_ev.action == SignalAction.SELL else "[dim]HOLD[/]")
                )

                dashboard_rows.append({
                    "symbol": sym,
                    "class": ASSET_CLASS_MAP.get(sym, "CFD"),
                    "price": curr_bar.close,
                    "regime": regime_badge,
                    "adx": regime_ev.adx,
                    "chop": regime_ev.chop,
                    "forecast": signal_ev.carver_forecast,
                    "action": action_badge,
                    "status": order_status_str,
                })

                # Thu thập dữ liệu cho Web Telemetry JSON
                web_assets_data.append({
                    "symbol": sym,
                    "class": ASSET_CLASS_MAP.get(sym, "CFD"),
                    "price": curr_bar.close,
                    "regime": regime_ev.state.value,
                    "adx": round(regime_ev.adx, 1),
                    "chop": round(regime_ev.chop, 1),
                    "forecast": round(signal_ev.carver_forecast, 1),
                    "action": signal_ev.action.value,
                    "status": order_status_str.replace("[bold green]", "").replace("[/]", "").replace("[bold yellow]", "").replace("[dim red]", "").replace("[dim yellow]", "").replace("[bold red]", ""),
                })

            # Cập nhật giá nến mới vào Allocator để tinh chỉnh ma trận tương quan
            allocator.update_prices(price_snapshot)

            # =============================================================
            # STAGE 3: EXPORT WEB TELEMETRY JSON & RENDER CONSOLE DASHBOARD
            # =============================================================
            acc_data = {
                "login": login,
                "server": server,
                "balance": current_balance,
                "equity": current_equity,
                "free_margin": snapshot.free_margin,
                "daily_drawdown_pct": snapshot.daily_drawdown_pct,
                "circuit_breaker_active": snapshot.circuit_breaker_active,
            }
            export_telemetry_state(acc_data, web_assets_data, dashboard_json_path)

            console.clear()
            header_text = (
                f"[bold white]Account:[/] [cyan]{login}[/] ({server}) | "
                f"[bold white]Balance:[/] [yellow]${current_balance:,.2f}[/] | "
                f"[bold white]Equity:[/] [bold green]${current_equity:,.2f}[/] | "
                f"[bold white]Free Margin:[/] [green]${snapshot.free_margin:,.2f}[/] | "
                f"[bold white]Daily DD:[/] [yellow]{snapshot.daily_drawdown_pct*100:.2f}%[/] | "
                f"[bold white]Circuit Breaker:[/] [{'green]NORMAL' if not snapshot.circuit_breaker_active else 'red]LOCKED'}[/]"
            )
            console.print(Panel(header_text, border_style="cyan", title=f"WuWei Institutional Telemetry [{datetime.now().strftime('%H:%M:%S')} - {tf}]"))

            table = Table(show_header=True, header_style="bold magenta", expand=True)
            table.add_column("Symbol", style="bold cyan", width=9)
            table.add_column("Asset Class", style="dim", width=14)
            table.add_column("Price", justify="right", width=11)
            table.add_column("Thiên Thời (Regime)", justify="center", width=19)
            table.add_column("ADX/CHOP", justify="center", width=12)
            table.add_column("Forecast (F)", justify="right", width=13)
            table.add_column("Action", justify="center", width=9)
            table.add_column("Position / Execution", justify="left", width=25)

            for r in dashboard_rows:
                p_fmt = f"{r['price']:.5f}" if "FX" in r["class"] else f"{r['price']:,.2f}"
                table.add_row(
                    r["symbol"],
                    r["class"],
                    p_fmt,
                    r["regime"],
                    f"{r['adx']:.1f}/{r['chop']:.1f}",
                    f"{r['forecast']:+.1f}",
                    r["action"],
                    r["status"],
                )

            console.print(table)

            # Thực thi các lệnh kích hoạt
            if triggered_orders:
                console.print(f"\n[bold green]⚡ TRIGGERED ORDERS IN THIS CYCLE: {len(triggered_orders)}[/]")
                for sym, order_ev, c_price, forecast_val in triggered_orders:
                    console.print(
                        f"  -> [bold yellow]{order_ev.side.value} {order_ev.volume:.2f} lots {sym}[/] @ {c_price:.2f} | "
                        f"SL={order_ev.stop_loss:.2f} | Forecast={forecast_val:+.1f}"
                    )
                    if is_live_mode:
                        ticket = connector.send_market_order(
                            generic_symbol=sym,
                            side=order_ev.side.value,
                            volume=order_ev.volume,
                            stop_loss=order_ev.stop_loss,
                            comment=f"WuWei_{order_ev.side.value[:4]}",
                        )
                        if ticket:
                            console.print(f"     [bold green]✔ Live Order Executed on Exness! Ticket: #{ticket}[/]")
                            notifier.send_order_opened(sym, order_ev.side.value, order_ev.volume, c_price, order_ev.stop_loss, forecast_val, ticket)
                    else:
                        console.print(f"     [bold cyan]⚡ [DRY-RUN] Simulated execution (no broker order sent).[/]")

            if args.once:
                break

            time.sleep(args.scan_interval)

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Stopping WuWei Multi-Asset Live Runner gracefully...[/]")
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
