"""
engine/ranker.py — V4 Layer 3: Composite Opportunity Ranker
============================================================
The core V4 insight: don't ask "is this signal good enough?"
Ask "is this the BEST opportunity in the market right now?"

Takes the full universe + pre-fetched OHLC frames and returns
only the TOP_N stocks by composite opportunity score.

Composite score weights (sum to 1.0):
  0.25 × relative_strength   — vs Nifty 50, 20d and 60d blended
  0.20 × volume_trend         — delivery-vol expansion vs 20-bar avg
  0.20 × proximity_to_breakout— how close price is to 52w high
  0.15 × trend_quality        — ADX + linearity of recent daily move
  0.10 × reward_to_cost_ratio — weekly ATR × 5 / estimated charges
  0.10 × regime_alignment     — TRENDING on BOTH daily and weekly TF

Usage:
    from ranker import rank_universe
    ranked = rank_universe(universe_data, top_n=5)
    # ranked = [("RELIANCE.NS", 73.2), ("INFY.NS", 68.1), ...]

Design notes:
  - All sub-scores are normalized to 0-100 before weighting
  - Missing data returns a sub-score of 0 (conservative)
  - Relative strength requires Nifty 50 returns (fetched once, cached)
  - The ranker does NOT call evaluate_signal() — it's a pre-filter
    that determines WHICH stocks deserve a full signal evaluation
"""

import time
import numpy as np
import pandas as pd
import pandas_ta as ta
from dataclasses import dataclass, field
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────
TOP_N_DEFAULT = 5       # maximum candidates forwarded to full signal evaluation
CACHE_TTL_SECONDS = 1800  # Nifty benchmark cached for 30 min

# Composite score weights
WEIGHTS = {
    "relative_strength": 0.10,  # V4.2: Reduced from 0.25
    "volume_trend":      0.40,  # V4.2: Increased from 0.20 (Money flow is king)
    "proximity_breakout":0.15,  # V4.2: Reduced from 0.20
    "trend_quality":     0.15,
    "reward_to_cost":    0.10,
    "regime_alignment":  0.10,
}

# ── Cache ──────────────────────────────────────────────────────────────────────
_nifty_cache: dict = {}


@dataclass
class StockRanking:
    symbol: str
    composite_score: float       # 0-100
    relative_strength: float     # 0-100
    volume_trend: float          # 0-100
    proximity_breakout: float    # 0-100
    trend_quality: float         # 0-100
    reward_to_cost: float        # 0-100
    regime_alignment: float      # 0-100
    notes: list = field(default_factory=list)


# ── Nifty 50 benchmark ────────────────────────────────────────────────────────

