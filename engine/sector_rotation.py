"""
engine/sector_rotation.py — V4 Layer 2: Sector Rotation Filter
===============================================================
Sector momentum has 3-6 month persistence in Indian equity markets.
A stock in a lagging sector fights a headwind. A stock in a leading
sector gets a tailwind for free — without any prediction.

How it works:
  1. Groups the trading universe into 10 NSE sectors
  2. Computes 20-day and 60-day price momentum for each sector basket
  3. Computes sector relative strength (RS) vs Nifty 50
  4. Returns the set of sectors ranked in top half by relative strength

Usage:
    from sector_rotation import get_allowed_sectors, get_sector_for_stock, get_sector_rs_dict
    allowed = get_allowed_sectors()          # e.g. {"BANKING", "IT", "PHARMA"}
    sector = get_sector_for_stock("INFY.NS") # "IT"
    if sector not in allowed:
        skip()

Cache: 4 hours (sector momentum doesn't change intraday)
"""

import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

# ── Sector Definitions ───────────────────────────────────────────────────────
SECTOR_MAP: dict[str, list[str]] = {
    "BANKING": [
        "HDFCBANK.NS", "ICICIBANK.NS", "AXISBANK.NS", "SBIN.NS", "KOTAKBANK.NS",
        "INDUSINDBK.NS", "BANKBARODA.NS", "CANBK.NS", "PNB.NS", "IDFCFIRSTB.NS",
    ],
    "FINANCIALS": [
        "BAJFINANCE.NS", "BAJAJFINSV.NS", "CHOLAFIN.NS", "SBILIFE.NS",
        "HDFCLIFE.NS", "ICICIGI.NS", "ICICIPRULI.NS", "SBICARD.NS", "PFC.NS", "RECLTD.NS",
    ],
    "IT": [
        "TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS",
        "LTIM.NS", "OFSS.NS", "TATACOMM.NS",
    ],
    "PHARMA": [
        "SUNPHARMA.NS", "DIVISLAB.NS", "CIPLA.NS", "DRREDDY.NS", "ZYDUSLIFE.NS",
        "APOLLOHOSP.NS",
    ],
    "AUTO": [
        "MARUTI.NS", "TATAMOTORS.NS", "BAJAJ-AUTO.NS", "M&M.NS", "HEROMOTOCO.NS",
        "EICHERMOT.NS", "TVSMOTOR.NS",
    ],
    "ENERGY": [
        "RELIANCE.NS", "ONGC.NS", "BPCL.NS", "IOC.NS", "GAIL.NS",
        "ADANIPOWER.NS", "NTPC.NS", "POWERGRID.NS", "TATAPOWER.NS",
        "ADANIGREEN.NS", "ADANIENSOL.NS", "ATGL.NS",
    ],
    "METALS": [
        "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "COALINDIA.NS",
        "NMDC.NS", "JINDALSTEL.NS",
    ],
    "FMCG": [
        "ITC.NS", "HINDUNILVR.NS", "BRITANNIA.NS", "NESTLEIND.NS", "DABUR.NS",
        "MARICO.NS", "COLPAL.NS", "GODREJCP.NS", "TATACONSUM.NS", "VBL.NS",
    ],
    "INFRA": [
        "LT.NS", "DLF.NS", "LODHA.NS", "CONCOR.NS", "IRCTC.NS", "HAL.NS",
        "BEL.NS", "BHEL.NS", "SIEMENS.NS",
    ],
    "COMMODITIES": [
        "ADANIENT.NS", "ADANIPORTS.NS", "AMBUJACEM.NS", "ACC.NS", "GRASIM.NS",
        "ULTRACEMCO.NS", "SHREECEM.NS", "PIDILITIND.NS", "SRF.NS",
    ],
}

# Reverse map: stock → sector
_STOCK_TO_SECTOR: dict[str, str] = {
    stock: sector
    for sector, stocks in SECTOR_MAP.items()
    for stock in stocks
}

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL_SECONDS = 4 * 3600  # 4 hours


@dataclass
class SectorScore:
    name: str
    momentum_20d: float   # % return over 20 days
    momentum_60d: float   # % return over 60 days
    composite: float      # blended momentum score
    rank: int             # 1 = best momentum
    # ── New Relative Strength & Outperformance Fields (M2 Refactoring) ────────
    rs_20d: float = 0.0       # Sector 20d return minus Nifty 20d return
    rs_60d: float = 0.0       # Sector 60d return minus Nifty 60d return
    composite_rs: float = 0.0 # 0.60 * rs_20d + 0.40 * rs_60d
    outperforming: bool = True # True if composite_rs > 0.0
    rs_rank: int = 1          # Rank based on composite_rs


def get_sector_for_stock(symbol: str) -> Optional[str]:
    """Returns the sector name for a given stock ticker, or None if not mapped."""
    return _STOCK_TO_SECTOR.get(symbol)


