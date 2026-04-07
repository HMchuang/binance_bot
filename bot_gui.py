#!/usr/bin/env python3
"""
Binance Automated Trading Bot — GUI Edition
Uses real Binance market data. Three modes: Simulator / Testnet / Live.
Binance Spot fee: 0.1% per order.
Dependencies: pip install requests numpy python-dotenv matplotlib
Run: python3 bot_gui.py
"""

import os, time, hmac, hashlib, logging, threading, queue, json
from datetime import datetime, timedelta
from dotenv import load_dotenv

import numpy as np
import requests
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec
import matplotlib.dates as mdates
import matplotlib.ticker as mticker

matplotlib.rcParams.update({
    "font.size":       11,
    "axes.titlesize":  12,
    "axes.labelsize":  11,
    "xtick.labelsize": 11,
    "ytick.labelsize": 11,
    "legend.fontsize": 10,
    "font.family":     "sans-serif",
})

load_dotenv()

# ── shared sentiment between GUI and bot thread ─────────
_current_sentiment: float = 55.0   # updated by both GUI refresh and bot loop

# ── saved config file (API keys, last mode) ─────────────
_CFG_FILE = os.path.join(os.path.dirname(__file__), "saved_config.json")

def _load_saved_config():
    try:
        with open(_CFG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_config_file(data: dict):
    try:
        with open(_CFG_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        pass

_saved = _load_saved_config()

# ═══════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════
CONFIG = {
    "api_key":         _saved.get("api_key",    os.getenv("BINANCE_API_KEY",    "")),
    "api_secret":      _saved.get("api_secret", os.getenv("BINANCE_API_SECRET", "")),
    "testnet":         True,
    "sim_mode":        True,
    "symbols":         ["BTCUSDT", "ETHUSDT"],
    "strategy":        "buffett",
    "order_pct":       0.20,
    "fee_rate":        float(_saved.get("sim_fee_pct", 0.1)) / 100,
    "buy_win_thresh":  60,   # lowered: 75 was unreachable in normal markets
    "sell_win_thresh": 35,
    "take_profit_pct": 0.10,
    "stop_loss_pct":   0.05,
    "loop_interval":   1,
    "log_file":        "bot.log",
}

TESTNET_URL = "https://testnet.binance.vision"
LIVE_URL    = "https://api.binance.com"

entry_prices: dict = {}
log_queue:     queue.Queue = queue.Queue()
scanner_queue: queue.Queue = queue.Queue()   # per-symbol rows pushed immediately

SIM_PRINCIPAL = float(_saved.get("principal", 10000.0))

trade_stats = {
    "principal":   SIM_PRINCIPAL,
    "trade_count": 0,
    "wins":        0,
    "total_fees":  0.0,
    "peak_value":  SIM_PRINCIPAL,
}

sim_portfolio: dict = {
    "USDT": SIM_PRINCIPAL,
    "BTC":  0.0,
    "ETH":  0.0,
}

def reset_sim():
    sim_portfolio["USDT"] = trade_stats["principal"]
    for k in list(sim_portfolio.keys()):
        if k != "USDT":
            sim_portfolio[k] = 0.0
    trade_stats["trade_count"] = 0
    trade_stats["wins"]        = 0
    trade_stats["total_fees"]  = 0.0
    trade_stats["peak_value"]  = trade_stats["principal"]
    entry_prices.clear()
    log.info(f"=== Simulator reset — principal ${trade_stats['principal']:,.0f} ===")

# ═══════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════
class QueueHandler(logging.Handler):
    def __init__(self, q): super().__init__(); self.q = q
    def emit(self, record): self.q.put(self.format(record))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(CONFIG["log_file"], encoding="utf-8")]
)
log = logging.getLogger("BinanceBot")
log.addHandler(QueueHandler(log_queue))

# ═══════════════════════════════════════════════════════
# API HELPERS
# ═══════════════════════════════════════════════════════
def base_url():
    return TESTNET_URL if CONFIG["testnet"] else LIVE_URL

def _safe_key():
    return CONFIG["api_key"].encode("ascii", errors="ignore").decode("ascii")

def _auth_headers():
    return {"X-MBX-APIKEY": _safe_key()}

def _sign(params):
    q      = "&".join(f"{k}={v}" for k, v in params.items())
    secret = CONFIG["api_secret"].encode("ascii", errors="ignore")
    return hmac.new(secret, q.encode("utf-8"), hashlib.sha256).hexdigest()

def api_get(path, params=None, signed=False):
    params = params or {}
    if signed:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = _sign(params)
    headers = _auth_headers() if signed else {}
    try:
        r = requests.get(base_url() + path, params=params,
                         headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"GET {path}: {e}")
        return None

def market_get(path, params=None):
    """Always fetch from live Binance (market data is not on testnet)."""
    params = params or {}
    try:
        r = requests.get(LIVE_URL + path, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"market GET {path}: {e}")
        return None

def api_post(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    try:
        r = requests.post(base_url() + path, params=params,
                          headers=_auth_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"POST {path}: {e}")
        return None

# ═══════════════════════════════════════════════════════
# MARKET DATA
# ═══════════════════════════════════════════════════════
def get_account():
    return api_get("/api/v3/account", signed=True)

def get_balance(asset):
    if CONFIG["sim_mode"]:
        return sim_portfolio.get(asset, 0.0)
    acc = get_account()
    if not acc: return 0.0
    for b in acc.get("balances", []):
        if b["asset"] == asset:
            return float(b["free"])
    return 0.0

def get_symbol_price(symbol):
    d = market_get("/api/v3/ticker/price", {"symbol": symbol})
    return float(d["price"]) if d else 0.0

def get_klines(symbol, interval="15m", limit=100):
    d = market_get("/api/v3/klines",
                   {"symbol": symbol, "interval": interval, "limit": limit})
    if not d: return []
    return [float(k[4]) for k in d]

def get_klines_ohlcv(symbol, interval="15m", limit=120):
    d = market_get("/api/v3/klines",
                   {"symbol": symbol, "interval": interval, "limit": limit})
    if not d: return []
    return [{"o": float(k[1]), "h": float(k[2]),
             "l": float(k[3]), "c": float(k[4]),
             "v": float(k[5])} for k in d]

# ═══════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════
def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    d  = np.diff(closes)
    g  = np.where(d > 0, d, 0)
    l  = np.where(d < 0, -d, 0)
    ag = np.mean(g[-period:])
    al = np.mean(l[-period:])
    if al == 0: return 100.0
    return round(100 - 100 / (1 + ag / al), 2)

def calc_rsi_series(closes, period=14):
    result = [None] * len(closes)
    if len(closes) < period + 1: return result
    for i in range(period, len(closes)):
        sl = closes[i - period: i + 1]
        d  = np.diff(sl)
        g  = np.where(d > 0, d, 0)
        l  = np.where(d < 0, -d, 0)
        ag = np.mean(g); al = np.mean(l)
        result[i] = 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 2)
    return result

def ema_series(data, n):
    k      = 2 / (n + 1)
    result = []
    for v in data:
        if v is None:
            result.append(None)
        elif not result or result[-1] is None:
            result.append(v)
        else:
            result.append(v * k + result[-1] * (1 - k))
    return result

def calc_macd_series(closes):
    e12    = ema_series(closes, 12)
    e26    = ema_series(closes, 26)
    macd   = [a - b if a is not None and b is not None else None
              for a, b in zip(e12, e26)]
    signal = ema_series(macd, 9)
    hist   = [a - b if a is not None and b is not None else None
              for a, b in zip(macd, signal)]
    return macd, signal, hist

def calc_ma(closes, n):
    if len(closes) < n: return closes[-1] if closes else 0.0
    return round(float(np.mean(closes[-n:])), 2)

def calc_ma_series(closes, n):
    return [None if i < n - 1
            else round(float(np.mean(closes[i - n + 1: i + 1])), 2)
            for i in range(len(closes))]

def calc_bb_series(closes, n=20, mult=2.0):
    upper, lower = [], []
    for i in range(len(closes)):
        if i < n - 1:
            upper.append(None); lower.append(None)
        else:
            w = np.array(closes[i - n + 1: i + 1])
            m = float(np.mean(w)); s = float(np.std(w))
            upper.append(round(m + mult * s, 2))
            lower.append(round(m - mult * s, 2))
    return upper, lower

def _raw_macd_hist(closes):
    if len(closes) < 26: return 0.0
    arr = np.array(closes, dtype=float)
    def ema(data, n):
        k = 2 / (n + 1); r = [data[0]]
        for v in data[1:]: r.append(v * k + r[-1] * (1 - k))
        return np.array(r)
    e12 = ema(arr, 12); e26 = ema(arr, 26)
    mac = e12 - e26;    sig = ema(mac, 9)
    return float(mac[-1] - sig[-1])

def calc_macd_current(closes):
    if len(closes) < 26: return 0.0, 0.0, 0.0
    arr = np.array(closes, dtype=float)
    def ema(data, n):
        k = 2 / (n + 1); r = [data[0]]
        for v in data[1:]: r.append(v * k + r[-1] * (1 - k))
        return np.array(r)
    e12  = ema(arr, 12); e26 = ema(arr, 26)
    macd = e12 - e26;    sig = ema(macd, 9); hist = macd - sig
    return round(float(macd[-1]), 4), round(float(sig[-1]), 4), round(float(hist[-1]), 4)

def calc_bollinger_current(closes, n=20, mult=2.0):
    if len(closes) < n: return 0.0, 0.0, 0.0
    w   = np.array(closes[-n:])
    mid = float(np.mean(w)); std = float(np.std(w))
    return round(mid + mult * std, 2), round(mid, 2), round(mid - mult * std, 2)

def calc_win_chance(closes, sentiment=55.0):
    """
    Score 0-100 estimating the probability of a profitable trade.

    Scoring breakdown (max without sentiment = 80, max with bullish = 88):
      Base                    50
      RSI < 35 (oversold)    +10  /  RSI > 65 (overbought)  -10
      Price below BB lower   +8   /  Price above BB upper    -8
      MACD hist > 0 (bull)   +5   /  MACD hist < 0 (bear)   -5
      MA20 > MA50 (uptrend)  +7   /  MA20 < MA50 (downtrend)-7
      Sentiment ≥ 70         +8   /  Sentiment ≤ 35          -8
    Clamped to [15, 90].

    IMPORTANT: pass the current market sentiment for accurate results.
    Default sentiment=55 (neutral) is only used when sentiment is unknown.
    """
    if len(closes) < 20: return 50
    rsi             = calc_rsi(closes)
    _, _, hist      = calc_macd_current(closes)
    upper, _, lower = calc_bollinger_current(closes)
    ma20  = calc_ma(closes, 20)
    ma50  = calc_ma(closes, 50)
    price = closes[-1]; score = 50

    if sentiment >= 70: score += 8
    elif sentiment <= 35: score -= 8
    if rsi < 35:    score += 10
    elif rsi > 65:  score -= 10
    if price < lower:   score += 8
    elif price > upper: score -= 8
    score += 5 if hist > 0 else -5
    score += 7 if ma20 > ma50 else -7
    return max(15, min(90, round(score)))

# ═══════════════════════════════════════════════════════
# STRATEGY
# ═══════════════════════════════════════════════════════
def get_signal(symbol, closes, holding):
    """
    Returns "buy", "sell", or "hold".

    Uses _current_sentiment (module-level, updated by GUI & bot loop)
    so that both chart display and bot loop see the same win-chance score.

    RSI BUY thresholds use < 40 (not < 35):
      - RSI 35-40 is already in the oversold/near-oversold zone
      - RSI < 35 is crash-level and almost never occurs in normal markets
      - The original RSI < 35 caused the bot to never trade in sideways markets

    TA strategy buy: RSI < 40 AND (MACD bullish OR price below BB lower)
      - Original required all three simultaneously (RSI<35 AND MACD>0 AND price<BB_lo)
      - MACD is often negative when price drops below BB lower (they conflict),
        so the triple-AND condition was nearly impossible to satisfy.
    """
    strat                = CONFIG["strategy"]
    rsi                  = calc_rsi(closes)
    _, _, hist           = calc_macd_current(closes)
    upper_bb, _, lower_bb = calc_bollinger_current(closes)
    ma20  = calc_ma(closes, 20)
    ma50  = calc_ma(closes, 50)
    price = closes[-1]

    # Use the F&G-blended sentiment maintained by the sentiment worker.
    # Do NOT overwrite it here — sentiment worker is the sole updater.
    win = calc_win_chance(closes, _current_sentiment)

    log.info(f"[{symbol}] Price={price:.2f} RSI={rsi:.1f} Win={win}% "
             f"MACD_hist={hist:.4f} BB_lo={lower_bb:.2f} BB_hi={upper_bb:.2f} "
             f"MA20={ma20:.2f} MA50={ma50:.2f} Sentiment={_current_sentiment:.0f}")

    buy_t  = CONFIG["buy_win_thresh"]
    sell_t = CONFIG["sell_win_thresh"]

    if strat == "winrate":
        if holding <= 0:
            if win >= buy_t:
                return "buy"
            log.info(f"[{symbol}] winrate: Win={win}% < buy_thresh={buy_t}% — hold")
        else:
            if win <= sell_t:
                return "sell"
            log.info(f"[{symbol}] winrate: Win={win}% > sell_thresh={sell_t}% — hold")

    elif strat == "ta":
        oversold   = rsi < 40;  overbought = rsi > 65
        macd_bull  = hist > 0;  macd_bear  = hist < 0
        below_bb   = price < lower_bb;  above_bb = price > upper_bb
        if holding <= 0:
            if oversold and (macd_bull or below_bb):
                return "buy"
            missing = []
            if not oversold:  missing.append(f"RSI={rsi:.1f}≥40")
            if not macd_bull: missing.append(f"MACD={hist:.4f}≤0")
            if not below_bb:  missing.append(f"price above BB_lo={lower_bb:.2f}")
            log.info(f"[{symbol}] ta: buy blocked — {', '.join(missing)}")
        else:
            if overbought and (macd_bear or above_bb):
                return "sell"
            # also exit on deteriorating win chance
            if win <= sell_t:
                return "sell"

    elif strat == "buffett":
        if holding <= 0:
            if rsi < 40:
                return "buy"
            log.info(f"[{symbol}] buffett: RSI={rsi:.1f} ≥ 40 — waiting for dip")
        else:
            # exit on RSI overbought or win chance collapse
            if rsi > 70 or win <= sell_t:
                return "sell"

    elif strat == "hybrid":
        if holding <= 0:
            if rsi < 40 and hist > 0 and win >= buy_t:
                return "buy"
            missing = []
            if rsi >= 40:      missing.append(f"RSI={rsi:.1f}≥40")
            if hist <= 0:      missing.append(f"MACD={hist:.4f}≤0")
            if win < buy_t:    missing.append(f"Win={win}%<{buy_t}%")
            log.info(f"[{symbol}] hybrid: buy blocked — {', '.join(missing)}")
        else:
            if win <= sell_t:
                return "sell"

    return "hold"

