"""
Risk Manager — ATR-Based Position Sizing & Trailing Stops
==========================================================
Replaces V1's fixed 1.5% stop / 3% target with intelligent,
volatility-adaptive risk management.

Key principles:
  1. Stop loss = 2x ATR below entry (breathes with the market)
  2. Target = 3x ATR above entry (minimum 1.5:1 reward:risk)
  3. Position size = risk_amount / (2 * ATR) — never risk more than 2% of capital
  4. Trailing stop: once +1.5 ATR in profit, trail by 1x ATR
  5. Volatile regime → halve position size
"""

import pandas as pd
import pandas_ta as ta
from dataclasses import dataclass
from typing import Optional


# Configuration  (V2.1)
RISK_PER_TRADE = 0.02    # 2% of capital per trade
ATR_SL_MULTIPLIER = 2.0  # stop = entry - 2*ATR
ATR_TP_MULTIPLIER = 4.0  # target = entry + 4*ATR  (↑ from 3x → better RR)
TRAIL_ACTIVATION = 2.0   # activate trailing after 2*ATR profit (gives trade room)
TRAIL_DISTANCE = 1.0     # trail stop by 1*ATR once activated
MAX_POSITIONS = 10        # max simultaneous positions (synced with live engine)


@dataclass
class PositionPlan:
    """Complete trade plan with entry, stop, target, and sizing."""
    stock: str
    entry_price: float
    stop_loss: float
    target: float
    quantity: int
    risk_amount: float
    atr: float
    reward_risk_ratio: float
    regime_adjusted: bool  # True if size was reduced due to volatility


@dataclass
class TrailingStopUpdate:
    """Result of checking if a trailing stop should move."""
    stock: str
    current_price: float
    old_stop: float
    new_stop: float
    should_update: bool
    should_close: bool  # True if stop was hit
    unrealized_pnl: float


def calculate_atr(df: pd.DataFrame, length: int = 14) -> Optional[float]:
    """Get the current ATR value for a stock."""
    if len(df) < length + 1:
        return None
    atr = ta.atr(df["high"], df["low"], df["close"], length=length)
    if atr is None or len(atr.dropna()) == 0:
        return None
    return float(atr.dropna().iloc[-1])


def plan_position(
    stock: str,
    entry_price: float,
    atr: float,
    capital: float,
    regime: str,
) -> Optional[PositionPlan]:
    """
    Calculate the complete trade plan: stop, target, quantity.
    
    The key insight: position size is DERIVED from the stop distance,
    not the other way around. We decide how much to risk (2% of capital),
    then calculate how many shares that allows given the ATR-based stop.
    """
    if atr <= 0 or entry_price <= 0:
        return None

    # Dynamic stop and target based on ATR
    sl_distance = ATR_SL_MULTIPLIER * atr
    tp_distance = ATR_TP_MULTIPLIER * atr
    stop_loss = round(entry_price - sl_distance, 2)
    target = round(entry_price + tp_distance, 2)

    # Reward:risk ratio
    rr_ratio = tp_distance / sl_distance if sl_distance > 0 else 0

    # Only take trades with minimum 1.5:1 RR
    if rr_ratio < 1.5:
        return None

    # Position sizing: risk_amount / stop_distance = quantity
    risk_amount = capital * RISK_PER_TRADE
    
    # Volatile regime → halve the risk
    regime_adjusted = False
    if regime == "VOLATILE":
        risk_amount *= 0.5
        regime_adjusted = True

    quantity = int(risk_amount / sl_distance)
    
    # Sanity checks
    if quantity <= 0:
        return None
    
    total_cost = entry_price * quantity
    if total_cost > capital * 0.33:  # never deploy more than 33% in one trade
        quantity = int((capital * 0.33) / entry_price)
        if quantity <= 0:
            return None

    return PositionPlan(
        stock=stock,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target=target,
        quantity=quantity,
        risk_amount=risk_amount,
        atr=atr,
        reward_risk_ratio=rr_ratio,
        regime_adjusted=regime_adjusted,
    )


def check_trailing_stop(
    stock: str,
    entry_price: float,
    current_price: float,
    current_stop: float,
    atr: float,
    adx: Optional[float] = None,
) -> TrailingStopUpdate:
    """
    Manage trailing stops on open positions.
    
    Logic:
    1. If price hits stop → close the trade
    2. If price moved 1.5*ATR above entry → activate trailing
    3. Trail stop = current_price - 1*ATR (ratchets up, never down)
    4. ADX Decay (V2.5): if ADX < 25 while in profit, slam stop up to 0.5 ATR
    """
    unrealized_pnl = current_price - entry_price

    # Check if stop was hit
    if current_price <= current_stop:
        return TrailingStopUpdate(
            stock=stock,
            current_price=current_price,
            old_stop=current_stop,
            new_stop=current_stop,
            should_update=False,
            should_close=True,
            unrealized_pnl=unrealized_pnl,
        )

    # Check if trailing should activate
    profit_in_atr = unrealized_pnl / atr if atr > 0 else 0
    new_stop = current_stop

    if profit_in_atr >= TRAIL_ACTIVATION:
        trail_dist = TRAIL_DISTANCE
        
        # ADX Decay Check
        if adx is not None and adx < 25:
            trail_dist = 0.5  # tighten aggressively
        
        # Trail stop = price - dist*ATR, but never move it DOWN
        candidate_stop = round(current_price - (trail_dist * atr), 2)
        # Ensure we're at least at breakeven
        candidate_stop = max(candidate_stop, entry_price)
        # Never lower the stop
        new_stop = max(candidate_stop, current_stop)

    should_update = new_stop > current_stop

    return TrailingStopUpdate(
        stock=stock,
        current_price=current_price,
        old_stop=current_stop,
        new_stop=new_stop,
        should_update=should_update,
        should_close=False,
        unrealized_pnl=unrealized_pnl,
    )
