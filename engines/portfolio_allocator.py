"""
WuWei Core - Portfolio Allocator & Correlation Clustering Engine
Based on Robert Carver (pysystemtrade) & Risk Parity / Cluster Allocation
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple
import numpy as np
import pandas as pd

from core.types import Position, PositionSide


SECTOR_MAP: Dict[str, str] = {
    "XAUUSD": "METALS",
    "XAGUSD": "METALS",
    "EURUSD": "FX_MAJOR",
    "GBPUSD": "FX_MAJOR",
    "USDJPY": "FX_MAJOR",
    "AUDUSD": "FX_MAJOR",
    "USDCAD": "FX_MAJOR",
    "USDCHF": "FX_MAJOR",
    "NZDUSD": "FX_MAJOR",
    "EURJPY": "FX_CROSS",
    "GBPJPY": "FX_CROSS",
    "EURGBP": "FX_CROSS",
    "AUDJPY": "FX_CROSS",
    "US30": "EQUITY_INDEX",
    "US500": "EQUITY_INDEX",
    "USTEC": "EQUITY_INDEX",
    "DE30": "EQUITY_INDEX",
    "USOIL": "ENERGIES",
    "UKOIL": "ENERGIES",
    "BTCUSD": "CRYPTO",
    "ETHUSD": "CRYPTO",
}

# Hạn mức rủi ro tối đa cho từng nhóm ngành (% trên tổng danh mục)
SECTOR_MAX_RISK_CAPS: Dict[str, float] = {
    "METALS": 0.25,
    "FX_MAJOR": 0.30,
    "FX_CROSS": 0.20,
    "EQUITY_INDEX": 0.25,
    "ENERGIES": 0.20,
    "CRYPTO": 0.15,
}


class PortfolioAllocator:
    """
    Quản lý phân bổ danh mục định lượng đa tài sản:
    1. Tính toán ma trận tương quan trượt (Rolling Correlation Matrix).
    2. Chống dồn cược (De-correlation filter): Ngăn chặn mở 2 vị thế cùng chiều trên các cặp có tương quan >= 0.75.
    3. Kiểm soát hạn mức theo nhóm ngành (Sector Risk Cap): Đảm bảo không vượt quá hạn mức rủi ro nhóm.
    4. Tính toán trọng số danh mục động (Risk-Weighted Instrument Allocation).
    """

    def __init__(
        self,
        symbols: List[str],
        correlation_lookback: int = 40,
        max_correlation_threshold: float = 0.75,
    ):
        self.symbols = symbols
        self.lookback = correlation_lookback
        self.max_corr = max_correlation_threshold
        self.corr_matrix: pd.DataFrame = pd.DataFrame()
        self.price_history: Dict[str, List[float]] = {s: [] for s in symbols}

    def update_prices(self, price_snapshot: Dict[str, float]) -> None:
        """Cập nhật chuỗi giá đóng cửa gần nhất để tính toán tỷ suất sinh lời"""
        for sym, price in price_snapshot.items():
            if sym not in self.price_history:
                self.price_history[sym] = []
            self.price_history[sym].append(price)
            if len(self.price_history[sym]) > self.lookback * 2:
                self.price_history[sym].pop(0)

        # Tính toán ma trận tương quan nếu có đủ dữ liệu
        valid_series = {}
        for sym, prices in self.price_history.items():
            if len(prices) >= 15:
                s = pd.Series(prices)
                returns = s.pct_change().dropna()
                if len(returns) > 5 and returns.std() > 1e-6:
                    valid_series[sym] = returns

        if len(valid_series) >= 2:
            df_returns = pd.DataFrame(valid_series)
            self.corr_matrix = df_returns.corr()

    def get_correlation(self, sym_a: str, sym_b: str) -> float:
        """Lấy hệ số tương quan giữa 2 cặp tài sản"""
        if (
            not self.corr_matrix.empty
            and sym_a in self.corr_matrix.columns
            and sym_b in self.corr_matrix.columns
        ):
            val = self.corr_matrix.loc[sym_a, sym_b]
            if not np.isnan(val):
                return float(val)
        # Giá trị mặc định theo nhóm ngành nếu chưa đủ nến
        sec_a = SECTOR_MAP.get(sym_a, "OTHER")
        sec_b = SECTOR_MAP.get(sym_b, "OTHER")
        if sec_a == sec_b:
            if sec_a == "ENERGIES":
                return 0.90
            if sec_a == "EQUITY_INDEX":
                return 0.85
            if sec_a == "METALS":
                return 0.70
            return 0.60
        return 0.15

    def check_correlation_conflict(
        self,
        candidate_symbol: str,
        candidate_side: str,  # "BUY" hoặc "SELL"
        open_positions: Dict[str, Position],
    ) -> Tuple[bool, Optional[str]]:
        """
        Kiểm tra xem có bị xung đột tương quan quá mức với vị thế đang mở không:
        Nếu một cặp có tương quan >= max_corr đang mở cùng chiều -> Từ chối để chống dồn rủi ro.
        """
        for sym, pos in open_positions.items():
            if sym == candidate_symbol or pos.side == PositionSide.FLAT:
                continue

            pos_side_str = "BUY" if pos.side == PositionSide.LONG else "SELL"
            corr = self.get_correlation(candidate_symbol, sym)

            # Cùng chiều và tương quan dương cao
            if candidate_side == pos_side_str and corr >= self.max_corr:
                return True, (
                    f"Concentration Risk: {candidate_symbol} ({candidate_side}) has high correlation "
                    f"({corr:.2f}) with open {sym} ({pos_side_str})"
                )

            # Ngược chiều và tương quan âm cao (tương đương cùng chiều)
            if candidate_side != pos_side_str and corr <= -self.max_corr:
                return True, (
                    f"Concentration Risk: {candidate_symbol} ({candidate_side}) has inverse correlation "
                    f"({corr:.2f}) with open {sym} ({pos_side_str})"
                )

        return False, None

    def check_sector_risk_limit(
        self,
        candidate_symbol: str,
        open_positions: Dict[str, Position],
        max_positions_per_sector: int = 2,
    ) -> Tuple[bool, Optional[str]]:
        """Kiểm soát số lượng vị thế tối đa cho phép trong cùng 1 nhóm ngành"""
        target_sector = SECTOR_MAP.get(candidate_symbol, "OTHER")
        current_sector_count = sum(
            1 for s, p in open_positions.items()
            if p.side != PositionSide.FLAT and SECTOR_MAP.get(s) == target_sector
        )

        if current_sector_count >= max_positions_per_sector:
            return True, (
                f"Sector Cap Exceeded: {target_sector} already has {current_sector_count} "
                f"active positions (Max: {max_positions_per_sector})"
            )

        return False, None

    def compute_diversified_weights(
        self,
        active_symbols: List[str],
        open_positions: Dict[str, Position],
    ) -> Dict[str, float]:
        """
        Tính trọng số đa dạng hóa Robert Carver cho từng tài sản.
        Tài sản thuộc nhóm ngành có ít vị thế sẽ nhận tỷ trọng cao hơn.
        """
        total = len(active_symbols)
        if total == 0:
            return {}

        base_weight = 1.0 / total
        weights: Dict[str, float] = {}

        sector_counts: Dict[str, int] = {}
        for s in active_symbols:
            sec = SECTOR_MAP.get(s, "OTHER")
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

        for s in active_symbols:
            sec = SECTOR_MAP.get(s, "OTHER")
            cap = SECTOR_MAX_RISK_CAPS.get(sec, 0.20)
            sec_n = max(sector_counts.get(sec, 1), 1)
            # Trọng số được giới hạn không vượt quá cap / số lượng tài sản trong nhóm
            w = min(base_weight, cap / sec_n)
            weights[s] = max(w, 0.01)

        # Chuẩn hóa về tổng = 1.0
        sum_w = sum(weights.values())
        if sum_w > 0:
            weights = {k: v / sum_w for k, v in weights.items()}

        return weights
