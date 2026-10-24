"""
WuWei Core - Regime Engine (Thiên Thời - State Detection)
Xác định trạng thái cấu trúc thị trường: Xu hướng vs. Sideway/Nén biến động
Triết lý Vô Vi: "Tĩnh vi táo quân" - Thị trường không rõ xu hướng thì tuyệt đối không cưỡng cầu.
"""
from __future__ import annotations

import collections
import math
from typing import Deque, List, Optional
import numpy as np

from config.settings import RegimeConfig
from core.events import BarEvent, RegimeEvent
from core.types import RegimeState


class RegimeEngine:
    """
    Module Thiên Thời (Market Regime Classifier):
    - ADX(14) đo lường độ dốc và xung lượng xu hướng.
    - Choppiness Index (CHOP 14) đo lường tính fractal/hỗn loạn của biến động giá.
    - ATR Ratio = ATR(14) / SMA(ATR(14), 50) đo lường sự nén dãn biên độ (volatility expansion/contraction).
    """

    def __init__(self, config: RegimeConfig):
        self.config = config
        self.adx_period = config.adx_period
        self.chop_period = config.chop_period
        self.atr_period = config.atr_period
        self.atr_sma_period = config.atr_sma_period

        # Rolling history buffers
        self.max_buffer_size = max(self.atr_sma_period + self.atr_period, 100)
        self.bars: Deque[BarEvent] = collections.deque(maxlen=self.max_buffer_size)
        self.atr_history: Deque[float] = collections.deque(maxlen=self.atr_sma_period + 10)

        # ADX state variables (Wilder's Smoothing)
        self.prev_bar: Optional[BarEvent] = None
        self.tr_smooth: float = 0.0
        self.plus_dm_smooth: float = 0.0
        self.minus_dm_smooth: float = 0.0
        self.adx_smooth: float = 0.0
        self.adx_initialized: bool = False
        self.warmup_count: int = 0

    def reset(self) -> None:
        self.bars.clear()
        self.atr_history.clear()
        self.prev_bar = None
        self.tr_smooth = 0.0
        self.plus_dm_smooth = 0.0
        self.minus_dm_smooth = 0.0
        self.adx_smooth = 0.0
        self.adx_initialized = False
        self.warmup_count = 0

    def update(self, bar: BarEvent) -> RegimeEvent:
        """
        Nhận nến mới và cập nhật trạng thái Thiên Thời.
        """
        self.bars.append(bar)
        self.warmup_count += 1

        # 1. Tính True Range và Directional Movement
        if self.prev_bar is None:
            tr1 = bar.high - bar.low
            plus_dm = 0.0
            minus_dm = 0.0
        else:
            tr1 = max(
                bar.high - bar.low,
                abs(bar.high - self.prev_bar.close),
                abs(bar.low - self.prev_bar.close)
            )
            up_move = bar.high - self.prev_bar.high
            down_move = self.prev_bar.low - bar.low

            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        # Cập nhật Wilder's Smoothing cho ADX
        alpha = 1.0 / self.adx_period
        if self.warmup_count <= self.adx_period:
            self.tr_smooth += tr1
            self.plus_dm_smooth += plus_dm
            self.minus_dm_smooth += minus_dm
            if self.warmup_count == self.adx_period:
                self.tr_smooth /= self.adx_period
                self.plus_dm_smooth /= self.adx_period
                self.minus_dm_smooth /= self.adx_period
        else:
            self.tr_smooth = self.tr_smooth * (1.0 - alpha) + tr1 * alpha
            self.plus_dm_smooth = self.plus_dm_smooth * (1.0 - alpha) + plus_dm * alpha
            self.minus_dm_smooth = self.minus_dm_smooth * (1.0 - alpha) + minus_dm * alpha

        # Tính toán DI và DX
        adx_val = 20.0  # Mặc định an toàn
        if self.tr_smooth > 1e-8:
            plus_di = 100.0 * (self.plus_dm_smooth / self.tr_smooth)
            minus_di = 100.0 * (self.minus_dm_smooth / self.tr_smooth)
            di_sum = plus_di + minus_di
            dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 1e-8 else 0.0

            if not self.adx_initialized and self.warmup_count >= self.adx_period * 2:
                self.adx_smooth = dx
                self.adx_initialized = True
            elif self.adx_initialized:
                self.adx_smooth = self.adx_smooth * (1.0 - alpha) + dx * alpha
            adx_val = self.adx_smooth if self.adx_initialized else dx

        # 2. Cập nhật Choppiness Index (CHOP 14)
        chop_val = self._calculate_chop(self.chop_period)

        # 3. Cập nhật ATR(14) và ATR Ratio
        current_atr = self.tr_smooth if self.tr_smooth > 0 else (bar.high - bar.low)
        self.atr_history.append(current_atr)

        atr_sma = (
            float(np.mean(self.atr_history))
            if len(self.atr_history) > 0
            else current_atr
        )
        atr_ratio = current_atr / atr_sma if atr_sma > 1e-8 else 1.0

        self.prev_bar = bar

        # 4. Xác định cấu trúc Thiên Thời (Regime State)
        state, is_trading_allowed = self._classify_regime(bar, adx_val, chop_val, atr_ratio)

        return RegimeEvent(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            state=state,
            adx=adx_val,
            chop=chop_val,
            atr=current_atr,
            atr_ratio=atr_ratio,
            is_trading_allowed=is_trading_allowed,
        )

    def _calculate_chop(self, period: int) -> float:
        """
        Công thức Choppiness Index chuẩn:
        CHOP = 100 * LOG10( SUM(ATR(1), period) / (MaxHigh(period) - MinLow(period)) ) / LOG10(period)
        """
        if len(self.bars) < period + 1:
            return 50.0  # Giá trị trung tính khi chưa đủ nến

        recent_bars = list(self.bars)[-period:]
        prev_bar = list(self.bars)[-(period + 1)]

        sum_tr = 0.0
        max_high = -1e12
        min_low = 1e12

        curr_prev = prev_bar
        for b in recent_bars:
            tr = max(b.high - b.low, abs(b.high - curr_prev.close), abs(b.low - curr_prev.close))
            sum_tr += tr
            if b.high > max_high:
                max_high = b.high
            if b.low < min_low:
                min_low = b.low
            curr_prev = b

        price_range = max_high - min_low
        if price_range <= 1e-8 or sum_tr <= 1e-8:
            return 100.0  # Maximum chop / flat line

        ratio = sum_tr / price_range
        if ratio <= 0:
            return 100.0

        chop = 100.0 * (math.log10(ratio) / math.log10(period))
        return float(np.clip(chop, 0.0, 100.0))

    def _classify_regime(
        self,
        bar: BarEvent,
        adx: float,
        chop: float,
        atr_ratio: float
    ) -> tuple[RegimeState, bool]:
        """
        Quy tắc phân loại chế độ thị trường:
        - ADX < 20 và CHOP > 61.8: NO_TREND (Trạng thái Vô Vi, tuyệt đối không mở vị thế mới).
        - ADX >= 25 và CHOP < 38.2: Kích hoạt xu hướng (TRENDING_BULL / TRENDING_BEAR).
        - Vùng còn lại: RANGING / chuyển tiếp (Không vào lệnh mới).
        """
        # Kiểm tra điều kiện NO_TREND (Vô Vi)
        if adx < self.config.adx_no_trend_threshold and chop > self.config.chop_no_trend_threshold:
            return RegimeState.NO_TREND, False

        # Kiểm tra điều kiện Trend bùng nổ
        if adx >= self.config.adx_trend_threshold and chop < self.config.chop_trend_threshold:
            # Xác định hướng xu hướng qua vị thế giá so với vùng trung bình 50 nến
            if len(self.bars) >= 20:
                sma_mid = float(np.mean([b.close for b in list(self.bars)[-20:]]))
                if bar.close >= sma_mid:
                    return RegimeState.TRENDING_BULL, True
                else:
                    return RegimeState.TRENDING_BEAR, True
            else:
                return RegimeState.TRENDING_BULL, True

        # Trạng thái sideway hoặc tích lũy trung gian
        return RegimeState.RANGING, False
