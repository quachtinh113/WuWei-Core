"""
WuWei Core - Signal Engine (Thuận Dòng - Signal Generator)
Cấu trúc phá vỡ thuần túy Donchian Channel (Zero-Prediction, Zero-Forecast)
Triết lý: "Thủy thiện lợi vạn vật nhi bất tranh" - Nước chảy chỗ trũng, giá phá vỡ hướng nào thì nương theo hướng đó.
"""
from __future__ import annotations

import collections
from typing import Deque, Optional
import numpy as np

from config.settings import SignalConfig
from core.carver_quant import MultiHorizonForecastCombiner
from core.events import BarEvent, RegimeEvent, SignalEvent
from core.types import PositionSide, RegimeState, SignalAction

ENTRY_FREEZE_HOUR: int = 18


def check_entry_freeze(timestamp) -> bool:
    """
    Khóa vòng lặp Friday De-risk (ENTRY_FREEZE):
    Tự động kích hoạt sau 18:00 Server Time mỗi thứ Sáu (weekday == 4).
    Chốt chặn này từ chối toàn bộ tín hiệu mở mới, để risk_engine.py chuyên tâm
    thanh lý các vị thế đang tồn đọng trước 20:45 mà không gây ra vòng lặp đóng/mở.
    """
    if hasattr(timestamp, "weekday") and hasattr(timestamp, "hour"):
        return timestamp.weekday() == 4 and timestamp.hour >= ENTRY_FREEZE_HOUR
    return False


