"""
WuWei Core - Event-Driven Backtesting Engine
Institutional Simulation Pipeline with Microstructure Cost Modeling
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from config.settings import AppSettings
from core.events import BarEvent, EventType
from core.types import (
    AccountSnapshot,
    ExitReason,
    PositionSide,
    TradeRecord,
)
from engines.execution_engine import ExecutionEngine
from engines.regime_engine import RegimeEngine
from engines.risk_engine import RiskEngine
from engines.signal_engine import SignalEngine
from backtest.metrics import MetricsCalculator, PerformanceMetrics


@dataclass
class BacktestResult:
    metrics: PerformanceMetrics
    trades: List[TradeRecord]
    equity_curve: pd.DataFrame
    daily_snapshots: List[AccountSnapshot]


class EventDrivenBacktester:
    """
    Event-Driven Backtest Pipeline kết nối 5 tầng độc lập:
    Bar Data -> RegimeEngine -> SignalEngine -> RiskEngine -> ExecutionEngine
    """

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.symbol_specs = settings.symbols
        self.console = Console()

        # Khởi tạo các module engine độc lập
        self.regime_engine = RegimeEngine(settings.regime)
        self.signal_engine = SignalEngine(
            settings.signal,
            fdm=settings.carver.fdm if settings.carver else 1.25,
            forecast_cap=settings.carver.forecast_cap if settings.carver else 20.0
        )
        self.risk_engine = RiskEngine(
            settings.risk,
            self.symbol_specs,
            carver_config=settings.carver
        )
        self.execution_engine = ExecutionEngine(settings.execution, self.symbol_specs)

    def run(self, df: pd.DataFrame, symbol: str) -> BacktestResult:
        """
        Thực thi mô phỏng sự kiện trên chuỗi dữ liệu nến OHLCV.
        DataFrame yêu cầu các cột: ['timestamp' hoặc Index, 'open', 'high', 'low', 'close', 'volume']
        """
        # Reset toàn bộ trạng thái hệ thống
        self.regime_engine.reset()
        self.signal_engine.reset()
        self.risk_engine.reset(self.settings.risk.initial_capital)
        self.execution_engine.reset()

        initial_capital = self.settings.risk.initial_capital
        current_balance = initial_capital
        current_equity = initial_capital

        equity_records = []
        account_snapshots = []

        # Chuẩn bị dữ liệu nến chuẩn hóa
        bars_data = self._prepare_data(df, symbol)

        for bar in bars_data:
            # 1. Quản lý vị thế mở: Kiểm tra Trailing Stop, Break-Even & SL
            current_pos = self.execution_engine.open_positions.get(symbol)
            pos_side = current_pos.side if current_pos else PositionSide.FLAT

            # 2. Cập nhật Thiên Thời (Regime Engine)
            regime_event = self.regime_engine.update(bar)

            # 3. Phát sinh tín hiệu Thuận Dòng (Signal Engine)
            signal_event = self.signal_engine.update(bar, regime_event, pos_side)

            # 4. Cập nhật vị thế mở với nến mới và tín hiệu thoát (nếu có)
            closed_trades = self.execution_engine.on_bar_update(bar, signal_event)
            for trade in closed_trades:
                current_balance += trade.net_pnl

            # 5. Cập nhật Equity tức thời (Balance + Floating PnL)
            open_pos = self.execution_engine.open_positions.get(symbol)
            unrealized_pnl = open_pos.unrealized_pnl if open_pos else 0.0
            current_equity = current_balance + unrealized_pnl

            # 6. Kiểm tra Quản lý Rủi ro & Circuit Breaker (Tri Chỉ)
            snapshot = self.risk_engine.update_account_state(
                timestamp=bar.timestamp,
                current_equity=current_equity,
                current_balance=current_balance
            )
            account_snapshots.append(snapshot)

            # Nếu Circuit Breaker vừa kích hoạt -> Khẩn cấp đóng toàn bộ vị thế
            if snapshot.circuit_breaker_active and open_pos is not None:
                forced_closes = self.execution_engine.emergency_liquidate_all(
                    bar, reason=ExitReason.CIRCUIT_BREAKER
                )
                for trade in forced_closes:
                    current_balance += trade.net_pnl
                current_equity = current_balance
                open_pos = None

            # 7. Đánh giá và định cỡ lệnh mới (chỉ khi không có vị thế mở)
            if open_pos is None or open_pos.side == PositionSide.FLAT:
                order_event, cb_event = self.risk_engine.evaluate_order(
                    signal=signal_event,
                    current_price=bar.close,
                    account=snapshot,
                    open_positions=self.execution_engine.open_positions
                )
                if order_event is not None:
                    # Khớp lệnh qua ExecutionEngine
                    fill_event = self.execution_engine.process_order(order_event, bar)
                    # Trừ ngay commission vào balance
                    current_balance -= fill_event.commission

            # Ghi nhận đường cong vốn
            equity_records.append({
                "timestamp": bar.timestamp,
                "balance": current_balance,
                "equity": current_equity,
                "drawdown_pct": snapshot.daily_drawdown_pct,
                "regime": regime_event.state.value,
                "is_trading_allowed": regime_event.is_trading_allowed
            })

        # Đóng tất cả các vị thế còn sót lại tại nến cuối cùng
        last_bar = bars_data[-1]
        final_closes = self.execution_engine.emergency_liquidate_all(last_bar, reason=ExitReason.MANUAL)
        for trade in final_closes:
            current_balance += trade.net_pnl
        current_equity = current_balance

        equity_df = pd.DataFrame(equity_records).set_index("timestamp")
        equity_series = equity_df["equity"]

        metrics = MetricsCalculator.calculate(
            trades=self.execution_engine.closed_trades,
            equity_series=equity_series,
            initial_capital=initial_capital
        )

        return BacktestResult(
            metrics=metrics,
            trades=self.execution_engine.closed_trades,
            equity_curve=equity_df,
            daily_snapshots=account_snapshots
        )

    def run_portfolio(
        self,
        market_data: Dict[str, pd.DataFrame],
        weights: Optional[Dict[str, float]] = None
    ) -> BacktestResult:
        """
        Mô phỏng Danh mục Đa tài sản Cấp Quỹ (Multi-Asset Carver Portfolio):
        Kết hợp XAUUSD, EURUSD, USDJPY với hệ số nhân đa dạng hóa IDM (Instrument Diversification Multiplier)
        và Vô Vi Position Buffering để tối ưu hóa Sharpe Ratio toàn diện.
        """
        symbols = list(market_data.keys())
        if weights is None:
            # Phân bổ tỷ trọng đều
            w_val = 1.0 / len(symbols)
            weights = {s: w_val for s in symbols}

        # Khởi tạo riêng từng engine Thiên Thời và Thuận Dòng cho từng mã
        regime_engines = {s: RegimeEngine(self.settings.regime) for s in symbols}
        signal_engines = {
            s: SignalEngine(
                self.settings.signal,
                fdm=self.settings.carver.fdm if self.settings.carver else 1.25,
                forecast_cap=self.settings.carver.forecast_cap if self.settings.carver else 20.0
            )
            for s in symbols
        }

        self.risk_engine.reset(self.settings.risk.initial_capital)
        self.execution_engine.reset()

        initial_capital = self.settings.risk.initial_capital
        current_balance = initial_capital
        current_equity = initial_capital

        equity_records = []
        account_snapshots = []

        # Chuẩn hóa chuỗi thời gian cho từng symbol
        symbol_bars: Dict[str, List[BarEvent]] = {
            s: self._prepare_data(market_data[s], s) for s in symbols
        }

        # Tạo chuỗi thời gian hợp nhất
        all_timestamps = sorted(list({b.timestamp for s in symbols for b in symbol_bars[s]}))
        bars_lookup: Dict[Tuple[str, datetime], BarEvent] = {
            (b.symbol, b.timestamp): b for s in symbols for b in symbol_bars[s]
        }

        for ts in all_timestamps:
            latest_bars: Dict[str, BarEvent] = {}
            latest_signals: Dict[str, SignalEvent] = {}

            # 1. Quản lý các vị thế đang mở và cập nhật trailing stops
            for sym in symbols:
                bar = bars_lookup.get((sym, ts))
                if bar is None:
                    continue
                latest_bars[sym] = bar

                pos = self.execution_engine.open_positions.get(sym)
                pos_side = pos.side if pos else PositionSide.FLAT

                regime_ev = regime_engines[sym].update(bar)
                signal_ev = signal_engines[sym].update(bar, regime_ev, pos_side)
                latest_signals[sym] = signal_ev

                closed_trades = self.execution_engine.on_bar_update(bar, signal_ev)
                for tr in closed_trades:
                    current_balance += tr.net_pnl

            # 2. Tính Unrealized PnL tổng thể
            total_unrealized = sum(
                p.unrealized_pnl for p in self.execution_engine.open_positions.values()
            )
            current_equity = current_balance + total_unrealized

            # 3. Cập nhật trạng thái rủi ro tài khoản & Circuit Breakers
            snapshot = self.risk_engine.update_account_state(
                timestamp=ts,
                current_equity=current_equity,
                current_balance=current_balance
            )
            account_snapshots.append(snapshot)

            # Xử lý đóng khẩn cấp nếu Circuit Breaker kích hoạt
            if snapshot.circuit_breaker_active and self.execution_engine.open_positions:
                for sym, pos in list(self.execution_engine.open_positions.items()):
                    bar = latest_bars.get(sym)
                    if bar:
                        cb_closes = self.execution_engine.emergency_liquidate_all(bar, ExitReason.CIRCUIT_BREAKER)
                        for tr in cb_closes:
                            current_balance += tr.net_pnl
                current_equity = current_balance

            # 4. Đánh giá mở vị thế mới cho từng tài sản với Carver IDM & Buffering
            if not snapshot.circuit_breaker_active:
                for sym in symbols:
                    bar = latest_bars.get(sym)
                    sig_ev = latest_signals.get(sym)
                    if bar is None or sig_ev is None:
                        continue

                    open_pos = self.execution_engine.open_positions.get(sym)
                    if open_pos is None or open_pos.side == PositionSide.FLAT:
                        ord_ev, _ = self.risk_engine.evaluate_order(
                            signal=sig_ev,
                            current_price=bar.close,
                            account=snapshot,
                            open_positions=self.execution_engine.open_positions
                        )
                        if ord_ev is not None:
                            fill = self.execution_engine.process_order(ord_ev, bar)
                            current_balance -= fill.commission

            equity_records.append({
                "timestamp": ts,
                "balance": current_balance,
                "equity": current_equity,
                "drawdown_pct": snapshot.daily_drawdown_pct,
                "open_positions": len(self.execution_engine.open_positions)
            })

        # Đóng tất cả các vị thế còn sót lại
        for sym in symbols:
            last_b = symbol_bars[sym][-1]
            for tr in self.execution_engine.emergency_liquidate_all(last_b, ExitReason.MANUAL):
                current_balance += tr.net_pnl
        current_equity = current_balance

        eq_df = pd.DataFrame(equity_records).set_index("timestamp")
        metrics = MetricsCalculator.calculate(
            trades=self.execution_engine.closed_trades,
            equity_series=eq_df["equity"],
            initial_capital=initial_capital
        )

        return BacktestResult(
            metrics=metrics,
            trades=self.execution_engine.closed_trades,
            equity_curve=eq_df,
            daily_snapshots=account_snapshots
        )

    def _prepare_data(self, df: pd.DataFrame, symbol: str) -> List[BarEvent]:
        bars: List[BarEvent] = []
        df_sorted = df.copy()
        if not isinstance(df_sorted.index, pd.DatetimeIndex):
            if "timestamp" in df_sorted.columns:
                df_sorted["timestamp"] = pd.to_datetime(df_sorted["timestamp"])
                df_sorted = df_sorted.sort_values("timestamp")
            elif "datetime" in df_sorted.columns:
                df_sorted["timestamp"] = pd.to_datetime(df_sorted["datetime"])
                df_sorted = df_sorted.sort_values("timestamp")
            else:
                df_sorted["timestamp"] = pd.date_range(start="2024-01-01", periods=len(df_sorted), freq="1h")

        for idx, row in df_sorted.iterrows():
            ts = row["timestamp"] if "timestamp" in row else idx
            if not isinstance(ts, datetime):
                ts = pd.to_datetime(ts).to_pydatetime()

            bar = BarEvent(
                symbol=symbol,
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0))
            )
            bars.append(bar)
        return bars

    @staticmethod
    def generate_synthetic_market_data(
        symbol: str = "XAUUSD",
        bars_count: int = 1500,
        start_price: float = 2050.0,
        seed: int = 42
    ) -> pd.DataFrame:
        """
        Tạo dữ liệu thị trường giả lập cấp tổ chức (Institutional Geometric Brownian Motion + Regime Switching):
        - Mô phỏng các giai đoạn Sideway tích lũy (Chop cao, ADX thấp)
        - Đan xen các giai đoạn bùng nổ xu hướng (Trend dốc, ADX tăng vọt)
        """
        np.random.seed(seed)
        start_time = datetime(2024, 1, 1, 0, 0)
        timestamps = [start_time + timedelta(hours=i) for i in range(bars_count)]

        prices = [start_price]
        regime_phases = []  # 0: Sideway, 1: Bull Trend, 2: Bear Trend

        phase = 0
        phase_counter = 0

        for _ in range(bars_count - 1):
            if phase_counter <= 0:
                phase = np.random.choice([0, 1, 2], p=[0.45, 0.30, 0.25])
                phase_counter = np.random.randint(40, 120)

            phase_counter -= 1
            regime_phases.append(phase)

            if phase == 0:  # Sideway / Mean reverting chop
                drift = 0.0
                vol = 1.5
            elif phase == 1:  # Bull trend burst
                drift = 1.2
                vol = 2.8
            else:  # Bear trend burst
                drift = -1.2
                vol = 3.0

            change = drift + np.random.normal(0, vol)
            next_price = max(prices[-1] + change, 50.0)
            prices.append(next_price)

        regime_phases.append(phase)

        # Tạo OHLCV từ chuỗi giá
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []

        for i, p in enumerate(prices):
            bar_vol = np.random.uniform(1.0, 3.5)
            o = p + np.random.normal(0, 0.5)
            c = p + np.random.normal(0, 0.5)
            h = max(o, c) + abs(np.random.normal(0, bar_vol))
            l = min(o, c) - abs(np.random.normal(0, bar_vol))
            v = np.random.uniform(500, 5000)

            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)
            volumes.append(v)

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        })
        return df

    def print_tear_sheet(self, result: BacktestResult, symbol: str) -> None:
        """
        Hiển thị Tear Sheet hiệu suất định lượng bằng Rich Console sang trọng.
        """
        m = result.metrics
        self.console.print("\n")
        self.console.rule("[bold cyan]WUWEI CORE - INSTITUTIONAL PERFORMANCE TEAR SHEET[/bold cyan]")
        self.console.print(f"[bold yellow]Asset Under Test:[/] {symbol} | [bold yellow]Philosophy:[/] {self.settings.system.philosophy}\n")

        table = Table(title="Quantitative Risk & Return Profile", show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="dim", width=32)
        table.add_column("Value", justify="right", width=22)
        table.add_column("Institutional Benchmark", justify="right", style="cyan", width=25)

        ret_color = "green" if m.total_return_pct >= 0 else "red"
        table.add_row("Initial Capital", f"${m.initial_capital:,.2f}", "$100,000.00")
        table.add_row("Ending Equity", f"${m.ending_equity:,.2f}", "Capital Preservation")
        table.add_row("Net Profit", f"[{ret_color}]${m.total_net_profit:,.2f}[/{ret_color}]", "> 0.0")
        table.add_row("Total Return (%)", f"[{ret_color}]{m.total_return_pct:+.2f}%[/{ret_color}]", "> 15.0% / yr")
        table.add_row("CAGR (%)", f"{m.cagr_pct:+.2f}%", "> 12.0%")
        table.add_row("Sharpe Ratio (Ann.)", f"{m.sharpe_ratio:.2f}", "Target >= 1.50")
        table.add_row("Sortino Ratio", f"{m.sortino_ratio:.2f}", "Target >= 2.00")
        table.add_row("Calmar Ratio", f"{m.calmar_ratio:.2f}", "Target >= 2.00")
        table.add_row("Max Drawdown (%)", f"[bold red]{m.max_drawdown_pct:.2f}%[/bold red]", "Strict Gate < 6.0%")
        table.add_row("Max Drawdown Amount", f"${m.max_drawdown_amount:,.2f}", "< $6,000.00")
        table.add_row("Max DD Duration", f"{m.max_drawdown_duration_bars} bars", "Fast Recovery")
        table.add_row("Profit Factor", f"{m.profit_factor:.2f}", "Target >= 1.60")
        table.add_row("Win Rate (%)", f"{m.win_rate_pct:.2f}%", "35% - 50% (Trend Riding)")
        table.add_row("Win / Loss Payoff Ratio", f"{m.win_loss_payoff_ratio:.2f}", "Target >= 2.50")
        table.add_row("Average R-Multiple", f"{m.avg_r_multiple:+.2f} R", "Positive Expectancy")
        table.add_row("Total Executed Trades", f"{m.total_trades}", "Selective Discipline")
        table.add_row("Long / Short Trades", f"{m.long_trades_count} / {m.short_trades_count}", "Direction Agnostic")
        table.add_row("Commission Paid", f"${m.total_commission_paid:,.2f}", "$7.00 / lot")
        table.add_row("Slippage Incurred", f"${m.total_slippage_cost:,.2f}", "0.5 pip model")

        self.console.print(table)
        self.console.print("[dim]Triết lý Vô Vi: Thua nhỏ theo kỷ luật ATR, ăn trọn con sóng lớn khi xu hướng bùng nổ.[/dim]\n")
