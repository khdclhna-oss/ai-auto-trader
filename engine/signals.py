"""
engine/signals.py — Shared Signal Evaluation Module
====================================================
Single source of truth for QuantumTrader signal generation.
Both trader.py (live) and backtest.py call evaluate_signal() from here.

Design decisions:
  - sentiment_fn: real Gemini LLM in live, `lambda s: 0.0` neutral stub in backtest
  - Price sanity check (>20% bar change) guards both paths
  - Sentiment-gated final_action: effective_score >= BUY_THRESHOLD required to BUY
"""

import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional
import pandas as pd

# Allow importing sibling modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from multi_timeframe import get_confluence
from regime import detect_regime
from risk_manager import plan_position

# ─── Constants (single source of truth, synced with trader.py) ────────────────
BUY_THRESHOLD  = 6    # V4.0: raised from 5. Score-4/5 trades were primarily noise.
SELL_THRESHOLD = -2   # score at which a held position gets a confluence sell
PRICE_SANITY_PCT = 20  # reject bars with >20% change (split/stale data guard)
GAP_SLIPPAGE   = 0.001  # 0.1% extra fill haircut for gap-through exits
MIN_TARGET_PCT = 2.0   # V3.5: minimum target move % to justify charges (~0.40% breakeven)

# V3.6: Sentiment disabled until live LLM produces non-zero scores.
# All 31 closed trades had sentiment_score = 0. Re-enable once you have
# verified that get_llm_sentiment() returns non-zero values in production.
SENTIMENT_ACTIVE = False


def _default_sentiment(symbol: str) -> float:
    """Real-time Gemini LLM sentiment. Disabled until non-zero live coverage confirmed."""
    if not SENTIMENT_ACTIVE:
        return 0.0  # V3.6: All 31 trades had score=0 — LLM is inactive in production
    try:
        from sentiment_llm import get_llm_sentiment
        return get_llm_sentiment(symbol)
    except Exception:
        return 0.0


def _neutral_sentiment(symbol: str) -> float:
    """Neutral stub. Used in backtest to avoid LLM calls and lookahead bias."""
    return 0.0


@dataclass
class SignalResult:
    """Output of evaluate_signal()."""
    symbol: str
    final_action: str          # BUY / SELL / HOLD
    effective_score: int        # confluencse_score + sentiment adjustment
    confluence_score: int       # raw multi-TF score before sentiment
    sentiment: int              # -1 / 0 / +1 bucket
    sentiment_score: float      # raw float from LLM
    reason_str: str
    price: float
    atr: float
    regime: str
    plan: object                # PositionPlan from risk_manager, or None
    skipped: bool = False       # True when price sanity or data check failed


