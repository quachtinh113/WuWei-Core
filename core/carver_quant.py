"""
WuWei Core - Carver Institutional Quantitative Engine
Bản hợp nhất đỉnh cao giữa Triết lý Vô Vi và Cỗ máy pysystemtrade (Robert Carver)
Tích hợp:
  1. Continuous Forecast Scaling & Capping [-20, +20]
  2. Multi-Horizon Forecast Diversification (FDM)
  3. Cash Volatility Targeting (Annual Vol Target -> Daily Cash Target)
  4. Instrument Diversification Multiplier (IDM)
  5. Position Buffering (Vô Vi Inertia - Cắt giảm 50% phí giao dịch)
  6. Asymmetric Drawdown Deleveraging Ladder
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


@dataclass
class CarverConfig:
    annual_vol_target: float = 0.20          # 20% Annual Portfolio Volatility Target
    average_absolute_forecast: float = 10.0  # Tiêu chuẩn Robert Carver: Forecast trung bình = 10.0
    forecast_cap: float = 20.0               # Giới hạn cực đại Forecast [-20.0, +20.0]
    buffer_ratio: float = 0.10               # Độ rộng Buffer (10% của Volatility Scalar)
    volatility_lookback_days: int = 35       # EWMA span cho ước lượng biến động giá
    fdm: float = 1.25                        # Forecast Diversification Multiplier
    idm: float = 1.35                        # Instrument Diversification Multiplier
    deleveraging_dd_threshold: float = 0.015 # Bắt đầu hạ tỷ trọng khi DD vượt 1.5%
    circuit_breaker_dd: float = 0.03         # Đóng toàn bộ và khóa khi DD chạm 3.0%


class CarverPriceVolatility:
    """
    Ước lượng độ biến động giá hàng ngày (Daily Price Volatility in Points/Price)
    sử dụng Exponential Moving Average (EWMA) của bình phương sai biệt giá,
    tương đương thuật toán price_volatility trong syscore/pysystemtrade.
    """

    def __init__(self, span: int = 35):
        self.span = span
        self.alpha = 2.0 / (span + 1.0)
        self.variance: float = 0.0
        self.prev_price: Optional[float] = None
        self.is_initialized: bool = False

    def update(self, price: float) -> float:
        if self.prev_price is None:
            self.prev_price = price
            # Khởi tạo phương sai ban đầu
            self.variance = max((price * 0.01) ** 2, 1e-4)
            return math.sqrt(self.variance)

        diff = price - self.prev_price
        diff_sq = diff ** 2

        if not self.is_initialized:
            self.variance = diff_sq
            self.is_initialized = True
        else:
            self.variance = (1.0 - self.alpha) * self.variance + self.alpha * diff_sq

        self.prev_price = price
        return max(math.sqrt(self.variance), 1e-4)


class MultiHorizonForecastCombiner:
    """
    Kết hợp đa khung thời gian theo phong cách pysystemtrade:
    - Donchian Fast (10 bars)
    - Donchian Medium (20 bars)
    - Donchian Slow (40 bars)
    - EWMAC (8 / 32)
    Được chuẩn hóa về thang đo Carver [-20, +20] và nhân hệ số FDM (Forecast Diversification Multiplier).
    """

    def __init__(self, fdm: float = 1.25, forecast_cap: float = 20.0):
        self.fdm = fdm
        self.forecast_cap = forecast_cap
        self.prices: collections.deque[float] = collections.deque(maxlen=100)
        self.highs: collections.deque[float] = collections.deque(maxlen=100)
        self.lows: collections.deque[float] = collections.deque(maxlen=100)

        # Trọng số phân bổ cho các chu kỳ sóng (Weights)
        self.weights = {
            "donchian_fast": 0.25,
            "donchian_med": 0.35,
            "donchian_slow": 0.25,
            "ewmac_8_32": 0.15,
        }

    def update(self, high: float, low: float, close: float) -> Tuple[float, Dict[str, float]]:
        self.prices.append(close)
        self.highs.append(high)
        self.lows.append(low)

        if len(self.prices) < 20:
            return 0.0, {k: 0.0 for k in self.weights}

        p_list = list(self.prices)
        h_list = list(self.highs)
        l_list = list(self.lows)

        # 1. Donchian Fast (10)
        fc_fast = self._donchian_forecast(p_list, h_list, l_list, period=10)

        # 2. Donchian Med (20)
        fc_med = self._donchian_forecast(p_list, h_list, l_list, period=20)

        # 3. Donchian Slow (40)
        fc_slow = self._donchian_forecast(p_list, h_list, l_list, period=40 if len(p_list) >= 40 else len(p_list) - 1)

        # 4. EWMAC (8, 32)
        fc_ewmac = self._ewmac_forecast(p_list, fast_span=8, slow_span=32)

        rule_forecasts = {
            "donchian_fast": fc_fast,
            "donchian_med": fc_med,
            "donchian_slow": fc_slow,
            "ewmac_8_32": fc_ewmac,
        }

        # Weighted combination
        raw_combined = sum(rule_forecasts[k] * self.weights[k] for k in self.weights)

        # Nhân FDM và Giới hạn Cap [-20, +20]
        scaled_combined = raw_combined * self.fdm
        capped_forecast = float(np.clip(scaled_combined, -self.forecast_cap, self.forecast_cap))

        return capped_forecast, rule_forecasts

    def _donchian_forecast(
        self,
        prices: List[float],
        highs: List[float],
        lows: List[float],
        period: int
    ) -> float:
        if len(prices) <= period:
            return 0.0
        cur_close = prices[-1]
        window_highs = highs[-period - 1 : -1]
        window_lows = lows[-period - 1 : -1]

        upper = max(window_highs)
        lower = min(window_lows)
        rng = upper - lower

        if rng <= 1e-6:
            return 0.0

        mid = (upper + lower) / 2.0
        # Chuẩn hóa khoảng cách từ tâm: [-1.0, +1.0]
        norm_pos = (cur_close - mid) / (rng / 2.0)

        # Nhân hệ số Carver Scalar: 10.0 khi chạm dải phá vỡ
        forecast = norm_pos * 10.0
        return float(np.clip(forecast, -20.0, 20.0))

    def _ewmac_forecast(self, prices: List[float], fast_span: int, slow_span: int) -> float:
        s = pd.Series(prices)
        fast_ema = s.ewm(span=fast_span).mean().iloc[-1]
        slow_ema = s.ewm(span=slow_span).mean().iloc[-1]
        std = s.diff().std()
        if std <= 1e-6 or np.isnan(std):
            return 0.0

        # EWMAC raw: (Fast - Slow) / Volatility
        diff = (fast_ema - slow_ema) / std
        # Carver standard scalar cho 8/32 ~ 5.3
        forecast = diff * 5.3
        return float(np.clip(forecast, -20.0, 20.0))


class CarverPositionSizer:
    """
    Cơ cấu Định cỡ Vị thế chuẩn Quỹ (Carver Sizing + Buffering):
    1. Daily Cash Volatility Target = Equity * (Annual Vol / sqrt(252))
    2. Instrument Value Volatility = sigma_price * Contract Size
    3. Volatility Scalar = Daily Cash Vol Target / Instrument Value Volatility
    4. Target Position = Volatility Scalar * (Forecast / 10.0) * IDM * Weight
    5. Buffer Band = [Target - Buffer, Target + Buffer]
    6. "Vô Vi" Inertia: Nếu vị thế hiện tại nằm trong Buffer, TUYỆT ĐỐI KHÔNG GIAO DỊCH!
    """

    def __init__(self, config: CarverConfig, contract_size: float, lot_step: float = 0.01, min_lot: float = 0.01, max_lot: float = 50.0):
        self.config = config
        self.contract_size = contract_size
        self.lot_step = lot_step
        self.min_lot = min_lot
        self.max_lot = max_lot

    def compute_target_and_buffers(
        self,
        equity: float,
        price_vol: float,
        forecast: float,
        instrument_weight: float = 1.0,
        daily_dd_pct: float = 0.0
    ) -> Tuple[float, float, float, float]:
        """
        Trả về:
          (target_lots, lower_buffer_lots, upper_buffer_lots, vol_scalar)
        """
        if equity <= 0 or price_vol <= 0:
            return 0.0, 0.0, 0.0, 0.0

        # 1. Asymmetric Drawdown De-leveraging Overlay
        capital_scalar = 1.0
        if daily_dd_pct >= self.config.circuit_breaker_dd:
            capital_scalar = 0.0  # Khóa giao dịch
        elif daily_dd_pct >= self.config.deleveraging_dd_threshold:
            # Hạ tỷ trọng tuyến tính từ 1.0 xuống 0.2 khi DD tiến sát ngưỡng circuit breaker
            dd_range = self.config.circuit_breaker_dd - self.config.deleveraging_dd_threshold
            capital_scalar = max(0.2, 1.0 - 0.8 * ((daily_dd_pct - self.config.deleveraging_dd_threshold) / dd_range))

        effective_equity = equity * capital_scalar

        # 2. Daily Cash Volatility Target
        daily_cash_vol_target = effective_equity * (self.config.annual_vol_target / math.sqrt(252.0))

        # 3. Instrument Value Volatility in Account Currency
        instrument_value_vol = price_vol * self.contract_size

        if instrument_value_vol <= 1e-6:
            return 0.0, 0.0, 0.0, 0.0

        # 4. Volatility Scalar (Số hợp đồng để chịu đúng rủi ro mục tiêu khi forecast = 10.0)
        vol_scalar = daily_cash_vol_target / instrument_value_vol

        # 5. Raw Target Position
        # Target = Vol_Scalar * (Forecast / 10.0) * IDM * Weight
        raw_target = (
            vol_scalar
            * (forecast / self.config.average_absolute_forecast)
            * self.config.idm
            * instrument_weight
        )

        # 6. Buffer Calculation (Vô Vi Inertia)
        # Buffer = 10% của giá trị quy mô chuẩn
        buffer_width = max(
            self.config.buffer_ratio * vol_scalar * self.config.idm * instrument_weight,
            self.min_lot
        )

        lower_buffer = raw_target - buffer_width
        upper_buffer = raw_target + buffer_width

        return raw_target, lower_buffer, upper_buffer, vol_scalar

    def resolve_buffered_trade(
        self,
        current_lots: float,
        target_lots: float,
        lower_buffer: float,
        upper_buffer: float
    ) -> float:
        """
        Triết lý Vô Vi - Carver Buffering:
        - Nếu current_lots nằm giữa [lower_buffer, upper_buffer]:
          KHÔNG LÀM GÌ CẢ (return current_lots). Tiết kiệm hoàn toàn spread & commission!
        - Nếu current_lots < lower_buffer:
          Chỉ mua bù lên mức lower_buffer!
        - Nếu current_lots > upper_buffer:
          Chỉ bán giảm về mức upper_buffer!
        """
        # Nếu target bằng 0 (đóng trạng thái hoàn toàn do NO_TREND hoặc Circuit Breaker)
        if abs(target_lots) < 1e-5:
            return 0.0

        if current_lots < lower_buffer:
            new_position = lower_buffer
        elif current_lots > upper_buffer:
            new_position = upper_buffer
        else:
            # Ở trạng thái Vô Vi (Không giao dịch vi tế)
            return current_lots

        # Làm tròn theo lot_step
        sign = 1.0 if new_position >= 0 else -1.0
        abs_lots = abs(new_position)
        stepped_lots = math.floor(abs_lots / self.lot_step) * self.lot_step
        clipped_lots = float(np.clip(stepped_lots, 0.0, self.max_lot))

        if clipped_lots < self.min_lot:
            return 0.0

        return sign * round(clipped_lots, 4)
