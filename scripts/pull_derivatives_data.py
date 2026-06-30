"""Pull derivatives (funding rate / open interest / long-short ratio / taker
volume) time-series for BTC and ETH perpetuals from OKX public endpoints.

READ-ONLY. This script NEVER places orders, NEVER authenticates, NEVER touches
any account endpoint. It only reads public market data via ccxt.

Run on a machine with internet access, then copy the resulting feather files
back to the trading machine's user_data/data/okx/ directory.

Usage:
    pip install ccxt pandas pyarrow
    python pull_derivatives_data.py                      # default: BTC+ETH, ~1500h
    python pull_derivatives_data.py --hours 4000         # more history
    python pull_derivatives_data.py --exchange binance   # fallback source
    python pull_derivatives_data.py --proxy socks5h://127.0.0.1:10808

Output (per pair, aligned to 1h grid, matching OHLCV feather naming):
    user_data/data/okx/{PAIR}_derivatives-1h-futures.feather

Columns:
    date                          (ms epoch int, aligned to OHLCV date column)
    funding_rate                  (8h funding rate, forward-filled to 1h)
    funding_rate_next             (next funding rate if available)
    open_interest                 (USDT open interest)
    open_interest_change_1h       (1h pct change)
    long_short_ratio              (account long/short ratio)
    taker_buy_sell_ratio          (taker buy / sell volume ratio)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

# ---- Hard safety guard: this script is read-only --------------------------------
_FORBIDDEN = ("create_order", "cancel_order", "fetch_balance", "fetch_positions",
              "set_leverage", "set_margin_mode", "fetch_position")
# (fetched for reference; none of these are ever called below)

PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
TIMEFRAME = "1h"
OUT_DIR = Path("user_data/data/okx")


def make_exchange(name: str, proxy: str | None):
    """Create a read-only public exchange client."""
    import ccxt
    cfg: dict = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
    if proxy:
        # ccxt accepts a 'proxies' dict or aiohttp socks; for sync requests use proxies
        cfg["proxies"] = {"http": proxy, "https": proxy}
    ex = ccxt.okx(cfg) if name == "okx" else ccxt.binance(cfg)
    ex.load_markets()
    print(f"[exchange] {name}: {len(ex.markets)} markets loaded")
    return ex


def _retry(fn, *args, retries=4, **kwargs):
    """Retry with backoff on rate-limit / transient errors."""
    last = None
    for i in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            wait = 2 ** i
            print(f"    retry {i+1}/{retries} after error: {e} (wait {wait}s)")
            time.sleep(wait)
    raise last


def pull_funding_rate_history(ex, symbol: str, since_ms: int) -> pd.DataFrame:
    """8h funding rate history → DataFrame[date, funding_rate]."""
    rows = []
    since = since_ms
    # OKX funding rate is every 8h; fetch in pages
    for _ in range(200):
        try:
            data = _retry(ex.fetch_funding_rate_history, symbol, since=since, limit=100)
        except Exception as e:
            print(f"    [funding] stop paging: {e}")
            break
        if not data:
            break
        for r in data:
            ts = r.get("timestamp")
            rate = r.get("fundingRate")
            if ts is not None and rate is not None:
                rows.append({"date": int(ts), "funding_rate": float(rate)})
                nxt = r.get("nextFundingRate")
                if nxt is not None:
                    rows[-1]["funding_rate_next"] = float(nxt)
        last_ts = data[-1].get("timestamp")
        if last_ts is None or last_ts <= since:
            break
        since = last_ts + 1
        if len(data) < 100:
            break
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.drop_duplicates("date").sort_values("date")
    return df


def _binance_paged(ex, endpoint_name: str, params: dict, since_ms: int, until_ms: int,
                   period: str = "1h"):
    """Page through a Binance fapi/data endpoint by startTime.

    Binance returns at most 500 rows per call; we roll startTime forward.
    Returns a flat list of raw row lists.
    """
    fn = getattr(ex, endpoint_name, None)
    if fn is None:
        return []
    all_rows = []
    start = since_ms
    for _ in range(400):  # safety cap
        try:
            r = _retry(fn, {**params, "period": period, "startTime": start,
                            "endTime": until_ms, "limit": 500})
        except Exception as e:
            print(f"    [{endpoint_name}] paging stop: {e}")
            break
        data = r if isinstance(r, list) else r.get("data", []) if isinstance(r, dict) else []
        if not data:
            break
        all_rows.extend(data)
        # Binance data rows: first element is open time (ms)
        last_ts = int(data[-1][0]) if data[-1] and data[-1][0] else start
        if last_ts <= start:
            break
        start = last_ts + 1
        if len(data) < 500:
            break
        time.sleep(0.2)
    return all_rows


def pull_open_interest_history(ex, symbol: str, since_ms: int) -> pd.DataFrame:
    """1h open interest history → DataFrame[date, open_interest]."""
    rows = []
    until_ms = int(time.time() * 1000)
    # Try ccxt unified method first
    if hasattr(ex, "fetch_open_interest_history"):
        try:
            data = _retry(ex.fetch_open_interest_history, symbol, timeframe=TIMEFRAME,
                          since=since_ms, limit=100)
            for r in data:
                ts = r.get("timestamp")
                oi = r.get("openInterestAmount") or r.get("openInterestValue") or r.get("openInterest")
                if ts is not None and oi is not None:
                    rows.append({"date": int(ts), "open_interest": float(oi)})
        except Exception as e:
            print(f"    [OI] unified method failed: {e}, trying raw endpoint")

    # Binance fallback (USDT-perp fapi): fapiDataGetOpenInterestHist
    if not rows and hasattr(ex, "fapiDataGetOpenInterestHist"):
        cc = ex.market(symbol)
        raw = _binance_paged(ex, "fapiDataGetOpenInterestHist",
                             {"symbol": cc["id"]}, since_ms, until_ms)
        # Binance: {symbol, sumOpenInterest, sumOpenInterestValue, timestamp}
        for row in raw:
            if isinstance(row, dict):
                ts = int(row.get("timestamp", 0))
                oi = row.get("sumOpenInterestValue") or row.get("sumOpenInterest")
                if ts and oi:
                    rows.append({"date": ts, "open_interest": float(oi)})
        print(f"    [OI] binance fapi: {len(rows)} rows")

    # OKX fallback: rubik raw endpoint
    if not rows and hasattr(ex, "publicGetRubikStatContractsOpenInterestHistory"):
        cc = ex.market(symbol)
        inst = cc.get("id", symbol)
        for page in range(100):
            try:
                r = _retry(ex.publicGetRubikStatContractsOpenInterestHistory,
                           {"instId": inst, "period": "1H", "limit": "100"})
            except Exception as e:
                print(f"    [OI] raw endpoint stop: {e}")
                break
            data = r.get("data", []) if isinstance(r, dict) else []
            if not data:
                break
            for row in data:
                ts = int(row[0]) if row[0] else None
                oi = float(row[2]) if len(row) > 2 and row[2] else (float(row[1]) if len(row) > 1 and row[1] else None)
                if ts and oi is not None:
                    rows.append({"date": ts, "open_interest": oi})
            if len(data) < 100:
                break
            time.sleep(0.3)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.drop_duplicates("date").sort_values("date")
    return df


def pull_long_short_ratio_history(ex, symbol: str, since_ms: int) -> pd.DataFrame:
    """Long/short ratio history → DataFrame[date, long_short_ratio]."""
    rows = []
    until_ms = int(time.time() * 1000)
    if hasattr(ex, "fetch_long_short_ratio_history"):
        try:
            data = _retry(ex.fetch_long_short_ratio_history, symbol, timeframe=TIMEFRAME,
                          since=since_ms, limit=100)
            for r in data:
                ts = r.get("timestamp")
                ratio = r.get("longShortRatio") or r.get("ratio")
                if ts is not None and ratio is not None:
                    rows.append({"date": int(ts), "long_short_ratio": float(ratio)})
        except Exception as e:
            print(f"    [LS] unified failed: {e}")

    # Binance fallback: fapiDataGetGlobalLongShortAccountRatio
    if not rows and hasattr(ex, "fapiDataGetGlobalLongShortAccountRatio"):
        cc = ex.market(symbol)
        raw = _binance_paged(ex, "fapiDataGetGlobalLongShortAccountRatio",
                             {"symbol": cc["id"]}, since_ms, until_ms)
        # Binance: {symbol, longShortRatio, longAccount, shortAccount, timestamp}
        for row in raw:
            if isinstance(row, dict):
                ts = int(row.get("timestamp", 0))
                ratio = row.get("longShortRatio")
                if ts and ratio:
                    rows.append({"date": ts, "long_short_ratio": float(ratio)})
        print(f"    [LS] binance fapi: {len(rows)} rows")

    if not rows and hasattr(ex, "publicGetRubikStatContractsLongShortAccountRatioContract"):
        cc = ex.market(symbol)
        inst = cc.get("id", symbol)
        for page in range(100):
            try:
                r = _retry(ex.publicGetRubikStatContractsLongShortAccountRatioContract,
                           {"instId": inst, "period": "1H", "limit": "100"})
            except Exception as e:
                print(f"    [LS] raw stop: {e}")
                break
            data = r.get("data", []) if isinstance(r, dict) else []
            if not data:
                break
            for row in data:
                ts = int(row[0]) if row[0] else None
                ratio = float(row[3]) if len(row) > 3 and row[3] else None
                if ts and ratio is not None:
                    rows.append({"date": ts, "long_short_ratio": ratio})
            if len(data) < 100:
                break
            time.sleep(0.3)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates("date").sort_values("date")


def pull_taker_volume(ex, symbol: str, since_ms: int) -> pd.DataFrame:
    """Taker buy/sell volume ratio → DataFrame[date, taker_buy_sell_ratio]."""
    rows = []
    until_ms = int(time.time() * 1000)
    # Binance: fapiDataGetTakerlongshortRatio
    if hasattr(ex, "fapiDataGetTakerlongshortRatio"):
        cc = ex.market(symbol)
        raw = _binance_paged(ex, "fapiDataGetTakerlongshortRatio",
                             {"symbol": cc["id"]}, since_ms, until_ms)
        # Binance: {buySellRatio, buyVol, sellVol, timestamp}
        for row in raw:
            if isinstance(row, dict):
                ts = int(row.get("timestamp", 0))
                ratio = row.get("buySellRatio")
                if ts and ratio:
                    rows.append({"date": ts, "taker_buy_sell_ratio": float(ratio)})
        print(f"    [taker] binance fapi: {len(rows)} rows")

    if not rows and hasattr(ex, "publicGetRubikStatTakerVolumeContract"):
        cc = ex.market(symbol)
        base = cc.get("base", symbol.split("/")[0])
        for page in range(100):
            try:
                r = _retry(ex.publicGetRubikStatTakerVolumeContract,
                           {"instId": base, "period": "1H", "limit": "100"})
            except Exception as e:
                print(f"    [taker] stop: {e}")
                break
            data = r.get("data", []) if isinstance(r, dict) else []
            if not data:
                break
            for row in data:
                ts = int(row[0]) if row[0] else None
                buy = float(row[1]) if len(row) > 1 and row[1] else 0
                sell = float(row[2]) if len(row) > 2 and row[2] else 0
                if ts and sell > 0:
                    rows.append({"date": ts, "taker_buy_sell_ratio": buy / sell})
            if len(data) < 100:
                break
            time.sleep(0.3)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates("date").sort_values("date")


def align_to_1h_grid(dfs: dict, since_ms: int, until_ms: int) -> pd.DataFrame:
    """Merge all derivatives sources onto a 1h grid (ms epoch, floored to hour)."""
    grid = pd.date_range(start=pd.Timestamp(since_ms, unit="ms"),
                         end=pd.Timestamp(until_ms, unit="ms"),
                         freq="1h")
    out = pd.DataFrame({"date": (grid.astype("int64") // 10**6).astype(int)})
    for key, df in dfs.items():
        if df is None or df.empty:
            continue
        tmp = df.copy()
        # floor each source's date to the hour for alignment
        tmp["date"] = (pd.to_datetime(tmp["date"], unit="ms").dt.floor("1h").astype("int64") // 10**6)
        tmp = tmp.drop_duplicates("date")
        out = out.merge(tmp, on="date", how="left")
    # forward-fill funding_rate / ratios (they update slower than 1h); OI keep as-is then ffill
    for col in ["funding_rate", "funding_rate_next", "long_short_ratio", "taker_buy_sell_ratio"]:
        if col in out.columns:
            out[col] = out[col].ffill()
    if "open_interest" in out.columns:
        out["open_interest"] = out["open_interest"].ffill()
        out["open_interest_change_1h"] = out["open_interest"].pct_change()
    return out


def pull_pair(ex, pair: str, hours: int) -> pd.DataFrame:
    until_ms = int(time.time() * 1000)
    since_ms = until_ms - hours * 3600 * 1000
    print(f"\n[{pair}] pulling since {pd.Timestamp(since_ms, unit='ms')} "
          f"(~{hours}h, until {pd.Timestamp(until_ms, unit='ms')})")
    print(f"  (read-only public data, NO orders, NO auth)")

    sources = {
        "funding": _retry(pull_funding_rate_history, ex, pair, since_ms, retries=3),
        "oi": _retry(pull_open_interest_history, ex, pair, since_ms, retries=3),
        "ls": _retry(pull_long_short_ratio_history, ex, pair, since_ms, retries=3),
        "taker": _retry(pull_taker_volume, ex, pair, since_ms, retries=3),
    }
    for k, df in sources.items():
        print(f"    {k:6}: {len(df) if df is not None else 0} rows")
    merged = align_to_1h_grid(sources, since_ms, until_ms)
    print(f"  merged 1h grid: {len(merged)} rows, columns={list(merged.columns)}")
    print(f"  non-null counts:\n{merged.notna().sum().to_string()}")
    return merged


def main():
    ap = argparse.ArgumentParser(description="Pull derivatives data (READ-ONLY, no orders)")
    # Binance default: its fapi/data endpoints keep full OI/long-short/taker
    # history (OKX rubik history is patchy → earlier run got ~2.5% coverage).
    ap.add_argument("--exchange", default="binance", choices=["okx", "binance"])
    ap.add_argument("--hours", type=int, default=22000,
                    help="Hours of history (22000 ≈ 2.5y, aligns with OHLCV)")
    ap.add_argument("--proxy", default=None, help="e.g. socks5h://127.0.0.1:10808")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    print("=" * 60)
    print(" DERIVATIVES DATA PULLER — READ-ONLY, NO ORDERS, NO AUTH ")
    print("=" * 60)

    ex = make_exchange(args.exchange, args.proxy)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for pair in PAIRS:
        try:
            df = pull_pair(ex, pair, args.hours)
            if df.empty:
                print(f"  [SKIP] {pair}: no data")
                continue
            safe = pair.replace("/", "_").replace(":", "_")
            path = out_dir / f"{safe}-derivatives-1h-futures.feather"
            df.to_feather(path)
            print(f"  [SAVED] {path}  ({len(df)} rows)")
        except Exception as e:
            print(f"  [ERROR] {pair}: {e}")
            import traceback; traceback.print_exc()

    print("\nDone. Copy these feather files to the trading machine:")
    print(f"  {out_dir}/*-derivatives-1h-futures.feather")


if __name__ == "__main__":
    main()