def check_sl_tp(symbol, price, holding):
    if holding <= 0 or symbol not in entry_prices: return "hold"
    pnl = (price - entry_prices[symbol]) / entry_prices[symbol]
    if pnl >=  CONFIG["take_profit_pct"]:
        log.info(f"[{symbol}] Take-profit triggered +{pnl*100:.2f}%"); return "sell_tp"
    if pnl <= -CONFIG["stop_loss_pct"]:
        log.info(f"[{symbol}] Stop-loss triggered {pnl*100:.2f}%");    return "sell_sl"
    return "hold"

# ═══════════════════════════════════════════════════════
# ORDERS
# ═══════════════════════════════════════════════════════
def get_lot_size(symbol):
    info = api_get("/api/v3/exchangeInfo", {"symbol": symbol})
    if not info: return {"minQty": 0.0001, "stepSize": 0.0001, "minNotional": 10.0}
    filters = info["symbols"][0]["filters"]
    result  = {"minQty": 0.0001, "stepSize": 0.0001, "minNotional": 10.0}
    for f in filters:
        if f["filterType"] == "LOT_SIZE":
            result["minQty"]    = float(f["minQty"])
            result["stepSize"]  = float(f["stepSize"])
        if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            result["minNotional"] = float(f.get("minNotional", f.get("notional", 10.0)))
    return result

def round_step(qty, step):
    p = len(str(step).rstrip("0").split(".")[-1]) if "." in str(step) else 0
    return round(qty - (qty % step), p)

def _sim_lot_step(symbol):
    if symbol.startswith("BTC"): return 0.00001
    if symbol.startswith("ETH"): return 0.0001
    return 0.0001

def place_buy(symbol):
    usdt  = get_balance("USDT")
    price = get_symbol_price(symbol)
    if price <= 0 or usdt <= 0:
        log.warning(f"[{symbol}] Invalid balance or price"); return
    order_usdt = usdt * CONFIG["order_pct"]
    fee        = order_usdt * CONFIG["fee_rate"]
    net_usdt   = order_usdt - fee
    if CONFIG["sim_mode"]:
        step        = _sim_lot_step(symbol)
        min_notional = 0.0   # no exchange restriction in sim
    else:
        lot          = get_lot_size(symbol)
        step         = lot["stepSize"]
        min_notional = lot["minNotional"]
    qty = round_step(net_usdt / price, step)
    if qty <= 0:
        log.warning(f"[{symbol}] Qty is zero, skipping"); return
    if order_usdt < min_notional:
        log.warning(f"[{symbol}] Order ${order_usdt:.2f} below min notional "
                    f"${min_notional:.2f} — skipping buy"); return
    mode_tag = "[SIM]" if CONFIG["sim_mode"] else ("[TESTNET]" if CONFIG["testnet"] else "[LIVE]")
    log.info(f"[{symbol}] {mode_tag} BUY {qty} @${price:,.2f}  "
             f"cost ${order_usdt:.2f}  fee ${fee:.4f}")
    if CONFIG["sim_mode"]:
        base = symbol.replace("USDT", "")
        sim_portfolio["USDT"]  -= order_usdt
        sim_portfolio[base]    = sim_portfolio.get(base, 0.0) + qty
        entry_prices[symbol]   = price
        trade_stats["trade_count"] += 1
        trade_stats["total_fees"]  += fee
        log.info(f"[{symbol}] {mode_tag} Buy filled — holding {sim_portfolio[base]:.6f} {base}")
    else:
        r = api_post("/api/v3/order", {
            "symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": qty
        })
        if r and "orderId" in r:
            entry_prices[symbol] = price
            trade_stats["trade_count"] += 1
            trade_stats["total_fees"]  += fee
            log.info(f"[{symbol}] Buy filled #{r['orderId']}")
        else:
            log.error(f"[{symbol}] Buy FAILED: {r}")

def place_sell(symbol, reason="Signal sell"):
    base    = symbol.replace("USDT", "")
    holding = get_balance(base)
    price   = get_symbol_price(symbol)
    if CONFIG["sim_mode"]:
        step         = _sim_lot_step(symbol)
        min_notional = 0.0
    else:
        lot          = get_lot_size(symbol)
        step         = lot["stepSize"]
        min_notional = lot["minNotional"]
    qty   = round_step(holding, step)
    if qty <= 0:
        log.warning(f"[{symbol}] No position to sell"); return
    gross = qty * price
    if gross < min_notional and not CONFIG["sim_mode"]:
        log.warning(f"[{symbol}] Sell value ${gross:.2f} below min notional "
                    f"${min_notional:.2f} — position too small to sell, clearing tracking")
        entry_prices.pop(symbol, None)   # remove from tracking — can't be sold
        return
    fee   = gross * CONFIG["fee_rate"]
    net   = gross - fee
    mode_tag = "[SIM]" if CONFIG["sim_mode"] else ("[TESTNET]" if CONFIG["testnet"] else "[LIVE]")
    log.info(f"[{symbol}] {mode_tag} SELL {qty} @${price:,.2f}  "
             f"gross ${gross:.2f}  fee ${fee:.4f}  net ${net:.2f} | {reason}")
    if CONFIG["sim_mode"]:
        ep = entry_prices.pop(symbol, None)
        sim_portfolio["USDT"]  += net
        sim_portfolio[base]     = 0.0
        trade_stats["total_fees"] += fee
        if ep and price > ep:
            trade_stats["wins"] += 1
            pnl_pct = (price - ep) / ep * 100
            log.info(f"[{symbol}] {mode_tag} Sell filled ✓ profit +{pnl_pct:.2f}%")
        else:
            pnl_pct = (price - ep) / ep * 100 if ep else 0
            log.info(f"[{symbol}] {mode_tag} Sell filled  P&L {pnl_pct:.2f}%")
    else:
        r = api_post("/api/v3/order", {
            "symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty
        })
        if r and "orderId" in r:
            ep = entry_prices.pop(symbol, None)
            trade_stats["total_fees"] += fee
            if ep and price > ep:
                trade_stats["wins"] += 1
            log.info(f"[{symbol}] Sell filled #{r['orderId']}")
        else:
            log.error(f"[{symbol}] Sell FAILED: {r}")

# ═══════════════════════════════════════════════════════
# BOT THREAD
# ═══════════════════════════════════════════════════════
bot_running = False
bot_thread  = None

def bot_loop(update_cb):
    global bot_running
    if CONFIG["sim_mode"]:
        mode = "SIMULATOR"
    elif CONFIG["testnet"]:
        mode = "TESTNET"
    else:
        mode = "LIVE ⚠"
    log.info(f"=== Bot started [{mode}] strategy={CONFIG['strategy']} "
             f"TP={CONFIG['take_profit_pct']*100}% SL={CONFIG['stop_loss_pct']*100}% "
             f"fee={CONFIG['fee_rate']*100:.1f}%/order ===")
    while bot_running:
        try:
            log.info(f"--- Scanning {datetime.now().strftime('%H:%M:%S')} ---")
            for sym in CONFIG["symbols"]:
                closes = get_klines(sym, "15m", 100)
                if len(closes) < 30:
                    log.warning(f"[{sym}] Not enough candles, skipping"); continue
                base    = sym.replace("USDT", "")
                holding = get_balance(base)
                price   = closes[-1]
                sl_tp   = check_sl_tp(sym, price, holding)
                if sl_tp == "sell_tp":   place_sell(sym, "Take-profit"); continue
                elif sl_tp == "sell_sl": place_sell(sym, "Stop-loss");   continue
                sig = get_signal(sym, closes, holding)
                log.info(f"[{sym}] holding={holding:.6f}  signal={sig.upper()}")
                if sig == "buy":    place_buy(sym)
                elif sig == "sell": place_sell(sym, "Strategy sell")
                else:               log.info(f"[{sym}] Watching — no action")
            update_cb()
            log.info(f"Waiting {CONFIG['loop_interval']}s...")
            for _ in range(CONFIG["loop_interval"]):
                if not bot_running: break
                time.sleep(1)
        except Exception as e:
            log.error(f"Main loop error: {e}"); time.sleep(30)
    log.info("Bot stopped")

# ═══════════════════════════════════════════════════════
# REAL SENTIMENT  —  Fear & Greed Index + Live News
# ═══════════════════════════════════════════════════════
_FNG_CACHE  = {"value": None, "label": None, "ts": 0.0}
_NEWS_CACHE = {"items": [],                  "ts": 0.0}
_FNG_TTL    = 300    # re-fetch at most every 5 minutes (index updates once/day)
_NEWS_TTL   = 300    # re-fetch news every 5 minutes

def get_fear_greed_index():
    """
    Fetch the Crypto Fear & Greed Index from alternative.me.
    Free API, no key needed.  Returns {"value": 0-100, "label": str} or None on failure.
    The index is updated once per day by the provider.
    """
    now = time.time()
    if _FNG_CACHE["value"] is not None and now - _FNG_CACHE["ts"] < _FNG_TTL:
        return {"value": _FNG_CACHE["value"], "label": _FNG_CACHE["label"]}
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=8)
        r.raise_for_status()
        d = r.json()["data"][0]
        _FNG_CACHE.update({
            "value": int(d["value"]),
            "label": d["value_classification"],   # "Extreme Fear" … "Extreme Greed"
            "ts":    now,
        })
        log.info(f"Fear & Greed Index: {_FNG_CACHE['value']} — {_FNG_CACHE['label']}")
        return {"value": _FNG_CACHE["value"], "label": _FNG_CACHE["label"]}
    except Exception as e:
        log.warning(f"Fear & Greed fetch failed: {e}")
        return None

# ── keyword sets for classifying real headlines ──────────
_BULL_KW = {"rally", "surge", "gain", "rise", "bull", "record", "high", "adopt",
            "approve", "launch", "buy", "accumulate", "etf", "inflow", "upgrade",
            "partnership", "growth", "support", "breakout", "soar", "jump"}
_BEAR_KW = {"crash", "drop", "fall", "bear", "low", "ban", "hack", "fraud",
            "sell", "liquidate", "decline", "loss", "fear", "outflow", "restrict",
            "fine", "penalty", "investigation", "bankrupt", "exploit", "attack",
            "dump", "plunge", "tumble", "warning", "concern", "risk"}

def _classify_headline(title: str) -> str:
    words = set(title.lower().replace(",", " ").replace(".", " ").split())
    bull  = len(words & _BULL_KW)
    bear  = len(words & _BEAR_KW)
    if bull > bear:  return "bullish"
    if bear > bull:  return "bearish"
    return "neutral"

def get_crypto_news(limit=5):
    """
    Fetch latest English crypto news from CryptoCompare.
    Free API, no key needed.
    Returns list of (title, source, category) tuples.
    Falls back to empty list on failure.
    """
    now = time.time()
    if _NEWS_CACHE["items"] and now - _NEWS_CACHE["ts"] < _NEWS_TTL:
        return _NEWS_CACHE["items"][:limit]
    try:
        r = requests.get(
            "https://min-api.cryptocompare.com/data/v2/news/",
            params={"lang": "EN", "sortOrder": "latest"},
            timeout=8
        )
        r.raise_for_status()
        articles = r.json().get("Data", [])
        items = []
        for a in articles[:30]:
            title  = a.get("title", "").strip()
            source = (a.get("source_info", {}) or {}).get("name", "") or a.get("source", "")
            if not title: continue
            cat = _classify_headline(title)
            items.append((title, source, cat))
        _NEWS_CACHE.update({"items": items, "ts": now})
        log.info(f"Fetched {len(items)} real news headlines")
        return items[:limit]
    except Exception as e:
        log.warning(f"News fetch failed: {e}")
        return []

# ═══════════════════════════════════════════════════════
# COLOURS
# ═══════════════════════════════════════════════════════
BG         = "#0e1117"
CARD       = "#161b26"
BORDER     = "#2a2f3e"
TEXT       = "#e8eaf0"
MUTED      = "#6b7280"
GREEN      = "#22c55e"
RED        = "#ef4444"
BLUE       = "#3b82f6"
YELLOW     = "#f59e0b"
CHART_BG   = "#0e1117"
CHART_GRID = "#1e2433"


