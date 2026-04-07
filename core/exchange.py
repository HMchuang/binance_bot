"""
Binance REST API wrapper.
- HMAC-SHA256 signed requests
- Exponential backoff with jitter on 5xx
- Respects 429 Retry-After header
- market_get() always uses LIVE_URL (market data not on testnet)
- Exchange info cached per session
"""
from __future__ import annotations
import hashlib
import hmac
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

from core.config import LIVE_URL, TradingConfig
from utils.logger import get_logger

if TYPE_CHECKING:
    pass

log = get_logger("exchange")


@dataclass
class LotSize:
    min_qty: float
    step_size: float
    min_notional: float


class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, config: TradingConfig):
        self._api_key    = api_key
        self._api_secret = api_secret
        self._config     = config
        self._session    = requests.Session()
        self._session.headers.update({"X-MBX-APIKEY": api_key})
        self._exchange_info_cache: dict[str, dict] = {}
        self._lot_size_cache: dict[str, LotSize]   = {}

    # ── Authentication ──────────────────────────────────────────────────────

    @staticmethod
    def _fmt_float(v: float) -> str:
        """Format a float without scientific notation (e.g. 0.00009 not 9e-05)."""
        return f"{v:.10f}".rstrip("0").rstrip(".")

    def _normalise_params(self, params: dict) -> dict:
        """Convert any float values to decimal strings so the query string
        never contains scientific notation, which Binance rejects."""
        return {k: (self._fmt_float(v) if isinstance(v, float) else v)
                for k, v in params.items()}

    def _sign(self, params: dict) -> str:
        query  = "&".join(f"{k}={v}" for k, v in params.items())
        secret = self._api_secret.encode("ascii", errors="ignore")
        return hmac.new(secret, query.encode("utf-8"), hashlib.sha256).hexdigest()

    # ── Core request ────────────────────────────────────────────────────────

    def _request(self, method: str, url: str, params: dict,
                 signed: bool = False, retries: int = 5) -> dict | None:
        params = self._normalise_params(params)
        headers = {"X-MBX-APIKEY": self._api_key} if signed or self._api_key else {}
        for attempt in range(retries):
            try:
                # Re-sign on every attempt so the timestamp is always fresh.
                send_params = dict(params)
                if signed:
                    send_params["timestamp"] = int(time.time() * 1000)
                    send_params["signature"] = self._sign(send_params)
                resp = self._session.request(method, url, params=send_params,
                                             headers=headers, timeout=10)
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", 60))
                    log.warning(f"Rate limited — sleeping {wait:.0f}s")
                    time.sleep(wait)
                    continue
                if resp.status_code == 418:
                    log.error("IP banned by Binance (418) — sleeping 120s")
                    time.sleep(120)
                    continue
                if resp.status_code >= 500:
                    wait = min(0.5 * (2 ** attempt) + random.uniform(0, 1), 60)
                    log.warning(f"Server error {resp.status_code} — retry {attempt+1}/{retries} in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                if 400 <= resp.status_code < 500:
                    # Client errors won't be fixed by retrying the same request.
                    body = resp.json() if resp.content else {}
                    log.error(f"Request error: {resp.status_code} {body.get('msg', resp.reason)} — {url}")
                    return None
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                wait = min(0.5 * (2 ** attempt), 30)
                log.warning(f"Timeout — retry {attempt+1}/{retries} in {wait:.1f}s")
                time.sleep(wait)
            except requests.exceptions.RequestException as e:
                log.error(f"Request error: {e}")
                if attempt < retries - 1:
                    time.sleep(2)
        return None

    def _market_request(self, path: str, params: dict | None = None) -> dict | None:
        """Always uses LIVE_URL — market data is not available on testnet."""
        return self._request("GET", LIVE_URL + path, params or {})

    def _api_request(self, method: str, path: str, params: dict | None = None,
                     signed: bool = False) -> dict | None:
        return self._request(method, self._config.base_url + path, params or {}, signed=signed)

    # ── Market data ─────────────────────────────────────────────────────────

    def get_ticker_price(self, symbol: str) -> float:
        d = self._market_request("/api/v3/ticker/price", {"symbol": symbol})
        return float(d["price"]) if d else 0.0

    def get_ticker_24hr(self, symbol: str) -> dict:
        d = self._market_request("/api/v3/ticker/24hr", {"symbol": symbol})
        return d or {}

    def get_klines(self, symbol: str, interval: str = "15m", limit: int = 100) -> list[float]:
        """Returns list of close prices."""
        d = self._market_request("/api/v3/klines",
                                 {"symbol": symbol, "interval": interval, "limit": limit})
        if not d:
            return []
        return [float(k[4]) for k in d]

    def get_klines_ohlcv(self, symbol: str, interval: str = "15m",
                         limit: int = 120) -> list[dict]:
        """Returns list of {o, h, l, c, v} dicts."""
        d = self._market_request("/api/v3/klines",
                                 {"symbol": symbol, "interval": interval, "limit": limit})
        if not d:
            return []
        return [{"o": float(k[1]), "h": float(k[2]),
                 "l": float(k[3]), "c": float(k[4]),
                 "v": float(k[5])} for k in d]

    def get_klines_historical(self, symbol: str, interval: str,
                              start_ms: int, end_ms: int | None = None,
                              limit: int = 1000) -> list[dict]:
        """
        Paginated historical klines for backtesting.
        Returns list of {t, o, h, l, c, v} dicts.
        """
        all_candles: list[dict] = []
        current_start = start_ms
        while True:
            params: dict = {
                "symbol":    symbol,
                "interval":  interval,
                "startTime": current_start,
                "limit":     limit,
            }
            if end_ms:
                params["endTime"] = end_ms
            d = self._market_request("/api/v3/klines", params)
            if not d:
                break
            for k in d:
                all_candles.append({
                    "t": int(k[0]),
                    "o": float(k[1]),
                    "h": float(k[2]),
                    "l": float(k[3]),
                    "c": float(k[4]),
                    "v": float(k[5]),
                })
            if len(d) < limit:
                break
            current_start = int(d[-1][0]) + 1
            if end_ms and current_start >= end_ms:
                break
            time.sleep(0.1)  # be kind to the API
        return all_candles

    def get_exchange_info(self, symbol: str) -> dict | None:
        if symbol in self._exchange_info_cache:
            return self._exchange_info_cache[symbol]
        d = self._market_request("/api/v3/exchangeInfo", {"symbol": symbol})
        if d:
            self._exchange_info_cache[symbol] = d
        return d

    def get_all_usdt_symbols(self) -> list[str]:
        """All active USDT spot trading pairs."""
        d = self._market_request("/api/v3/exchangeInfo")
        if not d:
            return []
        return [
            s["symbol"] for s in d.get("symbols", [])
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
        ]

    # ── Account ─────────────────────────────────────────────────────────────

    def get_account(self) -> dict | None:
        return self._api_request("GET", "/api/v3/account", signed=True)

    def get_balance(self, asset: str) -> float:
        acc = self.get_account()
        if not acc:
            return 0.0
        for b in acc.get("balances", []):
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    def get_open_orders(self, symbol: str) -> list:
        d = self._api_request("GET", "/api/v3/openOrders", {"symbol": symbol}, signed=True)
        return d if isinstance(d, list) else []

    def cancel_order(self, symbol: str, order_id: int) -> dict | None:
        return self._api_request("DELETE", "/api/v3/order",
                                 {"symbol": symbol, "orderId": order_id}, signed=True)

    # ── Lot size ─────────────────────────────────────────────────────────────

    def get_lot_size(self, symbol: str) -> LotSize:
        if symbol in self._lot_size_cache:
            return self._lot_size_cache[symbol]
        info = self.get_exchange_info(symbol)
        result = LotSize(min_qty=0.0001, step_size=0.0001, min_notional=10.0)
        if info:
            for f in info["symbols"][0].get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    result.min_qty   = float(f["minQty"])
                    result.step_size = float(f["stepSize"])
                if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                    result.min_notional = float(f.get("minNotional", f.get("notional", 10.0)))
        self._lot_size_cache[symbol] = result
        return result

    @staticmethod
    def round_step(qty: float, step: float) -> float:
        if step <= 0:
            return qty
        # Use fixed-point formatting to avoid scientific notation (e.g. 1e-05)
        # so that decimal precision is counted correctly for very small step sizes.
        step_str = f"{step:.10f}".rstrip("0")
        p = len(step_str.split(".")[-1]) if "." in step_str else 0
        return round(qty - (qty % step), p)

    # ── Orders ───────────────────────────────────────────────────────────────

    def place_market_order(self, symbol: str, side: str, quantity: float) -> dict | None:
        return self._api_request("POST", "/api/v3/order", {
            "symbol":   symbol,
            "side":     side.upper(),
            "type":     "MARKET",
            "quantity": quantity,
        }, signed=True)

    def place_limit_order(self, symbol: str, side: str, quantity: float,
                          price: float) -> dict | None:
        return self._api_request("POST", "/api/v3/order", {
            "symbol":      symbol,
            "side":        side.upper(),
            "type":        "LIMIT",
            "timeInForce": "GTC",
            "quantity":    quantity,
            "price":       round(price, 2),
        }, signed=True)

    def place_oco_order(self, symbol: str, side: str, quantity: float,
                        price: float, stop_price: float,
                        stop_limit_price: float) -> dict | None:
        return self._api_request("POST", "/api/v3/order/oco", {
            "symbol":         symbol,
            "side":           side.upper(),
            "quantity":       quantity,
            "price":          round(price, 2),
            "stopPrice":      round(stop_price, 2),
            "stopLimitPrice": round(stop_limit_price, 2),
            "stopLimitTimeInForce": "GTC",
        }, signed=True)