def _get_nifty_returns() -> dict:
    """Returns Nifty 50 20d and 60d returns. Cached for 30 min."""
    cache_key = "nifty_returns"
    if cache_key in _nifty_cache:
        ts, data = _nifty_cache[cache_key]
        if time.time() - ts < CACHE_TTL_SECONDS:
            return data

    result = {"ret_20d": 0.0, "ret_60d": 0.0}
    try:
        import yfinance as yf
        df = yf.download("^NSEI", period="90d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is not None and len(df) >= 65:
            df.columns = [c.lower() for c in df.columns]
            close = df["close"].dropna()
            result["ret_20d"] = (close.iloc[-1] / close.iloc[-21] - 1) * 100
            result["ret_60d"] = (close.iloc[-1] / close.iloc[-61] - 1) * 100
    except Exception:
        pass

    _nifty_cache[cache_key] = (time.time(), result)
    return result


# ── Sub-score calculators ─────────────────────────────────────────────────────

def _relative_strength_score(df_daily: pd.DataFrame, nifty: dict) -> tuple[float, str]:
    """
    How much is this stock outperforming Nifty 50?
    Blends 20d (60%) and 60d (40%) relative return.
    Score: 100 = beats Nifty by 10%+, 50 = in-line, 0 = lags by 10%+
    """
    if df_daily is None or len(df_daily) < 65:
        return 0.0, "insufficient daily data"

    close = df_daily["close"].dropna()
    try:
        ret_20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100
        ret_60 = (close.iloc[-1] / close.iloc[-61] - 1) * 100
    except Exception:
        return 0.0, "return calc failed"

    rel_20 = ret_20 - nifty["ret_20d"]
    rel_60 = ret_60 - nifty["ret_60d"]
    blended = 0.60 * rel_20 + 0.40 * rel_60

    # Normalize: +10% outperformance = 100, -10% = 0
    score = max(0.0, min(100.0, 50.0 + blended * 5.0))
    return score, f"RS={blended:+.1f}% vs Nifty"


def _volume_trend_score(df_daily: pd.DataFrame) -> tuple[float, str]:
    """
    Is volume expanding? Today's volume vs 20-bar average.
    Score: 100 = 3x average, 50 = 1x, 0 = <0.3x
    """
    if df_daily is None or len(df_daily) < 22:
        return 50.0, "no volume data"  # neutral

    try:
        vol = df_daily["volume"].dropna()
        avg_vol = float(vol.iloc[-21:-1].mean())
        last_vol = float(vol.iloc[-1])
        if avg_vol <= 0:
            return 50.0, "zero avg vol"
        ratio = last_vol / avg_vol
        # Normalize: ratio=3.0 → 100, ratio=1.0 → 50, ratio=0.3 → 0
        score = max(0.0, min(100.0, (ratio - 0.3) / (3.0 - 0.3) * 100))
        return score, f"vol={ratio:.1f}x avg"
    except Exception:
        return 50.0, "vol calc error"


def _proximity_to_breakout_score(df_daily: pd.DataFrame) -> tuple[float, str]:
    """
    How close is the price to its 52-week high?
    Score: 100 = AT the 52w high, 0 = >30% below
    Rationale: stocks near 52w highs are breaking out, not breaking down.
    """
    if df_daily is None or len(df_daily) < 30:
        return 0.0, "insufficient data"

    try:
        close = df_daily["close"].dropna()
        lookback = close.tail(252) if len(close) >= 252 else close
        high_52w = float(lookback.max())
        current = float(close.iloc[-1])
        pct_below = (high_52w - current) / high_52w * 100  # 0 = at high
        # Score: 0% below = 100, 30% below = 0
        score = max(0.0, min(100.0, (1 - pct_below / 30.0) * 100))
        return score, f"{pct_below:.1f}% below 52wH"
    except Exception:
        return 0.0, "52w high calc failed"


def _trend_quality_score(df_daily: pd.DataFrame) -> tuple[float, str]:
    """
    ADX-based trend quality + linearity of the recent 20-day move.
    Score: 100 = ADX > 40 + linear uptrend, 0 = ADX < 15 + choppy
    """
    if df_daily is None or len(df_daily) < 30:
        return 0.0, "insufficient data"

    try:
        adx_data = ta.adx(df_daily["high"], df_daily["low"], df_daily["close"], length=14)
        adx_val = 0.0
        if adx_data is not None and "ADX_14" in adx_data.columns:
            adx_series = adx_data["ADX_14"].dropna()
            if len(adx_series) > 0:
                adx_val = float(adx_series.iloc[-1])

        # Linearity: R² of close vs time over last 20 bars
        close = df_daily["close"].dropna().tail(20)
        if len(close) >= 10:
            x = np.arange(len(close))
            correlation = np.corrcoef(x, close.values)[0, 1]
            linearity = max(0.0, correlation) * 100  # only reward positive linearity
        else:
            linearity = 50.0

        # ADX normalized: 15→0, 40→100
        adx_score = max(0.0, min(100.0, (adx_val - 15) / (40 - 15) * 100))
        score = 0.6 * adx_score + 0.4 * linearity
        return score, f"ADX={adx_val:.0f} linearity={linearity:.0f}%"
    except Exception:
        return 0.0, "trend quality calc failed"


def _reward_to_cost_score(df_daily: pd.DataFrame, entry_price: float) -> tuple[float, str]:
    """
    Estimated reward-to-charge ratio using weekly ATR.
    Weekly ATR × 5 (target) / estimated round-trip charges.
    Score: 100 = reward >= 15× charges, 0 = reward <= 3× charges
    """
    if df_daily is None or len(df_daily) < 30 or entry_price <= 0:
        return 50.0, "no data"

    try:
        # Weekly ATR proxy: daily ATR × √5
        atr_series = ta.atr(df_daily["high"], df_daily["low"], df_daily["close"], length=14)
        if atr_series is None or atr_series.isna().iloc[-1]:
            return 50.0, "no ATR"
        daily_atr = float(atr_series.dropna().iloc[-1])
        weekly_atr = daily_atr * (5 ** 0.5)

        # Target using 5× weekly ATR
        target_dist = 5.0 * weekly_atr
        target_pct = target_dist / entry_price * 100

        # Estimated charges: ~0.2% of trade + ₹15.93 DP (using qty=10 as proxy)
        trade_value = entry_price * 10
        est_charges = trade_value * 0.002 + 15.93
        planned_risk = trade_value * 0.01  # 1% risk
        reward_to_cost = (target_dist * 10) / est_charges if est_charges > 0 else 0

        # Normalize: 15× = 100, 3× = 0
        score = max(0.0, min(100.0, (reward_to_cost - 3) / (15 - 3) * 100))
        return score, f"target={target_pct:.1f}%, R/C={reward_to_cost:.1f}×"
    except Exception:
        return 50.0, "reward calc failed"


def _regime_alignment_score(df_daily: pd.DataFrame) -> tuple[float, str]:
    """
    Is the stock in a TRENDING regime on both daily AND weekly timeframes?
    Score: 100 = TRENDING on both, 50 = TRENDING on daily only, 0 = RANGING
    """
    if df_daily is None or len(df_daily) < 50:
        return 0.0, "insufficient data"

    try:
        from regime import detect_regime

        # Daily regime
        daily_regime = detect_regime(df_daily)

        # Weekly proxy: resample daily to weekly
        df_weekly = df_daily.resample("W").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum"
        }).dropna()
        weekly_regime = detect_regime(df_weekly) if len(df_weekly) >= 50 else None

        if daily_regime.regime == "TRENDING" and weekly_regime and weekly_regime.regime == "TRENDING":
            return 100.0, "TRENDING on daily+weekly"
        elif daily_regime.regime == "TRENDING":
            return 50.0, "TRENDING on daily only"
        else:
            return 0.0, f"regime={daily_regime.regime}"
    except Exception:
        return 0.0, "regime check failed"


