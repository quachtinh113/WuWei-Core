"""
WuWei Core - Comprehensive Unit Tests
Testing 5-Layer Institutional Architecture
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timedelta
import pytest
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import AppSettings
from core.events import BarEvent, RegimeEvent, SignalEvent
from core.types import (
    ExitReason,
    OrderSide,
    Position,
    PositionSide,
    RegimeState,
    SignalAction,
)
from engines.execution_engine import ExecutionEngine
from engines.regime_engine import RegimeEngine
from engines.risk_engine import RiskEngine
from engines.signal_engine import SignalEngine
from backtest.engine import EventDrivenBacktester


@pytest.fixture
def default_settings():
    return AppSettings.load_from_yaml()


@pytest.fixture
def sample_bars():
    """Tạo chuỗi nến giả lập có kiểm soát cho test"""
    start_time = datetime(2024, 1, 1, 10, 0)
    bars = []
    base_price = 2000.0
    for i in range(100):
        t = start_time + timedelta(hours=i)
        # Nến tăng dần
        price = base_price + i * 2.0
        b = BarEvent(
            symbol="XAUUSD",
            timestamp=t,
            open=price - 1.0,
            high=price + 2.0,
            low=price - 2.0,
            close=price + 1.0,
            volume=1000.0
        )
        bars.append(b)
    return bars


class TestRegimeEngine:
    def test_regime_classification_logic(self, default_settings):
        engine = RegimeEngine(default_settings.regime)
        # Kiểm tra hàm phân loại trực tiếp
        dummy_bar = BarEvent("XAUUSD", datetime(2024, 1, 1), 2000, 2010, 1990, 2005)

        # 1. Trạng thái Vô Vi (NO_TREND): ADX < 20 và CHOP > 61.8
        state, allowed = engine._classify_regime(dummy_bar, adx=15.0, chop=65.0, atr_ratio=0.8)
        assert state == RegimeState.NO_TREND
        assert allowed is False

        # 2. Trạng thái Xu Hướng Bùng Nổ: ADX >= 25 và CHOP < 38.2
        state, allowed = engine._classify_regime(dummy_bar, adx=30.0, chop=32.0, atr_ratio=1.5)
        assert state in (RegimeState.TRENDING_BULL, RegimeState.TRENDING_BEAR)
        assert allowed is True

        # 3. Trạng thái Chuyển tiếp (Ranging)
        state, allowed = engine._classify_regime(dummy_bar, adx=22.0, chop=45.0, atr_ratio=1.0)
        assert state == RegimeState.RANGING
        assert allowed is False


class TestSignalEngine:
    def test_donchian_breakout_and_vowi_block(self, default_settings, sample_bars):
        signal_engine = SignalEngine(default_settings.signal)

        # Nạp 30 nến để warmup Donchian
        for b in sample_bars[:30]:
            regime = RegimeEvent(
                symbol="XAUUSD",
                timestamp=b.timestamp,
                state=RegimeState.TRENDING_BULL,
                adx=30.0,
                chop=30.0,
                atr=4.0,
                atr_ratio=1.2,
                is_trading_allowed=True
            )
            sig = signal_engine.update(b, regime, PositionSide.FLAT)

        # Nến 31 tạo đột phá tăng vọt (Breakout High20)
        breakout_bar = BarEvent(
            symbol="XAUUSD",
            timestamp=datetime(2024, 1, 2, 12, 0),
            open=2100.0,
            high=2150.0,
            low=2090.0,
            close=2145.0,  # Vượt đỉnh 20 nến trước đó
            volume=2000.0
        )

        # 1. Khi Thiên Thời cho phép -> Bắt buộc sinh tín hiệu BUY
        regime_allow = RegimeEvent(
            symbol="XAUUSD",
            timestamp=breakout_bar.timestamp,
            state=RegimeState.TRENDING_BULL,
            adx=32.0,
            chop=28.0,
            atr=5.0,
            atr_ratio=1.3,
            is_trading_allowed=True
        )
        sig_allow = signal_engine.update(breakout_bar, regime_allow, PositionSide.FLAT)
        assert sig_allow.action == SignalAction.BUY

        # 2. Khi Thiên Thời ở trạng thái Vô Vi (NO_TREND) -> Bị chặn hoàn toàn
        signal_engine.reset()
        for b in sample_bars[:30]:
            signal_engine.update(b, regime_allow, PositionSide.FLAT)

        regime_block = RegimeEvent(
            symbol="XAUUSD",
            timestamp=breakout_bar.timestamp,
            state=RegimeState.NO_TREND,
            adx=15.0,
            chop=68.0,
            atr=3.0,
            atr_ratio=0.7,
            is_trading_allowed=False
        )
        sig_block = signal_engine.update(breakout_bar, regime_block, PositionSide.FLAT)
        assert sig_block.action == SignalAction.NONE
        assert "Vô Vi State" in sig_block.reason


class TestRiskEngine:
    def test_volatility_sizing_and_circuit_breaker(self, default_settings):
        risk_engine = RiskEngine(default_settings.risk, default_settings.symbols)
        t0 = datetime(2024, 1, 1, 10, 0)
        initial_cap = 100000.0
        account = risk_engine.update_account_state(t0, current_equity=initial_cap, current_balance=initial_cap)

        # 1. Kiểm tra tính Volatility Position Sizing (Chế độ Cổ điển WuWei 1% ATR)
        classic_risk_engine = RiskEngine(
            default_settings.risk,
            default_settings.symbols,
            carver_config=None  # Không dùng Carver
        )
        classic_risk_engine.carver_config.enabled = False

        sig = SignalEvent(
            symbol="XAUUSD",
            timestamp=t0,
            action=SignalAction.BUY,
            entry_donchian_high=2050.0,
            entry_donchian_low=2000.0,
            exit_donchian_high=2040.0,
            exit_donchian_low=2010.0,
            atr=10.0,
            regime_state=RegimeState.TRENDING_BULL,
            reason="Test Breakout"
        )

        order_classic, _ = classic_risk_engine.evaluate_order(sig, current_price=2055.0, account=account, open_positions={})
        assert order_classic is not None
        assert order_classic.volume == 0.50
        assert order_classic.stop_loss == pytest.approx(2055.0 - 20.0)

        # 1.1. Kiểm tra tính Carver Institutional Sizing (Cash Vol Target & Buffering)
        carver_risk_engine = RiskEngine(default_settings.risk, default_settings.symbols, carver_config=default_settings.carver)
        order_carver, cb = carver_risk_engine.evaluate_order(sig, current_price=2055.0, account=account, open_positions={})
        assert order_carver is not None
        assert cb is None
        assert order_carver.volume > 0.0
        assert "Carver Sizing" in order_carver.reason

        # 2. Kiểm tra Circuit Breaker khi lỗ vượt 3.0% trong ngày
        # Equity sụt giảm từ 100,000 về 96,500 (Lỗ 3.5%)
        bad_equity = 96500.0
        account_dd = risk_engine.update_account_state(t0 + timedelta(hours=2), current_equity=bad_equity, current_balance=bad_equity)
        assert account_dd.circuit_breaker_active is True
        assert account_dd.daily_drawdown_pct >= 0.03

        # Lệnh mới bị từ chối và phát sinh CircuitBreakerEvent
        order_blocked, cb_event = risk_engine.evaluate_order(sig, current_price=2055.0, account=account_dd, open_positions={})
        assert order_blocked is None
        assert cb_event is not None
        assert "Circuit Breaker Triggered" in cb_event.reason


class TestExecutionEngine:
    def test_breakeven_and_trailing_stop(self, default_settings):
        exec_engine = ExecutionEngine(default_settings.execution, default_settings.symbols)
        t0 = datetime(2024, 1, 1, 10, 0)
        entry_bar = BarEvent("XAUUSD", t0, 2000.0, 2005.0, 1995.0, 2000.0)

        from core.events import OrderEvent
        from core.types import OrderType

        order = OrderEvent(
            order_id="TEST_01",
            symbol="XAUUSD",
            timestamp=t0,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            volume=1.0,
            price=2000.0,
            stop_loss=1980.0  # SL = -20 (N=10)
        )

        # Khớp lệnh Buy
        fill = exec_engine.process_order(order, entry_bar)
        pos = exec_engine.open_positions["XAUUSD"]
        assert pos.side == PositionSide.LONG
        assert pos.breakeven_locked is False

        # Giá tăng vọt +25 điểm (+2.5 * N) -> Kích hoạt Break-Even (+2.0 * N)
        fav_bar = BarEvent("XAUUSD", t0 + timedelta(hours=1), 2005.0, 2026.0, 2004.0, 2025.0)
        sig = SignalEvent(
            symbol="XAUUSD",
            timestamp=fav_bar.timestamp,
            action=SignalAction.NONE,
            entry_donchian_high=2020.0,
            entry_donchian_low=1990.0,
            exit_donchian_high=2025.0,
            exit_donchian_low=2012.0,  # Donchian 10 Exit low tăng lên 2012
            atr=10.0,
            regime_state=RegimeState.TRENDING_BULL
        )

        exec_engine.on_bar_update(fav_bar, sig)
        # Stop Loss phải được dời lên ít nhất bằng hòa vốn (hoặc theo Donchian 10 = 2012.0)
        assert pos.breakeven_locked is True
        assert pos.stop_loss >= pos.entry_price

        # Nến tiếp theo giá lao dốc chạm Stop Loss
        hit_bar = BarEvent("XAUUSD", t0 + timedelta(hours=2), 2015.0, 2016.0, 2000.0, 2001.0)
        closed = exec_engine.on_bar_update(hit_bar, sig)
        assert len(closed) == 1
        assert closed[0].exit_reason in (ExitReason.TRAILING_STOP, ExitReason.BREAK_EVEN)
        assert closed[0].net_pnl > 0  # Chốt lời thành công bằng Trailing


class TestEndToEndBacktest:
    def test_full_backtest_pipeline(self, default_settings):
        backtester = EventDrivenBacktester(default_settings)
        # Sinh 500 nến và chạy toàn bộ pipeline
        df = backtester.generate_synthetic_market_data(symbol="XAUUSD", bars_count=500, seed=123)
        result = backtester.run(df, symbol="XAUUSD")

        assert result.metrics is not None
        assert isinstance(result.metrics.sharpe_ratio, float)
        assert len(result.equity_curve) == len(df)
        assert result.metrics.initial_capital == 100000.0

    def test_carver_multi_asset_portfolio(self, default_settings):
        backtester = EventDrivenBacktester(default_settings)
        df_gold = backtester.generate_synthetic_market_data("XAUUSD", bars_count=300, start_price=2050.0, seed=1)
        df_eur = backtester.generate_synthetic_market_data("EURUSD", bars_count=300, start_price=1.0850, seed=2)
        df_jpy = backtester.generate_synthetic_market_data("USDJPY", bars_count=300, start_price=155.00, seed=3)

        result = backtester.run_portfolio(
            market_data={"XAUUSD": df_gold, "EURUSD": df_eur, "USDJPY": df_jpy},
            weights={"XAUUSD": 0.4, "EURUSD": 0.3, "USDJPY": 0.3}
        )
        assert result.metrics is not None
        assert result.metrics.ending_equity > 0
        assert len(result.equity_curve) == 300
