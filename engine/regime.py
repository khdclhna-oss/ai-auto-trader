"""
Market Regime Detector
=======================
Determines if the market is TRENDING, RANGING, or VOLATILE.
The strategy adapts its behavior based on the current regime:

TRENDING  (ADX > 25)  → Use momentum strategies, wider stops
RANGING   (ADX < 20)  → Use mean-reversion, tighter entries
VOLATILE  (ATR spike) → Reduce position size by 50%
"""

import pandas as pd
import pandas_ta as ta
from dataclasses import dataclass


@dataclass
class RegimeResult:
    regime: str        # TRENDING, RANGING, VOLATILE
    adx: float         # ADX value
    atr: float         # current ATR
    atr_percentile: float  # ATR vs recent history (0-100)
    description: str


def detect_regime(df: pd.DataFrame) -> RegimeResult:
    """
    Detect the current market regime using ADX and ATR.
    
    ADX (Average Directional Index):
      > 25 → strong trend
      20-25 → weak trend  
      < 20 → no trend (range-bound)
    
    ATR percentile:
      > 80th percentile → unusual volatility → reduce risk
    """
    if len(df) < 50:
        return RegimeResult("UNKNOWN", 0.0, 0.0, 50.0, "Insufficient data for regime detection")

    high, low, close = df["high"], df["low"], df["close"]

    # ADX for trend strength
    adx_data = ta.adx(high, low, close, length=14)
    adx_val = 0.0
    if adx_data is not None and len(adx_data.dropna()) > 0:
        adx_val = float(adx_data["ADX_14"].dropna().iloc[-1])

    # ATR for volatility measurement
    atr_series = ta.atr(high, low, close, length=14)
    atr_val = 0.0
    atr_pctl = 50.0
    if atr_series is not None and len(atr_series.dropna()) > 20:
        atr_clean = atr_series.dropna()
        atr_val = float(atr_clean.iloc[-1])
        # Percentile rank vs last 50 periods
        lookback = atr_clean.tail(50)
        atr_pctl = float((lookback < atr_val).sum() / len(lookback) * 100)

    # Classification
    if atr_pctl > 80:
        regime = "VOLATILE"
        desc = f"High volatility regime (ATR {atr_pctl:.0f}th percentile). Position sizes halved."
    elif adx_val >= 25:
        regime = "TRENDING"
        desc = f"Strong trend detected (ADX: {adx_val:.1f}). Momentum strategies active."
    else:
        regime = "RANGING"
        desc = f"Weak trend or range-bound (ADX: {adx_val:.1f}). Force skip to conserve capital."

    return RegimeResult(
        regime=regime,
        adx=adx_val,
        atr=atr_val,
        atr_percentile=atr_pctl,
        description=desc,
    )
