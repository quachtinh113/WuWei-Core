"""
WuWei Core - Backtest Performance Metrics
Institutional Tear Sheet & Quantitative Performance Evaluation
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from core.types import PositionSide, TradeRecord


@dataclass
class PerformanceMetrics:
    """Tập hợp đầy đủ các chỉ số hiệu suất cấp Quỹ (Institutional Metrics)"""
    initial_capital: float
    ending_equity: float
    total_net_profit: float
    total_return_pct: float
    cagr_pct: float
    annualized_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    max_drawdown_amount: float
    max_drawdown_duration_bars: int
    profit_factor: float
    win_rate_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    break_even_trades: int
    avg_trade_pnl: float
    avg_win: float
    avg_loss: float
    win_loss_payoff_ratio: float
    expectancy_usd: float
    avg_r_multiple: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    long_trades_count: int
    short_trades_count: int
    long_win_rate_pct: float
    short_win_rate_pct: float
    total_commission_paid: float
    total_slippage_cost: float


class MetricsCalculator:
    """
    Tính toán chỉ số hiệu suất từ lịch sử giao dịch (Trade Log) và Đường cong vốn (Equity Curve).
    """

    @staticmethod
    def calculate(
        trades: List[TradeRecord],
        equity_series: pd.Series,
        initial_capital: float,
        annualization_factor: float = 252.0  # Daily bars
    ) -> PerformanceMetrics:
        ending_equity = float(equity_series.iloc[-1]) if len(equity_series) > 0 else initial_capital
        total_net_profit = ending_equity - initial_capital
        total_return_pct = (total_net_profit / initial_capital) * 100.0 if initial_capital > 0 else 0.0

        # 1. Tính toán Drawdown Series
        peak = equity_series.cummax()
        drawdown_series = (peak - equity_series) / peak
        max_drawdown_pct = float(drawdown_series.max() * 100.0) if len(drawdown_series) > 0 else 0.0
        max_drawdown_amount = float((peak - equity_series).max()) if len(peak) > 0 else 0.0

        # Tính Max Drawdown Duration (số bars)
        is_dd = drawdown_series > 0
        dd_runs = (~is_dd).cumsum()[is_dd]
        if not dd_runs.empty:
            max_dd_duration = int(dd_runs.value_counts().max())
        else:
            max_dd_duration = 0

        # 2. Daily returns & Sharpe / Sortino
        returns = equity_series.pct_change().dropna()
        if len(returns) > 1 and returns.std() > 1e-8:
            mean_ret = float(returns.mean())
            std_ret = float(returns.std())
            ann_vol = std_ret * math.sqrt(annualization_factor) * 100.0
            sharpe_ratio = float((mean_ret / std_ret) * math.sqrt(annualization_factor))

            downside_returns = returns[returns < 0]
            downside_std = float(downside_returns.std()) if len(downside_returns) > 1 else std_ret
            sortino_ratio = (
                float((mean_ret / downside_std) * math.sqrt(annualization_factor))
                if downside_std > 1e-8
                else sharpe_ratio
            )
        else:
            ann_vol = 0.0
            sharpe_ratio = 0.0
            sortino_ratio = 0.0

        # CAGR
        num_periods = len(equity_series)
        years = num_periods / annualization_factor if num_periods > 0 else 1.0
        if years > 0 and ending_equity > 0 and initial_capital > 0:
            cagr_pct = (((ending_equity / initial_capital) ** (1.0 / max(years, 0.1))) - 1.0) * 100.0
        else:
            cagr_pct = 0.0

        calmar_ratio = (cagr_pct / max_drawdown_pct) if max_drawdown_pct > 1e-4 else 0.0

        # 3. Phân tích chi tiết từng Trade
        total_trades = len(trades)
        if total_trades == 0:
            return PerformanceMetrics(
                initial_capital=initial_capital,
                ending_equity=ending_equity,
                total_net_profit=total_net_profit,
                total_return_pct=total_return_pct,
                cagr_pct=cagr_pct,
                annualized_volatility=ann_vol,
                sharpe_ratio=sharpe_ratio,
                sortino_ratio=sortino_ratio,
                calmar_ratio=calmar_ratio,
                max_drawdown_pct=max_drawdown_pct,
                max_drawdown_amount=max_drawdown_amount,
                max_drawdown_duration_bars=max_dd_duration,
                profit_factor=0.0,
                win_rate_pct=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                break_even_trades=0,
                avg_trade_pnl=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                win_loss_payoff_ratio=0.0,
                expectancy_usd=0.0,
                avg_r_multiple=0.0,
                max_consecutive_wins=0,
                max_consecutive_losses=0,
                long_trades_count=0,
                short_trades_count=0,
                long_win_rate_pct=0.0,
                short_win_rate_pct=0.0,
                total_commission_paid=0.0,
                total_slippage_cost=0.0,
            )

        profits = [t.net_pnl for t in trades if t.net_pnl > 1e-6]
        losses = [t.net_pnl for t in trades if t.net_pnl < -1e-6]
        evens = [t for t in trades if abs(t.net_pnl) <= 1e-6]

        winning_trades = len(profits)
        losing_trades = len(losses)
        break_even_trades = len(evens)

        win_rate_pct = (winning_trades / total_trades) * 100.0
        gross_profit = sum(profits) if profits else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 1e-6 else (99.0 if gross_profit > 0 else 0.0)

        avg_win = float(np.mean(profits)) if profits else 0.0
        avg_loss = float(abs(np.mean(losses))) if losses else 0.0
        payoff_ratio = (avg_win / avg_loss) if avg_loss > 1e-6 else 0.0

        all_pnls = [t.net_pnl for t in trades]
        avg_trade_pnl = float(np.mean(all_pnls))
        expectancy_usd = (win_rate_pct / 100.0 * avg_win) - ((1.0 - win_rate_pct / 100.0) * avg_loss)
        avg_r_multiple = float(np.mean([t.r_multiple for t in trades]))

        # Chuỗi thắng/thua liên tiếp
        max_cons_wins = 0
        max_cons_losses = 0
        cur_cons_wins = 0
        cur_cons_losses = 0
        for p in all_pnls:
            if p > 0:
                cur_cons_wins += 1
                cur_cons_losses = 0
                max_cons_wins = max(max_cons_wins, cur_cons_wins)
            elif p < 0:
                cur_cons_losses += 1
                cur_cons_wins = 0
                max_cons_losses = max(max_cons_losses, cur_cons_losses)

        # Phân tách Long vs Short
        long_trades = [t for t in trades if t.side == PositionSide.LONG]
        short_trades = [t for t in trades if t.side == PositionSide.SHORT]
        long_count = len(long_trades)
        short_count = len(short_trades)
        long_wins = len([t for t in long_trades if t.net_pnl > 0])
        short_wins = len([t for t in short_trades if t.net_pnl > 0])
        long_win_rate = (long_wins / long_count * 100.0) if long_count > 0 else 0.0
        short_win_rate = (short_wins / short_count * 100.0) if short_count > 0 else 0.0

        total_commission = sum(t.commission for t in trades)
        total_slippage = sum(t.slippage * t.volume for t in trades)

        return PerformanceMetrics(
            initial_capital=initial_capital,
            ending_equity=ending_equity,
            total_net_profit=total_net_profit,
            total_return_pct=total_return_pct,
            cagr_pct=cagr_pct,
            annualized_volatility=ann_vol,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            calmar_ratio=calmar_ratio,
            max_drawdown_pct=max_drawdown_pct,
            max_drawdown_amount=max_drawdown_amount,
            max_drawdown_duration_bars=max_dd_duration,
            profit_factor=profit_factor,
            win_rate_pct=win_rate_pct,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            break_even_trades=break_even_trades,
            avg_trade_pnl=avg_trade_pnl,
            avg_win=avg_win,
            avg_loss=avg_loss,
            win_loss_payoff_ratio=payoff_ratio,
            expectancy_usd=expectancy_usd,
            avg_r_multiple=avg_r_multiple,
            max_consecutive_wins=max_cons_wins,
            max_consecutive_losses=max_cons_losses,
            long_trades_count=long_count,
            short_trades_count=short_count,
            long_win_rate_pct=long_win_rate,
            short_win_rate_pct=short_win_rate,
            total_commission_paid=total_commission,
            total_slippage_cost=total_slippage,
        )
