"""
Manual position reconciliation:
- DRREDDY.NS: SL (1345.74) was ABOVE entry (1338.40) — inverted SL error. 
  The position has been deeply in loss. Close at last known price.
- SUNPHARMA.NS: T1 (1817.18) was hit on Apr 30. SL moved to breakeven (entry=1736.50).
  Current price 1880+ so position should be profitable / partially exited.
- ADANIPOWER.NS: Currently above SL, near entry. Live engine will manage.

This script manually closes DRREDDY (loss position stuck due to SL bug).
"""
import psycopg2, sys
sys.stdout.reconfigure(encoding='utf-8')

# Last known prices (May 19, 2026 close)
prices = {
    'DRREDDY.NS':    1335.20,   # last close May 19
    'SUNPHARMA.NS':  1882.30,   # last close May 19
    'ADANIPOWER.NS': 219.09,    # last close May 19
}

conn = psycopg2.connect('postgresql://neondb_owner:npg_ie0GzmROxE9f@ep-proud-bird-an4ydv35-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require')
cur = conn.cursor()

# Only manually close DRREDDY since it's been in loss with broken SL
# SUNPHARMA and ADANIPOWER have sane SL levels — live engine will manage them
manual_close = ['DRREDDY.NS']

for stock in manual_close:
    cur.execute('''
        SELECT id, quantity, entry_price, stop_loss FROM open_positions
        WHERE stock = %s
    ''', (stock,))
    row = cur.fetchone()
    if not row:
        print(f'{stock}: not found in open_positions')
        continue
    pos_id, qty, entry, sl = row
    exit_price = prices[stock]
    pnl = (exit_price - float(entry)) * qty

    # Estimate charges: ~0.2% round trip + flat fees
    charges = (float(entry) * qty * 0.001) + (exit_price * qty * 0.001) + 15.93
    net_pnl = pnl - charges

    print(f'{stock}:')
    print(f'  Entry: {entry} x {qty} = Rs {float(entry)*qty:,.0f}')
    print(f'  Exit:  {exit_price} x {qty} = Rs {exit_price*qty:,.0f}')
    print(f'  Gross P&L: Rs {pnl:+,.2f}')
    print(f'  Charges:   Rs {charges:,.2f}')
    print(f'  Net P&L:   Rs {net_pnl:+,.2f}')

    # Update trades table
    cur.execute('''
        UPDATE trades
        SET status = 'CLOSED',
            exit_price = %s,
            exit_time = NOW(),
            pnl = %s,
            charges = %s,
            exit_type = 'MANUAL_RECONCILE'
        WHERE stock = %s AND status = 'OPEN'
    ''', (exit_price, round(net_pnl, 2), round(charges, 2), stock))
    print(f'  trades updated: {cur.rowcount} rows')

    # Remove from open_positions
    cur.execute('DELETE FROM open_positions WHERE stock = %s', (stock,))
    print(f'  open_positions removed: {cur.rowcount} rows')

    # Update portfolio cash
    proceeds = exit_price * qty - charges
    cur.execute('''
        UPDATE portfolio
        SET cash = cash + %s,
            invested = invested - (entry_price * quantity)
        FROM (SELECT %s::numeric as entry_price, %s as quantity) AS sub
        WHERE portfolio.id = (SELECT id FROM portfolio ORDER BY updated_at DESC LIMIT 1)
    ''', (proceeds, float(entry), qty))
    print(f'  Portfolio cash updated (+Rs {proceeds:,.2f})')

conn.commit()
conn.close()
print('\nReconciliation complete.')
