# 🌿 WuWei Core - Institutional Carver Trend Engine
> **Triết lý Thiết kế:** *"Thuận theo tự nhiên / Vô vi" (Pure Price Action, Zero-Forecast) kết hợp cùng cỗ máy định lượng **pysystemtrade** (Robert Carver - cựu Head of Fixed Income tại Quỹ AHL Man Group)*.
> **Mục tiêu:** Tạo ra cỗ máy giao dịch định lượng theo xu hướng (Trend Following / CTA) hiệu quả nhất, kiểm soát rủi ro bằng mục tiêu biến động tiền mặt (Cash Volatility Target) và triệt tiêu phí giao dịch bằng vùng đệm quán tính (Carver Buffering).

---

## 🏛️ 1. Kiến Trúc Hợp Nhất Đỉnh Cao (5-Layer Pipeline)

Hệ thống được thiết kế theo mô hình **Event-Driven Micro-Architecture**, tích hợp toàn diện giữa tư duy Vô Vi và cỗ máy định lượng cấp Quỹ:

```text
[Multi-Asset OHLCV Feeds: XAUUSD, EURUSD, USDJPY]
                     │
                     ▼ (BarEvent)
┌────────────────────────────────────────────────────────┐
│ 1. regime_engine (Thiên Thời - State Detection)        │
│    • ADX(14) + Choppiness Index (CHOP 14) + ATR Ratio  │
│    • Trạng thái VÔ VI (NO_TREND): Khóa mở lệnh, dập tắt│
│      mọi tín hiệu Carver Forecast về 0 (F -> 0.0)      │
└──────────────────────────┬─────────────────────────────┘
                           │ (RegimeEvent: is_trading_allowed)
                           ▼
┌────────────────────────────────────────────────────────┐
│ 2. signal_engine (Thuận Dòng & Carver Multi-Horizon)   │
│    • Donchian Breakout 20 bars (Zero-Forecast)         │
│    • Cụm mô hình Carver: Donchian 10, 20, 40 + EWMAC   │
│    • Continuous Forecast F in [-20, +20] x FDM (1.25)  │
└──────────────────────────┬─────────────────────────────┘
                           │ (SignalEvent: F, Donchian Bands)
                           ▼
┌────────────────────────────────────────────────────────┐
│ 3. risk_engine (Tri Chỉ - Cash Volatility Targeting)   │
│    • 20% Annual Portfolio Vol Target -> Daily Cash Vol │
│    • EWMA Price Volatility (35-day span)               │
│    • Volatility Scalar = Daily Cash Target / Point Vol │
│    • Instrument Diversification Multiplier (IDM 1.35)  │
│    • De-leveraging Ladder: Hạ 50% đòn bẩy khi DD > 1.5%│
│    • Circuit Breaker 3.0% Max Daily DD (Kill-switch)   │
└──────────────────────────┬─────────────────────────────┘
                           │ (OrderEvent)
                           ▼
┌────────────────────────────────────────────────────────┐
│ 4. execution_engine (Hành Vô Vi & Carver Buffering)    │
│    • Carver Buffer Band: [Target - 10%, Target + 10%]  │
│    • "Vô Vi" Inertia: Nằm trong Buffer -> KHÔNG TRADE  │
│      (Cắt giảm 40% - 60% phí hoa hồng & trượt giá)     │
│    • Dời hòa vốn (Break-Even) khi giá chạy +2.0*N      │
│    • Trailing Stop vô hạn (Donchian 10 / Chandelier)   │
│    • R:R mở, trôi theo dòng nước con sóng lớn          │
└──────────────────────────┬─────────────────────────────┘
                           │ (FillEvent, TradeRecord)
                           ▼
┌────────────────────────────────────────────────────────┐
│ 5. backtest & metrics (Thực Chứng & Multi-Asset Portfolio)
│    • Mô phỏng đa tài sản đồng thời: XAUUSD + Forex     │
│    • Chi phí thực tế: Slippage 0.5 pip, Commission $7  │
│    • Báo cáo cấp Quỹ: Sharpe, Sortino, Calmar, Max DD  │
└────────────────────────────────────────────────────────┘
```

---

## 📐 2. Các Công Thức Định Lượng Cốt Lõi

### A. Thiên Thời (Regime Engine)
* **Choppiness Index ($CHOP$):**
  $$CHOP = 100 \times \frac{\log_{10}\left( \frac{\sum_{i=1}^{14} ATR_1(t-i)}{\max(High_{14}) - \min(Low_{14})} \right)}{\log_{10}(14)}$$
* **Quy tắc Vô Vi:**
  $$\text{Nếu } ADX(14) < 20.0 \land CHOP(14) > 61.8 \implies \text{Regime} = \text{NO\_TREND (Vô Vi, Cấm Mở Lệnh)}$$
  $$\text{Kích hoạt Trend:} \iff ADX(14) \ge 25.0 \land CHOP(14) < 38.2$$