# ═══════════════════════════════════════════════════════
# GUI
# ═══════════════════════════════════════════════════════
class BotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Binance Auto Trading Bot  |  Simulator / Testnet / Live")
        self.root.configure(bg=BG)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        win_w = max(1200, int(sw * 0.92))
        win_h = max(750,  int(sh * 0.92))
        self.root.geometry(f"{win_w}x{win_h}+{(sw-win_w)//2}+{(sh-win_h)//2}")
        self.root.minsize(1100, 700)
        self._screen_w = sw
        self._left_panel_w = max(320, int(sw * 0.22))

        self.sel_symbol      = tk.StringVar(value="BTCUSDT")
        self.sel_interval    = tk.StringVar(value="1m")
        self.sentiment_score = 55.0
        self._fng_label      = None   # e.g. "Greed", "Extreme Fear"
        self._fng_raw        = None   # raw 0-100 F&G value before blending
        self._last_news      = []
        self._fetching           = False
        self._chart_fetching     = False
        self._live_baseline      = None
        self._klines_cache       = []
        self._klines_cache_sym   = ""
        self._klines_cache_iv    = ""
        self._klines_cache_ts    = 0.0
        self._price_cache        = {}
        # steps: 0=select mode, 1=apply API/principal, 2=apply strategy, 3=start bot
        self._steps_done              = [True, False, False, False]
        self._step_status_widgets     = []
        self._apply_api_btn_ref       = None
        self._apply_principal_btn_ref = None
        self._apply_strategy_btn_ref  = None
        self._api_key_visible         = False
        self._api_secret_visible      = False
        self._api_key_entry           = None
        self._api_secret_entry        = None
        self._prev_mode               = "sim"   # track last applied mode for change-guard

        self._build_ui()
        self._refresh_chart_and_stats()
        self._poll_logs()
        self._schedule_auto_refresh()
        self._schedule_chart_refresh()
        self._scanner_dict = {}   # keyed by symbol; updated per-symbol via scanner_queue
        threading.Thread(target=self._scanner_worker,   daemon=True).start()
        threading.Thread(target=self._sentiment_worker, daemon=True).start()
        self.root.after(400, self._schedule_scanner_render)

    # ─── layout ───────────────────────────────────────────
    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────
        top = tk.Frame(self.root, bg=CARD, pady=6)
        top.pack(fill="x", side="top")

        tk.Label(top, text="Binance Auto Trading Bot",
                 bg=CARD, fg=TEXT,
                 font=("Helvetica", 14, "bold")).pack(side="left", padx=16)

        self.mode_badge = tk.Label(top, text="SIMULATOR", bg="#7c3aed", fg="white",
                 font=("Helvetica", 9, "bold"), padx=8, pady=3)
        self.mode_badge.pack(side="left", padx=4)

        self.env_lbl = tk.Label(top, text="● Simulation mode — no real funds at risk",
                                bg=CARD, fg="#a78bfa",
                                font=("Helvetica", 10))
        self.env_lbl.pack(side="left", padx=8)

        self.status_lbl = tk.Label(top, text="● Stopped",
                                   bg=CARD, fg=RED,
                                   font=("Helvetica", 11, "bold"))
        self.status_lbl.pack(side="left", padx=8)

        self.fee_lbl = tk.Label(top,
                 text=f"Binance Spot  fee {CONFIG['fee_rate']*100:.1f}%/order  "
                      f"round-trip {CONFIG['fee_rate']*200:.1f}%",
                 bg=CARD, fg=MUTED, font=("Helvetica", 10))
        self.fee_lbl.pack(side="right", padx=16)

        # ── Main panes ───────────────────────────────────
        paned = tk.PanedWindow(self.root, orient="horizontal", bg=BG, sashwidth=5)
        paned.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        left  = tk.Frame(paned, bg=BG)
        right = tk.Frame(paned, bg=BG)
        paned.add(left,  minsize=self._left_panel_w, width=self._left_panel_w)
        paned.add(right, minsize=600)
        self._build_left(left)
        self._build_right(right)

        # ── Status bar ───────────────────────────────────
        self.statusbar = tk.Label(self.root,
                                  text="Ready  |  Select a mode and click ▶ Start Bot to begin.",
                                  bg="#080b12", fg=MUTED,
                                  font=("Helvetica", 9), anchor="w", padx=10)
        self.statusbar.pack(fill="x", side="bottom", ipady=3)

    # ─── left panel ───────────────────────────────────────
    def _build_left(self, p):
        canvas = tk.Canvas(p, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(p, orient="vertical", command=canvas.yview)
        self._left_inner = tk.Frame(canvas, bg=BG)
        self._left_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._left_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        p = self._left_inner  # now build inside scrollable frame

        # ── Setup steps ──────────────────────────────────
        self._lbl(p, "Setup Steps  (follow in order)").pack(fill="x", pady=(8, 2))
        steps_card = self._card(p); steps_card.pack(fill="x", pady=(0, 8), padx=4)
        _step_defs = [
            ("①", "Select Trading Mode"),
            ("②", "Apply API Keys or Principal"),
            ("③", "Apply Strategy Settings"),
            ("④", "Click ▶ Start Bot"),
        ]
        self._step_status_widgets = []
        for icon, label in _step_defs:
            srow = tk.Frame(steps_card, bg=CARD); srow.pack(fill="x", pady=2)
            tk.Label(srow, text=icon, bg=CARD, fg=MUTED,
                     font=("Helvetica", 11, "bold"), width=3).pack(side="left")
            nlbl = tk.Label(srow, text=label, bg=CARD, fg=MUTED,
                            font=("Helvetica", 10), anchor="w")
            nlbl.pack(side="left", fill="x", expand=True)
            slbl = tk.Label(srow, text="○", bg=CARD, fg=MUTED,
                            font=("Helvetica", 11, "bold"))
            slbl.pack(side="right")
            self._step_status_widgets.append((nlbl, slbl))
        self._update_step_display()

        # ── Mode selection ────────────────────────────────
        self._lbl(p, "Trading Mode").pack(fill="x", pady=(4, 2), padx=4)
        mf = self._card(p); mf.pack(fill="x", pady=(0, 6), padx=4)
        self.mode_var = tk.StringVar(value="sim")

        modes_info = [
            ("sim",     "SIMULATOR",  "#7c3aed",
             "Virtual funds + real Binance prices\nNo API key needed. Safe to experiment."),
            ("testnet", "TESTNET",    "#0891b2",
             "Real orders on Binance test network\nFree testnet API key required.\nNo real money at risk."),
            ("live",    "LIVE ⚠",    RED,
             "REAL MONEY — connects to your Binance account\nRequires live API key & secret.\nUse with caution!"),
        ]
        for val, badge, color, desc in modes_info:
            row = tk.Frame(mf, bg=CARD, pady=3); row.pack(fill="x")
            rb  = tk.Radiobutton(row, variable=self.mode_var, value=val,
                                 bg=CARD, fg=color, selectcolor=BG,
                                 activebackground=CARD, activeforeground=color,
                                 command=self._on_mode_change, width=0)
            rb.pack(side="left")
            badge_lbl = tk.Label(row, text=badge, bg=color, fg="white",
                                 font=("Helvetica", 8, "bold"), padx=5, pady=1)
            badge_lbl.pack(side="left", padx=(0, 6))
            badge_lbl.bind("<Button-1>", lambda e, v=val: (self.mode_var.set(v), self._on_mode_change()))
            tk.Label(row, text=desc, bg=CARD, fg=MUTED,
                     font=("Helvetica", 8), justify="left", anchor="w").pack(side="left")
        self._mode_colors = {v: c for v, _, c, _ in modes_info}

        # ── Simulator settings ────────────────────────────
        self._lbl(p, "Simulator Settings").pack(fill="x", pady=(4, 2), padx=4)
        self.sim_frame = self._card(p); self.sim_frame.pack(fill="x", pady=(0, 6), padx=4)

        pr = tk.Frame(self.sim_frame, bg=CARD); pr.pack(fill="x", pady=3)
        tk.Label(pr, text="Starting Capital $", bg=CARD, fg=MUTED,
                 width=18, anchor="w", font=("Helvetica", 10)).pack(side="left")
        self.principal_var = tk.StringVar(value=str(int(trade_stats["principal"])))
        tk.Entry(pr, textvariable=self.principal_var, bg=BG, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=0, highlightthickness=1,
                 highlightbackground=BORDER, width=12).pack(side="left")

        sim_fr = tk.Frame(self.sim_frame, bg=CARD); sim_fr.pack(fill="x", pady=2)
        tk.Label(sim_fr, text="Fee Rate %", bg=CARD, fg=MUTED,
                 width=18, anchor="w", font=("Helvetica", 9)).pack(side="left")
        self.sim_fee_var = tk.DoubleVar(value=round(CONFIG["fee_rate"] * 100, 2))
        sim_fee_val_lbl = tk.Label(sim_fr, bg=CARD, fg=TEXT, width=6,
                                   font=("Helvetica", 9, "bold"))
        sim_fee_val_lbl.pack(side="right")
        def _upd_fee(v, lbl=sim_fee_val_lbl): lbl.config(text=f"{float(v):.2f}%")
        tk.Scale(sim_fr, variable=self.sim_fee_var, from_=0.0, to=1.0, resolution=0.01,
                 orient="horizontal", bg=CARD, fg=MUTED, troughcolor=BG,
                 highlightthickness=0, bd=0, sliderrelief="flat",
                 command=_upd_fee, length=130).pack(side="left")
        _upd_fee(round(CONFIG["fee_rate"] * 100, 2))
        tk.Label(self.sim_frame, text="  ↳ 0% = no fees · 0.10% = Binance Spot standard",
                 bg=CARD, fg="#4b5563", font=("Helvetica", 7), anchor="w").pack(fill="x", padx=4)

        sbr = tk.Frame(self.sim_frame, bg=CARD); sbr.pack(fill="x", pady=(6, 2))
        self._apply_principal_btn_ref = tk.Button(
                  sbr, text="Apply Capital & Reset",
                  command=self._apply_principal,
                  bg=BLUE, fg="white", relief="flat", padx=8, pady=4,
                  cursor="hand2", font=("Helvetica", 10))
        self._apply_principal_btn_ref.pack(side="left", padx=(0, 6))
        tk.Button(sbr, text="Reset Simulator",
                  command=self._reset_sim,
                  bg="#dc2626", fg="white", relief="flat", padx=8, pady=4,
                  cursor="hand2", font=("Helvetica", 10)).pack(side="left")
        tk.Label(self.sim_frame,
                 text="Tip: Chart data still loads from Binance public API.\nNo account login needed.",
                 bg=CARD, fg=MUTED, font=("Helvetica", 8), justify="left").pack(anchor="w", pady=(4, 0))

        # ── API settings ──────────────────────────────────
        self._lbl(p, "API Settings  (Testnet or Live)").pack(fill="x", pady=(4, 2), padx=4)
        self.api_frame = self._card(p); self.api_frame.pack(fill="x", pady=(0, 6), padx=4)

        self._build_api_row(self.api_frame, "API Key",    "api_key_var",
                            CONFIG["api_key"],    "key")
        self._build_api_row(self.api_frame, "API Secret", "api_secret_var",
                            CONFIG["api_secret"], "secret")

        hint_row = tk.Frame(self.api_frame, bg=CARD); hint_row.pack(fill="x", pady=(4, 2))
        tk.Label(hint_row,
                 text="Testnet (free):  testnet.binance.vision\n"
                      "  → Log in with GitHub → Generate HMAC_SHA256 Key",
                 bg=CARD, fg=MUTED, font=("Helvetica", 8), justify="left").pack(side="left")

        api_btns = tk.Frame(self.api_frame, bg=CARD); api_btns.pack(fill="x", pady=(4, 2))
        self._apply_api_btn_ref = tk.Button(
                  api_btns, text="Apply API Keys",
                  command=self._apply_api,
                  bg=BLUE, fg="white", relief="flat", padx=8, pady=4,
                  cursor="hand2", font=("Helvetica", 10))
        self._apply_api_btn_ref.pack(side="left", padx=(0, 6))
        tk.Button(api_btns, text="Save Keys to File",
                  command=self._save_api_keys,
                  bg="#374151", fg=TEXT, relief="flat", padx=8, pady=4,
                  cursor="hand2", font=("Helvetica", 10)).pack(side="left", padx=(0, 6))
        tk.Button(api_btns, text="Clear Keys",
                  command=self._clear_api_keys,
                  bg="#374151", fg=RED, relief="flat", padx=8, pady=4,
                  cursor="hand2", font=("Helvetica", 10)).pack(side="left")

        self._on_mode_change()  # init visibility

        # ── Strategy settings ─────────────────────────────
        self._lbl(p, "Strategy Settings").pack(fill="x", pady=(4, 2), padx=4)
        sf = self._card(p); sf.pack(fill="x", pady=(0, 6), padx=4)

        sr = tk.Frame(sf, bg=CARD); sr.pack(fill="x", pady=3)
        tk.Label(sr, text="Strategy", bg=CARD, fg=MUTED,
                 width=16, anchor="w", font=("Helvetica", 10)).pack(side="left")
        self.strat_var = tk.StringVar(value=CONFIG["strategy"])
        strat_cb = ttk.Combobox(sr, textvariable=self.strat_var, state="readonly", width=14,
                     values=["winrate", "ta", "hybrid", "buffett"])
        strat_cb.pack(side="left")

        # Strategy descriptions
        strat_desc_frame = tk.Frame(sf, bg="#0d1220", padx=6, pady=4)
        strat_desc_frame.pack(fill="x", pady=(0, 4))
        self._strat_desc_lbl = tk.Label(strat_desc_frame, bg="#0d1220", fg="#94a3b8",
                                         font=("Helvetica", 8), justify="left", anchor="w",
                                         wraplength=290)
        self._strat_desc_lbl.pack(fill="x")
        strat_cb.bind("<<ComboboxSelected>>", self._on_strat_change)
        self._on_strat_change()

        self._slider(sf, "Order Size %",      "order_pct_var",  CONFIG["order_pct"]*100,       5,  100, 5, "%",
                     tip="% of available USDT to use per buy order")
        self._slider(sf, "Take-Profit %",     "tp_var",         CONFIG["take_profit_pct"]*100, 1,   50, 1, "%",
                     tip="Auto-sell when position gains this much")
        self._slider(sf, "Stop-Loss %",       "sl_var",         CONFIG["stop_loss_pct"]*100,   1,   50, 1, "%",
                     tip="Auto-sell when position loses this much")
        self._slider(sf, "Buy Win% Threshold","buy_thresh_var", CONFIG["buy_win_thresh"],      45,  85, 1, "%",
                     tip="winrate/hybrid: buy when win chance >= this (60 = achievable in normal markets, 75+ = crash-level only)")
        self._slider(sf, "Sell Win% Threshold","sell_thresh_var",CONFIG["sell_win_thresh"],    10,  55, 1, "%",
                     tip="winrate/hybrid: sell when win chance <= this")
        self._slider(sf, "Scan Interval (s)", "interval_var",   CONFIG["loop_interval"],        1, 300, 1, "s",
                     tip="How often the bot scans for signals")

        self._apply_strategy_btn_ref = tk.Button(
                  sf, text="Apply Strategy Settings",
                  command=self._apply_strategy,
                  bg="#6366f1", fg="white", relief="flat", padx=10, pady=5,
                  cursor="hand2", font=("Helvetica", 10, "bold"))
        self._apply_strategy_btn_ref.pack(anchor="e", pady=(6, 2))

        # ── Bot control ───────────────────────────────────
        self._lbl(p, "Bot Control").pack(fill="x", pady=(6, 2), padx=4)
        cf = self._card(p); cf.pack(fill="x", pady=(0, 8), padx=4)
        br = tk.Frame(cf, bg=CARD); br.pack(fill="x", pady=4)
        self.start_btn = tk.Button(br, text="▶  Start Bot",
                                   command=self._start_bot,
                                   bg=GREEN, fg="white", relief="flat",
                                   padx=16, pady=7, font=("Helvetica", 11, "bold"),
                                   cursor="hand2")
        self.start_btn.pack(side="left", padx=(0, 8))
        self.stop_btn = tk.Button(br, text="■  Stop",
                                  command=self._stop_bot,
                                  bg=RED, fg="white", relief="flat",
                                  padx=16, pady=7, font=("Helvetica", 11),
                                  state="disabled", cursor="hand2")
        self.stop_btn.pack(side="left", padx=(0, 8))
        tk.Button(br, text="⟲  Reset Session",
                  command=self._reset_session,
                  bg=YELLOW, fg="#0e1117", relief="flat", padx=10, pady=7,
                  font=("Helvetica", 10, "bold"),
                  cursor="hand2").pack(side="left", padx=(0, 8))
        tk.Button(br, text="⟳  Refresh",
                  command=self._refresh_chart_and_stats,
                  bg=CARD, fg=TEXT, relief="flat", padx=10, pady=7,
                  cursor="hand2").pack(side="left")

        # ── Account overview ──────────────────────────────
        self._lbl(p, "Account Overview").pack(fill="x", pady=(6, 2), padx=4)
        ac = self._card(p); ac.pack(fill="x", pady=(0, 8), padx=4)
        self.stat_vars = {}
        stat_rows = [
            ("Principal",       f"${trade_stats['principal']:,.0f}", TEXT),
            ("Total Assets",    "—",                                 TEXT),
            ("Available Cash",  "—",                                 TEXT),
            ("Net P&L",         "+$0.00",                            GREEN),
            ("Return %",        "+0.00%",                            GREEN),
            ("Total Fees Paid", "-$0.00",                            RED),
            ("Trade Count",     "0",                                 TEXT),
            ("Win Rate",        "—",                                 TEXT),
            ("Max Drawdown",    "0.0%",                              TEXT),
            ("Fee / Principal", "0.00%",                             MUTED),
        ]
        for lbl, default, color in stat_rows:
            row = tk.Frame(ac, bg=CARD); row.pack(fill="x", pady=1)
            tk.Label(row, text=lbl, bg=CARD, fg=MUTED,
                     width=16, anchor="w", font=("Helvetica", 9)).pack(side="left")
            v = tk.StringVar(value=default); self.stat_vars[lbl] = v
            lv = tk.Label(row, textvariable=v, bg=CARD, fg=color,
                          font=("Helvetica", 9, "bold"))
            lv.pack(side="left")
            self.stat_vars[lbl + "_lbl"] = lv

    # ─── API key row with show/hide ────────────────────────
    def _build_api_row(self, parent, label, attr, default, field_type):
        row = tk.Frame(parent, bg=CARD); row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=CARD, fg=MUTED,
                 width=12, anchor="w", font=("Helvetica", 10)).pack(side="left")
        var = tk.StringVar(value=default); setattr(self, attr, var)
        entry = tk.Entry(row, textvariable=var, bg=BG, fg=TEXT,
                         insertbackground=TEXT, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=BORDER,
                         width=24, show="●")
        entry.pack(side="left")

        eye_var = tk.BooleanVar(value=False)
        if field_type == "key":
            self._api_key_entry   = entry
            self._api_key_eye     = eye_var
        else:
            self._api_secret_entry = entry
            self._api_secret_eye   = eye_var

        def toggle_vis(e=entry, v=eye_var):
            v.set(not v.get())
            e.config(show="" if v.get() else "●")
            eye_btn.config(text="🙈" if v.get() else "👁")
        eye_btn = tk.Button(row, text="👁", bg=CARD, fg=MUTED, relief="flat",
                            font=("Helvetica", 10), cursor="hand2", bd=0,
                            command=toggle_vis)
        eye_btn.pack(side="left", padx=(4, 0))

    # ─── right panel ──────────────────────────────────────
    def _build_right(self, p):
        tr = tk.Frame(p, bg=BG); tr.pack(fill="x", pady=(0, 2))
        tk.Label(tr, text="Symbol:", bg=BG, fg=MUTED,
                 font=("Helvetica", 10)).pack(side="left")
        for sym in CONFIG["symbols"]:
            tk.Radiobutton(tr, text=sym, variable=self.sel_symbol, value=sym,
                           bg=BG, fg=TEXT, selectcolor=BG,
                           activebackground=BG, activeforeground=TEXT,
                           command=self._on_sym_iv_change).pack(side="left", padx=3)
        tk.Label(tr, text="|", bg=BG, fg=MUTED).pack(side="left", padx=4)
        tk.Label(tr, text="Candle:", bg=BG, fg=MUTED,
                 font=("Helvetica", 10)).pack(side="left")
        for iv in ["1m", "5m", "15m", "1h"]:
            tk.Radiobutton(tr, text=iv, variable=self.sel_interval, value=iv,
                           bg=BG, fg=TEXT, selectcolor=BG,
                           activebackground=BG, activeforeground=TEXT,
                           command=self._on_sym_iv_change).pack(side="left", padx=2)
        tk.Label(tr, text="  (Signals always use 15m candles)", bg=BG, fg=MUTED,
                 font=("Helvetica", 8)).pack(side="left", padx=4)
        tk.Button(tr, text="⟳", command=self._refresh_chart_and_stats,
                  bg=CARD, fg=TEXT, relief="flat", padx=6, pady=2,
                  cursor="hand2").pack(side="right")

        # ── Sentiment bar ─────────────────────────────────
        sf = tk.Frame(p, bg=CARD, padx=10, pady=6,
                      highlightbackground=BORDER, highlightthickness=1)
        sf.pack(fill="x", pady=(0, 4))
        sh = tk.Frame(sf, bg=CARD); sh.pack(fill="x")
        tk.Label(sh, text="Market Sentiment  (Fear & Greed Index + Technical)",
                 bg=CARD, fg=MUTED,
                 font=("Helvetica", 9, "bold")).pack(side="left", padx=(0, 8))
        self.sentiment_lbl = tk.Label(sh, text="— Neutral  (55)",
                                      bg=CARD, fg=TEXT,
                                      font=("Helvetica", 10, "bold"))
        self.sentiment_lbl.pack(side="left")
        self.sent_canvas = tk.Canvas(sh, bg=CARD, height=14, width=200,
                                     highlightthickness=0)
        self.sent_canvas.pack(side="left", padx=8)
        tk.Label(sh, text="Bearish ← → Bullish", bg=CARD, fg=MUTED,
                 font=("Helvetica", 8)).pack(side="left")
        tk.Frame(sf, bg=BORDER, height=1).pack(fill="x", pady=(5, 4))
        self.news_labels = []
        for _ in range(5):
            row = tk.Frame(sf, bg=CARD); row.pack(fill="x", pady=1)
            dot = tk.Label(row, text="●", bg=CARD, fg=MUTED,
                           font=("Helvetica", 8), width=2)
            dot.pack(side="left")
            lbl = tk.Label(row, text="", bg=CARD, fg=MUTED,
                           font=("Helvetica", 8), anchor="w")
            lbl.pack(side="left", fill="x")
            self.news_labels.append((dot, lbl))

        # ── Scanner ───────────────────────────────────────
        self._build_scanner_panel(p)

        # ── Chart + log/positions ─────────────────────────
        hpaned = tk.PanedWindow(p, orient="horizontal", bg=BG,
                                sashwidth=6, sashrelief="raised")
        hpaned.pack(fill="both", expand=True, pady=(4, 0))
        chart_f = tk.Frame(hpaned, bg=BG)
        log_f   = tk.Frame(hpaned, bg=BG)
        hpaned.add(chart_f, minsize=300)
        hpaned.add(log_f,   minsize=260)
        self._hpaned_sash_set = False
        def _set_sash(event=None):
            if self._hpaned_sash_set: return
            total = hpaned.winfo_width()
            if total > 100:
                hpaned.sashpos(0, total // 2)
                self._hpaned_sash_set = True
            else:
                self.root.after(80, _set_sash)
        hpaned.bind("<Map>", _set_sash)
        self.root.after(300, _set_sash)

        self.fig = Figure(figsize=(7, 7), dpi=100, facecolor=CHART_BG)
        gs = gridspec.GridSpec(4, 1, figure=self.fig,
                               height_ratios=[4, 1, 1.5, 1.5], hspace=0.06)
        self.ax_price = self.fig.add_subplot(gs[0])
        self.ax_vol   = self.fig.add_subplot(gs[1], sharex=self.ax_price)
        self.ax_rsi   = self.fig.add_subplot(gs[2], sharex=self.ax_price)
        self.ax_macd  = self.fig.add_subplot(gs[3], sharex=self.ax_price)
        self.fig.subplots_adjust(left=0.02, right=0.84, top=0.97, bottom=0.08)
        for ax in (self.ax_price, self.ax_vol, self.ax_rsi, self.ax_macd):
            ax.set_facecolor(CHART_BG)
            ax.tick_params(colors="#cbd5e1", labelsize=10)
            for sp in ax.spines.values(): sp.set_edgecolor(BORDER)
            ax.grid(color=CHART_GRID, linewidth=0.5)
        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_f)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self._build_log_positions_panel(log_f)

    # ─── scanner panel ────────────────────────────────────
    # ── scanner mini-bar drawing helpers ─────────────────
    @staticmethod
    def _draw_rsi_bar(canvas, rsi: float, row_bg: str):
        """RSI bar: green zone 0-40, yellow 40-65, red 65-100. White tick at current."""
        canvas.update_idletasks()
        W = max(60, canvas.winfo_width())
        H = 12
        canvas.config(height=H, bg=row_bg)
        canvas.delete("all")
        canvas.create_rectangle(0, 2, W, H - 2, fill="#1e2433", outline="")
        canvas.create_rectangle(0,          2, int(W * .40), H - 2, fill="#082010", outline="")
        canvas.create_rectangle(int(W*.40), 2, int(W * .65), H - 2, fill="#1a1600", outline="")
        canvas.create_rectangle(int(W*.65), 2, W,            H - 2, fill="#200808", outline="")
        x   = max(2, int(W * rsi / 100))
        col = "#4ade80" if rsi < 40 else ("#ef4444" if rsi > 65 else "#f59e0b")
        canvas.create_rectangle(0, 2, x, H - 2, fill=col, outline="")
        for pct in (0.40, 0.65):
            xd = int(W * pct)
            canvas.create_line(xd, 0, xd, H, fill="#374151", width=1)
        canvas.create_line(x, 0, x, H, fill="white", width=2)

    @staticmethod
    def _draw_win_bar(canvas, win: int, buy_t: int, sell_t: int, row_bg: str):
        """Win% bar with buy (green) and sell (red) threshold markers."""
        canvas.update_idletasks()
        W = max(60, canvas.winfo_width())
        H = 12
        canvas.config(height=H, bg=row_bg)
        canvas.delete("all")
        canvas.create_rectangle(0, 2, W, H - 2, fill="#1e2433", outline="")
        x   = max(2, int(W * win / 100))
        col = "#4ade80" if win >= buy_t else ("#ef4444" if win <= sell_t else "#f59e0b")
        canvas.create_rectangle(0, 2, x, H - 2, fill=col, outline="")
        canvas.create_line(int(W * buy_t / 100),  0, int(W * buy_t / 100),  H, fill="#4ade80", width=2)
        canvas.create_line(int(W * sell_t / 100), 0, int(W * sell_t / 100), H, fill="#ef4444", width=2)

    def _build_scanner_panel(self, p):
        sh = tk.Frame(p, bg=BG); sh.pack(fill="x", pady=(4, 1))
        tk.Label(sh, text="Market Scanner", bg=BG, fg=MUTED,
                 font=("Helvetica", 11, "bold"), anchor="w").pack(side="left")
        self._scanner_interval_lbl = tk.Label(sh,
                 text=f"  (15m candles · updates every {CONFIG['loop_interval']}s)",
                 bg=BG, fg=MUTED, font=("Helvetica", 10))
        self._scanner_interval_lbl.pack(side="left")
        self.scanner_status_lbl = tk.Label(sh, text="● Ready", bg=BG, fg=MUTED,
                                            font=("Helvetica", 10))
        self.scanner_status_lbl.pack(side="right", padx=8)

        sc = tk.Frame(p, bg="#0a0e1a",
                      highlightbackground=BORDER, highlightthickness=1)
        sc.pack(fill="x")

        # Proportional columns: Coin|Price|Change|RSI|MACD|BB|Sentiment|Win%|Suggest|觸發狀態
        _COL_WEIGHTS = [1, 2, 1, 2, 2, 1, 2, 1, 1, 2]
        _NCOLS = len(_COL_WEIGHTS)
        for i, cw in enumerate(_COL_WEIGHTS):
            sc.columnconfigure(i, weight=cw)

        # ── header row ────────────────────────────────────
        HDR_BG = "#0d1220"
        _hdrs = ["Coin", "Price", "Change", "RSI", "MACD", "BB", "Sentiment", "Win %", "Suggest", "觸發狀態"]
        for col, txt in enumerate(_hdrs):
            tk.Label(sc, text=txt, bg=HDR_BG, fg="#6b7280",
                     font=("Helvetica", 10, "bold"), anchor="w",
                     padx=6, pady=5).grid(row=0, column=col, sticky="ew")
        tk.Frame(sc, bg=BORDER, height=1).grid(row=1, column=0, columnspan=_NCOLS, sticky="ew")

        # ── one row per symbol ────────────────────────────
        self._scan_rows = []
        _row_bgs = ["#111827", "#0f1620"]
        for i, sym in enumerate(CONFIG["symbols"]):
            grid_row = i * 2 + 2
            row_w = self._build_scanner_row(sc, grid_row, _row_bgs[i % 2])
            self._scan_rows.append(row_w)
            if i < len(CONFIG["symbols"]) - 1:
                tk.Frame(sc, bg="#1e2433", height=1).grid(
                    row=grid_row + 1, column=0, columnspan=10, sticky="ew")

    def _build_scanner_row(self, parent, grid_row: int, default_bg: str) -> dict:
        """Place all scanner cell widgets into parent grid at grid_row."""
        w = {"default_bg": default_bg}
        PAD = dict(padx=6, pady=8)

        # Col 0 — Coin badge
        coin_lbl = tk.Label(parent, text="—", bg="#2a1a00", fg="#f7931a",
                            font=("Helvetica", 12, "bold"), padx=6, pady=4, anchor="w")
        coin_lbl.grid(row=grid_row, column=0, sticky="ew", **PAD)
        w["coin"] = coin_lbl

        # Col 1 — Price
        price_lbl = tk.Label(parent, text="—", bg=default_bg, fg=TEXT,
                             font=("Courier", 11, "bold"), anchor="w")
        price_lbl.grid(row=grid_row, column=1, sticky="ew", **PAD)
        w["price"] = price_lbl

        # Col 2 — 24h Change
        change_lbl = tk.Label(parent, text="—", bg=default_bg, fg=MUTED,
                              font=("Helvetica", 11, "bold"), anchor="w")
        change_lbl.grid(row=grid_row, column=2, sticky="ew", **PAD)
        w["change"] = change_lbl

        # Col 3 — RSI: value + zone (stacked)
        rf = tk.Frame(parent, bg=default_bg)
        rf.grid(row=grid_row, column=3, sticky="ew", **PAD)
        rsi_val = tk.Label(rf, text="—", bg=default_bg, fg=TEXT,
                           font=("Courier", 11, "bold"), anchor="w")
        rsi_val.pack(anchor="w")
        rsi_zone_lbl = tk.Label(rf, text="—", bg=default_bg, fg=MUTED,
                                font=("Helvetica", 9), anchor="w")
        rsi_zone_lbl.pack(anchor="w")
        w["rsi_val"] = rsi_val; w["rsi_zone_lbl"] = rsi_zone_lbl; w["_rf"] = rf

        # Col 4 — MACD: histogram + label (stacked)
        mf = tk.Frame(parent, bg=default_bg)
        mf.grid(row=grid_row, column=4, sticky="ew", **PAD)
        macd_hist_lbl = tk.Label(mf, text="—", bg=default_bg, fg=TEXT,
                                 font=("Courier", 11, "bold"), anchor="w")
        macd_hist_lbl.pack(anchor="w")
        macd_dir_lbl = tk.Label(mf, text="—", bg=default_bg, fg=MUTED,
                                font=("Helvetica", 9), anchor="w")
        macd_dir_lbl.pack(anchor="w")
        w["macd_hist_lbl"] = macd_hist_lbl; w["macd_dir_lbl"] = macd_dir_lbl; w["_mf"] = mf

        # Col 5 — BB status badge
        bb_badge = tk.Label(parent, text="—", bg="#1e2433", fg=MUTED,
                            font=("Helvetica", 10, "bold"), padx=6, pady=3, anchor="w")
        bb_badge.grid(row=grid_row, column=5, sticky="ew", **PAD)
        w["bb_badge"] = bb_badge

        # Col 6 — Sentiment: % + label (stacked)
        sentf = tk.Frame(parent, bg=default_bg)
        sentf.grid(row=grid_row, column=6, sticky="ew", **PAD)
        sent_val = tk.Label(sentf, text="—", bg=default_bg, fg=TEXT,
                            font=("Courier", 11, "bold"), anchor="w")
        sent_val.pack(anchor="w")
        sent_lbl_w = tk.Label(sentf, text="—", bg=default_bg, fg=MUTED,
                              font=("Helvetica", 9), anchor="w")
        sent_lbl_w.pack(anchor="w")
        w["sent_val"] = sent_val; w["sent_lbl_w"] = sent_lbl_w; w["_sentf"] = sentf

        # Col 7 — Win%: value + threshold bar (stacked)
        wf = tk.Frame(parent, bg=default_bg)
        wf.grid(row=grid_row, column=7, sticky="ew", **PAD)
        win_val = tk.Label(wf, text="—", bg=default_bg, fg=TEXT,
                           font=("Courier", 11, "bold"), anchor="w")
        win_val.pack(anchor="w")
        win_canvas = tk.Canvas(wf, bg=default_bg, height=8, highlightthickness=0)
        win_canvas.pack(fill="x", pady=(1, 0))
        w["win_val"] = win_val; w["win_canvas"] = win_canvas; w["_wf"] = wf

        # Col 8 — Suggest badge
        suggest_badge = tk.Label(parent, text="觀望", bg="#1e2433", fg="#6b7280",
                                 font=("Helvetica", 11, "bold"), padx=8, pady=5, anchor="w")
        suggest_badge.grid(row=grid_row, column=8, sticky="ew", **PAD)
        w["suggest_badge"] = suggest_badge

        # Col 9 — 觸發狀態
        trig_lbl = tk.Label(parent, text="—", bg=default_bg, fg=MUTED,
                            font=("Helvetica", 10), anchor="w", wraplength=180)
        trig_lbl.grid(row=grid_row, column=9, sticky="ew", **PAD)
        w["trig_lbl"] = trig_lbl

        # All frames/labels whose bg changes on signal (excludes direct-grid labels)
        w["_bg_frames"]  = [rf, mf, sentf, wf]
        w["_bg_labels"]  = [price_lbl, change_lbl, bb_badge, suggest_badge, trig_lbl]

        return w

    # ─── log + positions ──────────────────────────────────
    def _build_log_positions_panel(self, p):
        st = ttk.Style()
        st.configure("Dark.TNotebook", background=BG, borderwidth=0, tabmargins=[0, 2, 0, 0])
        st.configure("Dark.TNotebook.Tab", background=CARD, foreground=MUTED,
                     padding=[14, 5], font=("Helvetica", 9, "bold"),
                     focuscolor=BG, borderwidth=0)
        st.map("Dark.TNotebook.Tab",
               background=[("selected", "#1e3a5f"), ("active", "#263352")],
               foreground=[("selected", "#f1f5f9"), ("active", TEXT)])

        nb = ttk.Notebook(p, style="Dark.TNotebook")
        nb.pack(fill="both", expand=True, pady=(4, 0))

        # ── Tab 1: Trade Log ──────────────────────────────
        log_tab = tk.Frame(nb, bg="#080b12")
        nb.add(log_tab, text="  Trade Log  ")
        self.log_box = scrolledtext.ScrolledText(
            log_tab, height=8, bg="#080b12", fg=TEXT,
            font=("Courier", 10), relief="flat", bd=0, insertbackground=TEXT)
        self.log_box.pack(fill="both", expand=True, padx=2, pady=2)
        _bf = ("Courier", 10, "bold")
        self.log_box.tag_config("buy",     foreground="#4ade80", font=_bf)
        self.log_box.tag_config("profit",  foreground="#4ade80", font=_bf)
        self.log_box.tag_config("sell",    foreground="#f87171", font=_bf)
        self.log_box.tag_config("loss",    foreground="#f87171", font=_bf)
        self.log_box.tag_config("sltp",    foreground="#fbbf24", font=_bf)
        self.log_box.tag_config("fee",     foreground="#f59e0b")
        self.log_box.tag_config("err",     foreground="#ff6b6b", font=_bf)
        self.log_box.tag_config("info",    foreground="#9ca3af")
        self.log_box.tag_config("scan",    foreground="#374151")
        self.log_box.tag_config("waiting", foreground="#4b5563")
        self.log_box.insert("end", "Waiting for trade signals...\n", "waiting")
        self.log_box.config(state="disabled")

        # ── Tab 2: Positions ──────────────────────────────
        pos_tab = tk.Frame(nb, bg=CARD)
        nb.add(pos_tab, text="  Positions  ")

        POS_COLS = ("Coin", "Qty", "Entry Price", "Current", "P&L %", "Value", "Unrealized P&L")
        POS_W    = {"Coin": 55, "Qty": 95, "Entry Price": 90, "Current": 90,
                    "P&L %": 70, "Value": 90, "Unrealized P&L": 100}
        st.configure("Pos.Treeview", background=CARD, foreground=TEXT,
                     fieldbackground=CARD, borderwidth=0, rowheight=28)
        st.configure("Pos.Treeview.Heading", background="#0e1117", foreground="#6b7280",
                     borderwidth=0, font=("Helvetica", 9, "bold"), relief="flat")
        st.map("Pos.Treeview", background=[("selected", "#1e3a5f")])

        self.positions_tree = ttk.Treeview(pos_tab, columns=POS_COLS,
                                            show="headings", height=6,
                                            style="Pos.Treeview")
        for col in POS_COLS:
            self.positions_tree.heading(col, text=col)
            self.positions_tree.column(col, width=POS_W[col], anchor="center", stretch=True)
        _bf2 = ("Helvetica", 9, "bold")
        self.positions_tree.tag_configure("profit",  foreground="#4ade80", font=_bf2)
        self.positions_tree.tag_configure("loss",    foreground="#f87171", font=_bf2)
        self.positions_tree.tag_configure("neutral", foreground="#9ca3af")
        self.positions_tree.tag_configure("empty",   foreground="#4b5563")
        self.positions_tree.pack(fill="both", expand=True, padx=2, pady=2)
        self.positions_tree.insert("", "end",
            values=("No positions", "—", "—", "—", "—", "—", "—"), tags=("empty",))

    # ─── widget helpers ───────────────────────────────────
    def _lbl(self, p, t):
        return tk.Label(p, text=t, bg=BG, fg=MUTED,
                        font=("Helvetica", 9, "bold"), anchor="w")

    def _card(self, p):
        return tk.Frame(p, bg=CARD, bd=0, padx=10, pady=6,
                        highlightbackground=BORDER, highlightthickness=1)

    def _slider(self, p, label, attr, default, lo, hi, res, unit="", tip=""):
        row = tk.Frame(p, bg=CARD); row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=CARD, fg=MUTED,
                 width=18, anchor="w", font=("Helvetica", 10)).pack(side="left")
        var = tk.DoubleVar(value=default); setattr(self, attr, var)
        vl  = tk.Label(row, bg=CARD, fg=TEXT, width=6,
                       font=("Helvetica", 10, "bold"))
        vl.pack(side="right")
        def upd(v): vl.config(text=f"{float(v):.0f}{unit}")
        tk.Scale(row, variable=var, from_=lo, to=hi, resolution=res,
                 orient="horizontal", bg=CARD, fg=MUTED, troughcolor=BG,
                 highlightthickness=0, bd=0, sliderrelief="flat",
                 command=upd, length=130).pack(side="left")
        upd(default)
        if tip:
            tk.Label(p, text=f"  ↳ {tip}", bg=CARD, fg="#4b5563",
                     font=("Helvetica", 7), anchor="w").pack(fill="x", padx=4)

    # ─── strategy descriptions ─────────────────────────────
    _STRAT_DESC = {
        "winrate":  "Buy when Win Chance ≥ Buy threshold (default 60%).\n"
                    "Sell when Win Chance ≤ Sell threshold (default 35%).\n"
                    "Win Chance uses RSI, MACD, BB, MA trend + market sentiment.\n"
                    "Best for: beginners — single number to watch.",
        "ta":       "Buy: RSI < 40 (oversold) AND (MACD bullish OR price below BB lower).\n"
                    "Sell: RSI > 65 (overbought) AND (MACD bearish OR price above BB upper).\n"
                    "Stop-Loss and Take-Profit also active.\n"
                    "Best for: technical traders.",
        "hybrid":   "Buy: RSI < 40 AND MACD bullish AND Win Chance ≥ 58%.\n"
                    "Sell: Win Chance ≤ Sell threshold.\n"
                    "Best for: balanced approach with multiple confirmations.",
        "buffett":  "Buy: RSI < 40 (dip buying — oversold or near-oversold).\n"
                    "No strategy sell — exits only via Take-Profit or Stop-Loss.\n"
                    "Best for: hold-through-dips / value approach.",
    }

    def _on_strat_change(self, event=None):
        s = self.strat_var.get()
        self._strat_desc_lbl.config(text=self._STRAT_DESC.get(s, ""))

    # ─── mode change ──────────────────────────────────────
    def _on_mode_change(self):
        global bot_running
        mode = self.mode_var.get()

        # ── Guard: bot is running ─────────────────────────
        if bot_running and mode != self._prev_mode:
            if not messagebox.askyesno(
                    "Bot Is Running",
                    f"The bot is currently active.\n\n"
                    f"Switching to {mode.upper()} mode will:\n"
                    "  • Stop the bot immediately\n"
                    "  • Clear all open positions\n"
                    "  • Reset trade statistics\n\n"
                    "Proceed?",
                    icon="warning"):
                # Revert radio selection
                self.mode_var.set(self._prev_mode)
                return
            # Stop and wipe state
            bot_running = False
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.status_lbl.config(text="● Stopped", fg=RED)
            self._do_clear_session(log_msg=f"Mode changed while running → stopped. Switching to {mode.upper()}")

        self._prev_mode = mode
        CONFIG["sim_mode"] = (mode == "sim")
        CONFIG["testnet"]  = (mode == "testnet")
        self._steps_done[1] = False
        self._steps_done[2] = False
        self._steps_done[3] = False
        self._live_baseline = None
        if CONFIG["sim_mode"]:
            trade_stats["peak_value"] = trade_stats["principal"]
        if self._apply_api_btn_ref:
            self._apply_api_btn_ref.config(state="normal", text="Apply API Keys")
        if self._apply_principal_btn_ref:
            self._apply_principal_btn_ref.config(state="normal", text="Apply Capital & Reset")
        if self._apply_strategy_btn_ref:
            self._apply_strategy_btn_ref.config(state="normal", text="Apply Strategy Settings")
        self._update_step_display()

        if mode == "sim":
            self.sim_frame.pack(fill="x", pady=(0, 6), padx=4)
            self.api_frame.pack_forget()
            self.mode_badge.config(text="SIMULATOR", bg="#7c3aed")
            self.env_lbl.config(text="● Simulation mode — no real funds at risk", fg="#a78bfa")
            self._set_status("Simulator mode active. Set your starting capital, then click Apply.")
        elif mode == "testnet":
            self.sim_frame.pack_forget()
            self.api_frame.pack(fill="x", pady=(0, 6), padx=4)
            self.mode_badge.config(text="TESTNET", bg="#0891b2")
            self.env_lbl.config(text="● Testnet — virtual funds, real orders on test network", fg=GREEN)
            self._set_status("Testnet mode: enter your testnet.binance.vision API keys, then click Apply.")
            CONFIG["fee_rate"] = 0.001
            if hasattr(self, "fee_lbl"):
                self.fee_lbl.config(text="Binance Spot  fee 0.1%/order  round-trip 0.2%")
        else:
            self.sim_frame.pack_forget()
            self.api_frame.pack(fill="x", pady=(0, 6), padx=4)
            self.mode_badge.config(text="LIVE ⚠", bg=RED)
            self.env_lbl.config(text="● LIVE mode — REAL MONEY at risk!", fg=RED)
            self._set_status("⚠ LIVE mode: enter your real Binance API keys. Real money will be traded!")
            CONFIG["fee_rate"] = 0.001
            if hasattr(self, "fee_lbl"):
                self.fee_lbl.config(text="Binance Spot  fee 0.1%/order  round-trip 0.2%")

    # ─── API key actions ──────────────────────────────────
    def _apply_api(self):
        CONFIG["api_key"]    = self.api_key_var.get().strip()
        CONFIG["api_secret"] = self.api_secret_var.get().strip()
        mode_name = "Testnet" if CONFIG["testnet"] else "LIVE"
        log.info(f"API keys updated — mode={mode_name}")
        self._mark_step(1)
        if self._apply_api_btn_ref:
            self._apply_api_btn_ref.config(state="disabled", text="✓ Applied")
        self._set_status(f"API keys applied for {mode_name} mode.")
        messagebox.showinfo("API Keys Applied",
            f"API keys saved for {mode_name} mode.\n\n"
            "Tip: Click 'Save Keys to File' to remember them next time.")

    def _save_api_keys(self):
        data = _load_saved_config()
        data["api_key"]    = self.api_key_var.get().strip()
        data["api_secret"] = self.api_secret_var.get().strip()
        _save_config_file(data)
        self._set_status("API keys saved to saved_config.json.")
        messagebox.showinfo("Saved",
            "API keys saved to saved_config.json in the bot folder.\n"
            "They will be loaded automatically next time you start the bot.")

    def _clear_api_keys(self):
        if not messagebox.askyesno("Clear Keys", "Remove API keys from the fields?"):
            return
        self.api_key_var.set("")
        self.api_secret_var.set("")
        CONFIG["api_key"] = ""
        CONFIG["api_secret"] = ""
        data = _load_saved_config()
        data.pop("api_key", None)
        data.pop("api_secret", None)
        _save_config_file(data)
        self._set_status("API keys cleared.")

    # ─── principal / reset ────────────────────────────────
    def _apply_principal(self):
        try:
            val = float(self.principal_var.get().replace(",", ""))
            if val <= 0: raise ValueError
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter a positive number for starting capital.")
            return
        fee_pct = round(self.sim_fee_var.get(), 2) / 100
        CONFIG["fee_rate"] = fee_pct
        if hasattr(self, "fee_lbl"):
            self.fee_lbl.config(
                text=f"Simulator  fee {fee_pct*100:.2f}%/order  "
                     f"round-trip {fee_pct*200:.2f}%")
        trade_stats["principal"]  = val
        trade_stats["peak_value"] = val
        sim_portfolio["USDT"]     = val
        for k in list(sim_portfolio.keys()):
            if k != "USDT": sim_portfolio[k] = 0.0
        trade_stats["trade_count"] = 0
        trade_stats["wins"]        = 0
        trade_stats["total_fees"]  = 0.0
        entry_prices.clear()
        data = _load_saved_config()
        data["principal"] = val
        data["sim_fee_pct"] = fee_pct * 100
        _save_config_file(data)
        log.info(f"Starting capital set to ${val:,.0f}, fee={fee_pct*100:.2f}% — simulator reset")
        self._mark_step(1)
        if self._apply_principal_btn_ref:
            self._apply_principal_btn_ref.config(state="disabled", text="✓ Applied")
        self._set_status(f"Starting capital set to ${val:,.0f}, fee {fee_pct*100:.2f}%. Simulator reset.")
        messagebox.showinfo("Applied",
            f"Starting capital: ${val:,.0f}\nFee rate: {fee_pct*100:.2f}%/order\nSimulator has been reset.")
        self._refresh_chart_and_stats()

    # ─── shared session-clear core ────────────────────────
    def _do_clear_session(self, log_msg="Session cleared"):
        """
        Wipe runtime trade state without touching CONFIG or mode selection.
        Used by _reset_session and _on_mode_change (stop-while-running path).
        """
        entry_prices.clear()
        trade_stats["trade_count"] = 0
        trade_stats["wins"]        = 0
        trade_stats["total_fees"]  = 0.0
        trade_stats["peak_value"]  = trade_stats["principal"]
        if CONFIG["sim_mode"]:
            sim_portfolio["USDT"] = trade_stats["principal"]
            for k in list(sim_portfolio.keys()):
                if k != "USDT":
                    sim_portfolio[k] = 0.0
        self._live_baseline = None
        log.info(f"=== {log_msg} ===")

    # ─── reset session (button handler) ───────────────────
    def _reset_session(self):
        global bot_running
        was_running = bot_running
        mode_name   = {"sim": "Simulator", "testnet": "Testnet", "live": "LIVE"}.get(
                       self.mode_var.get(), self.mode_var.get())

        lines = []
        if was_running:
            lines.append("  • Stop the running bot")
        lines.append("  • Close / discard all tracked positions")
        lines.append("  • Reset trade count, win rate, fee totals")
        if CONFIG["sim_mode"]:
            lines.append(f"  • Restore simulator portfolio to ${trade_stats['principal']:,.0f}")
        lines.append(f"\nMode will remain: {mode_name}")

        if not messagebox.askyesno(
                "Reset Session",
                "This will:\n" + "\n".join(lines) + "\n\nProceed?",
                icon="warning"):
            return

        # Stop bot
        if was_running:
            bot_running = False
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.status_lbl.config(text="● Stopped", fg=RED)

        self._do_clear_session(log_msg="Session reset by user")

        # Step 3 must be repeated (bot needs to be restarted)
        self._steps_done[3] = False
        self._update_step_display()

        self._set_status("Session reset — positions cleared, stats zeroed. Ready to start again.")
        self._refresh_chart_and_stats()

    def _reset_sim(self):
        if not messagebox.askyesno("Reset Simulator",
                "Reset the simulator?\nAll trade history will be cleared."):
            return
        reset_sim()
        self._refresh_chart_and_stats()
        self._set_status("Simulator reset.")

    # ─── strategy ─────────────────────────────────────────
    def _apply_strategy(self):
        if bot_running:
            if not messagebox.askyesno(
                    "Bot Is Running",
                    "The bot is currently active.\n\n"
                    "New strategy settings will take effect on the next scan cycle.\n"
                    "Open positions will NOT be automatically closed.\n\n"
                    "Apply strategy change now?",
                    icon="warning"):
                return
            log.info("Strategy settings changed while bot was running — takes effect next cycle")
        CONFIG["strategy"]        = self.strat_var.get()
        CONFIG["order_pct"]       = self.order_pct_var.get() / 100
        CONFIG["take_profit_pct"] = self.tp_var.get() / 100
        CONFIG["stop_loss_pct"]   = self.sl_var.get() / 100
        CONFIG["buy_win_thresh"]  = int(self.buy_thresh_var.get())
        CONFIG["sell_win_thresh"] = int(self.sell_thresh_var.get())
        CONFIG["loop_interval"]   = max(1, int(self.interval_var.get()))
        if hasattr(self, "_scanner_interval_lbl"):
            self._scanner_interval_lbl.config(
                text=f"  (15m candles · updates every {CONFIG['loop_interval']}s)")
        log.info(f"Strategy updated: {CONFIG['strategy']} "
                 f"TP={CONFIG['take_profit_pct']*100}% "
                 f"SL={CONFIG['stop_loss_pct']*100}% "
                 f"interval={CONFIG['loop_interval']}s")
        self._mark_step(2)
        if self._apply_strategy_btn_ref:
            self._apply_strategy_btn_ref.config(state="disabled", text="✓ Applied")
        self._set_status(f"Strategy '{CONFIG['strategy']}' applied. Ready to start bot.")
        messagebox.showinfo("Applied", "Strategy settings updated.")

    # ─── bot start / stop ─────────────────────────────────
    def _start_bot(self):
        global bot_running, bot_thread
        if bot_running: return

        if not CONFIG["sim_mode"] and (not CONFIG["api_key"] or not CONFIG["api_secret"]):
            messagebox.showerror("Missing API Keys",
                "Testnet / Live mode requires API Key and Secret.\n\n"
                "Steps:\n"
                "1. Enter your API Key and Secret in the API Settings section\n"
                "2. Click 'Apply API Keys'\n"
                "3. Then start the bot")
            return

        mode = self.mode_var.get()

        # Extra confirmation for Live mode
        if mode == "live":
            confirm = messagebox.askyesno(
                "⚠ LIVE TRADING CONFIRMATION",
                "You are about to start LIVE trading with REAL MONEY.\n\n"
                f"  Strategy:    {CONFIG['strategy'].upper()}\n"
                f"  Order Size:  {CONFIG['order_pct']*100:.0f}% per trade\n"
                f"  Take-Profit: {CONFIG['take_profit_pct']*100:.0f}%\n"
                f"  Stop-Loss:   {CONFIG['stop_loss_pct']*100:.0f}%\n"
                f"  Scan Every:  {CONFIG['loop_interval']}s\n\n"
                "Are you sure you want to proceed?",
                icon="warning")
            if not confirm:
                return

        run_lbl = {
            "sim":     "● Simulator running",
            "testnet": "● Testnet running",
            "live":    "● LIVE trading active ⚠"
        }
        run_col = {"sim": "#a78bfa", "testnet": GREEN, "live": RED}

        bot_running = True
        self._mark_step(3)
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_lbl.config(text=run_lbl.get(mode, "● Running"),
                               fg=run_col.get(mode, GREEN))
        self._set_status(f"Bot started — {run_lbl.get(mode, 'Running')}  "
                         f"Strategy: {CONFIG['strategy'].upper()}  "
                         f"Interval: {CONFIG['loop_interval']}s")
        bot_thread = threading.Thread(
            target=bot_loop, args=(self._refresh_chart_and_stats,), daemon=True)
        bot_thread.start()

    def _stop_bot(self):
        global bot_running
        bot_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_lbl.config(text="● Stopped", fg=RED)
        self._set_status("Bot stopped. Existing positions are NOT automatically sold.")
        log.info("Stop command sent")

    def _on_sym_iv_change(self):
        self._klines_cache_ts = 0.0
        self._refresh_chart_and_stats()

    # ─── step indicator ───────────────────────────────────
    def _update_step_display(self):
        current = next((i for i, done in enumerate(self._steps_done) if not done), 4)
        for i, (nlbl, slbl) in enumerate(self._step_status_widgets):
            if self._steps_done[i]:
                nlbl.config(fg=GREEN)
                slbl.config(text="✓", fg=GREEN)
            elif i == current:
                nlbl.config(fg=TEXT)
                slbl.config(text="●", fg=YELLOW)
            else:
                nlbl.config(fg=MUTED)
                slbl.config(text="○", fg=MUTED)

    def _mark_step(self, idx):
        self._steps_done[idx] = True
        self._update_step_display()

    def _set_status(self, msg: str):
        try:
            self.statusbar.config(text=f"  {msg}")
        except Exception:
            pass

    # ─── data refresh ─────────────────────────────────────
    def _refresh_chart_and_stats(self):
        if self._fetching: return
        self._fetching = True
        threading.Thread(target=self._fetch_and_draw, daemon=True).start()

    def _fetch_and_draw(self):
        sym      = self.sel_symbol.get()
        interval = self.sel_interval.get()
        ohlcv    = get_klines_ohlcv(sym, interval, 120)
        if not ohlcv:
            log.warning(f"Cannot fetch {sym} candles (check internet connection)")
            self._fetching = False
            return
        closes = [c["c"] for c in ohlcv]
        self._klines_cache     = ohlcv
        self._klines_cache_sym = sym
        self._klines_cache_iv  = interval
        self._klines_cache_ts  = time.time()
        rsi  = calc_rsi(closes)
        hist = _raw_macd_hist(closes)
        ma20 = calc_ma(closes, 20)
        ma50 = calc_ma(closes, 50)

        # ── Technical sentiment (fallback) ────────────────
        tech_sent = 50
        if rsi < 35:   tech_sent += 15
        elif rsi > 65: tech_sent -= 15
        tech_sent += 10 if hist > 0 else -10
        tech_sent += 8  if ma20 > ma50 else -8
        tech_sent = max(5, min(95, round(tech_sent)))

        self.root.after(0, lambda o=ohlcv, iv=interval: self._draw_chart(sym, o, iv))
        self.root.after(0, self._update_stats)
        self._fetching = False

    def _draw_chart(self, sym, ohlcv, interval="1m"):
        if not ohlcv: return
        _isecs = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
        isec   = _isecs.get(interval, 60)
        now    = datetime.now()
        times  = [now - timedelta(seconds=isec * (len(ohlcv) - 1 - i))
                  for i in range(len(ohlcv))]
        bar_w  = timedelta(seconds=isec * 0.72)

        opens  = [c["o"] for c in ohlcv]
        highs  = [c["h"] for c in ohlcv]
        lows   = [c["l"] for c in ohlcv]
        closes = [c["c"] for c in ohlcv]
        vols   = [c["v"] for c in ohlcv]

        price_range = max(highs) - min(lows) if highs else 1.0
        min_body    = price_range * 0.0008

        ma20              = calc_ma_series(closes, 20)
        ma50              = calc_ma_series(closes, 50)
        bb_u, bb_l        = calc_bb_series(closes, 20)
        rsi_s             = calc_rsi_series(closes, 14)
        macd_s, sig_s, hist_s = calc_macd_series(closes)

        def clean(s):
            return [v if v is not None else float("nan") for v in s]

        C_BULL = "#26a69a"
        C_BEAR = "#ef5350"

        ax = self.ax_price; ax.cla()
        ax.set_facecolor(CHART_BG)
        ax.grid(color=CHART_GRID, linewidth=0.6, alpha=0.8, zorder=0)

        bull = [(t, o, h, l, c) for t, o, h, l, c in
                zip(times, opens, highs, lows, closes) if c >= o]
        bear = [(t, o, h, l, c) for t, o, h, l, c in
                zip(times, opens, highs, lows, closes) if c < o]

        for grp, color in ((bull, C_BULL), (bear, C_BEAR)):
            if not grp: continue
            gt, go, gh, gl, gc = zip(*grp)
            ax.vlines(gt, gl, gh, color=color, lw=0.9, zorder=1)
            bot = [min(o, c) for o, c in zip(go, gc)]
            hts = [max(abs(c - o), min_body) for o, c in zip(go, gc)]
            ax.bar(gt, hts, bottom=bot, color=color,
                   width=bar_w, zorder=2, edgecolor=color, linewidth=0.2)

        ax.plot(times, clean(bb_u), color="#4ade80", lw=0.9, alpha=0.6, label="BB±2σ")
        ax.plot(times, clean(bb_l), color="#4ade80", lw=0.9, alpha=0.6)
        ax.fill_between(times, clean(bb_u), clean(bb_l), alpha=0.06, color="#4ade80")
        ax.plot(times, clean(ma20), color="#fbbf24", lw=1.4, label="MA20", zorder=3)
        ax.plot(times, clean(ma50), color="#f472b6", lw=1.4, label="MA50", zorder=3)

        cur = closes[-1]
        ax.axhline(cur, color="#60a5fa", lw=0.8, ls="--", alpha=0.7, zorder=4)
        ax.annotate(f"  ${cur:,.2f}",
                    xy=(times[-1], cur), xycoords="data",
                    color="#60a5fa", fontsize=12, fontweight="bold", va="center", zorder=5)

        ax.set_title(f"{sym}  [{interval}]  Candlestick · MA20 · MA50 · Bollinger Bands(20,2)",
                     color="#f1f5f9", fontsize=11, fontweight="bold", loc="left", pad=4)
        ax.tick_params(labelbottom=False, colors="#cbd5e1", labelsize=10)
        ax.yaxis.tick_right()
        ax.yaxis.set_tick_params(labelsize=10, labelcolor="#cbd5e1")
        ax.legend(loc="upper left", fontsize=9, facecolor="#1e2533",
                  labelcolor="#f1f5f9", framealpha=0.95, edgecolor=BORDER,
                  handles=ax.get_legend_handles_labels()[0][:4])
        for sp in ax.spines.values(): sp.set_edgecolor(BORDER)

        ax_v = self.ax_vol; ax_v.cla()
        ax_v.set_facecolor(CHART_BG)
        ax_v.grid(color=CHART_GRID, linewidth=0.4, alpha=0.6, axis="y")
        vcol = [C_BULL if c >= o else C_BEAR for o, c in zip(opens, closes)]
        ax_v.bar(times, vols, color=vcol, width=bar_w, alpha=0.70, zorder=2)
        ax_v.set_title("Volume", color="#9ca3af", fontsize=11,
                       fontweight="bold", loc="left", pad=2)
        ax_v.tick_params(labelbottom=False, colors="#cbd5e1", labelsize=9)
        ax_v.yaxis.tick_right()
        ax_v.yaxis.set_major_formatter(
            mticker.FuncFormatter(
                lambda x, _: f"{x/1e6:.1f}M" if x >= 1e6 else f"{x/1e3:.0f}K"))
        ax_v.yaxis.set_tick_params(labelsize=9, labelcolor="#cbd5e1")
        for sp in ax_v.spines.values(): sp.set_edgecolor(BORDER)

        ax2 = self.ax_rsi; ax2.cla()
        ax2.set_facecolor(CHART_BG)
        ax2.grid(color=CHART_GRID, linewidth=0.6, alpha=0.8)
        ax2.plot(times, clean(rsi_s), color="#c4b5fd", lw=1.8)
        ax2.axhline(70, color="#f87171", lw=1.0, ls="--", alpha=0.9)
        ax2.axhline(30, color="#4ade80", lw=1.0, ls="--", alpha=0.9)
        ax2.axhline(50, color=MUTED,    lw=0.5, ls=":",  alpha=0.4)
        ax2.set_ylim(0, 100); ax2.set_yticks([30, 50, 70])
        ax2.tick_params(labelbottom=False, colors="#cbd5e1", labelsize=11)
        ax2.yaxis.tick_right()
        ax2.yaxis.set_tick_params(labelsize=11, labelcolor="#cbd5e1")
        cur_rsi = next((v for v in reversed(rsi_s) if v is not None), None)
        if cur_rsi is not None:
            rc = "#f87171" if cur_rsi > 70 else ("#4ade80" if cur_rsi < 30 else "#c4b5fd")
            ax2.annotate(f"  {cur_rsi:.1f}",
                         xy=(times[-1], cur_rsi), xycoords="data",
                         color=rc, fontsize=11, fontweight="bold", va="center")
        ax2.set_title("RSI (14)  — Overbought >70 / Oversold <30",
                      color="#f1f5f9", fontsize=10, fontweight="bold", loc="left", pad=3)
        ax2.tick_params(labelbottom=False, colors="#cbd5e1", labelsize=10)
        for sp in ax2.spines.values(): sp.set_edgecolor(BORDER)

        ax3 = self.ax_macd; ax3.cla()
        ax3.set_facecolor(CHART_BG)
        ax3.grid(color=CHART_GRID, linewidth=0.6, alpha=0.8)
        hcol = ["#4ade80" if (v or 0) > 0 else "#f87171" for v in hist_s]
        ax3.bar(times, clean(hist_s), color=hcol, alpha=0.80, width=bar_w, zorder=1)
        ax3.plot(times, clean(macd_s), color="#60a5fa", lw=1.5, label="MACD",   zorder=2)
        ax3.plot(times, clean(sig_s),  color="#fbbf24", lw=1.5, label="Signal", zorder=2)
        ax3.tick_params(colors="#cbd5e1", labelsize=10)
        ax3.yaxis.tick_right()
        ax3.yaxis.set_tick_params(labelsize=10, labelcolor="#cbd5e1")
        ax3.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax3.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
        ax3.xaxis.set_tick_params(labelsize=10, labelcolor="#cbd5e1", rotation=0)
        ax3.set_title("MACD (12/26/9)  — Histogram green=bullish / red=bearish",
                      color="#f1f5f9", fontsize=10, fontweight="bold", loc="left", pad=3)
        ax3.legend(loc="upper left", fontsize=9, facecolor="#1e2533",
                   labelcolor="#f1f5f9", framealpha=0.95, edgecolor=BORDER)
        for sp in ax3.spines.values(): sp.set_edgecolor(BORDER)

        self.fig.canvas.draw_idle()

    def _update_stats(self):
        sv   = self.stat_vars
        prin = trade_stats["principal"]
        sv["Principal"].set(f"${prin:,.0f}")
        try:
            if CONFIG["sim_mode"]:
                usdt = sim_portfolio["USDT"]
                btc  = sim_portfolio.get("BTC", 0.0)
                eth  = sim_portfolio.get("ETH", 0.0)
            else:
                if not CONFIG["api_key"]: return
                usdt = get_balance("USDT")
                # Only count crypto that the bot itself bought (has an open position).
                # Testnet / live accounts may have pre-existing balances that should
                # not inflate Total Assets or distort P&L tracking.
                btc  = get_balance("BTC") if "BTCUSDT" in entry_prices else 0.0
                eth  = get_balance("ETH") if "ETHUSDT" in entry_prices else 0.0
            btc_p = get_symbol_price("BTCUSDT") if btc > 0 else 0.0
            eth_p = get_symbol_price("ETHUSDT") if eth > 0 else 0.0
            if btc_p > 0: self._price_cache["BTCUSDT"] = btc_p
            if eth_p > 0: self._price_cache["ETHUSDT"] = eth_p
            assets = usdt + btc * btc_p + eth * eth_p

            if not CONFIG["sim_mode"]:
                if self._live_baseline is None:
                    self._live_baseline = assets
                    trade_stats["peak_value"] = assets
                calc_prin = self._live_baseline
                sv["Principal"].set(f"${calc_prin:,.0f}")
            else:
                calc_prin = prin

            if assets > trade_stats["peak_value"]:
                trade_stats["peak_value"] = assets
            peak    = trade_stats["peak_value"]
            dd      = (peak - assets) / peak * 100 if peak > 0 else 0.0
            pnl     = assets - calc_prin
            ret_pct = (assets / calc_prin - 1) * 100 if calc_prin > 0 else 0.0
            fees    = trade_stats["total_fees"]
            tc      = trade_stats["trade_count"]
            wins    = trade_stats["wins"]
            wr_txt  = f"{wins/tc*100:.1f}%  ({wins}/{tc})" if tc > 0 else "—"

            sv["Total Assets"].set(f"${assets:,.2f}")
            sv["Available Cash"].set(f"${usdt:,.2f}")

            pnl_txt = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
            sv["Net P&L"].set(pnl_txt)
            sv["Net P&L_lbl"].config(fg=GREEN if pnl >= 0 else RED)

            ret_txt = f"+{ret_pct:.2f}%" if ret_pct >= 0 else f"{ret_pct:.2f}%"
            sv["Return %"].set(ret_txt)
            sv["Return %_lbl"].config(fg=GREEN if ret_pct >= 0 else RED)

            sv["Total Fees Paid"].set(f"-${fees:,.2f}")
            sv["Trade Count"].set(str(tc))
            sv["Win Rate"].set(wr_txt)
            sv["Max Drawdown"].set(f"{dd:.1f}%")
            fee_ratio = fees / prin * 100 if prin > 0 else 0.0
            sv["Fee / Principal"].set(f"{fee_ratio:.2f}%  (${fees:.2f})")
        except Exception as e:
            log.error(f"Account update failed: {e}")
        self._update_positions()

    # ─── scanner ──────────────────────────────────────────
    _KLINE_TTL = 30   # re-fetch 15m klines every 30s for fresher indicators

    def _scanner_worker(self):
        kline_cache = {}   # sym -> {"closes": [...], "ts": float, indicators...}
        while True:
            try:
                now = time.time()
                for sym in CONFIG["symbols"]:
                    # ── slow path: refresh klines & indicators (60s TTL) ──
                    entry = kline_cache.get(sym, {})
                    if now - entry.get("ts", 0) >= self._KLINE_TTL:
                        closes = get_klines(sym, "15m", 100)
                        if not closes:
                            continue
                        curr_hist = calc_macd_current(closes)[2]
                        prev_hist = (calc_macd_current(closes[:-1])[2]
                                     if len(closes) > 26 else curr_hist)
                        entry = {
                            "closes":    closes,
                            "ts":        now,
                            "rsi":       calc_rsi(closes),
                            "hist":      curr_hist,
                            "prev_hist": prev_hist,
                            "upper_bb":  calc_bollinger_current(closes)[0],
                            "lower_bb":  calc_bollinger_current(closes)[2],
                            "ma20":      calc_ma(closes, 20),
                            "ma50":      calc_ma(closes, 50),
                        }
                        kline_cache[sym] = entry

                    closes   = entry["closes"]
                    rsi      = entry["rsi"]
                    hist     = entry["hist"]
                    prev_hist= entry.get("prev_hist", hist)
                    upper_bb = entry["upper_bb"]
                    lower_bb = entry["lower_bb"]
                    ma20     = entry["ma20"]
                    ma50     = entry["ma50"]

                    # ── fast path: ticker every cycle ─────────────
                    ticker     = market_get("/api/v3/ticker/24hr", {"symbol": sym})
                    if not ticker: continue
                    change_pct = float(ticker.get("priceChangePercent", 0))
                    price      = float(ticker["lastPrice"])
                    self._price_cache[sym] = price

                    base    = sym.replace("USDT", "")
                    holding = (sim_portfolio.get(base, 0.0) if CONFIG["sim_mode"]
                               else (get_balance(base) if CONFIG["api_key"] else 0.0))
                    sig = get_signal(sym, closes, holding)

                    # RSI zone (Chinese)
                    if rsi < 30:   rsi_zone = "強力超賣"
                    elif rsi < 40: rsi_zone = "超賣"
                    elif rsi < 45: rsi_zone = "中性偏低"
                    elif rsi < 55: rsi_zone = "中性"
                    elif rsi < 60: rsi_zone = "中性偏高"
                    elif rsi < 70: rsi_zone = "超買"
                    else:          rsi_zone = "強力超買"

                    # MACD label with cross detection
                    if   hist > 0 and prev_hist <= 0: macd_lbl = "黃金交叉 ▲"
                    elif hist < 0 and prev_hist >= 0: macd_lbl = "死亡交叉 ▼"
                    elif hist > 0:                    macd_lbl = "看多"
                    else:                             macd_lbl = "看空"
                    if   abs(hist) >= 100: hist_str = f"{hist:+.0f}"
                    elif abs(hist) >= 1:   hist_str = f"{hist:+.2f}"
                    else:                  hist_str = f"{hist:+.4f}"

                    # BB status (Chinese)
                    if price > upper_bb:   bb_lbl = "突破上軌"
                    elif price < lower_bb: bb_lbl = "跌破下軌"
                    else:                  bb_lbl = "正常"

                    # Sentiment: use same _current_sentiment as get_signal/bot_loop
                    sent = round(_current_sentiment)
                    if sent >= 75:   sent_lbl = "極度貪婪"
                    elif sent >= 60: sent_lbl = "看多"
                    elif sent >= 45: sent_lbl = "中性"
                    elif sent >= 30: sent_lbl = "看空"
                    else:            sent_lbl = "極度恐慌"

                    # Win% — identical computation to get_signal
                    win    = calc_win_chance(closes, float(sent))
                    buy_t  = CONFIG["buy_win_thresh"]
                    sell_t = CONFIG["sell_win_thresh"]

                    # Suggest + 觸發狀態
                    if sig == "buy":
                        suggest    = "買入"
                        trig_state = "✓ 買入信號!"
                        trig_type  = "buy"
                    elif sig == "sell":
                        suggest    = "賣出"
                        trig_state = "✓ 賣出信號!"
                        trig_type  = "sell"
                    else:
                        suggest   = "觀望"
                        trig_type = "neutral"
                        gap = buy_t - win
                        trig_state = f"觀望 (差{gap}%)" if gap > 0 else f"觀望 (超{-gap}%)"

                    scanner_queue.put({
                        "sym": sym, "coin": base, "price": price, "change": change_pct,
                        "rsi": rsi, "rsi_zone": rsi_zone,
                        "hist": hist, "hist_str": hist_str, "macd_lbl": macd_lbl,
                        "bb_lbl": bb_lbl,
                        "sent": sent, "sent_lbl": sent_lbl,
                        "win": win, "buy_t": buy_t, "sell_t": sell_t,
                        "sig": sig, "suggest": suggest, "trig_state": trig_state,
                        "trig_type": trig_type,
                    })  # pushed immediately — UI drains within 400 ms
            except Exception as e:
                log.error(f"Scanner worker error: {e}")
                time.sleep(2)   # back-off only on error

    # ─── sentiment worker (always real, mode-independent) ─────
    _SENTIMENT_TTL = 300   # re-fetch F&G every 5 minutes (API limit)

    def _sentiment_worker(self):
        last_fng_ts = 0.0
        while True:
            try:
                now = time.time()
                # F&G: slow (5 min TTL)
                if now - last_fng_ts >= self._SENTIMENT_TTL:
                    fng = get_fear_greed_index()
                    if fng:
                        self._fng_label = fng["label"]
                        self._fng_raw   = fng["value"]
                        last_fng_ts     = now

                # Technical component: use BTC 15m closes (fast, market_get)
                closes = get_klines("BTCUSDT", "15m", 52)
                if closes:
                    rsi  = calc_rsi(closes)
                    hist = _raw_macd_hist(closes)
                    ma20 = calc_ma(closes, 20)
                    ma50 = calc_ma(closes, 50)
                    tech = 50
                    if rsi < 35:   tech += 15
                    elif rsi > 65: tech -= 15
                    tech += 10 if hist > 0 else -10
                    tech += 8  if ma20 > ma50 else -8
                    tech = max(5, min(95, round(tech)))
                    if self._fng_raw is not None:
                        blended = round(self._fng_raw * 0.6 + tech * 0.4)
                    else:
                        blended = tech
                    self.sentiment_score = max(5, min(95, blended))
                    global _current_sentiment
                    _current_sentiment = float(self.sentiment_score)

                # News: same TTL as F&G
                if now - last_fng_ts < 5:   # just refreshed
                    self._last_news = get_crypto_news(limit=5)

                self.root.after(0, self._update_sentiment)
            except Exception as e:
                log.error(f"Sentiment worker error: {e}")
            time.sleep(30)

    def _schedule_scanner_render(self):
        # Drain scanner_queue — same pattern as _poll_logs drains log_queue.
        # Each symbol pushes its row immediately when computed, so the UI
        # reflects the latest data for each symbol within one 400 ms tick,
        # regardless of whether other symbols have finished their cycle.
        try:
            while True:
                row = scanner_queue.get_nowait()
                self._scanner_dict[row["sym"]] = row
        except queue.Empty:
            pass

        if self._scanner_dict:
            # Render in CONFIG["symbols"] order so row indices stay stable
            ordered = [self._scanner_dict[s]
                       for s in CONFIG["symbols"]
                       if s in self._scanner_dict]
            if ordered:
                self._render_scanner(ordered)

        self.root.after(400, self._schedule_scanner_render)

    def _render_scanner(self, rows):
        _COIN_STYLE = {
            "BTC": ("#2a1a00", "#f7931a"),
            "ETH": ("#0d1a2a", "#627eea"),
        }
        for i, d in enumerate(rows):
            if i >= len(self._scan_rows): break
            w   = self._scan_rows[i]
            sig = d["sig"]

            # ── row background ────────────────────────────────
            if sig == "buy":    row_bg = "#071510"
            elif sig == "sell": row_bg = "#150707"
            else:               row_bg = w["default_bg"]

            for fr in w["_bg_frames"]:
                fr.config(bg=row_bg)
                for child in fr.winfo_children():
                    try: child.config(bg=row_bg)
                    except tk.TclError: pass
            for lbl in w["_bg_labels"]:
                try: lbl.config(bg=row_bg)
                except tk.TclError: pass

            # ── Coin ──────────────────────────────────────────
            coin = d["coin"]
            cbg, cfg = _COIN_STYLE.get(coin, ("#1a1a2e", TEXT))
            w["coin"].config(text=coin, bg=cbg, fg=cfg)

            # ── Price ─────────────────────────────────────────
            w["price"].config(text=f"${d['price']:,.2f}")

            # ── Change ────────────────────────────────────────
            chg = d["change"]
            chg_col = "#4ade80" if chg > 0 else ("#ef4444" if chg < 0 else MUTED)
            w["change"].config(text=f"{chg:+.2f}%", fg=chg_col)

            # ── RSI (value + Chinese zone) ────────────────────
            rsi = d["rsi"]
            rsi_col = "#4ade80" if rsi < 40 else ("#ef4444" if rsi > 65 else TEXT)
            w["rsi_val"].config(text=f"{rsi:.1f}", fg=rsi_col)
            w["rsi_zone_lbl"].config(text=d["rsi_zone"])

            # ── MACD (histogram + Chinese label) ─────────────
            hist = d["hist"]
            macd_col = "#4ade80" if hist > 0 else "#ef4444"
            w["macd_hist_lbl"].config(text=d["hist_str"], fg=macd_col)
            w["macd_dir_lbl"].config(text=d["macd_lbl"], fg=macd_col)

            # ── BB ────────────────────────────────────────────
            bb = d["bb_lbl"]
            if bb == "突破上軌":   bb_bg, bb_fg = "#200a0a", "#ef4444"
            elif bb == "跌破下軌": bb_bg, bb_fg = "#0a2010", "#4ade80"
            else:                  bb_bg, bb_fg = "#1e2433", MUTED
            w["bb_badge"].config(text=bb, bg=bb_bg, fg=bb_fg)

            # ── Sentiment ─────────────────────────────────────
            sent = d["sent"]
            sent_col = "#4ade80" if sent >= 60 else ("#ef4444" if sent < 40 else "#f59e0b")
            w["sent_val"].config(text=f"{sent}%", fg=sent_col)
            w["sent_lbl_w"].config(text=d["sent_lbl"])

            # ── Win% + threshold bar ──────────────────────────
            win   = d["win"]
            buy_t = d["buy_t"]; sell_t = d["sell_t"]
            win_col = "#4ade80" if win >= buy_t else ("#ef4444" if win <= sell_t else "#f59e0b")
            w["win_val"].config(text=f"{win}%", fg=win_col)
            self._draw_win_bar(w["win_canvas"], win, buy_t, sell_t, row_bg)

            # ── Suggest badge ─────────────────────────────────
            tt = d["trig_type"]
            if tt == "buy":
                sb_bg, sb_fg = "#0a3010", "#4ade80"
            elif tt == "sell":
                sb_bg, sb_fg = "#300a0a", "#ef4444"
            else:
                sb_bg, sb_fg = "#1e2433", "#6b7280"
            w["suggest_badge"].config(text=d["suggest"], bg=sb_bg, fg=sb_fg)

            # ── 觸發狀態 ─────────────────────────────────────
            if tt == "buy":    trig_col = "#4ade80"
            elif tt == "sell": trig_col = "#ef4444"
            else:              trig_col = MUTED
            w["trig_lbl"].config(text=d["trig_state"], fg=trig_col)

        ts = datetime.now().strftime("%H:%M:%S")
        self.scanner_status_lbl.config(text=f"● {ts}", fg="#4ade80")

    def _update_positions(self):
        for item in self.positions_tree.get_children():
            self.positions_tree.delete(item)
        has_pos = False
        for sym in CONFIG["symbols"]:
            base = sym.replace("USDT", "")
            if CONFIG["sim_mode"]:
                qty = sim_portfolio.get(base, 0.0)
            else:
                # Only show positions the bot opened (has entry price recorded).
                # Prevents pre-existing testnet/live balances appearing as positions.
                qty = get_balance(base) if (CONFIG["api_key"] and sym in entry_prices) else 0.0
            if qty < 1e-8: continue
            has_pos = True
            ep  = entry_prices.get(sym, 0.0)
            cur = self._price_cache.get(sym) or get_symbol_price(sym)
            if cur <= 0: continue
            pnl_pct  = (cur - ep) / ep * 100 if ep > 0 else 0.0
            val      = qty * cur
            pnl_usd  = (cur - ep) * qty if ep > 0 else 0.0
            tag      = "profit" if pnl_pct >= 0 else "loss"
            self.positions_tree.insert("", "end", values=(
                base,
                f"{qty:.6f}",
                f"${ep:,.2f}" if ep > 0 else "—",
                f"${cur:,.2f}",
                f"{pnl_pct:+.2f}%",
                f"${val:,.2f}",
                f"{'+'if pnl_usd>=0 else ''}{pnl_usd:,.2f}",
            ), tags=(tag,))
        if not has_pos:
            self.positions_tree.insert("", "end",
                values=("No positions", "—", "—", "—", "—", "—", "—"),
                tags=("empty",))

    # ─── sentiment display ────────────────────────────────
    def _update_sentiment(self):
        s = self.sentiment_score

        # Colour by blended score
        if s >= 70:   col = GREEN
        elif s >= 55: col = "#86efac"
        elif s >= 45: col = TEXT
        elif s >= 30: col = "#fca5a5"
        else:         col = RED

        # Label: prefer real F&G label when available
        if self._fng_label:
            # Map standard F&G labels to display colours
            _fng_col = {
                "Extreme Greed": GREEN,
                "Greed":         "#86efac",
                "Neutral":       TEXT,
                "Fear":          "#fca5a5",
                "Extreme Fear":  RED,
            }
            col = _fng_col.get(self._fng_label, col)
            txt = f"{self._fng_label}  (F&G {self._fng_raw}  /  blended {s})"
        else:
            if s >= 70:   txt = "Strong Bull (technical)"
            elif s >= 55: txt = "Bullish (technical)"
            elif s >= 45: txt = "Neutral (technical)"
            elif s >= 30: txt = "Bearish (technical)"
            else:         txt = "Strong Bear (technical)"

        self.sentiment_lbl.config(text=txt, fg=col)

        # Draw bar
        c = self.sent_canvas; c.delete("all")
        w = 200; h = 14
        c.create_rectangle(0, 3, w, h - 3, fill="#2a2f3e", outline="")
        fill_w  = int(w * s / 100)
        bar_col = GREEN if s >= 55 else (RED if s < 45 else YELLOW)
        c.create_rectangle(0, 3, fill_w, h - 3, fill=bar_col, outline="")
        c.create_line(w // 2, 0, w // 2, h, fill=MUTED, width=1)

        # Real news headlines
        cat_colors = {"bullish": "#4ade80", "bearish": "#f87171", "neutral": "#9ca3af"}
        cat_tags   = {"bullish": "▲", "bearish": "▼", "neutral": "–"}
        news = self._last_news
        for i, (dot, lbl) in enumerate(self.news_labels):
            if i < len(news):
                title, source, cat = news[i]
                color  = cat_colors[cat]
                marker = cat_tags[cat]
                dot.config(fg=color)
                src_txt = f"  [{source}]" if source else ""
                # truncate title if too long
                display = title if len(title) <= 90 else title[:87] + "…"
                lbl.config(text=f"{marker} {display}{src_txt}", fg=color)
            else:
                dot.config(fg=MUTED); lbl.config(text="", fg=MUTED)

    # ─── chart-only fast refresh (2 s) ───────────────────
    def _refresh_chart_only(self):
        if self._chart_fetching: return
        self._chart_fetching = True
        threading.Thread(target=self._fetch_chart_only, daemon=True).start()

    def _fetch_chart_only(self):
        sym      = self.sel_symbol.get()
        interval = self.sel_interval.get()
        now_t    = time.time()
        if (sym != self._klines_cache_sym or
                interval != self._klines_cache_iv or
                now_t - self._klines_cache_ts > 30):
            fresh = get_klines_ohlcv(sym, interval, 120)
            if fresh:
                self._klines_cache     = fresh
                self._klines_cache_sym = sym
                self._klines_cache_iv  = interval
                self._klines_cache_ts  = now_t
        if not self._klines_cache:
            self._chart_fetching = False; return
        ohlcv = list(self._klines_cache)
        live  = get_symbol_price(sym)
        if live > 0 and ohlcv:
            last = dict(ohlcv[-1])
            last["c"] = live
            last["h"] = max(last["h"], live)
            last["l"] = min(last["l"], live)
            ohlcv[-1] = last
        self._chart_fetching = False
        self.root.after(0, lambda o=ohlcv, iv=interval: self._draw_chart(sym, o, iv))

    def _schedule_chart_refresh(self):
        self._refresh_chart_only()
        self.root.after(2000, self._schedule_chart_refresh)

    def _schedule_auto_refresh(self):
        self._refresh_chart_and_stats()
        self.root.after(30000, self._schedule_auto_refresh)

    # ─── log polling ──────────────────────────────────────
    def _poll_logs(self):
        new_msgs = []
        try:
            while True:
                new_msgs.append(log_queue.get_nowait())
        except queue.Empty:
            pass

        if new_msgs:
            self.log_box.config(state="normal")
            content = self.log_box.get("1.0", "end").strip()
            if content == "Waiting for trade signals...":
                self.log_box.delete("1.0", "end")
            for msg in new_msgs:
                m = msg.lower()
                if "buy filled" in m or "] buy " in m:
                    tag = "buy"
                elif ("sell filled" in m or "] sell " in m) and ("profit" in m or "✓" in m):
                    tag = "profit"
                elif "sell filled" in m or "] sell " in m:
                    tag = "sell"
                elif "take-profit" in m:
                    tag = "sltp"
                elif "stop-loss" in m:
                    tag = "loss"
                elif "fee" in m:
                    tag = "fee"
                elif "error" in m or "failed" in m:
                    tag = "err"
                elif "scanning" in m or "waiting" in m or "watching" in m:
                    tag = "scan"
                else:
                    tag = "info"
                self.log_box.insert("end", msg + "\n", tag)
            self.log_box.see("end")
            lines = int(self.log_box.index("end-1c").split(".")[0])
            if lines > 600:
                self.log_box.delete("1.0", "200.0")
            self.log_box.config(state="disabled")
        elif not bot_running:
            content = self.log_box.get("1.0", "end").strip()
            if not content:
                self.log_box.config(state="normal")
                self.log_box.insert("end", "Waiting for trade signals...\n", "waiting")
                self.log_box.config(state="disabled")

        self.root.after(400, self._poll_logs)


# ═══════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.2)
    except Exception:
        pass
    app = BotGUI(root)
    root.mainloop()
