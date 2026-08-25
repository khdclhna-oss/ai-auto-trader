"""
engine/macro_filter.py — V4 Layer 1: Macro Market Filter
==========================================================
The single most impactful layer. 70-80% of NSE large-cap moves
are explained by index direction. Trading stocks against the index
is fighting physics.

Gates checked:
  1. Nifty 50 is above its 200-day EMA (primary trend intact)
  2. Nifty 50's 50-day EMA slope is positive (near-term uptrend)
  3. India VIX < 20 (not in a fear spike)
  4. Nifty 500 breadth: > 50% of stocks above their 50-day EMA

Regime Classifications:
  - BULL_TREND      : Primary bull trend + short-term momentum + high breadth. Allows TREND_PULLBACK, BREAKOUT.
  - SIDEWAYS        : Primary bull trend + VIX ok, but flat momentum or low breadth. Allows MEAN_REVERSION.
  - BEAR_TREND      : Nifty below 200-EMA. Long entries blocked.
  - HIGH_VOLATILITY : VIX >= 20. Long entries blocked.

Usage:
    from macro_filter import get_macro_state
    state = get_macro_state()
    if not state.tradeable:
        print(state.reason)
        return
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
    "SUNPHARMA.NS", "HCLTECH.NS", "WIPRO.NS", "ADANIPORTS.NS", "ETERNAL.NS",
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
    tradeable: bool              # True = macro conditions allow standard long entries
    nifty_above_200ema: bool     # Gate 1: Primary trend
    nifty_50ema_slope_up: bool   # Gate 2: Short-term momentum
    vix: float                   # India VIX level (0.0 if unavailable)
    vix_ok: bool                 # Gate 3: True if VIX < 20.0
    breadth_pct: float           # Gate 4: % of basket above 50-EMA (0-100)
    breadth_ok: bool             # Gate 4: True if breadth_pct >= 50.0
    blocked_reasons: list = field(default_factory=list)
    reason: str = ""
    # ── New Regime Indicators for Dual-Engine Signals (M2 Refactoring) ────────
    macro_regime: str = "BULL_TREND"  # "BULL_TREND" | "SIDEWAYS" | "BEAR_TREND" | "HIGH_VOLATILITY"
    regime_bias: str = "BULLISH"     # "BULLISH" | "NEUTRAL" | "BEARISH" | "VOLATILE"
    allowed_setups: list = field(default_factory=lambda: ["TREND_PULLBACK"])
    macro_score: float = 100.0        # 0.0 to 100.0 composite market health score


def _fetch_nifty_state() -> dict:
    """Fetch Nifty 50 daily data and compute EMA checks."""
    import yfinance as yf
    import pandas_ta as ta

    result = {
        "above_200ema": True,  # Fail-open defaults
        "slope_up_50ema": True,
        "vix": 0.0,
        "vix_ok": True,
    }

    try:
        df = yf.download("^NSEI", period="1y", interval="1d",
                         progress=False, auto_adjust=True)
        if df is not None and len(df) >= 210:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]

            ema200 = ta.ema(df["close"].astype(float), length=200)
            ema50  = ta.ema(df["close"].astype(float), length=50)

            if ema200 is not None and len(ema200.dropna()) > 0:
                result["above_200ema"] = float(df["close"].iloc[-1]) > float(ema200.dropna().iloc[-1])

            if ema50 is not None and len(ema50.dropna()) > 5:
                clean = ema50.dropna()
                result["slope_up_50ema"] = float(clean.iloc[-1]) > float(clean.iloc[-6])

    except Exception as e:
        print(f"  [WARN] [macro] Nifty fetch failed: {e}")

    # India VIX
    try:
        vix_df = yf.download("^INDIAVIX", period="5d", interval="1d",
                              progress=False, auto_adjust=True)
        if vix_df is not None and len(vix_df) > 0:
            if isinstance(vix_df.columns, pd.MultiIndex):
                vix_df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in vix_df.columns]
            else:
                vix_df.columns = [c.lower() for c in vix_df.columns]
            vix_val = float(vix_df["close"].dropna().iloc[-1])
            result["vix"] = vix_val
            result["vix_ok"] = vix_val < 20.0
    except Exception as e:
        print(f"  [WARN] [macro] VIX fetch failed: {e}")

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

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
                else:
                    df.columns = [c.lower() for c in df.columns]

                ema50 = ta.ema(df["close"].astype(float), length=50)
                if ema50 is None or ema50.isna().iloc[-1]:
                    continue
                total += 1
                if float(df["close"].iloc[-1]) > float(ema50.iloc[-1]):
                    above_count += 1
            except Exception:
                continue

        return (above_count / total * 100.0) if total > 0 else 50.0

    except Exception as e:
        print(f"  [WARN] [macro] Breadth computation failed: {e}")
        return 50.0


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
            print("  [INFO] [macro] Using cached macro state")
            return cached

    print("  [INFO] [macro] Fetching Nifty + VIX + breadth...")

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

    # Macro score calculation
    score_200 = 100.0 if nifty["above_200ema"] else 0.0
    score_50 = 100.0 if nifty["slope_up_50ema"] else 0.0
    score_vix = 100.0 if nifty["vix_ok"] else 0.0
    score_breadth = min(100.0, max(0.0, float(breadth_pct)))
    macro_score = round(0.30 * score_200 + 0.25 * score_50 + 0.25 * score_vix + 0.20 * score_breadth, 1)

    # Regime Determination
    if nifty["vix"] >= vix_threshold and nifty["vix"] > 0:
        macro_regime = "HIGH_VOLATILITY"
        regime_bias = "VOLATILE"
        allowed_setups = []
        tradeable = False
    elif not nifty["above_200ema"]:
        macro_regime = "BEAR_TREND"
        regime_bias = "BEARISH"
        allowed_setups = []
        tradeable = False
    elif nifty["above_200ema"] and nifty["slope_up_50ema"] and nifty["vix_ok"] and (breadth_pct >= breadth_threshold):
        macro_regime = "BULL_TREND"
        regime_bias = "BULLISH"
        allowed_setups = ["TREND_PULLBACK", "BREAKOUT"]
        tradeable = True
    elif nifty["above_200ema"] and nifty["vix_ok"]:
        macro_regime = "SIDEWAYS"
        regime_bias = "NEUTRAL"
        allowed_setups = ["MEAN_REVERSION"]
        tradeable = True
    else:
        macro_regime = "BEAR_TREND"
        regime_bias = "BEARISH"
        allowed_setups = []
        tradeable = False

    reason = " | ".join(blocked) if blocked else f"Macro conditions clear ({macro_regime}) — longs allowed"

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
        macro_regime=macro_regime,
        regime_bias=regime_bias,
        allowed_setups=allowed_setups,
        macro_score=macro_score,
    )

    if use_cache:
        _cache[cache_key] = (time.time(), state)

    status = "CLEAR" if tradeable else f"BLOCKED ({len(blocked)} gate{'s' if len(blocked)>1 else ''})"
    print(f"  [macro] {status} [{macro_regime}]: {reason}")

    return state
