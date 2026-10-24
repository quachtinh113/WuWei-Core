"""
WuWei Core - Core Domain Types & Enums
Institutional Systematic Trading Data Structures
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class RegimeState(str, Enum):
    """
    Thiên thời - State of Market Regimes.
    Triết lý: Ở trạng thái NO_TREND, tâm trí và hệ thống ở trạng thái Vô Vi (không cưỡng cầu giao dịch).
    """
    NO_TREND = "NO_TREND"          # Thị trường nén biến động, sideway, ADX < 20 & CHOP > 61.8 (CẤM VÀO LỆNH)
    TRENDING_BULL = "TRENDING_BULL" # Xu hướng tăng mạnh (ADX >= 25 & CHOP < 38.2 & Price > MA)
    TRENDING_BEAR = "TRENDING_BEAR" # Xu hướng giảm mạnh (ADX >= 25 & CHOP < 38.2 & Price < MA)
    RANGING = "RANGING"             # Giai đoạn chuyển tiếp / bất định (Không vào lệnh mới)


class SignalAction(str, Enum):
    """Tín hiệu giao dịch sinh ra từ SignalEngine"""
    NONE = "NONE"
    BUY = "BUY"
    SELL = "SELL"
    CLOSE_BUY = "CLOSE_BUY"
    CLOSE_SELL = "CLOSE_SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    STOP = "STOP"
    LIMIT = "LIMIT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class PositionSide(str, Enum):
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TRAILING_STOP = "TRAILING_STOP"
    BREAK_EVEN = "BREAK_EVEN"
    SIGNAL_REVERSAL = "SIGNAL_REVERSAL"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    MANUAL = "MANUAL"


@dataclass
class Position:
    """Quản lý thực thể vị thế mở trong Execution Engine"""
    symbol: str
    side: PositionSide
    volume: float                    # Lot size
    entry_price: float
    entry_time: datetime
    current_price: float
    stop_loss: float
    take_profit: Optional[float] = None  # None: R:R mở (Trend riding)
    ticket: Optional[int] = None         # MT5 Broker Ticket ID
    breakeven_locked: bool = False
    highest_price: float = field(init=False)
    lowest_price: float = field(init=False)
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    accumulated_commission: float = 0.0
    atr_at_entry: float = 0.0

    def __post_init__(self):
        self.highest_price = self.entry_price
        self.lowest_price = self.entry_price

    def update_price(self, high: float, low: float, close: float) -> None:
        self.current_price = close
        if high > self.highest_price:
            self.highest_price = high
        if low < self.lowest_price:
            self.lowest_price = low


@dataclass
class TradeRecord:
    """Bản ghi giao dịch đã hoàn tất để tính toán chỉ số hiệu suất"""
    trade_id: str
    symbol: str
    side: PositionSide
    volume: float
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_loss: float
    gross_pnl: float
    net_pnl: float
    return_pct: float
    r_multiple: float
    exit_reason: ExitReason
    commission: float
    slippage: float
    duration_bars: int = 0


@dataclass
class AccountSnapshot:
    """Trạng thái tài khoản theo thời gian thực để kiểm soát rủi ro tức thời"""
    timestamp: datetime
    balance: float
    equity: float
    margin_used: float
    free_margin: float
    daily_starting_equity: float
    daily_drawdown_pct: float
    peak_equity: float
    total_drawdown_pct: float
    circuit_breaker_active: bool = False
    circuit_breaker_until: Optional[datetime] = None
