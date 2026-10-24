"""
WuWei Core - Execution Engine (Hành Vô Vi - Order Management & Trailing Exit)
Quản lý trạng thái vị thế, trôi theo dòng nước (Trend Riding), Dời hòa vốn (+2N)
Trailing Stop vô hạn (R:R mở, tuyệt đối không đặt Take Profit cố định).
Triết lý: "Hành bất ngôn chi giáo, xử vô vi chi sự" - Cứ để dòng giá tự quyết định điểm kết thúc.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np

from config.settings import ExecutionConfig, SymbolSpecification
from core.events import BarEvent, FillEvent, OrderEvent, SignalEvent
from core.types import (
    ExitReason,
    OrderSide,
    Position,
    PositionSide,
    SignalAction,
    TradeRecord,
)


class ExecutionEngine:
    """
    Module Hành Vô Vi (Order Management & Trailing Stop):
    - Trailing Stop động theo Đỉnh/Đáy Donchian(10) hoặc Chandelier Exit.
    - Cơ chế Break-even: Khi giá đạt được lợi nhuận +2.0 * N, tự động khóa Stop Loss về Entry + Buffer.
    - Zero Fixed TP: Giữ nguyên vị thế khi xu hướng còn chạy, chỉ thoát khi cấu trúc bị phá vỡ.
    - Mô phỏng độ trễ, trượt giá (Slippage: 0.5 pip) và phí hoa hồng (Commission: 7 USD/lot round-trip).
    """

    def __init__(self, config: ExecutionConfig, symbol_specs: Dict[str, SymbolSpecification]):
        self.config = config
        self.symbol_specs = symbol_specs
        self.open_positions: Dict[str, Position] = {}
        self.closed_trades: List[TradeRecord] = []
        self.trade_counter: int = 0

    def reset(self) -> None:
        self.open_positions.clear()
        self.closed_trades.clear()
        self.trade_counter = 0

    def process_order(self, order: OrderEvent, bar: BarEvent) -> FillEvent:
        """
        Khớp lệnh thị trường với trượt giá và phí hoa hồng.
        """
        spec = self.symbol_specs[order.symbol]
        slippage_price = spec.slippage_pips * spec.pip_size

        if order.side == OrderSide.BUY:
            fill_price = bar.close + slippage_price
            pos_side = PositionSide.LONG
        else:
            fill_price = bar.close - slippage_price
            pos_side = PositionSide.SHORT

        # Commission tính trên lot (e.g., $7/lot round-trip)
        commission = order.volume * spec.commission_per_lot

        # Khởi tạo vị thế mở
        position = Position(
            symbol=order.symbol,
            side=pos_side,
            volume=order.volume,
            entry_price=fill_price,
            entry_time=bar.timestamp,
            current_price=fill_price,
            stop_loss=order.stop_loss,
            take_profit=None,  # Vô Vi: R:R mở
            breakeven_locked=False,
            accumulated_commission=commission,
            atr_at_entry=abs(fill_price - order.stop_loss) / 2.0  # Ước lượng N từ SL
        )
        self.open_positions[order.symbol] = position

        return FillEvent(
            order_id=order.order_id,
            symbol=order.symbol,
            timestamp=bar.timestamp,
            side=order.side,
            volume=order.volume,
            fill_price=fill_price,
            commission=commission,
            slippage=slippage_price,
            position_side=pos_side
        )

    def on_bar_update(
        self,
        bar: BarEvent,
        signal: Optional[SignalEvent] = None
    ) -> List[TradeRecord]:
        """
        Cập nhật biến động nến cho từng vị thế mở:
        1. Kiểm tra dời hòa vốn (Break-even tại +2.0*N)
        2. Cập nhật Trailing Stop (Donchian 10 hoặc Chandelier Exit)
        3. Kiểm tra chạm Stop Loss trong nến (Intra-bar Stop hit)
        4. Kiểm tra tín hiệu thoát Donchian ngược chiều (Exit Signal)
        """
        completed_trades: List[TradeRecord] = []
        position = self.open_positions.get(bar.symbol)
        if not position or position.side == PositionSide.FLAT:
            return completed_trades

        spec = self.symbol_specs[bar.symbol]
        slippage_price = spec.slippage_pips * spec.pip_size
        breakeven_buffer = self.config.breakeven_buffer_pips * spec.pip_size

        # Cập nhật High/Low của vị thế
        position.update_price(bar.high, bar.low, bar.close)
        n_atr = position.atr_at_entry if position.atr_at_entry > 0 else (bar.high - bar.low)

        # -------------------------------------------------------------
        # 1. Kiểm tra Chạm Stop Loss Hiện Tại (Intra-bar Stop hit)
        # -------------------------------------------------------------
        sl_triggered = False
        exit_price = 0.0
        exit_reason = ExitReason.STOP_LOSS

        if position.side == PositionSide.LONG:
            if bar.low <= position.stop_loss:
                sl_triggered = True
                exit_price = min(bar.open, position.stop_loss) - slippage_price
                if position.stop_loss > position.entry_price:
                    exit_reason = ExitReason.TRAILING_STOP
                elif position.breakeven_locked and abs(position.stop_loss - position.entry_price) <= (breakeven_buffer * 2):
                    exit_reason = ExitReason.BREAK_EVEN
                else:
                    exit_reason = ExitReason.STOP_LOSS
        elif position.side == PositionSide.SHORT:
            if bar.high >= position.stop_loss:
                sl_triggered = True
                exit_price = max(bar.open, position.stop_loss) + slippage_price
                if position.stop_loss < position.entry_price:
                    exit_reason = ExitReason.TRAILING_STOP
                elif position.breakeven_locked and abs(position.stop_loss - position.entry_price) <= (breakeven_buffer * 2):
                    exit_reason = ExitReason.BREAK_EVEN
                else:
                    exit_reason = ExitReason.STOP_LOSS

        if sl_triggered:
            trade = self._close_position(position, exit_price, bar.timestamp, exit_reason, spec, slippage_price)
            completed_trades.append(trade)
            return completed_trades

        # -------------------------------------------------------------
        # 2. Cơ chế Break-Even: Dời SL về hòa vốn khi giá chạy +2.0 * N
        # -------------------------------------------------------------
        be_trigger_distance = self.config.breakeven_atr_trigger * n_atr
        if not position.breakeven_locked:
            if position.side == PositionSide.LONG:
                if position.highest_price >= position.entry_price + be_trigger_distance:
                    new_sl = position.entry_price + breakeven_buffer
                    if new_sl > position.stop_loss:
                        position.stop_loss = new_sl
                        position.breakeven_locked = True
            elif position.side == PositionSide.SHORT:
                if position.lowest_price <= position.entry_price - be_trigger_distance:
                    new_sl = position.entry_price - breakeven_buffer
                    if new_sl < position.stop_loss:
                        position.stop_loss = new_sl
                        position.breakeven_locked = True

        # -------------------------------------------------------------
        # 3. Cơ chế Trailing Stop: Donchian(10) hoặc Chandelier Exit
        # -------------------------------------------------------------
        if signal is not None:
            if self.config.trailing_mode == "DONCHIAN_10":
                if position.side == PositionSide.LONG:
                    # Nâng dần SL theo đáy 10 nến gần nhất (Không bao giờ hạ SL)
                    if signal.exit_donchian_low > position.stop_loss:
                        position.stop_loss = signal.exit_donchian_low
                elif position.side == PositionSide.SHORT:
                    # Kéo dần SL xuống theo đỉnh 10 nến gần nhất (Không bao giờ nâng SL)
                    if signal.exit_donchian_high < position.stop_loss:
                        position.stop_loss = signal.exit_donchian_high
            elif self.config.trailing_mode == "CHANDELIER":
                chandelier_dist = self.config.chandelier_atr_mult * n_atr
                if position.side == PositionSide.LONG:
                    chandelier_sl = position.highest_price - chandelier_dist
                    if chandelier_sl > position.stop_loss:
                        position.stop_loss = chandelier_sl
                elif position.side == PositionSide.SHORT:
                    chandelier_sl = position.lowest_price + chandelier_dist
                    if chandelier_sl < position.stop_loss:
                        position.stop_loss = chandelier_sl

        # -------------------------------------------------------------
        # 4. Kiểm tra Tín hiệu Thoát Lệnh từ SignalEngine (Kênh Donchian 10)
        # -------------------------------------------------------------
        if signal is not None:
            if position.side == PositionSide.LONG and signal.action == SignalAction.CLOSE_BUY:
                exit_price = bar.close - slippage_price
                trade = self._close_position(position, exit_price, bar.timestamp, ExitReason.SIGNAL_REVERSAL, spec, slippage_price)
                completed_trades.append(trade)
                return completed_trades
            elif position.side == PositionSide.SHORT and signal.action == SignalAction.CLOSE_SELL:
                exit_price = bar.close + slippage_price
                trade = self._close_position(position, exit_price, bar.timestamp, ExitReason.SIGNAL_REVERSAL, spec, slippage_price)
                completed_trades.append(trade)
                return completed_trades

        # Cập nhật Unrealized PnL cho vị thế còn mở
        self._update_unrealized_pnl(position, bar.close, spec)
        return completed_trades

    def emergency_liquidate_all(self, bar: BarEvent, reason: ExitReason = ExitReason.CIRCUIT_BREAKER) -> List[TradeRecord]:
        """
        Đóng khẩn cấp mọi vị thế đang mở khi chạm ngưỡng Circuit Breaker.
        """
        liquidated_trades: List[TradeRecord] = []
        for symbol, position in list(self.open_positions.items()):
            spec = self.symbol_specs[symbol]
            slippage_price = spec.slippage_pips * spec.pip_size
            exit_price = bar.close - slippage_price if position.side == PositionSide.LONG else bar.close + slippage_price
            trade = self._close_position(position, exit_price, bar.timestamp, reason, spec, slippage_price)
            liquidated_trades.append(trade)
        return liquidated_trades

    def _close_position(
        self,
        position: Position,
        exit_price: float,
        exit_time: datetime,
        reason: ExitReason,
        spec: SymbolSpecification,
        slippage: float
    ) -> TradeRecord:
        self.trade_counter += 1
        trade_id = f"TRD_{position.symbol}_{self.trade_counter:05d}"

        # Tính toán Gross PnL
        if position.side == PositionSide.LONG:
            price_diff = exit_price - position.entry_price
        else:
            price_diff = position.entry_price - exit_price

        gross_pnl = price_diff * position.volume * spec.contract_size
        net_pnl = gross_pnl - position.accumulated_commission

        initial_risk = abs(position.entry_price - position.stop_loss) * position.volume * spec.contract_size
        r_multiple = net_pnl / initial_risk if initial_risk > 1e-4 else 0.0
        return_pct = net_pnl / (position.entry_price * position.volume * spec.contract_size) if position.entry_price > 0 else 0.0

        trade = TradeRecord(
            trade_id=trade_id,
            symbol=position.symbol,
            side=position.side,
            volume=position.volume,
            entry_time=position.entry_time,
            exit_time=exit_time,
            entry_price=position.entry_price,
            exit_price=exit_price,
            stop_loss=position.stop_loss,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            return_pct=return_pct,
            r_multiple=r_multiple,
            exit_reason=reason,
            commission=position.accumulated_commission,
            slippage=slippage
        )

        self.closed_trades.append(trade)
        del self.open_positions[position.symbol]
        return trade

    def _update_unrealized_pnl(self, position: Position, current_price: float, spec: SymbolSpecification) -> None:
        if position.side == PositionSide.LONG:
            diff = current_price - position.entry_price
        else:
            diff = position.entry_price - current_price
        position.unrealized_pnl = (diff * position.volume * spec.contract_size) - position.accumulated_commission
