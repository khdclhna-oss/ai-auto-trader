"""
engine/sector_rotation.py — V4 Layer 2: Sector Rotation Filter
===============================================================
Sector momentum has 3-6 month persistence in Indian equity markets.
A stock in a lagging sector fights a headwind. A stock in a leading
sector gets a tailwind for free — without any prediction.

How it works:
  1. Groups the trading universe into 8 NSE sectors
  2. Computes 20-day and 60-day price momentum for each sector basket
  3. Blends momentum scores (60% short + 40% medium term)
  4. Returns the set of sectors ranked in top half — only these get trades

Usage:
    from sector_rotation import get_allowed_sectors, get_sector_for_stock
    allowed = get_allowed_sectors()          # e.g. {"BANKING", "IT", "PHARMA"}
    sector = get_sector_for_stock("INFY.NS") # "IT"
    if sector not in allowed:
        skip()

Cache: 4 hours (sector momentum doesn't change intraday)
"""

import time
from dataclasses import dataclass, field
from typing import Optional

# ── Sector Definitions ───────────────────────────────────────────────────────
# Each sector is represented by its most liquid constituent stocks
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
    composite: float      # blended score
    rank: int             # 1 = best


def get_sector_for_stock(symbol: str) -> Optional[str]:
    """Returns the sector name for a given stock ticker, or None if not mapped."""
    return _STOCK_TO_SECTOR.get(symbol)


def _compute_sector_momentum(sector: str, tickers: list[str]) -> SectorScore:
    """Compute equal-weight average momentum for a sector basket."""
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
                df.columns = [c.lower() for c in df.columns]
                close = df["close"].dropna()

                if len(close) >= 21:
                    m20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100
                    mom_20.append(m20)
                if len(close) >= 61:
                    m60 = (close.iloc[-1] / close.iloc[-61] - 1) * 100
                    mom_60.append(m60)
            except Exception:
                continue

    except Exception as e:
        print(f"  ⚠ [sector] Batch fetch failed for {sector}: {e}")

    avg_20 = sum(mom_20) / len(mom_20) if mom_20 else 0.0
    avg_60 = sum(mom_60) / len(mom_60) if mom_60 else 0.0
    composite = 0.60 * avg_20 + 0.40 * avg_60  # weight shorter-term more

    return SectorScore(
        name=sector,
        momentum_20d=avg_20,
        momentum_60d=avg_60,
        composite=composite,
        rank=0,  # filled in after sorting
    )


def get_sector_scores(use_cache: bool = True) -> list[SectorScore]:
    """
    Returns all sectors ranked by composite momentum score (best first).
    Results are cached for 4 hours.
    """
    global _cache
    cache_key = "sector_scores"

    if use_cache and cache_key in _cache:
        ts, cached = _cache[cache_key]
        if time.time() - ts < CACHE_TTL_SECONDS:
            print("  📋 [sector] Using cached sector scores")
            return cached

    print(f"  📊 [sector] Computing momentum for {len(SECTOR_MAP)} sectors...")

    scores = []
    for sector, tickers in SECTOR_MAP.items():
        s = _compute_sector_momentum(sector, tickers)
        scores.append(s)

    # Rank (1 = highest momentum)
    scores.sort(key=lambda x: x.composite, reverse=True)
    for i, s in enumerate(scores):
        s.rank = i + 1

    if use_cache:
        _cache[cache_key] = (time.time(), scores)

    for s in scores:
        indicator = "✅" if s.rank <= len(scores) // 2 else "❌"
        print(f"  {indicator} [{s.rank}] {s.name}: 20d={s.momentum_20d:+.1f}% 60d={s.momentum_60d:+.1f}% composite={s.composite:+.1f}%")

    return scores


def get_allowed_sectors(top_n_fraction: float = 0.5, use_cache: bool = True) -> set[str]:
    """
    Returns the set of sectors in the top half by composite momentum.

    Parameters
    ----------
    top_n_fraction : fraction of sectors to allow (0.5 = top half)
    use_cache      : reuse computed scores from cache

    Returns
    -------
    set of sector names e.g. {"BANKING", "IT", "PHARMA"}
    """
    scores = get_sector_scores(use_cache=use_cache)
    cutoff = max(1, int(len(scores) * top_n_fraction))
    allowed = {s.name for s in scores[:cutoff]}
    print(f"  📊 [sector] Allowed sectors ({cutoff}/{len(scores)}): {', '.join(sorted(allowed))}")
    return allowed
