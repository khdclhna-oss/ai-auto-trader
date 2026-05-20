import yfinance as yf

stocks = ['DRREDDY.NS', 'SUNPHARMA.NS', 'ADANIPOWER.NS']
positions = {
    'DRREDDY.NS':    {'entry': 1338.40, 'sl': 1345.74, 'target_1': 1382.82, 'qty': 23},
    'SUNPHARMA.NS':  {'entry': 1736.50, 'sl': 1696.16, 'target_1': 1817.18, 'qty': 18},
    'ADANIPOWER.NS': {'entry': 222.50,  'sl': 219.12,  'target_1': 229.26,  'qty': 142},
}

print("=" * 65)
print("  OPEN POSITION RECONCILIATION")
print("=" * 65)

for s in stocks:
    df = yf.download(s, period='5d', interval='1d', progress=False)
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    row = df.iloc[-1]
    close = float(row['close'])
    high  = float(row['high'])
    low   = float(row['low'])
    pos   = positions[s]
    pnl   = (close - pos['entry']) * pos['qty']
    sl_pnl = (pos['sl'] - pos['entry']) * pos['qty']
    t1_pnl = (pos['target_1'] - pos['entry']) * pos['qty']

    sl_hit     = low  <= pos['sl']
    t1_hit     = high >= pos['target_1']
    sl_breach  = "SL BREACHED" if sl_hit else "safe"
    t1_reached = "T1 REACHED" if t1_hit else "not yet"

    print(f"\n  {s.replace('.NS','')}")
    print(f"    Entry: {pos['entry']:.2f}  |  Qty: {pos['qty']}")
    print(f"    SL:    {pos['sl']:.2f}  |  SL P&L if hit: Rs {sl_pnl:+.0f}  | Status: {sl_breach}")
    print(f"    T1:    {pos['target_1']:.2f}  |  T1 P&L if hit: Rs {t1_pnl:+.0f}  | Status: {t1_reached}")
    print(f"    Last:  close={close:.2f}  high={high:.2f}  low={low:.2f}")
    print(f"    Mark-to-Market P&L: Rs {pnl:+.2f}")
