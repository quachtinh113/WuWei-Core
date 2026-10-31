"""
WuWei Core - MetaTrader 5 Live Connector
Institutional Live Execution Engine for Exness
"""
from __future__ import annotations

import collections
import os
from pathlib import Path
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from core.events import BarEvent
from core.types import Position, PositionSide


class MT5Connector:
    """
    Quản lý kết nối an toàn với MetaTrader 5 Terminal:
    - Tự động nhận diện suffix mã sản phẩm (e.g. XAUUSDm trên Exness Standard/Raw)
    - Tải nến lịch sử OHLCV trực tiếp từ broker để warmup
    - Dynamic Spread Gate: Hoãn vào lệnh nếu spread giãn do Rollover hoặc thiếu thanh khoản
    - Max Slippage Deviation Guard: Khóa trượt giá động theo khoảng cách Stop Loss
    - Ghi nhật ký thực thi chi tiết (execution_audit.log) để giám sát Execution Drag
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
        self.spread_history: Dict[str, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=60))
        self.audit_log_path = Path(__file__).resolve().parent.parent / "execution_audit.log"

    def connect(self) -> bool:
        """Khởi tạo kết nối MT5 Terminal"""
        if self.is_connected:
            try:
                acc = mt5.account_info()
                if acc is not None:
                    return True
            except Exception:
                pass
            self.disconnect()

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
            self.is_connected = False
            return False

        account_info = mt5.account_info()
        if account_info is None:
            print("[MT5Connector ERROR] Unable to retrieve account info")
            self.is_connected = False
            return False

        self.is_connected = True
        print(f"[MT5Connector] Connected successfully to Account: {account_info.login} ({account_info.server})")
        print(f"[MT5Connector] Balance: ${account_info.balance:,.2f} | Equity: ${account_info.equity:,.2f} | Leverage: 1:{account_info.leverage}")
        return True

    def disconnect(self) -> None:
        try:
            mt5.shutdown()
        except Exception:
            pass
        self.is_connected = False
        print("[MT5Connector] Disconnected from MT5")

    def get_account_state(self) -> Dict[str, float]:
        """Lấy số dư, vốn thực tế và ký quỹ hiện tại"""
        info = None
        try:
            info = mt5.account_info()
        except Exception:
            pass

        if info is None:
            self.is_connected = False
            if not self.connect():
                return {"balance": 0.0, "equity": 0.0, "margin": 0.0, "free_margin": 0.0}
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

    def _log_execution_audit(self, message: str) -> None:
        """Ghi nhật ký thực thi chi tiết phục vụ rà soát Execution Drag & Slippage"""
        try:
            ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{ts_str}] {message}\n"
            with open(self.audit_log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def check_spread_gate(
        self,
        generic_symbol: str,
        atr: Optional[float] = None
    ) -> Tuple[bool, float, float, str]:
        """
        Dynamic Spread Gate:
        Kiểm tra độ giãn Spread theo chuẩn:
        Spread_current <= min(2.5 * Spread_median, 0.20 * ATR)
        Tự động đóng vai trò Rollover & Illiquidity Lock khi spread bị bung rộng.
        Trả về: (is_allowed, current_spread_points, max_allowed_spread_points, reason)
        """
        broker_symbol = self.resolve_symbol(generic_symbol)
        if not broker_symbol:
            return False, 0.0, 0.0, "Symbol not resolved"

        s_info = mt5.symbol_info(broker_symbol)
        tick = mt5.symbol_info_tick(broker_symbol)
        if not s_info or not tick:
            return False, 0.0, 0.0, "Cannot get tick/symbol info"

        point = s_info.point if s_info.point > 0 else 0.0001
        current_spread_points = float(s_info.spread) if s_info.spread > 0 else float(round((tick.ask - tick.bid) / point, 1))

        # Lưu mẫu spread vào lịch sử trượt
        self.spread_history[generic_symbol].append(current_spread_points)

        # Baseline Median Spread (Points) theo từng nhóm tài sản
        clean_sym = generic_symbol.replace("m", "").replace("_", "").upper()
        if "XAU" in clean_sym or clean_sym == "GOLD":
            baseline_median = 200.0  # ~0.20 USD trên Vàng
        elif "XAG" in clean_sym or clean_sym == "SILVER":
            baseline_median = 30.0   # ~0.03 USD trên Bạc
        elif "BTC" in clean_sym:
            baseline_median = 2500.0 # ~25 USD trên Bitcoin
        elif "ETH" in clean_sym:
            baseline_median = 250.0  # ~2.5 USD trên ETH
        elif any(idx in clean_sym for idx in ("US500", "SPX", "USTEC", "NAS", "JP225", "DE30")):
            baseline_median = 80.0
        elif any(e in clean_sym for e in ("USOIL", "UKOIL", "OIL")):
            baseline_median = 40.0
        else:
            baseline_median = 15.0   # ~1.5 pips cho FX Majors

        if len(self.spread_history[generic_symbol]) >= 5:
            median_spread = float(np.median(list(self.spread_history[generic_symbol])))
        else:
            median_spread = baseline_median

        # Ngưỡng 1: 2.5 * Spread_median
        max_from_median = 2.5 * median_spread

        # Ngưỡng 2: 0.20 * ATR (quy đổi ra points)
        if atr is not None and atr > 0:
            atr_points = atr / point
            max_from_atr = 0.20 * atr_points
            max_allowed = min(max_from_median, max_from_atr)
        else:
            max_allowed = max_from_median

        # Đảm bảo mức sàn tối thiểu hợp lý (không thắt chặt dưới mức bình thường)
        max_allowed = max(max_allowed, baseline_median * 1.5)

        if current_spread_points > max_allowed:
            reason = f"Spread {current_spread_points:.1f} pts > Max Allowed {max_allowed:.1f} pts (2.5x Median: {max_from_median:.1f})"
            return False, current_spread_points, max_allowed, reason

        return True, current_spread_points, max_allowed, "Normal"

    def send_market_order(
        self,
        generic_symbol: str,
        side: str,  # "BUY" hoặc "SELL"
        volume: float,
        stop_loss: float,
        comment: str = "WuWei Core",
        atr: Optional[float] = None
    ) -> Optional[int]:
        """Gửi lệnh Market Order trực tiếp lên Exness Server với Dynamic Spread Gate & Max Slippage Guard"""
        broker_symbol = self.resolve_symbol(generic_symbol)
        if not broker_symbol:
            return None

        # 1. DYNAMIC SPREAD GATE CHECK (Chống giãn Spread phiên Mỹ/Rollover)
        is_allowed, cur_spread, max_spread, spread_reason = self.check_spread_gate(generic_symbol, atr=atr)
        if not is_allowed:
            log_msg = f"[EXECUTION_DEFERRED] {generic_symbol} Spread {cur_spread:.1f} > Max Allowed {max_spread:.1f} ({spread_reason})"
            print(f"[MT5Connector WARNING] {log_msg}")
            self._log_execution_audit(log_msg)
            return None

        tick = mt5.symbol_info_tick(broker_symbol)
        s_info = mt5.symbol_info(broker_symbol)
        if not tick or not s_info:
            print(f"[MT5Connector ERROR] Cannot get tick/info for {broker_symbol}")
            return None

        is_buy = side.upper() == "BUY"
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        price = tick.ask if is_buy else tick.bid

        # 2. MAX SLIPPAGE DEVIATION GUARD (Khóa trượt giá động theo khoảng cách SL)
        point = s_info.point if s_info.point > 0 else 0.0001
        sl_distance = abs(price - stop_loss)
        sl_distance_points = sl_distance / point if point > 0 else 50.0
        # Khống chế trượt giá tối đa 5% khoảng cách SL (kẹp trong [10, 80] points)
        dyn_deviation = int(sl_distance_points * 0.05)
        deviation = max(10, min(dyn_deviation, 80))

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": float(volume),
            "type": order_type,
            "price": price,
            "sl": float(round(stop_loss, s_info.digits)),
            "tp": 0.0,  # Vô Vi: R:R mở
            "deviation": deviation,
            "magic": self.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        self._log_execution_audit(
            f"[ORDER_SUBMITTED] {generic_symbol} {side} {volume:.2f}L @ {price} | "
            f"SL={stop_loss:.5f} | Spread={cur_spread:.1f} pts | DeviationGuard={deviation} pts"
        )

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error_code = result.retcode if result else mt5.last_error()
            err_comment = result.comment if result else ''
            err_msg = f"[ORDER_FAILED] Ticket: None | Retcode={error_code} | Comment: {err_comment}"
            print(f"[MT5Connector ERROR] {err_msg}")
            self._log_execution_audit(f"{err_msg} for {generic_symbol} {side}")
            return None

        exec_msg = (
            f"[ORDER_FILLED] Ticket=#{result.order} | {side} {volume:.2f}L {broker_symbol} @ {result.price} | "
            f"SL={stop_loss:.5f} | Retcode={result.retcode}"
        )
        print(f"[MT5Connector SUCCESS] {exec_msg}")
        self._log_execution_audit(exec_msg)
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