def evaluate_signal(
    symbol: str,
    frames: dict,
    capital: float,
    cash: float,
    held_stocks: set,
    sentiment_fn: Callable[[str], float] = _default_sentiment,
    open_count: int = 0,
    max_positions: int = 10,
) -> SignalResult:
    """
    Evaluate a trading signal for `symbol` using the supplied OHLC frames dict.

    Parameters
    ----------
    symbol        : Full ticker e.g. "RELIANCE.NS"
    frames        : {"1d": df_daily, "1h": df_hourly, "15m": df_15m}
                    In backtest FULL mode, all three are present.
                    In backtest DEGRADED mode, only "1d" is guaranteed.
    capital       : Current total portfolio capital
    cash          : Available cash (for position sizing)
    held_stocks   : Set of strings of currently held symbols
    sentiment_fn  : Callable[str -> float], default = real Gemini LLM
    open_count    : Number of currently open positions
    max_positions : Maximum allowed concurrent positions
    """

    df_15 = frames.get("15m")
    df_daily = frames.get("1d")
    short_name = symbol.replace(".NS", "")

    # Need at least 2 bars to compute the price sanity check
    if df_15 is None or len(df_15) < 2:
        return SignalResult(symbol, "HOLD", 0, 0, 0, 0.0, "Insufficient 15m data", 0.0, 0.0, "UNKNOWN", None, skipped=True)

    # ── Step 1: Regime ────────────────────────────────────────────────────────
    regime_src = df_daily if (df_daily is not None and len(df_daily) > 50) else df_15
    regime_result = detect_regime(regime_src)

    # ── Step 2: Multi-timeframe confluence ────────────────────────────────────
    confluence = get_confluence(symbol, frames, regime_result.regime)

    # ── Step 3: Sentiment-adjusted score ─────────────────────────────────────
    # [P0 FIX] Lazy LLM Evaluation: Only query the LLM if the technical confluence
    # is close enough to a threshold that a +1/-1 from sentiment could trigger a trade.
    # BUY needs score >= 4, so query if confluence >= 3.  SELL needs <= -2, so query if <= -1.
    score = confluence.confluence_score
    # V3.6: Only call LLM if sentiment is actually active (currently disabled).
    # When SENTIMENT_ACTIVE=False, sentiment_score is always 0.0 — skip the call entirely.
    if SENTIMENT_ACTIVE and (score >= (BUY_THRESHOLD - 1) or score <= (SELL_THRESHOLD + 1)):
        sentiment_score = sentiment_fn(symbol)
    else:
        sentiment_score = 0.0

    sentiment = 0
    if sentiment_score > 0.3:  sentiment = 1
    if sentiment_score < -0.3: sentiment = -1
    effective_score = confluence.confluence_score + sentiment

    # ── Step 4: Price sanity check ────────────────────────────────────────────
    price    = float(df_15["close"].iloc[-1])
    prev_close = float(df_15["close"].iloc[-2])
    chg_pct = abs((price - prev_close) / prev_close) * 100 if prev_close > 0 else 0
    if chg_pct > PRICE_SANITY_PCT:
        reason = f"PRICE ANOMALY: {chg_pct:.1f}% bar move — skipped"
        return SignalResult(symbol, "HOLD", effective_score, confluence.confluence_score,
                            sentiment, sentiment_score, reason, price, 0.0,
                            regime_result.regime, None, skipped=True)

    # ── Step 5: ATR ───────────────────────────────────────────────────────────
    try:
        import pandas_ta as ta
        atr_series = ta.atr(df_15["high"], df_15["low"], df_15["close"], length=14)
        atr = float(atr_series.iloc[-1]) if atr_series is not None and not atr_series.isna().iloc[-1] else price * 0.01
    except Exception:
        atr = price * 0.01

    # ── Step 6: Sentiment-gated final action ──────────────────────────────────
    # [P0 FIX] final_action is derived from effective_score, but MUST respect hard HOLD constraints 
    # (like the RANGING regime filter) from the confluence engine.
    if confluence.action == "HOLD":
        final_action = "HOLD"
    elif effective_score >= BUY_THRESHOLD:
        final_action = "BUY"
    elif effective_score <= SELL_THRESHOLD:
        final_action = "SELL"
    else:
        final_action = "HOLD"

    # Build reason string
    reason_parts = confluence.reasons.copy()
    if sentiment > 0:  reason_parts.append(f"Positive news +1 ({sentiment_score:+.2f})")
    elif sentiment < 0: reason_parts.append(f"Negative news -1 ({sentiment_score:+.2f})")
    reason_str = " | ".join(reason_parts) + f" → score {effective_score:+d} → {final_action}"

    # ── Step 7: Position plan (only if BUY warranted) ─────────────────────────
    plan = None
    if final_action == "BUY" and symbol not in held_stocks and open_count < max_positions and cash > 0:
        plan = plan_position(
            stock=symbol,
            entry_price=price,
            atr=atr,
            capital=capital,
            regime=regime_result.regime,
        )
        # V3.5: Minimum expected-move filter
        # If the target can't move > MIN_TARGET_PCT from entry, charges eat the edge
        if plan is not None:
            # V4.1: Check if the primary target (Tranche 2) moves enough to justify charges
            target_move_pct = (plan.target_2 - plan.entry_price) / plan.entry_price * 100
            if target_move_pct < MIN_TARGET_PCT:
                reason_str += f" | PRIMARY TARGET TOO SMALL ({target_move_pct:.1f}% < {MIN_TARGET_PCT}%) — SKIPPED"
                final_action = "HOLD"
                plan = None

    return SignalResult(
        symbol=symbol,
        final_action=final_action,
        effective_score=effective_score,
        confluence_score=confluence.confluence_score,
        sentiment=sentiment,
        sentiment_score=sentiment_score,
        reason_str=reason_str,
        price=price,
        atr=atr,
        regime=regime_result.regime,
        plan=plan,
        skipped=False,
    )


def apply_intrabar_exit(bar, entry: float, sl: float, target: float, qty: int,
                        entry_time, current_time) -> Optional[dict]:
    """
    Given a completed OHLC bar, determine if SL or TP was hit.
    Priority rule: if same bar hits both, stop wins (conservative).

    Returns dict with 'type' ('SL'|'TP'|None), 'fill_price', 'note', or None.
    """
    bar_open  = float(bar["open"])
    bar_high  = float(bar["high"])
    bar_low   = float(bar["low"])

    stop_breached  = bar_low  <= sl
    target_reached = bar_high >= target

    if stop_breached and target_reached:
        # Same-bar: conservative, stop wins
        target_reached = False

    if target_reached:
        if bar_open >= target:
            # Gapped above target — partial slippage at open
            fill_price = bar_open * (1 - GAP_SLIPPAGE)
            note = f"Gap-through TP (open ₹{bar_open:.2f} > TP ₹{target:.2f})"
        else:
            fill_price = target
            note = f"TP hit (bar H ₹{bar_high:.2f})"
        return {"type": "TP", "fill_price": fill_price, "note": note}

    if stop_breached:
        if bar_open <= sl:
            # Gapped below stop — fill at open with haircut
            fill_price = bar_open * (1 - GAP_SLIPPAGE)
            note = f"Gap-through SL (open ₹{bar_open:.2f} < SL ₹{sl:.2f})"
        else:
            fill_price = sl
            note = f"SL hit (bar L ₹{bar_low:.2f})"
        return {"type": "SL", "fill_price": fill_price, "note": note}

    return None  # no exit this bar
