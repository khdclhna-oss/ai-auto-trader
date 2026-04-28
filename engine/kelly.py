"""
engine/kelly.py — V4: Live Kelly Criterion Calculator
======================================================
Computes the Kelly-optimal position size fraction based on
the system's LIVE realized trade history.

The Kelly Formula:
    K = W - (1 - W) / R
    where W = win rate, R = payoff ratio (avg_win / avg_loss)

A negative K means the system has no edge. Do NOT bet.
We use half-Kelly (K/2) for safety margin.

Usage:
    from kelly import compute_kelly, KellyResult
    kr = compute_kelly(trades)        # pass list of realized PnL values
    print(kr.fraction)                # e.g. 0.05 = bet 5% of capital per trade
    print(kr.has_edge)                # False = system not profitable yet

Design notes:
  - Minimum 20 trades required before Kelly is considered reliable
  - Hard caps: fraction never > 5% (max) even if Kelly says more
  - If Kelly is negative, fraction = 0 (no bet — system has no edge)
  - Integrates with risk_manager.py: RISK_PER_TRADE is overridden
    by kelly.fraction when enough trade history exists
"""

from dataclasses import dataclass
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────
MIN_TRADES_FOR_KELLY = 20    # below this, use the fixed RISK_PER_TRADE
MAX_KELLY_FRACTION   = 0.05  # hard cap: never risk more than 5% even if Kelly says so
HALF_KELLY_DIVISOR   = 2.0   # always use half-Kelly for safety margin


@dataclass
class KellyResult:
    win_rate: float          # fraction 0-1
    payoff_ratio: float      # avg_win / avg_loss
    full_kelly: float        # raw Kelly fraction (can be negative)
    half_kelly: float        # K/2 (what we actually use)
    fraction: float          # clamped, usable risk fraction (0 if no edge)
    has_edge: bool           # True only if full_kelly > 0 and >= 20 trades
    sample_size: int         # number of trades used
    avg_win: float           # average winning trade PnL
    avg_loss: float          # average losing trade PnL (positive number)
    note: str                # human-readable verdict


def compute_kelly(pnl_list: list[float]) -> KellyResult:
    """
    Compute Kelly fraction from a list of realized net PnL values.

    Parameters
    ----------
    pnl_list : List of net PnL per trade (positive = win, negative = loss)
               Use charges-adjusted PnL (what's already in the trades table)

    Returns
    -------
    KellyResult with fraction=0 if system has no edge or insufficient data
    """
    n = len(pnl_list)

    wins  = [p for p in pnl_list if p > 0]
    losses= [p for p in pnl_list if p < 0]

    win_rate = len(wins) / n if n > 0 else 0.0
    avg_win  = sum(wins)  / len(wins)  if wins  else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 1.0  # avoid div/0

    payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

    # Kelly formula
    full_kelly = win_rate - (1 - win_rate) / payoff_ratio if payoff_ratio > 0 else -1.0
    half_kelly = full_kelly / HALF_KELLY_DIVISOR

    has_edge = (full_kelly > 0) and (n >= MIN_TRADES_FOR_KELLY)

    if not has_edge:
        fraction = 0.0  # No edge → no bet (or override with fixed risk during tuning)
        if n < MIN_TRADES_FOR_KELLY:
            note = (f"Insufficient data ({n}/{MIN_TRADES_FOR_KELLY} trades). "
                    f"Using fixed RISK_PER_TRADE until {MIN_TRADES_FOR_KELLY} trades.")
        else:
            note = (f"Negative edge: Kelly={full_kelly:.3f}. "
                    f"WR={win_rate:.0%}, Payoff={payoff_ratio:.2f}x. "
                    f"System is NOT profitable — review signal quality before sizing up.")
    else:
        # Clamp to max allowed fraction
        fraction = min(half_kelly, MAX_KELLY_FRACTION)
        note = (f"Positive edge ✅: Full Kelly={full_kelly:.3f}, "
                f"Half Kelly={half_kelly:.3f}, Using={fraction:.3f}. "
                f"WR={win_rate:.0%}, Payoff={payoff_ratio:.2f}x, n={n}")

    return KellyResult(
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        full_kelly=full_kelly,
        half_kelly=half_kelly,
        fraction=fraction,
        has_edge=has_edge,
        sample_size=n,
        avg_win=avg_win,
        avg_loss=avg_loss,
        note=note,
    )


def get_kelly_from_db(db) -> KellyResult:
    """
    Convenience wrapper: pull all closed trade PnL from DB and compute Kelly.
    
    Parameters
    ----------
    db : Database instance (engine/db.py)
    """
    try:
        with db.conn.cursor() as cur:
            cur.execute("SELECT pnl FROM trades WHERE status='CLOSED' AND pnl IS NOT NULL")
            rows = cur.fetchall()
        pnl_list = [float(r[0]) for r in rows]
        result = compute_kelly(pnl_list)
        print(f"  📐 [kelly] {result.note}")
        return result
    except Exception as e:
        print(f"  ⚠ [kelly] Could not compute Kelly: {e}")
        # Safe fallback
        return KellyResult(
            win_rate=0.0, payoff_ratio=0.0, full_kelly=-1.0,
            half_kelly=-0.5, fraction=0.0, has_edge=False,
            sample_size=0, avg_win=0.0, avg_loss=0.0,
            note=f"DB query failed: {e}",
        )
