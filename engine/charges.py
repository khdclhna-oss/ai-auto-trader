"""
Indian Stock Market Transaction Charges Calculator
====================================================
Current rates (FY 2025-26) for NSE Equity segment.

Charges applied per ROUND TRIP (one buy + one sell):

  Brokerage         : ₹20 per order (Zerodha flat rate) OR 0.03% whichever is lower
  STT               : 0.025% on SELL turnover (intraday) | 0.1% on BOTH sides (delivery)
  Exchange (NSE)    : 0.00335% on total turnover per side
  SEBI Charges      : ₹10 per crore = 0.0001% per side
  Stamp Duty        : 0.003% on BUY turnover (intraday) | 0.015% on BUY (delivery)
  GST               : 18% on (brokerage + exchange charges + SEBI charges)
  IPFT              : ₹1 per crore = 0.000001% (negligible, included for accuracy)

Reference:
  https://zerodha.com/charges/
  NSE circulars FY2025-26

Usage:
  from charges import calculate_charges, ChargeBreakdown
  charges = calculate_charges(buy_price=2500, sell_price=2520, quantity=10, is_intraday=True)
  print(f"Total charges: ₹{charges.total:.2f}")
  print(f"Net P&L after charges: ₹{charges.net_pnl:.2f}")
"""

from dataclasses import dataclass


# ─── Current Rates (FY 2025-26) ──────────────────────────────────────────────
BROKERAGE_FLAT = 20.0          # ₹20 per order (Zerodha / Groww style)
BROKERAGE_PCT = 0.0003         # 0.03% cap (whichever is lower)
STT_INTRADAY_SELL = 0.00025    # 0.025% on sell turnover (intraday)
STT_DELIVERY_BOTH = 0.001      # 0.1% on each side (delivery)
EXCHANGE_CHARGE = 0.0000335    # 0.00335% per side (NSE)
SEBI_CHARGE = 0.000001         # ₹10 per crore = 0.000001 of turnover
STAMP_DUTY_INTRADAY = 0.000030 # 0.003% on buy side (intraday)
STAMP_DUTY_DELIVERY = 0.000150 # 0.015% on buy side (delivery)
IPFT = 0.0000001               # ₹1 per crore
GST_RATE = 0.18                # 18% GST on brokerage + exchange + SEBI


@dataclass
class ChargeBreakdown:
    """Full breakdown of all charges for one round trip."""
    buy_turnover: float
    sell_turnover: float
    gross_pnl: float          # raw P&L ignoring charges

    brokerage: float          # per side × 2
    stt: float
    exchange_charge: float
    sebi_charge: float
    stamp_duty: float
    gst: float
    ipft: float

    total: float              # sum of all charges
    net_pnl: float            # gross_pnl - total
    net_pnl_pct: float        # net_pnl as % of buy_turnover

    is_intraday: bool

    def summary(self) -> str:
        lines = [
            f"  ┌─ Charges Breakdown ({'Intraday' if self.is_intraday else 'Delivery'})",
            f"  │  Gross P&L    : ₹{self.gross_pnl:+.2f}",
            f"  │  Brokerage    : ₹{self.brokerage:.2f} (₹20×2)",
            f"  │  STT          : ₹{self.stt:.2f}",
            f"  │  Exch Charges : ₹{self.exchange_charge:.2f}",
            f"  │  SEBI Charges : ₹{self.sebi_charge:.2f}",
            f"  │  Stamp Duty   : ₹{self.stamp_duty:.2f}",
            f"  │  GST (18%)    : ₹{self.gst:.2f}",
            f"  │  IPFT         : ₹{self.ipft:.2f}",
            f"  │  ─────────────────────",
            f"  │  Total Charges: ₹{self.total:.2f}",
            f"  └─ Net P&L      : ₹{self.net_pnl:+.2f} ({self.net_pnl_pct:+.3f}%)",
        ]
        return "\n".join(lines)


