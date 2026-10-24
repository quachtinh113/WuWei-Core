"""
WuWei Core Quantitative Trading Engine
Triết lý: "Thuận theo tự nhiên / Vô vi" (Zero-Forecast Trend Riding)
Main Entry Point - Backtesting & Research Runner
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Thêm thư mục hiện tại vào sys.path để hỗ trợ chạy trực tiếp
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Đảm bảo mã hóa UTF-8 an toàn trên Windows Console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config.settings import get_settings
from backtest.engine import EventDrivenBacktester


def parse_args():
    parser = argparse.ArgumentParser(
        description="WuWei Core - Institutional Quantitative Trend-Following Engine (pysystemtrade Integrated)"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="XAUUSD",
        choices=["XAUUSD", "EURUSD", "USDJPY"],
        help="Mã tài sản kiểm thử đơn lẻ (Default: XAUUSD)",
    )
    parser.add_argument(
        "--portfolio",
        action="store_true",
        help="Chạy danh mục Đa tài sản cấp Quỹ (XAUUSD + EURUSD + USDJPY) với Carver IDM & Buffering",
    )
    parser.add_argument(
        "--no-carver",
        action="store_true",
        help="Tắt cơ chế định lượng Robert Carver để chạy chế độ cổ điển WuWei 1% Risk",
    )
    parser.add_argument(
        "--bars",
        type=int,
        default=2000,
        help="Số lượng nến mô phỏng (Default: 2000)",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Đường dẫn file CSV dữ liệu OHLCV thực tế nếu có",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Đường dẫn file cấu hình YAML tùy chỉnh",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    console = Console()

    # Nạp cấu hình hệ thống
    settings = get_settings(args.config)
    if args.no_carver:
        settings.carver.enabled = False

    engine_mode = "CARVER INSTITUTIONAL (pysystemtrade + Vô Vi)" if settings.carver.enabled else "CLASSIC WUWEI (1% ATR)"

    # In banner triết lý thiết kế hệ thống
    banner_text = (
        "[bold cyan]WUWEI CORE - INSTITUTIONAL QUANTITATIVE SYSTEM[/bold cyan]\n"
        "[italic yellow]\"Thiên địa bất nhân, dĩ vạn vật vi sô cẩu - Thuận theo tự nhiên / Vô vi\"[/italic yellow]\n"
        f"[bold magenta]Operating Mode:[/] [bold green]{engine_mode}[/]\n\n"
        "[bold white]Deep Quantitative Architecture (pysystemtrade Synergy):[/bold white]\n"
        "  1. [bold green]Thiên thời[/] (Regime Engine): ADX(14) + CHOP(14) + Vol Ratio -> Khóa Vô Vi khi sideway (F -> 0)\n"
        "  2. [bold green]Thuận dòng[/] (Carver Multi-Horizon): Donchian Fast/Med/Slow + EWMAC [-20, +20] x FDM 1.25\n"
        "  3. [bold green]Tri chỉ[/]   (Cash Vol Targeting): 20% Ann Vol Target -> Daily Cash Target, De-leveraging > 1.5% DD\n"
        "  4. [bold green]Hành vô vi[/] (Carver Buffering): Vùng đệm 10% triệt tiêu 50% giao dịch vi tế, Trailing Stop mở R:R\n"
        "  5. [bold green]Thực chứng[/] (Multi-Asset IDM): Hệ số đa dạng hóa danh mục IDM 1.35, Slippage 0.5 pip, Commission $7/lot"
    )
    console.print(Panel(banner_text, border_style="cyan", title="Institutional Systematic Engine"))

    backtester = EventDrivenBacktester(settings)

    if args.portfolio:
        console.print("[bold green]✔[/] Running Multi-Asset Carver Portfolio Simulation (XAUUSD, EURUSD, USDJPY)...")
        market_data = {}
        for sym, start_p in [("XAUUSD", 2050.0), ("EURUSD", 1.0850), ("USDJPY", 155.00)]:
            market_data[sym] = backtester.generate_synthetic_market_data(
                symbol=sym,
                bars_count=args.bars,
                start_price=start_p,
                seed=42 if sym == "XAUUSD" else (100 if sym == "EURUSD" else 200)
            )

        weights = {"XAUUSD": 0.40, "EURUSD": 0.30, "USDJPY": 0.30}
        result = backtester.run_portfolio(market_data, weights=weights)
        backtester.print_tear_sheet(result, symbol="MULTI-ASSET PORTFOLIO (XAUUSD + EURUSD + USDJPY)")
    else:
        symbol = args.symbol
        console.print(f"[bold green]✔[/] Configuration loaded successfully. Asset: [bold yellow]{symbol}[/]")

        # Nạp dữ liệu thị trường
        if args.csv and Path(args.csv).exists():
            console.print(f"[bold blue]ℹ[/] Loading historical market data from: {args.csv}")
            df = pd.read_csv(args.csv)
        else:
            console.print(
                f"[bold blue]ℹ[/] Synthesizing institutional-grade market data ({args.bars} bars) with regime switching..."
            )
            start_price = 2050.0 if symbol == "XAUUSD" else (1.0850 if symbol == "EURUSD" else 155.00)
            df = backtester.generate_synthetic_market_data(
                symbol=symbol,
                bars_count=args.bars,
                start_price=start_price,
                seed=42
            )

        console.print(f"[bold green]✔[/] Market data ready: {len(df)} bars. Executing event-driven simulation...")

        # Chạy mô phỏng Backtest
        result = backtester.run(df, symbol=symbol)
        backtester.print_tear_sheet(result, symbol=symbol)

    # In chi tiết 10 giao dịch tiêu biểu
    if result.trades:
        console.rule("[bold magenta]SAMPLE EXECUTED TRADES (FIRST 5 & LAST 5)[/bold magenta]")
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Trade ID", width=16)
        table.add_column("Side", width=8)
        table.add_column("Entry Time", width=18)
        table.add_column("Exit Time", width=18)
        table.add_column("Entry Price", justify="right")
        table.add_column("Exit Price", justify="right")
        table.add_column("Net PnL ($)", justify="right")
        table.add_column("R-Mult", justify="right")
        table.add_column("Exit Reason", width=18)

        sample_trades = result.trades[:5] + (result.trades[-5:] if len(result.trades) > 5 else [])
        # Loại bỏ trùng lặp nếu số trade ít
        seen_ids = set()
        dedup_trades = []
        for t in sample_trades:
            if t.trade_id not in seen_ids:
                seen_ids.add(t.trade_id)
                dedup_trades.append(t)

        for t in dedup_trades:
            pnl_color = "green" if t.net_pnl > 0 else "red"
            side_color = "cyan" if t.side.value == "LONG" else "magenta"
            table.add_row(
                t.trade_id,
                f"[{side_color}]{t.side.value}[/{side_color}]",
                t.entry_time.strftime("%Y-%m-%d %H:%M"),
                t.exit_time.strftime("%Y-%m-%d %H:%M"),
                f"{t.entry_price:.2f}",
                f"{t.exit_price:.2f}",
                f"[{pnl_color}]${t.net_pnl:,.2f}[/{pnl_color}]",
                f"{t.r_multiple:+.2f}R",
                f"[yellow]{t.exit_reason.value}[/yellow]"
            )
        console.print(table)
        console.print("\n")


if __name__ == "__main__":
    main()
