"""
engine/signals.py — Shared Signal Evaluation & Intrabar Exit Engine
====================================================================
Single source of truth for QuantumTrader signal evaluation.
Shared between trader.py (live execution) and backtest.py (backtesting).
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
from setups import classify_long_setup, SetupResult
from risk_manager import plan_position, PositionPlan

# ─── Constants (single source of truth) ───────────────────────────────────────
BUY_THRESHOLD          = 6      # Default threshold for TRENDING / RANGING regimes
VOLATILE_BUY_THRESHOLD = 7      # Stricter threshold for VOLATILE regime
SELL_THRESHOLD         = -2     # Threshold for confluence SELL signal
PRICE_SANITY_PCT       = 20.0   # Reject bars moving > 20% (stock split guard)
GAP_SLIPPAGE           = 0.001  # 0.1% haircut for gap-through exits
MIN_TARGET_PCT         = 3.5    # Minimum expected move (%) to offset ₹113 charges
ATR_VOL_FLOOR          = 0.005  # 0.5% ATR floor (reject stagnant stocks)
MIN_VOL_RATIO          = 1.0    # Minimum volume ratio threshold
SENTIMENT_ACTIVE       = True   # Enable Gemini LLM news sentiment filter


def _default_sentiment(symbol: str) -> float:
    """Real-time Gemini LLM sentiment scoring function."""
    if not SENTIMENT_ACTIVE:
        return 0.0
    try:
        from sentiment_llm import get_llm_sentiment
        return get_llm_sentiment(symbol)
    except Exception:
        return 0.0


def _neutral_sentiment(symbol: str) -> float:
    """Neutral stub used in backtesting to avoid network calls."""
    return 0.0


@dataclass
class SignalResult:
    """Dataclass encapsulating evaluate_signal() output."""
    symbol: str
    final_action: str            # "BUY" | "SELL" | "HOLD"
    effective_score: int         # confluence_score + sentiment adjustment
    confluence_score: int        # raw multi-TF score + setup bonus
    sentiment: int               # -1 | 0 | +1
    sentiment_score: float       # raw LLM score (-1.0 to +1.0)
    reason_str: str              # Diagnostic rationale log
    price: float                 # Current close price
    atr: float                   # 14-period ATR
    regime: str                  # Market regime ("TRENDING" | "RANGING" | "VOLATILE" | "UNKNOWN")
    plan: Optional[object]       # PositionPlan object from risk_manager or None
    skipped: bool = False        # True if bar failed sanity/volatility floor checks
    setup_type: str = "UNCLASSIFIED" # Qualified setup name
    setup: str = "UNCLASSIFIED"  # Alias for setup_type for backward compatibility
    volume_ratio: float = 1.0    # Relative volume ratio
    liquidity_cap_qty: int = 0   # Liquidity cap quantity

    def __post_init__(self):
        if self.setup == "UNCLASSIFIED" and self.setup_type != "UNCLASSIFIED":
            object.__setattr__(self, "setup", self.setup_type)
        elif self.setup_type == "UNCLASSIFIED" and self.setup != "UNCLASSIFIED":
            object.__setattr__(self, "setup_type", self.setup)


def evaluate_signal(
    symbol: str,
    frames: dict,
    capital: float,
    cash: float,
    held_stocks: set,
    sentiment_fn: Callable[[str], float] = _default_sentiment,
    open_count: int = 0,
    max_positions: int = 5,
    kelly_fraction: Optional[float] = None,
) -> SignalResult:
    """
    Evaluate trading signal for symbol using 1d, 1h, 15m OHLC frames.
    """
    df_15 = frames.get("15m")
    df_daily = frames.get("1d")

    # Step 1: Input Validation
    if df_15 is None or len(df_15) < 2:
        return SignalResult(symbol, "HOLD", 0, 0, 0, 0.0, "Insufficient 15m data (< 2 bars)", 0.0, 0.0, "UNKNOWN", None, skipped=True)

    # Extract actual close price upfront for telemetry and sanity checks
    close_price = float(df_15["close"].iloc[-1])

    # Step 2: Regime Detection
    regime_src = df_daily if (df_daily is not None and len(df_daily) > 50) else df_15
    regime_result = detect_regime(regime_src)
    regime = regime_result.regime
    adx_val = regime_result.adx

    # Step 3: Setup Classification
    setup_res: SetupResult = classify_long_setup(frames, regime=regime, adx=adx_val)

    # Step 4: Price Sanity Guard (Executed BEFORE Strict Regime Gate to catch extreme bar anomalies)
    prev_close = float(df_15["close"].iloc[-2])
    chg_pct = abs((close_price - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0
    if chg_pct > PRICE_SANITY_PCT:
        return SignalResult(
            symbol, "HOLD", 0, 0, 0, 0.0,
            f"PRICE SANITY REJECT: {chg_pct:.1f}% bar move exceeds limit ({PRICE_SANITY_PCT}%)",
            close_price, 0.0, regime, None, skipped=True, setup_type=setup_res.name, volume_ratio=setup_res.rvol_15m
        )

    # Step 5: Strict Regime Gating (Prevents RANGING leakage & catch-falling-knives in TRENDING)
    if regime == "RANGING" and setup_res.name != "MEAN_REVERSION":
        return SignalResult(
            symbol, "HOLD", 0, 0, 0, 0.0,
            f"STRICT REGIME GATE: RANGING regime blocks non-mean-reversion setup '{setup_res.name}'",
            close_price, 0.0, regime, None, skipped=True, setup_type=setup_res.name, volume_ratio=setup_res.rvol_15m
        )

    if regime == "TRENDING" and setup_res.name == "MEAN_REVERSION":
        return SignalResult(
            symbol, "HOLD", 0, 0, 0, 0.0,
            f"STRICT REGIME GATE: TRENDING regime blocks mean-reversion setup '{setup_res.name}'",
            close_price, 0.0, regime, None, skipped=True, setup_type=setup_res.name, volume_ratio=setup_res.rvol_15m
        )

    # Step 6: Multi-Timeframe Confluence Engine
    confluence = get_confluence(symbol, frames, regime)
    raw_score = confluence.confluence_score + setup_res.score_bonus

    # Step 7: Sentiment Adjustment & Lazy LLM Evaluation
    effective_buy_threshold = VOLATILE_BUY_THRESHOLD if regime == "VOLATILE" else BUY_THRESHOLD
    if SENTIMENT_ACTIVE and (raw_score >= (effective_buy_threshold - 1) or raw_score <= (SELL_THRESHOLD + 1)):
        sentiment_score = sentiment_fn(symbol)
    else:
        sentiment_score = 0.0

    sentiment = 0
    if sentiment_score > 0.3:  sentiment = 1
    if sentiment_score < -0.3: sentiment = -1
    effective_score = raw_score + sentiment

    # Step 8: ATR Volatility Floor
    try:
        import pandas_ta as ta
        atr_series = ta.atr(df_15["high"].astype(float), df_15["low"].astype(float), df_15["close"].astype(float), length=14)
        atr = float(atr_series.iloc[-1]) if (atr_series is not None and not atr_series.isna().iloc[-1]) else close_price * 0.01
    except Exception:
        atr = close_price * 0.01

    volatility_pct = (atr / close_price) * 100.0 if close_price > 0 else 0.0
    if volatility_pct < (ATR_VOL_FLOOR * 100.0):
        return SignalResult(
            symbol, "HOLD", effective_score, raw_score, sentiment, sentiment_score,
            f"ATR FLOOR REJECT: Volatility ({volatility_pct:.2f}%) < floor ({ATR_VOL_FLOOR*100.0:.2f}%)",
            close_price, atr, regime, None, skipped=True, setup_type=setup_res.name, volume_ratio=setup_res.rvol_15m
        )

    # Step 9: Action Determination
    if confluence.action == "HOLD":
        final_action = "HOLD"
    elif effective_score >= effective_buy_threshold and setup_res.eligible:
        final_action = "BUY"
    elif effective_score <= SELL_THRESHOLD:
        final_action = "SELL"
    else:
        final_action = "HOLD"

    reason_parts = confluence.reasons.copy()
    reason_parts.append(f"Setup: {setup_res.name} ({setup_res.reason})")
    if sentiment > 0:  reason_parts.append(f"Positive LLM news +1 ({sentiment_score:+.2f})")
    elif sentiment < 0: reason_parts.append(f"Negative LLM news -1 ({sentiment_score:+.2f})")
    reason_str = " | ".join(reason_parts) + f" -> score {effective_score:+d} -> {final_action}"

    # Volume check
    rvol_val = setup_res.rvol_15m
    if final_action == "BUY" and rvol_val < MIN_VOL_RATIO:
        reason_str += f" | INSUFFICIENT RVOL ({rvol_val:.2f}x < {MIN_VOL_RATIO:.1f}x) — SKIPPED"
        final_action = "HOLD"

    # Step 10: Position Sizing & Target Move Gate
    # V5.5: Score-scaled Kelly — higher-confidence signals get larger positions
    plan = None
    if final_action == "BUY" and symbol not in held_stocks and open_count < max_positions and cash > 0:
        # Scale kelly fraction by signal strength: score 6=1.0x, 7=1.2x, 8+=1.5x
        score_multiplier = 1.0
        if effective_score >= 8:
            score_multiplier = 1.5
        elif effective_score >= 7:
            score_multiplier = 1.2
        scaled_kelly = (kelly_fraction * score_multiplier) if kelly_fraction is not None else None

        plan = plan_position(
            stock=symbol,
            entry_price=close_price,
            atr=atr,
            capital=capital,
            regime=regime,
            kelly_fraction=scaled_kelly,
        )
        if plan is not None:
            target_move_pct = (plan.target_2 - plan.entry_price) / plan.entry_price * 100.0 if plan.entry_price > 0 else 0.0
            if target_move_pct < MIN_TARGET_PCT:
                reason_str += f" | TARGET MOVE TOO SMALL ({target_move_pct:.1f}% < {MIN_TARGET_PCT}%) — SKIPPED"
                final_action = "HOLD"
                plan = None
            else:
                reason_str += f" | cost/risk {plan.cost_to_risk:.0%} | reward/cost {plan.reward_to_cost:.1f}x | score_mult {score_multiplier:.1f}x"
        else:
            reason_str += " | RISK MANAGER GATE OR SIZING FAILED -> HOLD"
            final_action = "HOLD"

    return SignalResult(
        symbol=symbol,
        final_action=final_action,
        effective_score=effective_score,
        confluence_score=raw_score,
        sentiment=sentiment,
        sentiment_score=sentiment_score,
        reason_str=reason_str,
        price=close_price,
        atr=atr,
        regime=regime,
        plan=plan,
        skipped=False,
        setup_type=setup_res.name,
        volume_ratio=rvol_val,
    )


def apply_intrabar_exit(
    bar: pd.Series,
    entry: float,
    sl: float,
    target: float,
    qty: int,
    entry_time: object,
    current_time: object,
) -> Optional[dict]:
    """
    Evaluates intrabar TP/SL hits. Conservative priority: SL wins if both hit on same bar.
    """
    bar_open = float(bar["open"])
    bar_high = float(bar["high"])
    bar_low  = float(bar["low"])

    stop_breached = bar_low <= sl
    target_reached = bar_high >= target

    if stop_breached and target_reached:
        target_reached = False  # SL takes priority

    if target_reached:
        if bar_open >= target:
            fill_price = bar_open * (1.0 - GAP_SLIPPAGE)
            note = f"Gap-through TP (open ₹{bar_open:.2f} > TP ₹{target:.2f})"
        else:
            fill_price = target
            note = f"TP hit (bar H ₹{bar_high:.2f})"
        return {"type": "TP", "fill_price": fill_price, "note": note}

    if stop_breached:
        if bar_open <= sl:
            fill_price = bar_open * (1.0 - GAP_SLIPPAGE)
            note = f"Gap-through SL (open ₹{bar_open:.2f} < SL ₹{sl:.2f})"
        else:
            fill_price = sl
            note = f"SL hit (bar L ₹{bar_low:.2f})"
        return {"type": "SL", "fill_price": fill_price, "note": note}

    return None
