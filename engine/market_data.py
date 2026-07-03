"""Shared market data utilities with OKX → Binance fallback.

Centralizes all market data fetching logic so sim_broker and live_trader
use the same fallback strategy when OKX is unreachable.
"""
import requests
import pandas as pd
from typing import Optional, Dict
from datetime import datetime


# OKX endpoints
OKX_BASE = "https://www.okx.com"
OKX_TICKER_URL = f"{OKX_BASE}/api/v5/market/ticker"
OKX_CANDLES_URL = f"{OKX_BASE}/api/v5/market/candles"
OKX_FUNDING_RATE_URL = f"{OKX_BASE}/api/v5/public/funding-rate"

# Binance endpoints
BINANCE_BASE = "https://api.binance.com"
BINANCE_TICKER_URL = f"{BINANCE_BASE}/api/v3/ticker/price"
BINANCE_CANDLES_URL = f"{BINANCE_BASE}/api/v3/klines"
BINANCE_FUNDING_RATE_URL = f"{BINANCE_BASE}/fapi/v1/fundingRate"

# Symbol mapping (trading pair → exchange symbol)
OKX_SYMBOLS = {
    "BTC/USDT:USDT": "BTC-USDT-SWAP",
    "ETH/USDT:USDT": "ETH-USDT-SWAP"
}
BINANCE_SYMBOLS = {
    "BTC/USDT:USDT": "BTCUSDT",
    "ETH/USDT:USDT": "ETHUSDT"
}


def get_ticker(pair: str) -> Optional[Dict]:
    """Get latest ticker from OKX, fallback to Binance if OKX fails.

    Args:
        pair: Trading pair (e.g., "BTC/USDT:USDT")

    Returns:
        Ticker dict or None if both exchanges fail
    """
    # Try OKX first
    try:
        okx_symbol = OKX_SYMBOLS.get(pair)
        if not okx_symbol:
            raise ValueError(f"Unknown pair: {pair}")

        resp = requests.get(
            OKX_TICKER_URL,
            params={"instId": okx_symbol},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") == "0" and data.get("data"):
            ticker_data = data["data"][0]
            return {
                "symbol": pair,
                "price": float(ticker_data["last"]),
                "timestamp": datetime.fromtimestamp(int(ticker_data["ts"]) / 1000).isoformat(),
                "source": "okx"
            }
    except Exception as e:
        print(f"[market_data] OKX ticker failed: {e}, trying Binance...")

    # Fallback to Binance
    try:
        binance_symbol = BINANCE_SYMBOLS.get(pair)
        if not binance_symbol:
            raise ValueError(f"Unknown pair: {pair}")

        resp = requests.get(
            BINANCE_TICKER_URL,
            params={"symbol": binance_symbol},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "symbol": pair,
            "price": float(data["price"]),
            "timestamp": datetime.utcnow().isoformat(),
            "source": "binance"
        }
    except Exception as e:
        print(f"[market_data] Binance ticker failed: {e}")
        return None


def get_ohlcv(
    pair: str,
    timeframe: str = "1H",
    limit: int = 100
) -> Optional[pd.DataFrame]:
    """Get OHLCV candles from OKX, fallback to Binance if OKX fails.

    Args:
        pair: Trading pair (e.g., "BTC/USDT:USDT")
        timeframe: Candle timeframe (e.g., "1H", "4H", "1D")
        limit: Number of candles to fetch

    Returns:
        DataFrame with OHLCV data or None if both exchanges fail
    """
    # Try OKX first
    try:
        okx_symbol = OKX_SYMBOLS.get(pair)
        if not okx_symbol:
            raise ValueError(f"Unknown pair: {pair}")

        resp = requests.get(
            OKX_CANDLES_URL,
            params={
                "instId": okx_symbol,
                "bar": timeframe,
                "limit": limit
            },
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") == "0" and data.get("data"):
            df = pd.DataFrame(data["data"], columns=[
                "timestamp", "open", "high", "low", "close",
                "vol", "volCcy", "volCcyQuote", "confirm"
            ])
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
            for col in ["open", "high", "low", "close", "vol"]:
                df[col] = pd.to_numeric(df[col])
            df = df.rename(columns={"timestamp": "datetime", "vol": "volume"})
            df = df[["datetime", "open", "high", "low", "close", "volume"]]
            df["source"] = "okx"
            return df.sort_values("datetime").reset_index(drop=True)
    except Exception as e:
        print(f"[market_data] OKX OHLCV failed: {e}, trying Binance...")

    # Fallback to Binance
    try:
        binance_symbol = BINANCE_SYMBOLS.get(pair)
        if not binance_symbol:
            raise ValueError(f"Unknown pair: {pair}")

        # Map timeframe to Binance format
        timeframe_map = {
            "1H": "1h",
            "4H": "4h",
            "1D": "1d"
        }
        binance_timeframe = timeframe_map.get(timeframe, timeframe.lower())

        resp = requests.get(
            BINANCE_CANDLES_URL,
            params={
                "symbol": binance_symbol,
                "interval": binance_timeframe,
                "limit": limit
            },
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()

        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ])
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])
        df = df[["datetime", "open", "high", "low", "close", "volume"]]
        df["source"] = "binance"
        return df.sort_values("datetime").reset_index(drop=True)
    except Exception as e:
        print(f"[market_data] Binance OHLCV failed: {e}")
        return None


def get_funding_rate(pair: str) -> Optional[float]:
    """Get current funding rate from OKX, fallback to Binance if OKX fails.

    Args:
        pair: Trading pair (e.g., "BTC/USDT:USDT")

    Returns:
        Funding rate as float or None if both exchanges fail
    """
    # Try OKX first
    try:
        okx_symbol = OKX_SYMBOLS.get(pair)
        if not okx_symbol:
            raise ValueError(f"Unknown pair: {pair}")

        resp = requests.get(
            OKX_FUNDING_RATE_URL,
            params={"instId": okx_symbol},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") == "0" and data.get("data"):
            return float(data["data"][0]["fundingRate"])
    except Exception as e:
        print(f"[market_data] OKX funding rate failed: {e}, trying Binance...")

    # Fallback to Binance
    try:
        binance_symbol = BINANCE_SYMBOLS.get(pair)
        if not binance_symbol:
            raise ValueError(f"Unknown pair: {pair}")

        resp = requests.get(
            BINANCE_FUNDING_RATE_URL,
            params={"symbol": binance_symbol},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()

        if data:
            return float(data[0]["fundingRate"])
    except Exception as e:
        print(f"[market_data] Binance funding rate failed: {e}")

    return None
