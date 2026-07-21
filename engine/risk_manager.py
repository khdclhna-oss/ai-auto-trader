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

from calculator import calculate_realistic_charges


# Configuration (V3.6)
RISK_PER_TRADE      = 0.015  # V4.1: increased from 1% to 1.5% to dilute fixed charges.
                              # Restore to 0.02 once system shows +expectancy over 50+ trades.
ATR_SL_MULTIPLIER   = 2.0    # stop = entry - 2.0*ATR (tightened for better R:R)
ATR_TP_MULTIPLIER   = 5.0    # target = entry + 5*ATR (aims for 1:2 RRR minimum)
TRAIL_ACTIVATION    = 2.0    # activate trailing after 2*ATR profit
TRAIL_DISTANCE      = 2.5    # trail stop by 2.5*ATR once activated
MAX_POSITIONS       = 5      # max simultaneous positions
MAX_COST_TO_RISK    = 0.20   # reject if charges consume >20% of planned risk
MIN_REWARD_TO_COST  = 4.0    # planned reward must be at least 4x estimated charges

@dataclass
class PositionPlan:
    """Complete trade plan with entry, stop, three targets, and sizing."""
    stock: str
    entry_price: float
    stop_loss: float
    target_1: float  # 40% qty @ 1:1 RR
    target_2: float  # 40% qty @ 1:2 RR
    target_3: float  # 20% qty @ Runner
    quantity: int
    risk_amount: float
    atr: float
    reward_risk_ratio: float
    regime_adjusted: bool  # True if size was reduced due to volatility
    estimated_charges: float = 0.0
    cost_to_risk: float = 0.0
    reward_to_cost: float = 0.0


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
    stop_loss = round(entry_price - sl_distance, 2)
    
    # V4.1 Three-Tranche Targets
    # T1: 1:1 R/R (Locks in profit, covers brokerage)
    # T2: 1:2 R/R (The "Meat" of the move)
    # T3: Runner (Captured with Trailing Stop)
    target_1 = round(entry_price + (1.0 * sl_distance), 2)
    target_2 = round(entry_price + (2.0 * sl_distance), 2)
    target_3 = round(entry_price + (4.0 * sl_distance), 2) # Initial target, will trail

    # Inverted stop loss & targets validation (R1)
    if stop_loss >= entry_price or target_1 <= entry_price or target_2 <= entry_price or target_3 <= entry_price:
        return None

    # Reward:risk ratio (blended for the first 2 tranches)
    rr_ratio = (target_2 - entry_price) / sl_distance if sl_distance > 0 else 0
    
    # Position sizing: risk_amount / stop_distance = quantity
    risk_amount = capital * RISK_PER_TRADE
    
    # Volatile regime → halve the risk
    regime_adjusted = False
    if regime == "VOLATILE":
        risk_amount *= 0.5
        regime_adjusted = True

    quantity = int(risk_amount / sl_distance)
    
    # Ensure quantity is at least 3 to allow 40/40/20 split
    if quantity < 3:
        # If risk budget is too small for tranches, we ensure at least 3 shares if possible
        quantity = 3
    
    # Sanity checks
    total_cost = entry_price * quantity
    if total_cost > capital * 0.33:  # never deploy more than 33% in one trade
        quantity = int((capital * 0.33) / entry_price)
        if quantity < 3:
            return None

    # V3.6: Cost-to-Risk Gate
    estimated_charges = calculate_realistic_charges(
        entry_price,
        target_2,
        quantity,
        is_intraday=False,
    ).total
    planned_risk = sl_distance * quantity
    planned_reward = max(0.0, (target_2 - entry_price) * quantity)
    cost_to_risk = estimated_charges / planned_risk if planned_risk > 0 else float("inf")
    reward_to_cost = planned_reward / estimated_charges if estimated_charges > 0 else float("inf")
    if cost_to_risk > MAX_COST_TO_RISK or reward_to_cost < MIN_REWARD_TO_COST:
        # Charges eat too much of the planned edge; skip instead of donating fees.
        return None

    return PositionPlan(
        stock=stock,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        target_3=target_3,
        quantity=quantity,
        risk_amount=risk_amount,
        atr=atr,
        reward_risk_ratio=rr_ratio,
        regime_adjusted=regime_adjusted,
        estimated_charges=estimated_charges,
        cost_to_risk=cost_to_risk,
        reward_to_cost=reward_to_cost,
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
            trail_dist = 1.5  # tighten, but not so much that a single wick stops it out
        
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