def _get_nifty_sector_returns() -> dict:
    """Fetch Nifty 50 20d and 60d benchmark returns."""
    import yfinance as yf
    result = {"ret_20d": 0.0, "ret_60d": 0.0}
    try:
        df = yf.download("^NSEI", period="90d", interval="1d", progress=False, auto_adjust=True)
        if df is not None and len(df) >= 65:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
            else:
                df.columns = [c.lower() for c in df.columns]
            close = df["close"].astype(float).dropna()
            result["ret_20d"] = float((close.iloc[-1] / close.iloc[-21] - 1) * 100.0)
            result["ret_60d"] = float((close.iloc[-1] / close.iloc[-61] - 1) * 100.0)
    except Exception as e:
        print(f"  [WARN] [sector] Nifty benchmark fetch failed: {e}")
    return result


def _compute_sector_momentum(sector: str, tickers: list[str], nifty_ret: dict) -> SectorScore:
    """Compute equal-weight average momentum and relative strength for a sector basket."""
    import yfinance as yf

    mom_20 = []
    mom_60 = []

    try:
        df_all = yf.download(
            tickers, period="90d", interval="1d",
            progress=False, group_by="ticker", auto_adjust=True
        )

        for ticker in tickers:
            try:
                df = df_all[ticker].copy() if ticker in df_all else None
                if df is None or len(df) < 65:
                    continue

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
                else:
                    df.columns = [c.lower() for c in df.columns]

                close = df["close"].astype(float).dropna()

                if len(close) >= 21:
                    m20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100.0
                    mom_20.append(m20)
                if len(close) >= 61:
                    m60 = (close.iloc[-1] / close.iloc[-61] - 1) * 100.0
                    mom_60.append(m60)
            except Exception:
                continue

    except Exception as e:
        print(f"  [WARN] [sector] Batch fetch failed for {sector}: {e}")

    avg_20 = sum(mom_20) / len(mom_20) if mom_20 else 0.0
    avg_60 = sum(mom_60) / len(mom_60) if mom_60 else 0.0
    composite = 0.60 * avg_20 + 0.40 * avg_60

    rs_20d = avg_20 - nifty_ret["ret_20d"]
    rs_60d = avg_60 - nifty_ret["ret_60d"]
    composite_rs = 0.60 * rs_20d + 0.40 * rs_60d
    outperforming = composite_rs > 0.0

    return SectorScore(
        name=sector,
        momentum_20d=avg_20,
        momentum_60d=avg_60,
        composite=composite,
        rank=0,
        rs_20d=rs_20d,
        rs_60d=rs_60d,
        composite_rs=composite_rs,
        outperforming=outperforming,
        rs_rank=0,
    )


def get_sector_scores(use_cache: bool = True) -> list[SectorScore]:
    """
    Returns all sectors ranked by composite relative strength (best composite_rs first).
    Results are cached for 4 hours.
    """
    global _cache
    cache_key = "sector_scores"

    if use_cache and cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < CACHE_TTL_SECONDS:
            print("  [INFO] [sector] Using cached sector scores")
            return cached

    print(f"  [INFO] [sector] Computing momentum & relative strength for {len(SECTOR_MAP)} sectors...")

    nifty_ret = _get_nifty_sector_returns()

    scores = []
    for sector, tickers in SECTOR_MAP.items():
        s = _compute_sector_momentum(sector, tickers, nifty_ret)
        scores.append(s)

    # Sort by composite_rs descending
    scores.sort(key=lambda x: x.composite_rs, reverse=True)
    for i, s in enumerate(scores):
        s.rs_rank = i + 1
        s.rank = i + 1  # sync rank with rs_rank

    if use_cache:
        _cache[cache_key] = (time.time(), scores)

    for s in scores:
        indicator = "[PASS]" if s.rs_rank <= len(scores) // 2 else "[FAIL]"
        print(f"  {indicator} [{s.rs_rank}] {s.name}: 20d={s.momentum_20d:+.1f}% 60d={s.momentum_60d:+.1f}% comp_rs={s.composite_rs:+.1f}% (outperforming={s.outperforming})")

    return scores


def get_allowed_sectors(
    top_n_fraction: float = 0.5,
    require_outperformance: bool = False,
    use_cache: bool = True,
) -> set[str]:
    """
    Returns the set of allowed sectors (e.g. top half by relative strength).

    Parameters
    ----------
    top_n_fraction        : fraction of sectors to allow (0.5 = top half)
    require_outperformance: if True, also requires sector composite_rs > 0.0
    use_cache             : reuse computed scores from cache
    """
    scores = get_sector_scores(use_cache=use_cache)
    cutoff = max(1, int(len(scores) * top_n_fraction))
    candidates = scores[:cutoff]

    if require_outperformance:
        allowed = {s.name for s in candidates if s.outperforming}
        # Guarantee at least 1 sector if all are underperforming
        if not allowed and candidates:
            allowed = {candidates[0].name}
    else:
        allowed = {s.name for s in candidates}

    print(f"  [sector] Allowed sectors ({len(allowed)}/{len(scores)}): {', '.join(sorted(allowed))}")
    return allowed


def get_sector_rs_dict(use_cache: bool = True) -> dict[str, float]:
    """Returns mapping of sector name -> composite_rs for ranker consumption."""
    scores = get_sector_scores(use_cache=use_cache)
    return {s.name: s.composite_rs for s in scores}
