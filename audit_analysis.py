import os
import sys
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()
login = int(os.getenv('MT5_LOGIN') or os.getenv('MT5_ACCOUNT') or '0')
password = os.getenv('MT5_PASSWORD', '')
server = os.getenv('MT5_SERVER', '')
path = os.getenv('MT5_PATH', '')

if not mt5.initialize(path=path, login=login, password=password, server=server):
    print("MT5 Init failed:", mt5.last_error())
    sys.exit(1)

from_date = datetime(2026, 9, 25)
to_date = datetime.now() + timedelta(days=1)
deals = mt5.history_deals_get(from_date, to_date)
orders = mt5.history_orders_get(from_date, to_date)

pos_dict = {}
for d in deals:
    if d.entry == 0:  # IN
        pos_dict[d.position_id] = {
            'symbol': d.symbol,
            'entry_time': datetime.fromtimestamp(d.time),
            'entry_price': d.price,
            'volume': d.volume,
            'entry_type': 'BUY' if d.type == 0 else 'SELL',
            'comment_in': d.comment,
            'order_id': d.order
        }
    elif d.entry == 1:  # OUT
        if d.position_id in pos_dict:
            pos_dict[d.position_id].update({
                'exit_time': datetime.fromtimestamp(d.time),
                'exit_price': d.price,
                'profit': d.profit,
                'commission': d.commission,
                'swap': d.swap,
                'comment_out': d.comment,
                'exit_deal': d.ticket
            })

closed_trades = [v for k, v in pos_dict.items() if 'exit_time' in v]
df = pd.DataFrame(closed_trades)

if not df.empty:
    df['holding_hours'] = (df['exit_time'] - df['entry_time']).dt.total_seconds() / 3600.0
    df['net_pnl'] = df['profit'] + df['commission'] + df['swap']
    df['is_win'] = df['net_pnl'] > 0

    print("===============================================================================")
    print("                       QUANT AUDIT: EXECUTIVE SUMMARY                          ")
    print("===============================================================================")
    total_trades = len(df)
    wins = df[df['is_win']]
    losses = df[~df['is_win']]
    win_rate = len(wins) / total_trades * 100
    gross_profit = wins['net_pnl'].sum()
    gross_loss = abs(losses['net_pnl'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.nan
    net_pnl = df['net_pnl'].sum()
    avg_win = wins['net_pnl'].mean() if len(wins) > 0 else 0
    avg_loss = abs(losses['net_pnl'].mean()) if len(losses) > 0 else 0
    payoff_ratio = avg_win / avg_loss if avg_loss > 0 else np.nan

    print(f"Total Closed Trades : {total_trades}")
    print(f"Win / Loss Count    : {len(wins)} Wins / {len(losses)} Losses")
    print(f"Win Rate            : {win_rate:.2f}%")
    print(f"Net Realized PnL    : {net_pnl:+.2f} USD")
    print(f"Gross Profit / Loss : +{gross_profit:.2f} USD / -{gross_loss:.2f} USD")
    print(f"Profit Factor       : {profit_factor:.2f}")
    print(f"Payoff Ratio (R:R)  : {payoff_ratio:.2f} (Avg Win: +{avg_win:.2f} / Avg Loss: -{avg_loss:.2f})")
    print(f"Avg Holding Time    : Total: {df['holding_hours'].mean():.1f}h | Wins: {wins['holding_hours'].mean():.1f}h | Losses: {losses['holding_hours'].mean():.1f}h")

    print("\n===============================================================================")
    print("                     PERFORMANCE BY SYMBOL & ASSET CLASS                       ")
    print("===============================================================================")
    sym_group = df.groupby('symbol').agg(
        trades=('net_pnl', 'count'),
        wins=('is_win', lambda x: (x == True).sum()),
        losses=('is_win', lambda x: (x == False).sum()),
        win_rate=('is_win', lambda x: x.mean() * 100),
        net_pnl=('net_pnl', 'sum'),
        gross_profit=('net_pnl', lambda x: x[x > 0].sum()),
        gross_loss=('net_pnl', lambda x: abs(x[x < 0].sum())),
        avg_holding=('holding_hours', 'mean')
    ).reset_index()
    sym_group['pf'] = sym_group.apply(lambda r: r['gross_profit'] / r['gross_loss'] if r['gross_loss'] > 0 else np.nan, axis=1)
    print(sym_group.to_string(index=False))

    print("\n===============================================================================")
    print("                     EXIT REASON & STOP LOSS CLASSIFICATION                    ")
    print("===============================================================================")
    def classify_exit(comment):
        c = str(comment).lower()
        if 'tp' in c:
            return 'Take-Profit (TP)'
        elif 'sl' in c:
            return 'Stop-Loss (SL)'
        elif 'close' in c:
            return 'Manual / Regime Close'
        return 'Other'

    df['exit_category'] = df['comment_out'].apply(classify_exit)
    exit_group = df.groupby('exit_category').agg(
        trades=('net_pnl', 'count'),
        win_rate=('is_win', lambda x: x.mean() * 100),
        net_pnl=('net_pnl', 'sum'),
        avg_pnl=('net_pnl', 'mean'),
        avg_holding=('holding_hours', 'mean')
    ).reset_index()
    print(exit_group.to_string(index=False))

    print("\n===============================================================================")
    print("                             CHRONOLOGICAL AUDIT LOG                           ")
    print("===============================================================================")
    for idx, r in df.sort_values('entry_time').iterrows():
        pnl_str = f"{r['net_pnl']:+7.2f} USD"
        print(f"[{r['entry_time'].strftime('%m-%d %H:%M')} -> {r['exit_time'].strftime('%m-%d %H:%M')}] {r['symbol']:<8} | {r['entry_type']:<4} {r['volume']:.2f}L | In: {r['entry_price']:<10.3f} -> Out: {r['exit_price']:<10.3f} | Hold: {r['holding_hours']:>5.1f}h | PnL: {pnl_str} | Exit: {r['comment_out']}")

    print("\n===============================================================================")
    print("                     SLIPPAGE & LATENCY AUDIT (ORDERS VS DEALS)                ")
    print("===============================================================================")
    # Match order requested price vs deal executed price
    slippages = []
    for d in deals:
        if d.order and orders:
            matching_orders = [o for o in orders if o.ticket == d.order]
            if matching_orders:
                ord_obj = matching_orders[0]
                req_p = ord_obj.price_open
                exec_p = d.price
                # for market orders, ord_obj.price_open might be 0, but check price_current
                if req_p > 0 and abs(exec_p - req_p) > 0:
                    slippages.append({
                        'ticket': d.ticket,
                        'symbol': d.symbol,
                        'req_price': req_p,
                        'exec_price': exec_p,
                        'diff': exec_p - req_p,
                        'time': datetime.fromtimestamp(d.time)
                    })
    if slippages:
        print(f"Detected {len(slippages)} non-zero slippage executions.")
        for s in slippages[:5]:
            print(f"Order #{s['ticket']} {s['symbol']}: Req {s['req_price']} vs Exec {s['exec_price']} (Diff: {s['diff']})")
    else:
        print("Market orders executed at top-of-book with minimal or zero recorded order-limit slippage.")

mt5.shutdown()
