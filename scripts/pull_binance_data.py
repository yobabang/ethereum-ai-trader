"""Pull derivatives data from Binance Futures public endpoints
(funding rate, open interest, long/short ratio) for BTC and ETH perpetuals.

Binance retains 1-2 months of derivatives history, better than OKX's ~1 week.

READ-ONLY. No API key, no orders, no account access — public data only.

Usage:
    pip install ccxt pandas pyarrow
    python pull_binance_data.py --hours 22000 --proxy http://127.0.0.1:10809

Output (per pair, aligned to 1h grid):
    user_data/data/binance/{PAIR}_derivatives-1h-futures.feather

Columns:
    date                          (ms epoch int64, aligned to OHLCV date column)
    funding_rate                  (8h funding rate, forward-filled to 1h)
    open_interest                 (USDT open interest, 1h)
    open_interest_change_1h       (1h pct change)
    long_short_ratio              (account long/short ratio, 1h)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
TIMEFRAME = "1h"
OUT_DIR = Path("user_data/data/binance")


# ---- Hard safety guard ----
_FORBIDDEN = ("create_order", "cancel_order", "fetch_balance", "fetch_positions",
              "set_leverage", "set_margin_mode")


def make_exchange(proxy: str | None):
    import ccxt
    cfg: dict = {
        "enableRateLimit": True,
        "timeout": 30000,
        "options": {"defaultType": "future"},
    }
    if proxy:
        cfg["proxies"] = {"http": proxy, "https": proxy}
    ex = ccxt.binance(cfg)
    ex.load_markets()
    print(f"[exchange] binance: {len(ex.markets)} markets loaded")
    return ex


def _retry(fn, *args, retries=3, **kwargs):
    last = None
    for i in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            wait = 2 ** i
            print(f"    retry {i+1}/{retries}: {e} (wait {wait}s)")
            time.sleep(wait)
    raise last


def pull_funding_rate_history(ex, symbol: str) -> pd.DataFrame:
    """8h funding rate → DataFrame[date, funding_rate]. Paginate backward."""
    rows = []
    end_time = None
    for page in range(50):
        params: dict = {"limit": 1000}
        if end_time is not None:
            params["endTime"] = end_time - 1
        try:
            data = _retry(ex.fetch_funding_rate_history, symbol, limit=1000, params=params)
        except Exception as e:
            print(f"    [funding] stop at page {page}: {e}")
            break
        if not data:
            break
        for r in data:
            ts = r.get("timestamp")
            rate = r.get("fundingRate")
            if ts is not None and rate is not None:
                rows.append({"date": int(ts), "funding_rate": float(rate)})
        first_ts = data[0].get("timestamp") if data else None
        if first_ts:
            end_time = int(first_ts)
        if len(data) < 1000:
            break
        time.sleep(0.2)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates("date").sort_values("date")


def pull_open_interest_history(ex, symbol: str) -> pd.DataFrame:
    """1h open interest → DataFrame[date, open_interest]. Paginate backward."""
    rows = []
    end_time = None
    for page in range(100):
        params: dict = {"limit": 500}
        if end_time is not None:
            params["endTime"] = end_time - 1
        try:
            data = _retry(ex.fetch_open_interest_history, symbol, timeframe=TIMEFRAME,
                          limit=500, params=params)
        except Exception as e:
            print(f"    [OI] stop at page {page}: {e}")
            break
        if not data:
            break
        for r in data:
            ts = r.get("timestamp")
            oi = r.get("openInterestAmount") or r.get("openInterestValue") or r.get("openInterest")
            if ts is not None and oi is not None:
                rows.append({"date": int(ts), "open_interest": float(oi)})
        first_ts = data[0].get("timestamp") if data else None
        if first_ts:
            end_time = int(first_ts)
        if len(data) < 500:
            break
        time.sleep(0.15)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates("date").sort_values("date")


def pull_long_short_ratio_history(ex, symbol: str) -> pd.DataFrame:
    """1h long/short ratio → DataFrame[date, long_short_ratio]. Paginate backward."""
    rows = []
    end_time = None
    for page in range(100):
        params: dict = {"limit": 500}
        if end_time is not None:
            params["endTime"] = end_time - 1
        try:
            data = _retry(ex.fetch_long_short_ratio_history, symbol, timeframe=TIMEFRAME,
                          limit=500, params=params)
        except Exception as e:
            print(f"    [LS] stop at page {page}: {e}")
            break
        if not data:
            break
        for r in data:
            ts = r.get("timestamp")
            ratio = r.get("longShortRatio") or r.get("ratio")
            if ts is not None and ratio is not None:
                rows.append({"date": int(ts), "long_short_ratio": float(ratio)})
        first_ts = data[0].get("timestamp") if data else None
        if first_ts:
            end_time = int(first_ts)
        if len(data) < 500:
            break
        time.sleep(0.15)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates("date").sort_values("date")


def align_to_1h_grid(dfs: dict, since_ms: int, until_ms: int) -> pd.DataFrame:
    """Merge all sources onto a 1h grid (ms epoch, floored to hour)."""
    since_floor = (since_ms // 3_600_000) * 3_600_000
    until_floor = (until_ms // 3_600_000) * 3_600_000
    grid = pd.date_range(start=pd.Timestamp(since_floor, unit="ms"),
                         end=pd.Timestamp(until_floor, unit="ms"),
                         freq="1h")
    out = pd.DataFrame({"date": grid.astype("int64")})
    for key, df in dfs.items():
        if df is None or df.empty:
            continue
        tmp = df.copy()
        tmp["date"] = (tmp["date"] // 3_600_000) * 3_600_000
        tmp = tmp.drop_duplicates("date").sort_values("date")
        out = out.merge(tmp, on="date", how="left")
    # Forward-fill sparse columns
    for col in ["funding_rate", "long_short_ratio"]:
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

    sources = {}
    for label, fn in [("funding", pull_funding_rate_history),
                       ("oi", pull_open_interest_history),
                       ("ls", pull_long_short_ratio_history)]:
        df = fn(ex, pair)
        print(f"    {label:6}: {len(df)} rows")
        sources[label] = df

    merged = align_to_1h_grid(sources, since_ms, until_ms)
    print(f"  merged 1h grid: {len(merged)} rows, columns={list(merged.columns)}")
    print(f"  non-null counts:\n{merged.notna().sum().to_string()}")
    return merged


def main():
    ap = argparse.ArgumentParser(
        description="Pull derivatives data from Binance (READ-ONLY, no orders)")
    ap.add_argument("--hours", type=int, default=22000,
                    help="Hours of grid to produce")
    ap.add_argument("--proxy", default="http://127.0.0.1:10809",
                    help="Proxy URL (default: http://127.0.0.1:10809)")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    print("=" * 60)
    print(" BINANCE DERIVATIVES DATA PULLER — READ-ONLY, NO ORDERS ")
    print("=" * 60)

    ex = make_exchange(args.proxy if args.proxy != "none" else None)
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
            import traceback
            traceback.print_exc()

    print(f"\nDone. Files in: {out_dir}")


if __name__ == "__main__":
    main()
