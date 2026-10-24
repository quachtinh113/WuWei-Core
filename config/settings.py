"""
WuWei Core - Configuration & Schema Validation
Institutional Quantitative Trading Engine
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Literal, Optional
import yaml
from pydantic import BaseModel, Field, field_validator


class SystemConfig(BaseModel):
    name: str = "WuWei Core Quantitative Engine"
    version: str = "1.0.0"
    philosophy: str = "Thuận theo tự nhiên / Vô vi - Pure Price Action Trend Riding"
    log_level: str = "INFO"
    structured_logging: bool = True


class SymbolSpecification(BaseModel):
    contract_size: float = Field(..., gt=0.0, description="Kích thước hợp đồng (Contract multiplier)")
    tick_size: float = Field(..., gt=0.0, description="Bước giá nhỏ nhất")
    tick_value: float = Field(..., gt=0.0, description="Giá trị quy đổi 1 tick (USD)")
    point_size: float = Field(..., gt=0.0, description="Đơn vị point tiêu chuẩn")
    pip_size: float = Field(..., gt=0.0, description="Đơn vị pip tiêu chuẩn")
    min_lot: float = Field(0.01, gt=0.0)
    max_lot: float = Field(50.0, gt=0.0)
    lot_step: float = Field(0.01, gt=0.0)
    slippage_pips: float = Field(0.5, ge=0.0)
    commission_per_lot: float = Field(7.0, ge=0.0)


class RegimeConfig(BaseModel):
    adx_period: int = Field(14, ge=5, le=100)
    chop_period: int = Field(14, ge=5, le=100)
    atr_period: int = Field(14, ge=5, le=100)
    atr_sma_period: int = Field(50, ge=10, le=200)
    adx_no_trend_threshold: float = Field(20.0, ge=5.0, le=50.0)
    chop_no_trend_threshold: float = Field(61.8, ge=50.0, le=90.0)
    adx_trend_threshold: float = Field(25.0, ge=10.0, le=60.0)
    chop_trend_threshold: float = Field(38.2, ge=10.0, le=50.0)


class SignalConfig(BaseModel):
    entry_channel_period: int = Field(20, ge=5, le=200, description="Donchian Breakout Entry period")
    exit_channel_period: int = Field(10, ge=3, le=100, description="Donchian Exit period")


class RiskConfig(BaseModel):
    initial_capital: float = Field(100000.0, gt=0.0)
    risk_per_trade_pct: float = Field(0.01, gt=0.0, le=0.05, description="Max 5% per trade")
    atr_period: int = Field(20, ge=5, le=100)
    stop_loss_atr_mult: float = Field(2.0, gt=0.5, le=5.0)
    max_daily_drawdown_pct: float = Field(0.03, gt=0.0, le=0.10, description="Circuit breaker threshold")
    max_total_drawdown_pct: float = Field(0.06, gt=0.0, le=0.20)
    max_open_trades_per_symbol: int = Field(1, ge=1, le=5)
    allow_dca: bool = Field(False, description="Strictly prohibited: No DCA on negative trades")
    allow_martingale: bool = Field(False, description="Strictly prohibited: No lot doubling")
    allow_hedge: bool = Field(False, description="Strictly prohibited: No opposite hedging")

    @field_validator("allow_dca", "allow_martingale", "allow_hedge")
    @classmethod
    def enforce_institutional_discipline(cls, v: bool) -> bool:
        if v is True:
            raise ValueError("Triết lý WuWei: Nghiêm cấm hoàn toàn DCA, Martingale hoặc Hedging!")
        return v


class ExecutionConfig(BaseModel):
    trailing_mode: Literal["DONCHIAN_10", "CHANDELIER"] = "DONCHIAN_10"
    chandelier_atr_mult: float = Field(3.0, gt=1.0, le=6.0)
    breakeven_atr_trigger: float = Field(2.0, gt=0.5, le=5.0)
    breakeven_buffer_pips: float = Field(1.0, ge=0.0)


class BacktestConfig(BaseModel):
    initial_cash: float = Field(100000.0, gt=0.0)
    default_symbol: str = "XAUUSD"
    generate_synthetic_if_missing: bool = True
    start_date: str = "2024-01-01"
    end_date: str = "2024-12-31"


class CarverConfigModel(BaseModel):
    enabled: bool = Field(True, description="Kích hoạt mô hình định lượng Robert Carver (pysystemtrade)")
    annual_vol_target: float = Field(0.20, ge=0.05, le=0.50, description="Mục tiêu biến động danh mục 20%/năm")
    average_absolute_forecast: float = Field(10.0, ge=5.0, le=20.0)
    forecast_cap: float = Field(20.0, ge=10.0, le=30.0)
    buffer_ratio: float = Field(0.10, ge=0.01, le=0.30, description="Độ rộng Buffer 10% để triệt tiêu giao dịch vi tế")
    volatility_lookback_days: int = Field(35, ge=10, le=100)
    fdm: float = Field(1.25, ge=1.0, le=2.0, description="Forecast Diversification Multiplier")
    idm: float = Field(1.35, ge=1.0, le=2.5, description="Instrument Diversification Multiplier")
    deleveraging_dd_threshold: float = Field(0.015, ge=0.005, le=0.05)


class AppSettings(BaseModel):
    system: SystemConfig = Field(default_factory=SystemConfig)
    symbols: Dict[str, SymbolSpecification] = Field(default_factory=dict)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    signal: SignalConfig = Field(default_factory=SignalConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    carver: CarverConfigModel = Field(default_factory=CarverConfigModel)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)

    @classmethod
    def load_from_yaml(cls, config_path: Optional[str | Path] = None) -> AppSettings:
        if config_path is None:
            default_path = Path(__file__).resolve().parent / "default_config.yaml"
            config_path = default_path

        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls.model_validate(data)


# Global singleton instance provider
_settings: Optional[AppSettings] = None


def get_settings(config_path: Optional[str | Path] = None) -> AppSettings:
    global _settings
    if _settings is None or config_path is not None:
        _settings = AppSettings.load_from_yaml(config_path)
    return _settings
