"""
WuWei Core - MetaTrader 5 Live Connector
Institutional Live Execution Engine for Exness
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import pandas as pd
import MetaTrader5 as mt5

from core.events import BarEvent
from core.types import Position, PositionSide


class MT5Connector:
    """
    Quản lý kết nối an toàn với MetaTrader 5 Terminal:
    - Tự động nhận diện suffix mã sản phẩm (e.g. XAUUSDm trên Exness Standard/Raw)
    - Tải nến lịch sử OHLCV trực tiếp từ broker để warmup
    - Đẩy lệnh Market có Hard Stop Loss gắn trực tiếp trên máy chủ sàn Exness
    - Cập nhật dời Stop Loss (Break-even & Trailing Stop) theo thời gian thực
    """

    def __init__(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        path: Optional[str] = None,
        magic: int = 260999
    ):
        login_val = login or os.getenv("MT5_LOGIN") or os.getenv("MT5_ACCOUNT") or "0"
        self.login = int(login_val)
        self.password = password or os.getenv("MT5_PASSWORD", "")
        self.server = server or os.getenv("MT5_SERVER", "Exness-MT5Trial6")
        self.path = path or os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe")
        self.magic = magic
        self.is_connected = False
        self.symbol_map: Dict[str, str] = {}

    def connect(self) -> bool:
        """Khởi tạo kết nối MT5 Terminal"""
        if self.is_connected:
            return True

        init_args = {}
        if self.path and os.path.exists(self.path):
            init_args["path"] = self.path
        if self.login and self.password and self.server:
            init_args["login"] = self.login
            init_args["password"] = self.password
            init_args["server"] = self.server

        res = mt5.initialize(**init_args)
        if not res:
            error = mt5.last_error()
            print(f"[MT5Connector ERROR] Initialize failed: {error}")
            return False

        account_info = mt5.account_info()
        if account_info is None:
            print("[MT5Connector ERROR] Unable to retrieve account info")
            return False

        self.is_connected = True
        print(f"[MT5Connector] Connected successfully to Account: {account_info.login} ({account_info.server})")
        print(f"[MT5Connector] Balance: ${account_info.balance:,.2f} | Equity: ${account_info.equity:,.2f} | Leverage: 1:{account_info.leverage}")
        return True

    def disconnect(self) -> None:
        if self.is_connected:
            mt5.shutdown()
            self.is_connected = False
            print("[MT5Connector] Disconnected from MT5")

    def get_account_state(self) -> Dict[str, float]:
        """Lấy số dư, vốn thực tế và ký quỹ hiện tại"""
        if not self.is_connected:
            self.connect()
        info = mt5.account_info()
        if not info:
            return {"balance": 0.0, "equity": 0.0, "margin": 0.0, "free_margin": 0.0}
        return {
            "balance": float(info.balance),
            "equity": float(info.equity),
            "margin": float(info.margin),
            "free_margin": float(info.margin_free),
        }

    def resolve_symbol(self, generic_symbol: str) -> Optional[str]:
        """Tự động khớp tên cặp tiền trên sàn Exness (XAUUSD, EURUSD, US30 -> US30_x10, etc.)"""
        if generic_symbol in self.symbol_map:
            return self.symbol_map[generic_symbol]

        candidates = [
            generic_symbol,
            f"{generic_symbol}m",
            f"{generic_symbol}.m",
            f"{generic_symbol}_i",
            f"{generic_symbol}_x10",
            f"{generic_symbol}_x100",
        ]
        for c in candidates:
            info = mt5.symbol_info(c)
            if info is not None:
                if not info.visible:
                    mt5.symbol_select(c, True)
                self.symbol_map[generic_symbol] = c
                return c

        # Tìm kiếm linh hoạt trong danh sách toàn bộ symbols của Broker
        all_syms = mt5.symbols_get()
        if all_syms:
            for s in all_syms:
                if s.name == generic_symbol or s.name.startswith(generic_symbol):
                    if not s.visible:
                        mt5.symbol_select(s.name, True)
                    self.symbol_map[generic_symbol] = s.name
                    return s.name

        print(f"[MT5Connector WARNING] Symbol {generic_symbol} not found on broker!")
        return None

    def fetch_historical_bars(
        self,
        generic_symbol: str,
        timeframe_str: str = "H1",
        count: int = 100
    ) -> List[BarEvent]:
        """Tải nến quá khứ từ MT5 để nạp trạng thái cho các Engine"""
        broker_symbol = self.resolve_symbol(generic_symbol)
        if not broker_symbol:
            return []

        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        tf = tf_map.get(timeframe_str.upper(), mt5.TIMEFRAME_H1)

        rates = mt5.copy_rates_from_pos(broker_symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            return []

        bars: List[BarEvent] = []
        for r in rates:
            ts = datetime.fromtimestamp(r["time"])
            bar = BarEvent(
                symbol=generic_symbol,
                timestamp=ts,
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r["tick_volume"]),
            )
            bars.append(bar)
        return bars

    def send_market_order(
        self,
        generic_symbol: str,
        side: str,  # "BUY" hoặc "SELL"
        volume: float,
        stop_loss: float,
        comment: str = "WuWei Core"
    ) -> Optional[int]:
        """Gửi lệnh Market Order trực tiếp lên Exness Server với Hard Stop Loss"""
        broker_symbol = self.resolve_symbol(generic_symbol)
        if not broker_symbol:
            return None

        tick = mt5.symbol_info_tick(broker_symbol)
        if not tick:
            print(f"[MT5Connector ERROR] Cannot get tick for {broker_symbol}")
            return None

        is_buy = side.upper() == "BUY"
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        price = tick.ask if is_buy else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": float(volume),
            "type": order_type,
            "price": price,
            "sl": float(round(stop_loss, 2 if "JPY" in broker_symbol or "XAU" in broker_symbol else 5)),
            "tp": 0.0,  # Vô Vi: R:R mở
            "deviation": 20,
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error_code = result.retcode if result else mt5.last_error()
            print(f"[MT5Connector ERROR] Order send failed: {error_code} | Comment: {result.comment if result else ''}")
            return None

        print(f"[MT5Connector SUCCESS] Order executed: Ticket={result.order} | {side} {volume} lots {broker_symbol} @ {result.price} | SL={stop_loss:.2f}")
        return result.order

    def modify_stop_loss(self, ticket: int, new_sl: float, symbol: Optional[str] = None) -> bool:
        """Cập nhật dời Stop Loss (Break-even hoặc Trailing Stop) trên Broker Server"""
        digits = 2
        if symbol:
            broker_sym = self.resolve_symbol(symbol) or symbol
            s_info = mt5.symbol_info(broker_sym)
            if s_info:
                digits = s_info.digits

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": float(round(new_sl, digits)),
            "tp": 0.0,
        }
        res = mt5.order_send(request)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return True
        err = res.retcode if res else mt5.last_error()
        print(f"[MT5Connector WARNING] modify_stop_loss failed for #{ticket}: {err}")
        return False

    def close_position(self, ticket: int, comment: str = "WuWei Close") -> bool:
        """Đóng vị thế theo Ticket ID bằng lệnh ngược chiều"""
        positions = mt5.positions_get(ticket=ticket)
        if not positions or len(positions) == 0:
            print(f"[MT5Connector WARNING] Position #{ticket} not found to close.")
            return False

        pos = positions[0]
        broker_symbol = pos.symbol
        tick = mt5.symbol_info_tick(broker_symbol)
        if not tick:
            print(f"[MT5Connector ERROR] Cannot get tick to close position #{ticket} for {broker_symbol}")
            return False

        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        order_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": broker_symbol,
            "volume": float(pos.volume),
            "type": order_type,
            "price": price,
            "deviation": 25,
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"[MT5Connector SUCCESS] Position #{ticket} closed at {result.price} (PnL recorded)")
            return True
        err = result.retcode if result else mt5.last_error()
        print(f"[MT5Connector ERROR] Close position #{ticket} failed: {err}")
        return False

    def get_open_positions(self) -> Dict[str, Position]:
        """Lấy danh sách các vị thế đang mở trên MT5 theo chuẩn hệ thống WuWei"""
        positions = mt5.positions_get()
        if positions is None:
            return {}

        inv_map = {v: k for k, v in self.symbol_map.items()}
        open_pos_map: Dict[str, Position] = {}
        for p in positions:
            side = PositionSide.LONG if p.type == mt5.POSITION_TYPE_BUY else PositionSide.SHORT
            sym = p.symbol
            generic_sym = inv_map.get(sym, sym)
            pos = Position(
                symbol=generic_sym,
                side=side,
                volume=float(p.volume),
                entry_price=float(p.price_open),
                entry_time=datetime.fromtimestamp(p.time),
                current_price=float(p.price_current),
                stop_loss=float(p.sl),
                take_profit=float(p.tp) if p.tp else None,
                ticket=int(p.ticket),
                unrealized_pnl=float(p.profit)
            )
            open_pos_map[generic_sym] = pos
        return open_pos_map
