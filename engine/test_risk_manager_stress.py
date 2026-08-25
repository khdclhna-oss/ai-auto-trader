"""
Comprehensive Boundary & Stress Test Suite for Risk Manager (engine/risk_manager.py)
and associated modules (engine/calculator.py, engine/kelly.py).
"""

import math
import sys
import os
import pandas as pd
import numpy as np

# Add engine directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from risk_manager import plan_position, check_trailing_stop, calculate_atr, PositionPlan, TrailingStopUpdate
from calculator import calculate_realistic_charges, TradeCharges
from kelly import compute_kelly, KellyResult


def run_all_stress_tests():
    results = []
    print("=" * 80)
    print("STARTING RISK MANAGER BOUNDARY STRESS TEST SUITE")
    print("=" * 80)

    # --------------------------------------------------------------------------
    # CATEGORY 1: plan_position() Boundary & Extreme Input Tests
    # --------------------------------------------------------------------------
    print("\n[SECTION 1] plan_position() Boundary Tests")
    
    test_cases_plan = [
        # (name, stock, entry_price, atr, capital, regime, kelly_fraction, expected_crash_or_none)
        ("1.1 Zero ATR", "RELIANCE.NS", 2500.0, 0.0, 100000.0, "NORMAL", None),
        ("1.2 Negative ATR", "RELIANCE.NS", 2500.0, -10.0, 100000.0, "NORMAL", None),
        ("1.3 Zero Entry Price", "RELIANCE.NS", 0.0, 50.0, 100000.0, "NORMAL", None),
        ("1.4 Negative Entry Price", "RELIANCE.NS", -100.0, 50.0, 100000.0, "NORMAL", None),
        ("1.5 Zero Capital", "RELIANCE.NS", 2500.0, 50.0, 0.0, "NORMAL", None),
        ("1.6 Negative Capital", "RELIANCE.NS", 2500.0, 50.0, -50000.0, "NORMAL", None),
        ("1.7 Micro Capital (Cannot buy 1 share)", "RELIANCE.NS", 2500.0, 50.0, 10.0, "NORMAL", None),
        ("1.8 Extreme Capital (1 Trillion)", "RELIANCE.NS", 2500.0, 50.0, 1e12, "NORMAL", None),
        ("1.9 Extreme Small ATR (0.00001)", "RELIANCE.NS", 2500.0, 0.00001, 100000.0, "NORMAL", None),
        ("1.10 Extreme Large ATR (1500 on 2500 stock -> stop_loss < 0)", "RELIANCE.NS", 2500.0, 1500.0, 100000.0, "NORMAL", None),
        ("1.11 Volatile Regime", "RELIANCE.NS", 2500.0, 50.0, 100000.0, "VOLATILE", None),
        ("1.12 Unknown Regime", "RELIANCE.NS", 2500.0, 50.0, 100000.0, "UNKNOWN_REGIME", None),
        ("1.13 Empty String Stock", "", 2500.0, 50.0, 100000.0, "NORMAL", None),
        ("1.14 Kelly Fraction Zero", "RELIANCE.NS", 2500.0, 50.0, 100000.0, "NORMAL", 0.0),
        ("1.15 Kelly Fraction Negative", "RELIANCE.NS", 2500.0, 50.0, 100000.0, "NORMAL", -0.05),
        ("1.16 Kelly Fraction Normal (0.02)", "RELIANCE.NS", 2500.0, 50.0, 100000.0, "NORMAL", 0.02),
        ("1.17 Kelly Fraction High (0.50)", "RELIANCE.NS", 2500.0, 50.0, 100000.0, "NORMAL", 0.50),
        ("1.18 Entry price penny stock (INR 1.0, ATR INR 0.05)", "PENNY.NS", 1.0, 0.05, 100000.0, "NORMAL", None),
        ("1.19 High cost-to-risk trigger (INR 10 stock, ATR INR 0.10, qty small)", "PENNY.NS", 10.0, 0.10, 5000.0, "NORMAL", None),
    ]

    for name, stock, entry_price, atr, capital, regime, kelly_fraction in test_cases_plan:
        try:
            plan = plan_position(stock, entry_price, atr, capital, regime, kelly_fraction)
            if plan is None:
                res_str = "Returned None (Graceful Rejection)"
            else:
                res_str = f"Returned Plan: Qty={plan.quantity}, SL={plan.stop_loss}, T1={plan.target_1}, T2={plan.target_2}, T3={plan.target_3}, Risk={plan.risk_amount:.2f}, C2R={plan.cost_to_risk:.4f}, R2C={plan.reward_to_cost:.2f}"
            print(f"  [PASS] {name:<60} -> {res_str}")
            results.append((name, "PASS", res_str))
        except Exception as e:
            res_str = f"CRASHED! Exception: {type(e).__name__}: {e}"
            print(f"  [FAIL] {name:<60} -> {res_str}")
            results.append((name, "FAIL", res_str))

    # Floating point NaN / Inf cases for plan_position
    nan_inf_cases = [
        ("1.20 NaN ATR", "RELIANCE.NS", 2500.0, float('nan'), 100000.0, "NORMAL", None),
        ("1.21 Inf ATR", "RELIANCE.NS", 2500.0, float('inf'), 100000.0, "NORMAL", None),
        ("1.22 NaN Entry Price", "RELIANCE.NS", float('nan'), 50.0, 100000.0, "NORMAL", None),
        ("1.23 Inf Entry Price", "RELIANCE.NS", float('inf'), 50.0, 100000.0, "NORMAL", None),
        ("1.24 NaN Capital", "RELIANCE.NS", 2500.0, 50.0, float('nan'), "NORMAL", None),
        ("1.25 Inf Capital", "RELIANCE.NS", 2500.0, 50.0, float('inf'), "NORMAL", None),
        ("1.26 NaN Kelly Fraction", "RELIANCE.NS", 2500.0, 50.0, 100000.0, "NORMAL", float('nan')),
        ("1.27 Inf Kelly Fraction", "RELIANCE.NS", 2500.0, 50.0, 100000.0, "NORMAL", float('inf')),
    ]

    print("\n[SECTION 1.1] plan_position() NaN / Inf Robustness Tests")
    for name, stock, entry_price, atr, capital, regime, kelly_fraction in nan_inf_cases:
        try:
            plan = plan_position(stock, entry_price, atr, capital, regime, kelly_fraction)
            if plan is None:
                res_str = "Returned None (Graceful Rejection)"
                status = "PASS"
            else:
                if math.isnan(plan.entry_price) or math.isnan(plan.stop_loss) or math.isnan(plan.cost_to_risk):
                    res_str = f"CORRUPTED OBJECT RETURNED! Entry={plan.entry_price}, SL={plan.stop_loss}, C2R={plan.cost_to_risk}"
                    status = "FAIL"
                else:
                    res_str = f"Returned Plan: Qty={plan.quantity}, SL={plan.stop_loss}"
                    status = "PASS"
            print(f"  [{status}] {name:<60} -> {res_str}")
            results.append((name, status, res_str))
        except Exception as e:
            res_str = f"CRASHED! Exception: {type(e).__name__}: {e}"
            print(f"  [FAIL] {name:<60} -> {res_str}")
            results.append((name, "FAIL", res_str))

    # --------------------------------------------------------------------------
    # CATEGORY 2: check_trailing_stop() Stress Tests
    # --------------------------------------------------------------------------
    print("\n[SECTION 2] check_trailing_stop() Stress Tests")
    
    test_cases_trail = [
        # (name, stock, entry, current, stop, atr, adx)
        ("2.1 Normal Profit below activation (<2x ATR)", "INFY.NS", 1500.0, 1520.0, 1450.0, 25.0, 30.0),
        ("2.2 Profit activates trailing (>=2x ATR, adx >= 25)", "INFY.NS", 1500.0, 1560.0, 1450.0, 25.0, 30.0),
        ("2.3 Profit activates trailing with ADX decay (adx < 25)", "INFY.NS", 1500.0, 1560.0, 1450.0, 25.0, 20.0),
        ("2.4 Stop hit condition (current <= stop)", "INFY.NS", 1500.0, 1440.0, 1450.0, 25.0, 30.0),
        ("2.5 Price exact at stop", "INFY.NS", 1500.0, 1450.0, 1450.0, 25.0, 30.0),
        ("2.6 Zero ATR", "INFY.NS", 1500.0, 1550.0, 1450.0, 0.0, 30.0),
        ("2.7 Negative ATR", "INFY.NS", 1500.0, 1550.0, 1450.0, -10.0, 30.0),
        ("2.8 Current price negative", "INFY.NS", 1500.0, -50.0, 1450.0, 25.0, 30.0),
        ("2.9 Current stop higher than current price", "INFY.NS", 1500.0, 1520.0, 1530.0, 25.0, 30.0),
        ("2.10 Massive gap up profit (+10x ATR)", "INFY.NS", 1500.0, 1750.0, 1450.0, 25.0, 35.0),
        ("2.11 ADX is None", "INFY.NS", 1500.0, 1560.0, 1450.0, 25.0, None),
        ("2.12 NaN current price", "INFY.NS", 1500.0, float('nan'), 1450.0, 25.0, 30.0),
        ("2.13 NaN ATR", "INFY.NS", 1500.0, 1560.0, 1450.0, float('nan'), 30.0),
        ("2.14 Inf current price", "INFY.NS", 1500.0, float('inf'), 1450.0, 25.0, 30.0),
    ]

    for name, stock, entry, current, stop, atr, adx in test_cases_trail:
        try:
            upd = check_trailing_stop(stock, entry, current, stop, atr, adx)
            res_str = f"Update: close={upd.should_close}, update={upd.should_update}, old_stop={upd.old_stop}, new_stop={upd.new_stop}, pnl={upd.unrealized_pnl:.2f}"
            print(f"  [PASS] {name:<60} -> {res_str}")
            results.append((name, "PASS", res_str))
        except Exception as e:
            res_str = f"CRASHED! Exception: {type(e).__name__}: {e}"
            print(f"  [FAIL] {name:<60} -> {res_str}")
            results.append((name, "FAIL", res_str))

    # --------------------------------------------------------------------------
    # CATEGORY 3: compute_kelly() Stress Tests
    # --------------------------------------------------------------------------
    print("\n[SECTION 3] compute_kelly() Stress Tests")
    
    test_cases_kelly = [
        ("3.1 Empty PnL list", []),
        ("3.2 Small sample (<20 trades, all wins)", [100.0] * 10),
        ("3.3 Small sample (<20 trades, mixed)", [100.0, -50.0, 200.0, -30.0]),
        ("3.4 20 trades, positive edge (60% win, 1.5 RRR)", [150.0 if i % 10 < 6 else -100.0 for i in range(20)]),
        ("3.5 20 trades, negative edge (30% win, 1.0 RRR)", [100.0 if i % 10 < 3 else -100.0 for i in range(20)]),
        ("3.6 All 0.0 PnL trades (20 trades)", [0.0] * 20),
        ("3.7 Extreme positive PnL values", [1e9] * 25),
        ("3.8 Extreme negative PnL values", [-1e9] * 25),
        ("3.9 Single large win with many small losses", [10000.0] + [-10.0] * 24),
        ("3.10 NaN in PnL list", [100.0, float('nan'), -50.0] * 10),
        ("3.11 Inf in PnL list", [100.0, float('inf'), -50.0] * 10),
    ]

    for name, pnl_list in test_cases_kelly:
        try:
            kr = compute_kelly(pnl_list)
            res_str = f"KellyResult: edge={kr.has_edge}, fraction={kr.fraction:.4f}, full_k={kr.full_kelly:.4f}, half_k={kr.half_kelly:.4f}, note='{kr.note}'"
            print(f"  [PASS] {name:<60} -> {res_str}")
            results.append((name, "PASS", res_str))
        except Exception as e:
            res_str = f"CRASHED! Exception: {type(e).__name__}: {e}"
            print(f"  [FAIL] {name:<60} -> {res_str}")
            results.append((name, "FAIL", res_str))

    # --------------------------------------------------------------------------
    # CATEGORY 4: calculate_realistic_charges() Stress Tests
    # --------------------------------------------------------------------------
    print("\n[SECTION 4] calculate_realistic_charges() Stress Tests")
    
    test_cases_charges = [
        ("4.1 Normal swing trade (100 shares @ 500, exit @ 550)", 500.0, 550.0, 100, False),
        ("4.2 Normal intraday trade (100 shares @ 500, exit @ 550)", 500.0, 550.0, 100, True),
        ("4.3 Zero quantity", 500.0, 550.0, 0, False),
        ("4.4 Negative quantity (-50)", 500.0, 550.0, -50, False),
        ("4.5 Zero entry price", 0.0, 550.0, 100, False),
        ("4.6 Zero exit price", 500.0, 0.0, 100, False),
        ("4.7 Negative entry price", -500.0, 550.0, 100, False),
        ("4.8 Extreme high price (1e8)", 1e8, 1.1e8, 10, False),
        ("4.9 NaN price", float('nan'), 550.0, 100, False),
        ("4.10 Inf price", float('inf'), 550.0, 100, False),
    ]

    for name, entry_p, exit_p, qty, is_intra in test_cases_charges:
        try:
            chg = calculate_realistic_charges(entry_p, exit_p, qty, is_intra)
            res_str = f"Charges: total={chg.total:.2f}, net_pnl={chg.net_pnl:.2f}, net_pnl_pct={chg.net_pnl_pct:.2f}%"
            print(f"  [PASS] {name:<60} -> {res_str}")
            results.append((name, "PASS", res_str))
        except Exception as e:
            res_str = f"CRASHED! Exception: {type(e).__name__}: {e}"
            print(f"  [FAIL] {name:<60} -> {res_str}")
            results.append((name, "FAIL", res_str))

    # --------------------------------------------------------------------------
    # CATEGORY 5: calculate_atr() Stress Tests
    # --------------------------------------------------------------------------
    print("\n[SECTION 5] calculate_atr() Stress Tests")

    # Construct sample DataFrames
    df_valid = pd.DataFrame({
        "high": [100 + i for i in range(20)],
        "low": [95 + i for i in range(20)],
        "close": [98 + i for i in range(20)]
    })
    df_short = df_valid.iloc[:5]
    df_empty = pd.DataFrame(columns=["high", "low", "close"])
    df_nans = pd.DataFrame({
        "high": [np.nan] * 20,
        "low": [np.nan] * 20,
        "close": [np.nan] * 20
    })

    test_cases_atr = [
        ("5.1 Valid DataFrame (20 rows)", df_valid),
        ("5.2 Short DataFrame (5 rows < length+1)", df_short),
        ("5.3 Empty DataFrame", df_empty),
        ("5.4 All NaN DataFrame", df_nans),
    ]

    for name, df in test_cases_atr:
        try:
            atr_val = calculate_atr(df, length=14)
            res_str = f"ATR: {atr_val}"
            print(f"  [PASS] {name:<60} -> {res_str}")
            results.append((name, "PASS", res_str))
        except Exception as e:
            res_str = f"CRASHED! Exception: {type(e).__name__}: {e}"
            print(f"  [FAIL] {name:<60} -> {res_str}")
            results.append((name, "FAIL", res_str))

    # Summary Statistics
    print("\n" + "=" * 80)
    print("STRESS TEST SUMMARY")
    print("=" * 80)
    total_tests = len(results)
    passed_tests = sum(1 for r in results if r[1] in ("PASS", "WARNING"))
    failed_tests = sum(1 for r in results if r[1] == "FAIL")

    print(f"Total Test Cases Evaluated: {total_tests}")
    print(f"Passed/Graceful Handling: {passed_tests}")
    print(f"Crashes/Exceptions:       {failed_tests}")

    if failed_tests > 0:
        print("\nCRASH SUMMARY:")
        for name, status, res in results:
            if status == "FAIL":
                print(f"  - {name}: {res}")
    print("=" * 80)

    return total_tests, passed_tests, failed_tests, results


if __name__ == "__main__":
    run_all_stress_tests()
