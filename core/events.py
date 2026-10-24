"""
WuWei Core - Event Definitions
Event-Driven Architecture Pipeline
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional
from core.types import OrderSide, OrderType, PositionSide, RegimeState, SignalAction


class EventType(str, Enum):
    BAR = "BAR"
    REGIME = "REGIME"
    SIGNAL = "SIGNAL"
    ORDER_REQUEST = "ORDER_REQUEST"
    FILL = "FILL"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


@dataclass
class Event:
    event_type: EventType
    timestamp: datetime


@dataclass
class BarEvent(Event):
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __init__(
        self,
        symbol: str,
        timestamp: datetime,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0
    ):
        super().__init__(event_type=EventType.BAR, timestamp=timestamp)
        self.symbol = symbol
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


@dataclass
class RegimeEvent(Event):
    symbol: str
    state: RegimeState
    adx: float
    chop: float
    atr: float
    atr_ratio: float
    is_trading_allowed: bool

    def __init__(
        self,
        symbol: str,
        timestamp: datetime,
        state: RegimeState,
        adx: float,
        chop: float,
        atr: float,
        atr_ratio: float,
        is_trading_allowed: bool
    ):
        super().__init__(event_type=EventType.REGIME, timestamp=timestamp)
        self.symbol = symbol
        self.state = state
        self.adx = adx
        self.chop = chop
        self.atr = atr
        self.atr_ratio = atr_ratio
        self.is_trading_allowed = is_trading_allowed


@dataclass
class SignalEvent(Event):
    symbol: str
    action: SignalAction
    entry_donchian_high: float
    entry_donchian_low: float
    exit_donchian_high: float
    exit_donchian_low: float
    atr: float
    regime_state: RegimeState
    reason: str
    carver_forecast: float = 0.0
    rule_forecasts: Optional[Dict[str, float]] = None

    def __init__(
        self,
        symbol: str,
        timestamp: datetime,
        action: SignalAction,
        entry_donchian_high: float,
        entry_donchian_low: float,
        exit_donchian_high: float,
        exit_donchian_low: float,
        atr: float,
        regime_state: RegimeState,
        reason: str = "",
        carver_forecast: float = 0.0,
        rule_forecasts: Optional[Dict[str, float]] = None
    ):
        super().__init__(event_type=EventType.SIGNAL, timestamp=timestamp)
        self.symbol = symbol
        self.action = action
        self.entry_donchian_high = entry_donchian_high
        self.entry_donchian_low = entry_donchian_low
        self.exit_donchian_high = exit_donchian_high
        self.exit_donchian_low = exit_donchian_low
        self.atr = atr
        self.regime_state = regime_state
        self.reason = reason
        self.carver_forecast = carver_forecast
        self.rule_forecasts = rule_forecasts or {}


@dataclass
class OrderEvent(Event):
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    volume: float
    price: float
    stop_loss: float
    take_profit: Optional[float]
    reason: str

    def __init__(
        self,
        order_id: str,
        symbol: str,
        timestamp: datetime,
        side: OrderSide,
        order_type: OrderType,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: Optional[float] = None,
        reason: str = ""
    ):
        super().__init__(event_type=EventType.ORDER_REQUEST, timestamp=timestamp)
        self.order_id = order_id
        self.symbol = symbol
        self.side = side
        self.order_type = order_type
        self.volume = volume
        self.price = price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.reason = reason


@dataclass
class FillEvent(Event):
    order_id: str
    symbol: str
    side: OrderSide
    volume: float
    fill_price: float
    commission: float
    slippage: float
    position_side: PositionSide

    def __init__(
        self,
        order_id: str,
        symbol: str,
        timestamp: datetime,
        side: OrderSide,
        volume: float,
        fill_price: float,
        commission: float,
        slippage: float,
        position_side: PositionSide
    ):
        super().__init__(event_type=EventType.FILL, timestamp=timestamp)
        self.order_id = order_id
        self.symbol = symbol
        self.side = side
        self.volume = volume
        self.fill_price = fill_price
        self.commission = commission
        self.slippage = slippage
        self.position_side = position_side


@dataclass
class CircuitBreakerEvent(Event):
    symbol: str
    daily_drawdown_pct: float
    locked_until: datetime
    reason: str

    def __init__(
        self,
        symbol: str,
        timestamp: datetime,
        daily_drawdown_pct: float,
        locked_until: datetime,
        reason: str
    ):
        super().__init__(event_type=EventType.CIRCUIT_BREAKER, timestamp=timestamp)
        self.symbol = symbol
        self.daily_drawdown_pct = daily_drawdown_pct
        self.locked_until = locked_until
        self.reason = reason
