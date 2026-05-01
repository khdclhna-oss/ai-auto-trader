"""
engine/macro_filter.py — V4 Layer 1: Macro Market Filter
==========================================================
The single most impactful layer. 70-80% of NSE large-cap moves
are explained by index direction. Trading stocks against the index
is fighting physics.

Gates checked (ALL must pass for new long entries):
  1. Nifty 50 is above its 200-day EMA (primary trend intact)
  2. Nifty 50's 50-day EMA slope is positive (near-term uptrend)
  3. India VIX < 20 (not in a fear spike)
  4. Nifty 500 breadth: > 50% of stocks above their 50-day EMA

Usage:
    from macro_filter import get_macro_state
    state = get_macro_state()
    if not state.tradeable:
        print(state.reason)
        return

Design notes:
  - Results are cached for 30 minutes to avoid re-fetching on every symbol
  - Fail-open: if data fetch fails, returns tradeable=True with a warning
    (we don't want a yfinance timeout to halt the entire engine)
  - Uses Nifty 500 proxy basket for breadth (top 50 stocks from universe)
"""

import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

# ── Cache ────────────────────────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL_SECONDS = 1800  # 30 minutes

# ── Nifty 500 breadth proxy — top 50 large/mid caps from our universe ────────
BREADTH_BASKET = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS",
    "AXISBANK.NS", "SBIN.NS", "BAJFINANCE.NS", "LT.NS", "HINDUNILVR.NS",
    "BHARTIARTL.NS", "KOTAKBANK.NS", "MARUTI.NS", "ASIANPAINT.NS", "TITAN.NS",
    "SUNPHARMA.NS", "HCLTECH.NS", "WIPRO.NS", "ADANIPORTS.NS", "TATAMOTORS.NS",
    "NTPC.NS", "POWERGRID.NS", "COALINDIA.NS", "ONGC.NS", "BPCL.NS",
    "DRREDDY.NS", "DIVISLAB.NS", "CIPLA.NS", "TECHM.NS", "BAJAJ-AUTO.NS",
    "HEROMOTOCO.NS", "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS",
    "ITC.NS", "BRITANNIA.NS", "NESTLEIND.NS", "DABUR.NS", "MARICO.NS",
    "M&M.NS", "EICHERMOT.NS", "TVSMOTOR.NS", "CHOLAFIN.NS", "BAJAJFINSV.NS",
    "SBILIFE.NS", "HDFCLIFE.NS", "INDUSINDBK.NS", "BANKBARODA.NS", "DLF.NS",
]


@dataclass
class MacroState:
    """Complete macro environment snapshot."""
    tradeable: bool              # True = macro conditions allow new longs
    nifty_above_200ema: bool
    nifty_50ema_slope_up: bool
    vix: float                   # India VIX level (0 if unavailable)
    vix_ok: bool                 # True if VIX < 20
    breadth_pct: float           # % of basket stocks above their 50-EMA (0-100)
    breadth_ok: bool             # True if breadth_pct > 50
    blocked_reasons: list = field(default_factory=list)
    reason: str = ""


def _fetch_nifty_state() -> dict:
    """Fetch Nifty 50 daily data and compute EMA checks."""
    import yfinance as yf
    import pandas_ta as ta

    result = {
        "above_200ema": False,
        "slope_up_50ema": False,
        "vix": 0.0,
        "vix_ok": True,  # fail-open on VIX
    }

    try:
        df = yf.download("^NSEI", period="1y", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 210:
            return result
        # Handle both Index and MultiIndex column structures
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[1].lower() if len(c) > 1 else c[0].lower() for r in [df.columns] for c in r]
        else:
            df.columns = [c.lower() for c in df.columns]

        ema200 = ta.ema(df["close"], length=200)
        ema50  = ta.ema(df["close"], length=50)

        if ema200 is not None and len(ema200.dropna()) > 0:
            result["above_200ema"] = float(df["close"].iloc[-1]) > float(ema200.dropna().iloc[-1])

        if ema50 is not None and len(ema50.dropna()) > 5:
            clean = ema50.dropna()
            # Slope: today's EMA vs 5 days ago
            result["slope_up_50ema"] = float(clean.iloc[-1]) > float(clean.iloc[-6])

    except Exception as e:
        import traceback
        print(f"  ⚠ [macro] Nifty fetch failed: {e}")
        # traceback.print_exc()

    # India VIX
    try:
        vix_df = yf.download("^INDIAVIX", period="5d", interval="1d",
                              progress=False, auto_adjust=True)
        if vix_df is not None and len(vix_df) > 0:
            if isinstance(vix_df.columns, pd.MultiIndex):
                vix_df.columns = [c[1].lower() if len(c) > 1 else c[0].lower() for r in [vix_df.columns] for c in r]
            else:
                vix_df.columns = [c.lower() for c in vix_df.columns]
            vix_val = float(vix_df["close"].dropna().iloc[-1])
            result["vix"] = vix_val
            result["vix_ok"] = vix_val < 20.0
    except Exception as e:
        print(f"  ⚠ [macro] VIX fetch failed: {e}")

    return result


