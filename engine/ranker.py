"""
engine/ranker.py — V4 Layer 3: Composite Opportunity Ranker
============================================================
The core V4 insight: don't ask "is this signal good enough?"
Ask "is this the BEST opportunity in the market right now?"

Takes the full universe + pre-fetched OHLC frames and returns
only the TOP_N stocks by composite opportunity score.

Composite score weights (sum to 1.0 = 100%):
  0.40 × volume_trend         — Money flow expansion vs 20-bar avg
  0.15 × proximity_breakout   — Setup proximity (52w high / EMA21 or BB lower / RSI)
  0.15 × trend_quality        — ADX + linearity (Trend) or Low ADX + BB stability (Mean Reversion)
  0.10 × relative_strength    — Stock RS (70%) + Sector RS (30%) vs Nifty 50
  0.10 × reward_to_cost_ratio — Expected target move / estimated charges
  0.10 × regime_alignment     — Multi-timeframe regime alignment for setup mode

Usage:
    from ranker import rank_universe
    ranked = rank_universe(universe_data, top_n=5, market_regime="BULL_TREND")
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import numpy as np
import pandas as pd
import pandas_ta as ta
from dataclasses import dataclass, field
from typing import Optional

try:
    from sector_rotation import get_allowed_sectors, get_sector_rs_dict, get_sector_for_stock
except ModuleNotFoundError:
    from engine.sector_rotation import get_allowed_sectors, get_sector_rs_dict, get_sector_for_stock

try:
    from regime import detect_regime
    classify_regime = detect_regime
except ModuleNotFoundError:
    from engine.regime import detect_regime
    classify_regime = detect_regime

# ── Constants ─────────────────────────────────────────────────────────────────
TOP_N_DEFAULT = 5         # maximum candidates forwarded to full signal evaluation
CACHE_TTL_SECONDS = 1800  # Nifty benchmark cached for 30 min

# Composite score weights (Sum = 1.00)
WEIGHTS = {
    "volume_trend":       0.40,  # Money flow expansion
    "proximity_breakout": 0.15,  # Setup proximity
    "trend_quality":      0.15,  # Trend/range quality
    "relative_strength":  0.10,  # Stock + Sector RS vs Nifty
    "reward_to_cost":     0.10,  # Reward / charge ratio
    "regime_alignment":   0.10,  # Multi-timeframe regime alignment
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
    setup_type: str = "TREND_PULLBACK"  # Setup candidate was ranked for


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
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
            else:
                df.columns = [str(c).lower() for c in df.columns]
            close = df["close"].astype(float).dropna()
            result["ret_20d"] = float((close.iloc[-1] / close.iloc[-21] - 1) * 100.0)
            result["ret_60d"] = float((close.iloc[-1] / close.iloc[-61] - 1) * 100.0)
    except Exception:
        pass

    _nifty_cache[cache_key] = (time.time(), result)
    return result


# ── Sub-score calculators ─────────────────────────────────────────────────────

def _relative_strength_score(
    df_daily: pd.DataFrame,
    nifty: dict,
    sector_rs_dict: Optional[dict] = None,
    symbol: str = "",
) -> tuple[float, str]:
    """
    Blends Stock RS vs Nifty (70%) and Sector RS vs Nifty (30%).
    Score: 100 = outperforming by +10%, 50 = in-line, 0 = lagging by -10%.
    """
    if df_daily is None or len(df_daily) < 65:
        return 0.0, "insufficient daily data"

    df_daily = df_daily.copy()
    df_daily.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df_daily.columns]
    if "close" not in df_daily.columns:
        return 0.0, "insufficient daily data or missing 'close' column"

    close = df_daily["close"].astype(float).dropna()
    try:
        ret_20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100.0
        ret_60 = (close.iloc[-1] / close.iloc[-61] - 1) * 100.0
    except Exception:
        return 0.0, "return calc failed"

    rel_20 = ret_20 - nifty["ret_20d"]
    rel_60 = ret_60 - nifty["ret_60d"]
    stock_rs = 0.60 * rel_20 + 0.40 * rel_60

    sector_rs = 0.0
    if sector_rs_dict:
        sec_name = get_sector_for_stock(symbol)
        if sec_name and sec_name in sector_rs_dict:
            sector_rs = sector_rs_dict[sec_name]

    combined_rs = 0.70 * stock_rs + 0.30 * sector_rs

    # Normalize: +10% outperformance = 100, -10% = 0
    score = max(0.0, min(100.0, 50.0 + combined_rs * 5.0))
    return score, f"RS={combined_rs:+.1f}% (stk={stock_rs:+.1f}%, sec={sector_rs:+.1f}%)"


def _volume_trend_score(df_daily: pd.DataFrame) -> tuple[float, str]:
    """
    Is volume expanding? Today's volume vs 20-bar average.
    Score: 100 = 3x average, 50 = 1x, 0 = <0.3x
    """
    if df_daily is None or len(df_daily) < 22:
        return 50.0, "no volume data"

    df_daily = df_daily.copy()
    df_daily.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df_daily.columns]
    if "volume" not in df_daily.columns:
        return 50.0, "no volume data"

    try:
        vol = df_daily["volume"].astype(float).dropna()
        avg_vol = float(vol.iloc[-21:-1].mean())
        last_vol = float(vol.iloc[-1])
        if avg_vol <= 0:
            return 50.0, "zero avg vol"
        ratio = last_vol / avg_vol
        score = max(0.0, min(100.0, (ratio - 0.3) / (3.0 - 0.3) * 100.0))
        return score, f"vol={ratio:.1f}x avg"
    except Exception:
        return 50.0, "vol calc error"


def _proximity_score(df_daily: pd.DataFrame, setup_mode: str = "TREND_PULLBACK") -> tuple[float, str]:
    """
    Dual-mode proximity score:
    - TREND_PULLBACK: Proximity to 52w high + proximity to 21-day EMA.
    - MEAN_REVERSION: Proximity to lower Bollinger Band + RSI oversold.
    """
    if df_daily is None or len(df_daily) < 30:
        return 0.0, "insufficient data"

    df_daily = df_daily.copy()
    df_daily.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df_daily.columns]
    if "close" not in df_daily.columns:
        return 0.0, "missing 'close' column"

    close = df_daily["close"].astype(float).dropna()
    current = float(close.iloc[-1])

    if setup_mode in ("MEAN_REVERSION", "SIDEWAYS", "RANGING"):
        try:
            sma20 = float(close.tail(20).mean())
            std20 = float(close.tail(20).std())
            lower_bb = sma20 - (2.0 * std20)
            upper_bb = sma20 + (2.0 * std20)
            bb_dist = (current - lower_bb) / current * 100.0 if current > 0 else 0.0

            if current <= lower_bb:
                score_bb = 100.0
            elif upper_bb > lower_bb:
                pct_b = (current - lower_bb) / (upper_bb - lower_bb)
                score_bb = max(0.0, (1.0 - pct_b / 0.30) * 100.0)
            else:
                score_bb = 0.0

            rsi = ta.rsi(close, length=14)
            rsi_val = float(rsi.dropna().iloc[-1]) if rsi is not None and len(rsi.dropna()) > 0 else 50.0
            if rsi_val < 30:
                score_rsi = 100.0
            elif rsi_val > 50:
                score_rsi = 0.0
            else:
                score_rsi = max(0.0, (50.0 - rsi_val) / 20.0 * 100.0)

            score = 0.50 * score_bb + 0.50 * score_rsi
            return score, f"BB dist={bb_dist:+.1f}%, RSI={rsi_val:.1f}"
        except Exception:
            return 0.0, "mean reversion proximity calc failed"
    else:
        # TREND_PULLBACK
        try:
            lookback = close.tail(252) if len(close) >= 252 else close
            high_52w = float(lookback.max())
            pct_below = (high_52w - current) / high_52w * 100.0 if high_52w > 0 else 0.0
            score_52w = max(0.0, min(100.0, (1.0 - pct_below / 30.0) * 100.0))

            ema21 = ta.ema(close, length=21)
            if ema21 is not None and len(ema21.dropna()) > 0:
                e21 = float(ema21.dropna().iloc[-1])
                ema_dist = (current - e21) / e21 * 100.0
                if 0.0 <= ema_dist <= 3.0:
                    score_ema = 100.0
                elif ema_dist > 3.0:
                    score_ema = max(0.0, 100.0 - (ema_dist - 3.0) * 10.0)
                else:
                    score_ema = max(0.0, 100.0 + ema_dist * 20.0)
            else:
                ema_dist = 0.0
                score_ema = 50.0

            score = 0.50 * score_52w + 0.50 * score_ema
            return score, f"{pct_below:.1f}% below 52wH, {ema_dist:+.1f}% from EMA21"
        except Exception:
            return 0.0, "pullback proximity calc failed"


def _trend_quality_score(df_daily: pd.DataFrame, setup_mode: str = "TREND_PULLBACK") -> tuple[float, str]:
    """
    Dual-mode trend quality score:
    - TREND_PULLBACK: ADX (>25) + price linearity (R² correlation over 20 bars).
    - MEAN_REVERSION: Low ADX (<20) + Bollinger Bandwidth stability.
    """
    if df_daily is None or len(df_daily) < 30:
        return 0.0, "insufficient data"

    df_daily = df_daily.copy()
    df_daily.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df_daily.columns]
    if "close" not in df_daily.columns:
        return 0.0, "missing 'close' column"

    try:
        adx_val = 0.0
        if "high" in df_daily.columns and "low" in df_daily.columns:
            adx_data = ta.adx(df_daily["high"].astype(float), df_daily["low"].astype(float), df_daily["close"].astype(float), length=14)
            if adx_data is not None and "ADX_14" in adx_data.columns:
                adx_series = adx_data["ADX_14"].dropna()
                if len(adx_series) > 0:
                    adx_val = float(adx_series.iloc[-1])

        if setup_mode in ("MEAN_REVERSION", "SIDEWAYS", "RANGING"):
            # Prefers low ADX (ranging market)
            low_adx_score = max(0.0, min(100.0, (35.0 - adx_val) / (35.0 - 15.0) * 100.0))
            close = df_daily["close"].astype(float).dropna()
            sma20 = float(close.tail(20).mean())
            std20 = float(close.tail(20).std())
            bw = (4.0 * std20) / sma20 * 100.0 if sma20 > 0 else 20.0
            bw_score = max(0.0, min(100.0, (20.0 - bw) / 15.0 * 100.0))
            score = 0.60 * low_adx_score + 0.40 * bw_score
            return score, f"Low ADX={adx_val:.0f} (Ranging TQ)"
        else:
            # TREND_PULLBACK
            close = df_daily["close"].astype(float).dropna().tail(20)
            if len(close) >= 10:
                x = np.arange(len(close))
                correlation = np.corrcoef(x, close.values)[0, 1]
                linearity = max(0.0, float(correlation)) * 100.0 if not np.isnan(correlation) else 50.0
            else:
                linearity = 50.0

            adx_score = max(0.0, min(100.0, (adx_val - 15.0) / (40.0 - 15.0) * 100.0))
            score = 0.60 * adx_score + 0.40 * linearity
            return score, f"ADX={adx_val:.0f} linearity={linearity:.0f}%"
    except Exception:
        return 0.0, "trend quality calc failed"


def _reward_to_cost_score(df_daily: pd.DataFrame, entry_price: float, setup_mode: str = "TREND_PULLBACK") -> tuple[float, str]:
    """
    Estimated reward-to-charge ratio using ATR.
    - TREND_PULLBACK: 5x weekly ATR target.
    - MEAN_REVERSION: 2x daily ATR target.
    Score: 100 = reward >= 15x charges, 0 = reward <= 3x charges.
    """
    if df_daily is None or len(df_daily) < 30 or entry_price <= 0:
        return 50.0, "no data"

    df_daily = df_daily.copy()
    df_daily.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df_daily.columns]
    if "close" not in df_daily.columns:
        return 50.0, "no data"

    try:
        if "high" in df_daily.columns and "low" in df_daily.columns:
            atr_series = ta.atr(df_daily["high"].astype(float), df_daily["low"].astype(float), df_daily["close"].astype(float), length=14)
            if atr_series is None or atr_series.isna().iloc[-1]:
                return 50.0, "no ATR"
            daily_atr = float(atr_series.dropna().iloc[-1])
        else:
            daily_atr = entry_price * 0.01

        if setup_mode in ("MEAN_REVERSION", "SIDEWAYS", "RANGING"):
            target_dist = 2.0 * daily_atr
        else:
            weekly_atr = daily_atr * (5.0 ** 0.5)
            target_dist = 5.0 * weekly_atr

        target_pct = (target_dist / entry_price) * 100.0
        trade_value = entry_price * 10.0
        est_charges = trade_value * 0.002 + 15.93
        reward_to_cost = (target_dist * 10.0) / est_charges if est_charges > 0 else 0.0

        score = max(0.0, min(100.0, (reward_to_cost - 3.0) / (15.0 - 3.0) * 100.0))
        return score, f"target={target_pct:.1f}%, R/C={reward_to_cost:.1f}x"
    except Exception:
        return 50.0, "reward calc failed"


def _regime_alignment_score(df_daily: pd.DataFrame, setup_mode: str = "TREND_PULLBACK") -> tuple[float, str]:
    """
    Evaluates alignment between multi-timeframe regime and target setup mode.
    Score: 100 = Perfect alignment, 50 = Partial alignment, 0 = Mismatched.
    """
    if df_daily is None or len(df_daily) < 50:
        return 0.0, "insufficient data"

    df_daily = df_daily.copy()
    df_daily.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df_daily.columns]
    if "close" not in df_daily.columns:
        return 0.0, "missing 'close' column"

    try:
        daily_regime = detect_regime(df_daily)

        df_weekly = None
        required_cols = {"open", "high", "low", "close", "volume"}
        if required_cols.issubset(df_daily.columns):
            df_weekly = df_daily.resample("W").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum"
            }).dropna()
        
        weekly_regime = detect_regime(df_weekly) if df_weekly is not None and len(df_weekly) >= 50 else None

        if setup_mode in ("TREND_PULLBACK", "BULL_TREND", "TRENDING"):
            if daily_regime.regime == "TRENDING" and weekly_regime and weekly_regime.regime == "TRENDING":
                return 100.0, "TRENDING on daily+weekly"
            elif daily_regime.regime == "TRENDING":
                return 50.0, "TRENDING on daily only"
            else:
                return 0.0, f"regime={daily_regime.regime}"
        else:
            if daily_regime.regime == "RANGING":
                return 100.0, "RANGING on daily (ideal mean-reversion)"
            else:
                return 25.0, f"regime={daily_regime.regime}"
    except Exception:
        return 0.0, "regime check failed"


# ── Main ranking function ─────────────────────────────────────────────────────

def rank_universe(
    universe_data: dict[str, dict],
    top_n: int = TOP_N_DEFAULT,
    allowed_sectors: Optional[set] = None,
    market_regime: str = "BULL_TREND",
    sector_rs_dict: Optional[dict[str, float]] = None,
) -> list[StockRanking]:
    """
    Rank all stocks in universe_data by composite opportunity score.
    Returns the top_n candidates for full signal evaluation.

    Parameters
    ----------
    universe_data   : {symbol: {"1d": df, "1h": df, "15m": df}} from fetch_batch_universe()
    top_n           : How many top stocks to return
    allowed_sectors : If provided, only rank stocks from these sectors
    market_regime   : Current macro regime ("BULL_TREND" | "SIDEWAYS" | "RANGING")
    sector_rs_dict  : Optional mapping of sector -> composite_rs

    Returns
    -------
    list of StockRanking, sorted by composite_score descending, length <= top_n
    """
    nifty = _get_nifty_returns()
    if sector_rs_dict is None:
        try:
            sector_rs_dict = get_sector_rs_dict(use_cache=True)
        except Exception:
            sector_rs_dict = {}

    setup_mode = "MEAN_REVERSION" if market_regime in ("SIDEWAYS", "RANGING", "MEAN_REVERSION") else "TREND_PULLBACK"

    rankings = []

    for symbol, frames in universe_data.items():
        df_daily = frames.get("1d")
        if df_daily is None or len(df_daily) < 65:
            continue

        df_daily = df_daily.copy()
        df_daily.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df_daily.columns]
        if "close" not in df_daily.columns:
            continue

        # Sector gate
        if allowed_sectors is not None:
            sector = get_sector_for_stock(symbol)
            if sector is None or sector not in allowed_sectors:
                continue

        close_series = df_daily["close"].astype(float).dropna()
        entry_price = float(close_series.iloc[-1]) if len(close_series) > 0 else 0.0
        if entry_price <= 0:
            continue

        # Compute all sub-scores
        rs_score, rs_note   = _relative_strength_score(df_daily, nifty, sector_rs_dict, symbol)
        vol_score, vol_note = _volume_trend_score(df_daily)
        brk_score, brk_note = _proximity_score(df_daily, setup_mode)
        tq_score, tq_note   = _trend_quality_score(df_daily, setup_mode)
        rc_score, rc_note   = _reward_to_cost_score(df_daily, entry_price, setup_mode)
        reg_score, reg_note = _regime_alignment_score(df_daily, setup_mode)

        composite = (
            WEIGHTS["volume_trend"]       * vol_score +
            WEIGHTS["proximity_breakout"] * brk_score +
            WEIGHTS["trend_quality"]      * tq_score +
            WEIGHTS["relative_strength"]  * rs_score +
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
            setup_type=setup_mode,
        ))

    rankings.sort(key=lambda r: r.composite_score, reverse=True)

    print(f"\n  [ranker] Top {min(top_n, len(rankings))} of {len(rankings)} scored stocks (setup={setup_mode}):")
    for r in rankings[:top_n]:
        print(f"    {r.symbol}: {r.composite_score:.0f}/100 "
              f"[Vol={r.volume_trend:.0f} Brk={r.proximity_breakout:.0f} "
              f"TQ={r.trend_quality:.0f} RS={r.relative_strength:.0f} "
              f"RC={r.reward_to_cost:.0f} Reg={r.regime_alignment:.0f}]")

    return rankings[:top_n]