def calculate_charges(
    buy_price: float,
    sell_price: float,
    quantity: int,
    is_intraday: bool = True,
) -> ChargeBreakdown:
    """
    Calculate all-in transaction charges for one round trip.
    
    Args:
        buy_price   : Price at which shares were bought
        sell_price  : Price at which shares were sold
        quantity    : Number of shares
        is_intraday : True = MIS/intraday, False = CNC/delivery
    
    Returns:
        ChargeBreakdown with full itemized charges and net P&L
    """
    buy_turnover = buy_price * quantity
    sell_turnover = sell_price * quantity
    total_turnover = buy_turnover + sell_turnover
    gross_pnl = sell_turnover - buy_turnover

    # Brokerage: ₹20 per order OR 0.03%, whichever is LOWER, per leg
    brokerage_per_order = min(BROKERAGE_FLAT, buy_turnover * BROKERAGE_PCT)
    brokerage = brokerage_per_order + min(BROKERAGE_FLAT, sell_turnover * BROKERAGE_PCT)

    # STT
    if is_intraday:
        stt = sell_turnover * STT_INTRADAY_SELL
    else:
        stt = (buy_turnover + sell_turnover) * STT_DELIVERY_BOTH

    # Exchange transaction charges (both sides)
    exchange_charge = total_turnover * EXCHANGE_CHARGE

    # SEBI charges (both sides)
    sebi_charge = total_turnover * SEBI_CHARGE

    # Stamp duty (on buy side only)
    stamp_duty = buy_turnover * (STAMP_DUTY_INTRADAY if is_intraday else STAMP_DUTY_DELIVERY)

    # GST on brokerage + exchange + SEBI
    gst = (brokerage + exchange_charge + sebi_charge) * GST_RATE

    # IPFT
    ipft = total_turnover * IPFT

    total = brokerage + stt + exchange_charge + sebi_charge + stamp_duty + gst + ipft
    net_pnl = gross_pnl - total
    net_pnl_pct = (net_pnl / buy_turnover * 100) if buy_turnover > 0 else 0

    return ChargeBreakdown(
        buy_turnover=buy_turnover,
        sell_turnover=sell_turnover,
        gross_pnl=gross_pnl,
        brokerage=brokerage,
        stt=stt,
        exchange_charge=exchange_charge,
        sebi_charge=sebi_charge,
        stamp_duty=stamp_duty,
        gst=gst,
        ipft=ipft,
        total=total,
        net_pnl=net_pnl,
        net_pnl_pct=net_pnl_pct,
        is_intraday=is_intraday,
    )


def estimate_charges_on_entry(buy_price: float, quantity: int, is_intraday: bool = True) -> float:
    """
    Estimate total charges for a trade at entry time (before we know exit price).
    Uses buy_price as a proxy for sell_price to get order-of-magnitude.
    Useful for position sizing decisions.
    """
    c = calculate_charges(buy_price, buy_price, quantity, is_intraday)
    return c.total


if __name__ == "__main__":
    # Example: RELIANCE, 5 shares, buy ₹2500, sell ₹2560 (intraday)
    print("\n=== INTRADAY EXAMPLE ===")
    c = calculate_charges(buy_price=2500, sell_price=2560, quantity=5, is_intraday=True)
    print(c.summary())

    print("\n=== DELIVERY EXAMPLE (same prices) ===")
    c2 = calculate_charges(buy_price=2500, sell_price=2560, quantity=5, is_intraday=False)
    print(c2.summary())

    print("\n=== BREAK-EVEN: how much must price move? ===")
    for qty in [1, 5, 10, 20]:
        be = calculate_charges(2500, 2500, qty, True)
        # How much does sell price need to rise to cover charges?
        breakeven_move = be.total / qty
        print(f"  {qty} shares @ ₹2500: need ₹{breakeven_move:.2f}/share move to break even (charges: ₹{be.total:.2f})")
