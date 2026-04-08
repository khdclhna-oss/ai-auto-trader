from dataclasses import dataclass

@dataclass
class TradeCharges:
    brokerage: float
    stt: float
    gst: float
    sebi: float
    stamp_duty: float
    exchange_txn: float
    slippage: float
    total: float
    net_pnl: float
    net_pnl_pct: float

def calculate_realistic_charges(entry_price: float, exit_price: float, qty: int, is_intraday: bool = False, slippage_pct: float = 0.00075) -> TradeCharges:
    """
    Calculates institutional-grade taxes and slippage for NSE/BSE India.
    Defaults to Delivery (Swing) charges as per current strategy.
    
    Taxes based on current NSE/Zerodha rates:
    - Brokerage: ₹0 for Delivery, 0.03% or ₹20 (whichever is lower) for Intraday
    - STT (Securities Transaction Tax): 0.1% on Buy & Sell (Delivery), 0.025% on Sell (Intraday)
    - Transaction Charges: 0.00345%
    - SEBI Charges: 0.0001%
    - Stamp Duty: 0.015% on Buy (Delivery), 0.003% on Buy (Intraday)
    - GST: 18% on (Brokerage + Transaction Charges + SEBI Fees)
    - Slippage: Artificial penalty to simulate spread impact (default 0.075%)
    """
    buy_val = entry_price * qty
    sell_val = exit_price * qty
    total_val = buy_val + sell_val
    
    # 1. Brokerage
    if is_intraday:
        buy_brk = min(20.0, buy_val * 0.0003)
        sell_brk = min(20.0, sell_val * 0.0003)
        brokerage = buy_brk + sell_brk
    else:
        brokerage = 0.0  # Most Indian brokers are commission-free on Delivery
        
    # 2. STT
    if is_intraday:
        stt = sell_val * 0.00025  # Only on Sell
    else:
        stt = total_val * 0.001  # 0.1% on both Buy and Sell
        
    # 3. Transaction Charges (EXCHANGE_TXN)
    exchange_txn = total_val * 0.0000345
    
    # 4. SEBI Fees
    sebi = total_val * 0.000001
    
    # 5. GST (18% on Service components)
    gst = (brokerage + exchange_txn + sebi) * 0.18
    
    # 6. Stamp Duty (Only on Buy side)
    if is_intraday:
        stamp_duty = buy_val * 0.00003
    else:
        stamp_duty = buy_val * 0.00015
        
    # 7. Slippage (Institutional Hardening)
    # Applied to both Buy (entry higher) and Sell (exit lower)
    slippage = total_val * slippage_pct
    
    total_charges = brokerage + stt + exchange_txn + sebi + gst + stamp_duty + slippage
    
    gross_pnl = sell_val - buy_val
    net_pnl = gross_pnl - total_charges
    net_pnl_pct = (net_pnl / buy_val) * 100 if buy_val > 0 else 0
    
    return TradeCharges(
        brokerage=brokerage, stt=stt, gst=gst, sebi=sebi, 
        stamp_duty=stamp_duty, exchange_txn=exchange_txn, 
        slippage=slippage, total=total_charges, 
        net_pnl=net_pnl, net_pnl_pct=net_pnl_pct
    )