# ── Main ranking function ─────────────────────────────────────────────────────

def rank_universe(
    universe_data: dict[str, dict],
    top_n: int = TOP_N_DEFAULT,
    allowed_sectors: Optional[set] = None,
) -> list[StockRanking]:
    """
    Rank all stocks in universe_data by composite opportunity score.
    Returns the top_n candidates for full signal evaluation.

    Parameters
    ----------
    universe_data   : {symbol: {"1d": df, "1h": df, "15m": df}} from fetch_batch_universe()
    top_n           : How many top stocks to return
    allowed_sectors : If provided, only rank stocks from these sectors

    Returns
    -------
    list of StockRanking, sorted by composite_score descending, length <= top_n
    """
    from sector_rotation import get_sector_for_stock

    nifty = _get_nifty_returns()
    rankings = []

    for symbol, frames in universe_data.items():
        df_daily = frames.get("1d")
        if df_daily is None or len(df_daily) < 65:
            continue

        # Sector gate
        if allowed_sectors is not None:
            sector = get_sector_for_stock(symbol)
            if sector is None or sector not in allowed_sectors:
                continue

        entry_price = float(df_daily["close"].dropna().iloc[-1]) if len(df_daily) > 0 else 0.0
        if entry_price <= 0:
            continue

        # Compute all sub-scores
        rs_score, rs_note       = _relative_strength_score(df_daily, nifty)
        vol_score, vol_note     = _volume_trend_score(df_daily)
        brk_score, brk_note     = _proximity_to_breakout_score(df_daily)
        tq_score, tq_note       = _trend_quality_score(df_daily)
        rc_score, rc_note       = _reward_to_cost_score(df_daily, entry_price)
        reg_score, reg_note     = _regime_alignment_score(df_daily)

        composite = (
            WEIGHTS["relative_strength"]  * rs_score +
            WEIGHTS["volume_trend"]       * vol_score +
            WEIGHTS["proximity_breakout"] * brk_score +
            WEIGHTS["trend_quality"]      * tq_score +
            WEIGHTS["reward_to_cost"]     * rc_score +
            WEIGHTS["regime_alignment"]   * reg_score
        )

        rankings.append(StockRanking(
            symbol=symbol,
            composite_score=round(composite, 1),
            relative_strength=round(rs_score, 1),
            volume_trend=round(vol_score, 1),
            proximity_breakout=round(brk_score, 1),
            trend_quality=round(tq_score, 1),
            reward_to_cost=round(rc_score, 1),
            regime_alignment=round(reg_score, 1),
            notes=[rs_note, vol_note, brk_note, tq_note, rc_note, reg_note],
        ))

    rankings.sort(key=lambda r: r.composite_score, reverse=True)

    print(f"\n  🏆 [ranker] Top {min(top_n, len(rankings))} of {len(rankings)} scored stocks:")
    for r in rankings[:top_n]:
        print(f"    {r.symbol}: {r.composite_score:.0f}/100 "
              f"[RS={r.relative_strength:.0f} Vol={r.volume_trend:.0f} "
              f"Brk={r.proximity_breakout:.0f} TQ={r.trend_quality:.0f} "
              f"RC={r.reward_to_cost:.0f} Reg={r.regime_alignment:.0f}]")

    return rankings[:top_n]
