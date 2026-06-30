"""Pull derivatives data from Coinglass API v4 (funding rate, open interest,
long/short ratio) for BTC and ETH perpetual futures.

Coinglass aggregates data across exchanges and retains 2+ years of history,
far exceeding OKX's ~1-month limit.

READ-ONLY. This script NEVER places orders, NEVER authenticates with any exchange.
It only reads public aggregated market data via Coinglass API.

Prerequisites:
    1. Register at https://www.coinglass.com
    2. Get API key from https://www.coinglass.com/zh/CryptoApi (free hobbyist tier)
    3. Set env: COINGLASS_API_KEY=your_key  OR  pass --apikey

Usage:
    pip install requests pandas pyarrow
    python pull_coinglass_data.py --apikey YOUR_KEY
    python pull_coinglass_data.py --apikey YOUR_KEY --hours 22000

Output (per pair, aligned to 1h grid, matching OKX feather naming):
    user_data/data/coinglass/{PAIR}_derivatives-1h-futures.feather

Columns:
    date                          (ms epoch int, aligned to OHLCV date column)
    funding_rate                  (OI-weighted average funding rate, 1h OHLC close)
    funding_rate_high             (max funding rate in 1h window)
    funding_rate_low              (min funding rate in 1h window)
    open_interest                 (aggregated OI in USD)
    open_interest_change_1h       (1h pct change)
    long_short_ratio              (global account long/short ratio)
    top_long_short_ratio          (top trader long/short ratio, if available)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://open-api-v4.coinglass.com"
SYMBOLS = ["BTC", "ETH"]
TIMEFRAME = "1h"
COINGLASS_INTERVAL = "h1"
OUT_DIR = Path("user_data/data/coinglass")


def _get(apikey: str, endpoint: str, params: dict, retries: int = 4) -> dict:
    """GET request with retry+backoff."""
    headers = {"CG-API-KEY": apikey, "Accept": "application/json"}
    last_err = None
    for i in range(retries):
        try:
            resp = requests.get(f"{BASE_URL}{endpoint}", headers=headers,
                                params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == "0" or "data" in data:
                return data
            # Coinglass sometimes returns code as int
            if isinstance(data.get("code"), int) and data["code"] == 0:
                return data
            last_err = RuntimeError(f"API error: {data.get('msg', data)}")
        except Exception as e:
            last_err = e
        wait = 2 ** i
        print(f"    retry {i+1}/{retries}: {last_err} (wait {wait}s)")
        time.sleep(wait)
    raise last_err  # type: ignore[misc]


def pull_funding_rate(apikey: str, symbol: str) -> pd.DataFrame:
    """Pull OI-weighted funding rate OHLC history."""
    rows = []
    endpoint = "/api/futures/fundingRate/oi-weight-ohlc-history"
    params = {"symbol": symbol, "interval": COINGLASS_INTERVAL, "limit": 1500}
    try:
        data = _get(apikey, endpoint, params)
        for item in data.get("data", []):
            ts = item.get("t")
            if ts:
                rows.append({
                    "date": int(ts),
                    "funding_rate": float(item.get("c", item.get("o", 0))),
                    "funding_rate_high": float(item.get("h", 0)),
                    "funding_rate_low": float(item.get("l", 0)),
                })
    except Exception as e:
        print(f"    [funding] failed: {e}")
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates("date").sort_values("date")


def pull_open_interest(apikey: str, symbol: str) -> pd.DataFrame:
    """Pull aggregated OI OHLC history (use close value)."""
    rows = []
    # Try aggregated OI first (sum across exchanges)
    for endpoint in ["/api/futures/openInterest/aggregated-history",
                     "/api/futures/openInterest/ohlc-history"]:
        params = {"symbol": symbol, "interval": COINGLASS_INTERVAL, "limit": 1500}
        try:
            data = _get(apikey, endpoint, params)
            for item in data.get("data", []):
                ts = item.get("t")
                # aggregated-history gives {t, o, h, l, c} or {t, v}
                oi = item.get("c") or item.get("v") or item.get("o")
                if ts and oi is not None:
                    rows.append({"date": int(ts), "open_interest": float(oi)})
            if rows:
                break
        except Exception as e:
            print(f"    [OI] {endpoint.split('/')[-1]}: {e}")
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates("date").sort_values("date")


def pull_long_short_ratio(apikey: str, symbol: str) -> dict[str, pd.DataFrame]:
    """Pull global AND top-trader long/short ratio history."""
    results = {}
    endpoints = {
        "global": "/api/futures/global-long-short-account-ratio/history",
        "top": "/api/futures/top-long-short-account-ratio/history",
    }
    for kind, endpoint in endpoints.items():
        rows = []
        params = {"symbol": symbol, "interval": COINGLASS_INTERVAL, "limit": 1500}
        try:
            data = _get(apikey, endpoint, params)
            for item in data.get("data", []):
                ts = item.get("t")
                long_r = item.get("longRatio")
                short_r = item.get("shortRatio")
                if ts and long_r is not None and short_r is not None:
                    # Store as long/short ratio (>1 = more longs)
                    short_val = float(short_r)
                    ratio = float(long_r) / short_val if short_val > 0 else 1.0
                    rows.append({"date": int(ts), "long_short_ratio": round(ratio, 6)})
        except Exception as e:
            print(f"    [LS/{kind}] failed: {e}")
        if rows:
            results[kind] = pd.DataFrame(rows).drop_duplicates("date").sort_values("date")
    return results


def align_to_1h_grid(dfs: dict, since_ms: int, until_ms: int) -> pd.DataFrame:
    """Merge all Coinglass sources onto a 1h grid (ms epoch)."""
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
        # floor source date to the hour
        tmp["date"] = (tmp["date"] // 3_600_000) * 3_600_000
        tmp = tmp.drop_duplicates("date")
        # If multiple rows per hour (e.g., OHLC), keep last
        tmp = tmp.sort_values("date").groupby("date", as_index=False).last()
        out = out.merge(tmp, on="date", how="left")
    # Forward-fill sparse data (funding rate every 8h → 1h)
    for col in ["funding_rate", "funding_rate_high", "funding_rate_low",
                "long_short_ratio", "top_long_short_ratio"]:
        if col in out.columns:
            out[col] = out[col].ffill()
    if "open_interest" in out.columns:
        out["open_interest"] = out["open_interest"].ffill()
        out["open_interest_change_1h"] = out["open_interest"].pct_change()
    return out


def pull_symbol(apikey: str, symbol: str, hours: int) -> pd.DataFrame:
    until_ms = int(time.time() * 1000)
    since_ms = until_ms - hours * 3600 * 1000
    print(f"\n[{symbol}] pulling since {pd.Timestamp(since_ms, unit='ms')} "
          f"(~{hours}h, until {pd.Timestamp(until_ms, unit='ms')})")

    print("  funding rate ...")
    funding_df = pull_funding_rate(apikey, symbol)
    print(f"    -> {len(funding_df)} rows")

    print("  open interest ...")
    oi_df = pull_open_interest(apikey, symbol)
    print(f"    -> {len(oi_df)} rows")

    print("  long/short ratio ...")
    ls_dfs = pull_long_short_ratio(apikey, symbol)

    sources = {"funding": funding_df, "oi": oi_df}
    for kind, df in ls_dfs.items():
        print(f"    LS/{kind}: {len(df)} rows")
        key = "long_short_ratio" if kind == "global" else "top_long_short_ratio"
        if not df.empty:
            df = df.rename(columns={"long_short_ratio": key})
        sources[key] = df

    merged = align_to_1h_grid(sources, since_ms, until_ms)
    print(f"  merged 1h grid: {len(merged)} rows, columns={list(merged.columns)}")
    print(f"  non-null counts:\n{merged.notna().sum().to_string()}")
    return merged


def main():
    ap = argparse.ArgumentParser(
        description="Pull derivatives data from Coinglass API v4 (READ-ONLY)")
    ap.add_argument("--apikey", default=os.getenv("COINGLASS_API_KEY"),
                    help="Coinglass API key (or set COINGLASS_API_KEY env)")
    ap.add_argument("--hours", type=int, default=22000,
                    help="Hours of grid to produce (Coinglass returns what it has)")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    if not args.apikey:
        print("ERROR: API key required. Get one at https://www.coinglass.com/zh/CryptoApi")
        print("Then: set COINGLASS_API_KEY env or pass --apikey")
        sys.exit(1)

    print("=" * 60)
    print(" COINGLASS DERIVATIVES DATA PULLER — READ-ONLY, NO ORDERS ")
    print("=" * 60)
    print(f"API: {BASE_URL}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for symbol in SYMBOLS:
        try:
            df = pull_symbol(args.apikey, symbol, args.hours)
            if df.empty:
                print(f"  [SKIP] {symbol}: no data")
                continue
            safe = symbol.replace("/", "_").replace(":", "_")
            path = out_dir / f"{safe}_USDT_USDT-derivatives-1h-futures.feather"
            df.to_feather(path)
            print(f"  [SAVED] {path}  ({len(df)} rows)")
        except Exception as e:
            print(f"  [ERROR] {symbol}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\nDone. Files in: {out_dir}")


if __name__ == "__main__":
    main()