class SignalEngine:
    """
    Module Thuận Dòng (Trend Breakout Signal Generator & Carver Multi-Horizon Forecaster):
    - Kênh Donchian Breakout 20 bars cho Entry:
        Upper_20 = max(High[t-20 : t-1])
        Lower_20 = min(Low[t-20 : t-1])
    - Kênh Donchian 10 bars cho Exit ngược chiều:
        Exit_Upper_10 = max(High[t-10 : t-1])
        Exit_Lower_10 = min(Low[t-10 : t-1])
    - Tích hợp cỗ máy Carver Multi-Horizon (Fast/Med/Slow Donchian + EWMAC) [-20, +20]
    - Zero lookahead bias: Bands chỉ tính từ nến t-1 trở về trước.
    - Chặn mở lệnh nếu Thiên Thời ở trạng thái NO_TREND / Vô Vi hoặc RANGING.
    """

    def __init__(self, config: SignalConfig, fdm: float = 1.25, forecast_cap: float = 20.0):
        self.config = config
        self.entry_period = config.entry_channel_period
        self.exit_period = config.exit_channel_period
        self.buffer_size = max(self.entry_period, self.exit_period) + 5
        self.bars: Deque[BarEvent] = collections.deque(maxlen=self.buffer_size)
        self.carver_combiner = MultiHorizonForecastCombiner(fdm=fdm, forecast_cap=forecast_cap)

    def reset(self) -> None:
        self.bars.clear()
        self.carver_combiner = MultiHorizonForecastCombiner(
            fdm=self.carver_combiner.fdm, forecast_cap=self.carver_combiner.forecast_cap
        )

    def update(
        self,
        bar: BarEvent,
        regime: RegimeEvent,
        current_position: PositionSide = PositionSide.FLAT
    ) -> SignalEvent:
        """
        Nhận nến hiện tại và trạng thái Thiên Thời để phát sinh tín hiệu Thuận Dòng.
        """
        # Nếu chưa đủ dữ liệu quá khứ để xây dựng Donchian Bands
        if len(self.bars) < self.entry_period:
            self.bars.append(bar)
            return SignalEvent(
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                action=SignalAction.NONE,
                entry_donchian_high=bar.high,
                entry_donchian_low=bar.low,
                exit_donchian_high=bar.high,
                exit_donchian_low=bar.low,
                atr=regime.atr,
                regime_state=regime.state,
                reason="Warmup: Insufficient bars for Donchian channel"
            )

        # Tính Donchian Channels từ N nến trước đó (loại trừ nến hiện tại bar[t])
        past_bars = list(self.bars)
        entry_window = past_bars[-self.entry_period:]
        exit_window = past_bars[-self.exit_period:]

        entry_high = max(b.high for b in entry_window)
        entry_low = min(b.low for b in entry_window)
        exit_high = max(b.high for b in exit_window)
        exit_low = min(b.low for b in exit_window)

        action = SignalAction.NONE
        reason = ""

        # 1. Kiểm tra tín hiệu thoát lệnh (Exit Signal) trước
        if current_position == PositionSide.LONG and bar.close < exit_low:
            action = SignalAction.CLOSE_BUY
            reason = f"Exit Long: Close ({bar.close:.2f}) < Donchian Lower Exit 10 ({exit_low:.2f})"
        elif current_position == PositionSide.SHORT and bar.close > exit_high:
            action = SignalAction.CLOSE_SELL
            reason = f"Exit Short: Close ({bar.close:.2f}) > Donchian Upper Exit 10 ({exit_high:.2f})"

        # 2. Kiểm tra tín hiệu mở vị thế mới (Entry Signal) nếu chưa có lệnh thoát
        if action == SignalAction.NONE and current_position == PositionSide.FLAT:
            # Chốt chặn rủi ro cuối tuần: ENTRY_FREEZE sau 18:00 Thứ Sáu (Server Time)
            if check_entry_freeze(bar.timestamp):
                reason = (
                    f"ENTRY_FREEZE: Pre-Weekend Risk Gate Active (Friday {bar.timestamp.strftime('%H:%M')} "
                    f">= {ENTRY_FREEZE_HOUR}:00 Server Time). All new entries locked."
                )
            # Điều kiện tiên quyết: Thiên Thời cho phép giao dịch (Không phải NO_TREND hoặc RANGING)
            elif regime.is_trading_allowed:
                if bar.close > entry_high:
                    action = SignalAction.BUY
                    reason = (
                        f"Donchian Bullish Breakout: Close ({bar.close:.2f}) > High20 ({entry_high:.2f}) "
                        f"| Regime: {regime.state.value} (ADX={regime.adx:.1f}, CHOP={regime.chop:.1f})"
                    )
                elif bar.close < entry_low:
                    action = SignalAction.SELL
                    reason = (
                        f"Donchian Bearish Breakout: Close ({bar.close:.2f}) < Low20 ({entry_low:.2f}) "
                        f"| Regime: {regime.state.value} (ADX={regime.adx:.1f}, CHOP={regime.chop:.1f})"
                    )
                else:
                    reason = "Price within Donchian 20 bands"
            else:
                reason = f"Vô Vi State: Trading barred by RegimeEngine ({regime.state.value})"

        # Tính toán Carver Continuous Forecast (Multi-Horizon Ensemble)
        raw_carver_fc, rule_fcs = self.carver_combiner.update(bar.high, bar.low, bar.close)
        # Triết lý Vô Vi & Pre-Weekend Freeze:
        # Nếu Thiên Thời ở trạng thái NO_TREND hoặc đang trong khung ENTRY_FREEZE, dập tắt Forecast về 0
        is_frozen = check_entry_freeze(bar.timestamp)
        effective_carver_fc = raw_carver_fc if (regime.is_trading_allowed and not is_frozen) else 0.0

        # Thêm nến hiện tại vào bộ đệm sau khi tính toán tín hiệu (Bảo toàn Zero Lookahead)
        self.bars.append(bar)

        return SignalEvent(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            action=action,
            entry_donchian_high=entry_high,
            entry_donchian_low=entry_low,
            exit_donchian_high=exit_high,
            exit_donchian_low=exit_low,
            atr=regime.atr,
            regime_state=regime.state,
            reason=reason,
            carver_forecast=effective_carver_fc,
            rule_forecasts=rule_fcs
        )
