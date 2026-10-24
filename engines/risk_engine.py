"""
WuWei Core - Risk Engine (Tri Chỉ - Volatility Targeting & Money Management)
Định cỡ vị thế động nghịch đảo biến động & Circuit Breakers bảo vệ vốn cấp tổ chức.
Triết lý: "Tri chỉ khả dĩ bất đãi" - Biết điểm dừng thì không gặp nguy.
Tuyệt đối KHÔNG DCA, KHÔNG Martingale, KHÔNG Grid.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
import numpy as np

from config.settings import CarverConfigModel, RiskConfig, SymbolSpecification
from core.carver_quant import CarverConfig, CarverPositionSizer, CarverPriceVolatility
from core.events import CircuitBreakerEvent, OrderEvent, SignalEvent
from core.types import (
    AccountSnapshot,
    OrderSide,
    OrderType,
    Position,
    PositionSide,
    SignalAction,
)


class RiskEngine:
    """
    Module Tri Chỉ (Institutional Risk & Money Management):
    - Tích hợp 2 chế độ:
      + Classic WuWei: Volatility Sizing theo ATR(20) & rủi ro 1.0% Equity.
      + Carver Institutional: Cash Volatility Target (20% Annual Vol) + Buffering (Vô Vi Inertia) + IDM.
    - Hard Stop Loss cố định = Entry ± 2.0 * N (với N = ATR(20)).
    - Circuit Breaker cấp tài khoản: Giới hạn lỗ tối đa 3.0%/ngày.
      Vượt ngưỡng lập tức đóng mọi vị thế và khóa tài khoản 24 giờ.
    - Bổ sung Thang De-leveraging phi đối xứng khi lỗ ngày > 1.5%.
    - Kiểm soát nghiêm ngặt: Chặn DCA âm vị thế, chặn Martingale, chặn Hedging.
    """

    def __init__(
        self,
        config: RiskConfig,
        symbol_specs: Dict[str, SymbolSpecification],
        carver_config: Optional[CarverConfigModel] = None
    ):
        self.config = config
        self.symbol_specs = symbol_specs
        self.carver_config = carver_config or CarverConfigModel()

        # Quản lý bộ ước lượng biến động Carver theo từng cặp
        self.carver_vol_estimators: Dict[str, CarverPriceVolatility] = {
            s: CarverPriceVolatility(span=self.carver_config.volatility_lookback_days)
            for s in symbol_specs
        }
        self.carver_sizers: Dict[str, CarverPositionSizer] = {}
        for s, spec in symbol_specs.items():
            cc = CarverConfig(
                annual_vol_target=self.carver_config.annual_vol_target,
                average_absolute_forecast=self.carver_config.average_absolute_forecast,
                forecast_cap=self.carver_config.forecast_cap,
                buffer_ratio=self.carver_config.buffer_ratio,
                volatility_lookback_days=self.carver_config.volatility_lookback_days,
                fdm=self.carver_config.fdm,
                idm=self.carver_config.idm,
                deleveraging_dd_threshold=self.carver_config.deleveraging_dd_threshold,
                circuit_breaker_dd=config.max_daily_drawdown_pct
            )
            self.carver_sizers[s] = CarverPositionSizer(
                config=cc,
                contract_size=spec.contract_size,
                lot_step=spec.lot_step,
                min_lot=spec.min_lot,
                max_lot=spec.max_lot
            )

        # Trạng thái quản lý rủi ro
        self.current_day: Optional[datetime.date] = None
        self.daily_starting_equity: float = config.initial_capital
        self.peak_equity: float = config.initial_capital
        self.circuit_breaker_active: bool = False
        self.circuit_breaker_until: Optional[datetime] = None

    def reset(self, initial_capital: float) -> None:
        self.daily_starting_equity = initial_capital
        self.peak_equity = initial_capital
        self.circuit_breaker_active = False
        self.circuit_breaker_until = None
        self.current_day = None

    def update_account_state(
        self,
        timestamp: datetime,
        current_equity: float,
        current_balance: float,
        margin_used: float = 0.0
    ) -> AccountSnapshot:
        """
        Cập nhật trạng thái số dư tài khoản theo từng chu kỳ nến và kiểm tra Circuit Breaker.
        """
        date_today = timestamp.date()
        if self.current_day is None or date_today > self.current_day:
            self.current_day = date_today
            self.daily_starting_equity = current_equity

        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        # Kiểm tra mở khóa Circuit Breaker nếu đã hết thời gian phạt
        if self.circuit_breaker_active and self.circuit_breaker_until is not None:
            if timestamp >= self.circuit_breaker_until:
                self.circuit_breaker_active = False
                self.circuit_breaker_until = None
                self.daily_starting_equity = current_equity

        # Tính toán Drawdown ngày và Drawdown tổng
        daily_dd_pct = (
            (self.daily_starting_equity - current_equity) / self.daily_starting_equity
            if self.daily_starting_equity > 0
            else 0.0
        )
        total_dd_pct = (
            (self.peak_equity - current_equity) / self.peak_equity
            if self.peak_equity > 0
            else 0.0
        )

        # Kích hoạt Circuit Breaker nếu chạm giới hạn 3.0% Max Daily DD
        if daily_dd_pct >= self.config.max_daily_drawdown_pct and not self.circuit_breaker_active:
            self.circuit_breaker_active = True
            self.circuit_breaker_until = timestamp + timedelta(hours=24)

        return AccountSnapshot(
            timestamp=timestamp,
            balance=current_balance,
            equity=current_equity,
            margin_used=margin_used,
            free_margin=max(current_equity - margin_used, 0.0),
            daily_starting_equity=self.daily_starting_equity,
            daily_drawdown_pct=daily_dd_pct,
            peak_equity=self.peak_equity,
            total_drawdown_pct=total_dd_pct,
            circuit_breaker_active=self.circuit_breaker_active,
            circuit_breaker_until=self.circuit_breaker_until,
        )

    def evaluate_order(
        self,
        signal: SignalEvent,
        current_price: float,
        account: AccountSnapshot,
        open_positions: Dict[str, Position],
        instrument_weight: float = 1.0
    ) -> Tuple[Optional[OrderEvent], Optional[CircuitBreakerEvent]]:
        """
        Kiểm định và định cỡ rủi ro cho tín hiệu trước khi chuyển thành lệnh thực thi.
        """
        symbol = signal.symbol
        spec = self.symbol_specs.get(symbol)
        if not spec:
            return None, None

        # 1. Kiểm tra Circuit Breaker đang khóa
        if account.circuit_breaker_active:
            cb_event = CircuitBreakerEvent(
                symbol=symbol,
                timestamp=signal.timestamp,
                daily_drawdown_pct=account.daily_drawdown_pct,
                locked_until=account.circuit_breaker_until or (signal.timestamp + timedelta(hours=24)),
                reason=f"Circuit Breaker Triggered: Daily Drawdown reached {account.daily_drawdown_pct*100:.2f}% (Limit {self.config.max_daily_drawdown_pct*100:.1f}%)"
            )
            return None, cb_event

        # 2. Xử lý tín hiệu thoát lệnh hoặc giữ nguyên
        if signal.action in (SignalAction.NONE, SignalAction.CLOSE_BUY, SignalAction.CLOSE_SELL):
            return None, None

        # 3. Quy tắc kiểm soát số lượng vị thế & Chống DCA/Martingale/Grid
        current_pos = open_positions.get(symbol)
        if current_pos is not None and current_pos.side != PositionSide.FLAT:
            # Nghiêm cấm mở thêm vị thế khi đang có vị thế mở (No DCA / No Stacking)
            return None, None

        # 4. Tính toán Định cỡ Vị thế Biến động (Volatility Sizing)
        atr_n = max(signal.atr, 1e-4)
        sl_distance = self.config.stop_loss_atr_mult * atr_n

        if self.carver_config.enabled and symbol in self.carver_sizers:
            # -------------------------------------------------------------
            # Phương pháp Định Lượng Robert Carver (pysystemtrade)
            # -------------------------------------------------------------
            price_vol = self.carver_vol_estimators[symbol].update(current_price)
            sizer = self.carver_sizers[symbol]

            # Xác định Carver Forecast (sử dụng multi-horizon ensemble hoặc quy đổi từ breakout)
            if abs(signal.carver_forecast) > 1e-4:
                forecast_val = signal.carver_forecast
            else:
                forecast_val = 10.0 if signal.action == SignalAction.BUY else -10.0

            target_lots, low_buf, high_buf, vol_scalar = sizer.compute_target_and_buffers(
                equity=account.equity,
                price_vol=price_vol,
                forecast=forecast_val,
                instrument_weight=instrument_weight,
                daily_dd_pct=account.daily_drawdown_pct
            )

            current_lots = (
                current_pos.volume if (current_pos and current_pos.side == PositionSide.LONG)
                else (-current_pos.volume if (current_pos and current_pos.side == PositionSide.SHORT) else 0.0)
            )

            resolved_lots = sizer.resolve_buffered_trade(
                current_lots=current_lots,
                target_lots=target_lots,
                lower_buffer=low_buf,
                upper_buffer=high_buf
            )

            # Nếu nằm trong vùng Buffer (Vô Vi), không mở thêm lệnh
            if abs(resolved_lots) < spec.min_lot or abs(resolved_lots - current_lots) < spec.lot_step:
                return None, None

            clipped_lots = abs(resolved_lots)
            order_side = OrderSide.BUY if resolved_lots > 0 else OrderSide.SELL
            sizing_reason = (
                f"Carver Sizing: {clipped_lots} lots | Forecast={forecast_val:+.1f} | "
                f"VolScalar={vol_scalar:.2f} | PriceVol={price_vol:.3f} | SL={sl_distance:.2f}"
            )
        else:
            # -------------------------------------------------------------
            # Phương pháp Cổ điển WuWei: 1% Fixed Risk ATR Sizing
            # -------------------------------------------------------------
            risk_capital = account.equity * self.config.risk_per_trade_pct
            cost_per_lot_at_risk = sl_distance * spec.contract_size

            if cost_per_lot_at_risk <= 1e-6:
                return None, None

            raw_lots = risk_capital / cost_per_lot_at_risk
            step = spec.lot_step
            rounded_lots = math.floor(raw_lots / step) * step
            rounded_lots = round(rounded_lots, 4)
            clipped_lots = float(np.clip(rounded_lots, spec.min_lot, spec.max_lot))

            if clipped_lots < spec.min_lot:
                return None, None

            order_side = OrderSide.BUY if signal.action == SignalAction.BUY else OrderSide.SELL
            sizing_reason = f"Volatility Sized: {clipped_lots} lots | ATR={atr_n:.3f} (Risk 1.0%)"

        # 5. Xác định Hard Stop Loss
        if order_side == OrderSide.BUY:
            stop_loss = current_price - sl_distance
        else:
            stop_loss = current_price + sl_distance

        order_id = f"ORD_{symbol}_{signal.timestamp.strftime('%Y%m%d%H%M%S')}_{order_side.value}"

        order_event = OrderEvent(
            order_id=order_id,
            symbol=symbol,
            timestamp=signal.timestamp,
            side=order_side,
            order_type=OrderType.MARKET,
            volume=clipped_lots,
            price=current_price,
            stop_loss=stop_loss,
            take_profit=None,  # R:R mở - Trend Following thuần túy
            reason=sizing_reason
        )

        return order_event, None