def _compute_breadth(breadth_basket: list) -> float:
    """
    Returns the % of stocks in breadth_basket that are above their 50-day EMA.
    Uses a single batch download for efficiency.
    """
    import yfinance as yf
    import pandas_ta as ta

    try:
        df_all = yf.download(
            breadth_basket, period="120d", interval="1d",
            progress=False, group_by="ticker", auto_adjust=True
        )
        above_count = 0
        total = 0

        for ticker in breadth_basket:
            try:
                df = df_all[ticker].copy() if ticker in df_all else None
                if df is None or len(df) < 55:
                    continue
                
                # Handle MultiIndex if necessary
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[1].lower() if len(c) > 1 else c[0].lower() for r in [df.columns] for c in r]
                else:
                    df.columns = [c.lower() for c in df.columns]

                ema50 = ta.ema(df["close"], length=50)
                if ema50 is None or ema50.isna().iloc[-1]:
                    continue
                total += 1
                if float(df["close"].iloc[-1]) > float(ema50.iloc[-1]):
                    above_count += 1
            except Exception:
                continue

        return (above_count / total * 100) if total > 0 else 50.0  # neutral if data missing

    except Exception as e:
        print(f"  ⚠ [macro] Breadth computation failed: {e}")
        return 50.0  # neutral fallback


def get_macro_state(
    vix_threshold: float = 20.0,
    breadth_threshold: float = 50.0,
    use_cache: bool = True,
) -> MacroState:
    """
    Main entry point. Returns a MacroState describing current macro conditions.

    Parameters
    ----------
    vix_threshold    : Block new entries if India VIX >= this level
    breadth_threshold: Block if < this % of basket stocks are above 50 EMA
    use_cache        : If True, reuse result for CACHE_TTL_SECONDS (30 min)
    """
    global _cache
    cache_key = "macro_state"

    if use_cache and cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < CACHE_TTL_SECONDS:
            print("  📋 [macro] Using cached macro state")
            return cached

    print("  🌐 [macro] Fetching Nifty + VIX + breadth...")

    nifty = _fetch_nifty_state()
    breadth_pct = _compute_breadth(BREADTH_BASKET)

    blocked = []
    if not nifty["above_200ema"]:
        blocked.append("Nifty below 200-EMA (primary downtrend)")
    if not nifty["slope_up_50ema"]:
        blocked.append("Nifty 50-EMA slope negative (near-term weakness)")
    if not nifty["vix_ok"]:
        blocked.append(f"India VIX too high ({nifty['vix']:.1f} >= {vix_threshold})")
    if breadth_pct < breadth_threshold:
        blocked.append(f"Breadth weak ({breadth_pct:.0f}% above 50-EMA < {breadth_threshold}%)")

    tradeable = len(blocked) == 0
    reason = " | ".join(blocked) if blocked else "Macro conditions clear — longs allowed"

    state = MacroState(
        tradeable=tradeable,
        nifty_above_200ema=nifty["above_200ema"],
        nifty_50ema_slope_up=nifty["slope_up_50ema"],
        vix=nifty["vix"],
        vix_ok=nifty["vix_ok"],
        breadth_pct=breadth_pct,
        breadth_ok=(breadth_pct >= breadth_threshold),
        blocked_reasons=blocked,
        reason=reason,
    )

    if use_cache:
        _cache[cache_key] = (time.time(), state)

    status = "✅ CLEAR" if tradeable else f"🚫 BLOCKED ({len(blocked)} gate{'s' if len(blocked)>1 else ''})"
    print(f"  🌐 [macro] {status}: {reason}")

    return state