### B. Thuận Dòng (Signal Engine)
* **Donchian Breakout Channel (Zero Lookahead):**
  $$\text{Upper}_{20} = \max(High_{t-20 \dots t-1}), \quad \text{Lower}_{20} = \min(Low_{t-20 \dots t-1})$$
  $$\text{Signal} = \begin{cases} \text{BUY} & \text{khi } Close_t > \text{Upper}_{20} \land \text{Trading Allowed} \\ \text{SELL} & \text{khi } Close_t < \text{Lower}_{20} \land \text{Trading Allowed} \end{cases}$$

### C. Tri Chỉ (Risk Engine & Volatility Targeting)
* **Kích thước vị thế động (Position Sizing):**
  $$N = ATR(20), \quad \Delta P_{\text{SL}} = 2.0 \times N$$
  $$\text{Risk Amount} = \text{Account Equity} \times 1.0\%$$
  $$\text{Volume (Lots)} = \frac{\text{Risk Amount}}{\Delta P_{\text{SL}} \times \text{Contract Size}}$$
* **Circuit Breaker:** Khi Daily Drawdown $\ge 3.0\%$, kích hoạt đóng toàn bộ vị thế và phong tỏa hệ thống trong 24 giờ.

### D. Hành Vô Vi (Execution Engine)
* **Break-Even:** Khi $P_{\text{favorable}} \ge \text{Entry} \pm (2.0 \times N)$, dời Stop Loss về $\text{Entry} \pm \text{Buffer}$.
* **Trailing Stop:** Nâng dần Stop Loss theo đáy Donchian(10) (cho Long) hoặc hạ dần theo đỉnh Donchian(10) (cho Short). Tuyệt đối không đặt Take Profit cố định (R:R mở).

---

## 📁 3. Cấu Trúc Thư Mục Dự Án

```text
wuwei_quant_engine/
├── config/
│   ├── default_config.yaml       # Cấu hình tham số tổ chức chuẩn
│   └── settings.py               # Pydantic v2 Settings & Schema validation
├── core/
│   ├── __init__.py
│   ├── types.py                  # Enums (OrderType, RegimeState, SignalAction) & Domain classes
│   └── events.py                 # Event-Driven data structures (BarEvent, SignalEvent, OrderEvent)
├── engines/
│   ├── __init__.py
│   ├── regime_engine.py          # ADX + Choppiness Index + Volatility bandwidth
│   ├── signal_engine.py          # Donchian Breakout logic (Zero Lookahead)
│   ├── risk_engine.py            # Volatility Sizing & 3% Circuit Breaker
│   └── execution_engine.py       # Trailing Exit & Order State Machine
├── backtest/
│   ├── __init__.py
│   ├── engine.py                 # Event-driven backtester & Market simulator
│   └── metrics.py                # Sharpe, Sortino, Max Drawdown, Calmar, Profit Factor
├── main.py                       # CLI entry point chạy mô phỏng backtest & Tear Sheet
├── requirements.txt              # Thư viện phụ thuộc
└── tests/
    ├── conftest.py               # Cấu hình môi trường pytest
    └── test_engines.py           # Unit tests độc lập cho cả 5 tầng kiến trúc
```

---

## 🚀 4. Hướng Dẫn Cài Đặt & Sử Dụng

### Cài đặt môi trường
```bash
pip install -r requirements.txt
```

### Chạy kiểm thử tự động (Unit Tests)
```bash
python -m pytest tests/ -v
```

### Chạy mô phỏng Backtest định lượng
```bash
# 1. Chạy danh mục Đa tài sản cấp Quỹ (XAUUSD + EURUSD + USDJPY) với Carver IDM & Buffering
python main.py --portfolio --bars 2000

# 2. Mô phỏng tài sản đơn lẻ (XAUUSD)
python main.py --symbol XAUUSD --bars 2000

# 3. So sánh với chế độ Cổ điển WuWei (Tắt Carver, dùng 1% Fixed Risk ATR)
python main.py --symbol XAUUSD --no-carver --bars 2000

# 4. Nạp dữ liệu CSV lịch sử thực tế
python main.py --symbol XAUUSD --csv data/xauusd_h1.csv
```

### ⚡ Khởi Chạy Live Trading với MT5 Exness (1-Click)
```bash
# Nhấp đúp chuột vào file:
start_bot.bat

# Hoặc khởi chạy qua dòng lệnh:
python live_trade.py
```
*Hệ thống sẽ tự động đọc tài khoản `413956172` trên máy chủ `Exness-MT5Trial6` từ file `.env`, kết nối với terminal MT5 và tiến hành giao dịch theo thời gian thực.*
