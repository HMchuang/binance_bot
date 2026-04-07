#!/usr/bin/env python3
"""
Headless Binance trading bot — runs without a GUI.
Suitable for server deployment.

Usage:
  python bot.py                        # uses config.json, sim mode
  python bot.py --mode testnet         # override mode
  python bot.py --config my.json       # custom config file

Master password for credentials:
  Set MASTER_PASSWORD env var, or enter interactively when prompted.
  In sim mode, no credentials are needed.
"""
from __future__ import annotations
import argparse
import getpass
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import time

from core.config import TradingConfig, load_config, validate_config
from core.exchange import BinanceClient
from core.indicators import calc_win_chance, calc_atr, is_mtf_bullish
from core.risk import calc_kelly_fraction
from core.notifications import NotificationEvent, NotificationManager
from core.portfolio import Portfolio
from core.risk import RiskManager
from core.strategies import BaseStrategy, Signal, StrategyFactory
from utils.logger import get_logger, setup_logging
from utils.security import AuthenticationError, CredentialStore

log = get_logger("bot")

# Cache for Fear & Greed Index
_FNG_CACHE: dict = {"value": None, "ts": 0.0}
_FNG_TTL = 300.0  # 5 minutes


class TradingBot:
    def __init__(self, config: TradingConfig, exchange: BinanceClient,
                 portfolio: Portfolio, risk_manager: RiskManager,
                 notifications: NotificationManager):
        self.config        = config
        self.exchange      = exchange
        self.portfolio     = portfolio
        self.risk_manager  = risk_manager
        self.notifications = notifications
        self.strategy: BaseStrategy = StrategyFactory.get_strategy(config.strategy, config.strategy_params)
        self._stop_event   = threading.Event()
        self._sentiment    = 55.0
        # Manual trading mode — when True, strategy signals queue instead of auto-execute
        self.manual_mode   = False
        self.pending_orders: queue.Queue = queue.Queue(maxsize=20)

    def start(self) -> None:
        mode = {"sim": "SIMULATOR", "testnet": "TESTNET", "live": "LIVE ⚠"}.get(
            self.config.mode, self.config.mode.upper())
        log.info(
            f"=== Bot started [{mode}] strategy={self.config.strategy} "
            f"symbols={self.config.symbols} "
            f"interval={self.config.loop_interval}s "
            f"kline={self.config.kline_interval} "
            f"TP={self.config.take_profit_pct*100:.1f}% "
            f"SL={self.config.stop_loss_pct*100:.1f}% "
            f"fee={self.config.fee_rate*100:.2f}%/order ==="
        )
        while not self._stop_event.is_set():
            try:
                log.info(f"--- Scanning {time.strftime('%H:%M:%S')} ---")
                self._sentiment = self._update_sentiment()
                self._run_cycle()
            except Exception as e:
                log.error(f"Main loop error: {e}")
                time.sleep(30)
                continue
            # Interruptible sleep
            for _ in range(self.config.loop_interval):
                if self._stop_event.is_set():
                    break
                time.sleep(1)
        log.info("Bot stopped")

    def stop(self) -> None:
        self._stop_event.set()

    def _run_cycle(self) -> None:
        for sym in self.config.symbols:
            try:
                # Fetch full OHLCV — needed by RegimeAwareStrategy (ADX, Stoch, OBV)
                ohlcv = self.exchange.get_klines_ohlcv(sym, self.config.kline_interval, 100)
                if len(ohlcv) < 50:
                    log.warning(f"[{sym}] Not enough candles ({len(ohlcv)}), skipping")
                    continue

                closes  = [b["c"] for b in ohlcv]
                highs   = [b["h"] for b in ohlcv]
                lows    = [b["l"] for b in ohlcv]
                volumes = [b["v"] for b in ohlcv]

                # Replace the last (forming) candle's close with the live price so
                # indicators respond to real-time price movement.
                try:
                    live = self.exchange.get_ticker_price(sym)
                    if live > 0:
                        closes[-1] = live
                        # Keep high/low consistent with the live price
                        highs[-1]  = max(highs[-1], live)
                        lows[-1]   = min(lows[-1],  live)
                except Exception:
                    pass

                price       = closes[-1]
                open_pos    = self.portfolio.get_open_position(sym)
                holding_qty = open_pos.qty if open_pos else 0.0

                # ── Multi-Timeframe filter ─────────────────────────────────
                # Fetch higher-TF candles and check if the broader trend is
                # bullish.  When bearish on the higher TF, long entries are
                # blocked in the strategy (MTF veto).
                mtf_bullish = None
                mtf_iv = self.config.mtf_interval
                if mtf_iv and mtf_iv != self.config.kline_interval:
                    try:
                        mtf_ohlcv  = self.exchange.get_klines_ohlcv(sym, mtf_iv, 100)
                        mtf_closes = [b["c"] for b in mtf_ohlcv]
                        mtf_bullish = is_mtf_bullish(mtf_closes)
                    except Exception:
                        pass

                # ── 1. Drawdown circuit breaker ────────────────────────────
                # Pause trading on this symbol if account has dropped >20%
                # from the starting principal.  Non-fatal: never blocks on error.
                try:
                    stats = self.portfolio.get_stats({sym: price})
                    if self.risk_manager.check_drawdown_limit(
                            stats.current_value, stats.principal, 0.20):
                        log.warning(
                            f"[{sym}] Drawdown circuit breaker — "
                            f"${stats.current_value:.0f} vs principal "
                            f"${stats.principal:.0f}, pausing"
                        )
                        continue
                except Exception:
                    pass

                # ── 2. Check risk exits FIRST ──────────────────────────────
                if open_pos and holding_qty > 0:
                    exit_sig = self.risk_manager.check_exit(
                        sym, price, open_pos.entry_price, holding_qty)
                    if exit_sig:
                        log.info(f"[{sym}] EXIT — {exit_sig.reason}")
                        self._execute_sell(sym, price, exit_sig.reason)
                        self.risk_manager.on_position_closed(sym)
                        # Set cooldown after a stop-loss to prevent revenge entries
                        if exit_sig.exit_type == "sl":
                            self.risk_manager.on_stop_loss(
                                sym, self.config.cooldown_minutes)
                        continue

                # ── 3. Strategy signal ─────────────────────────────────────
                result = self.strategy.get_signal(
                    sym, closes, holding_qty, self._sentiment,
                    highs=highs, lows=lows, volumes=volumes,
                    buy_win_thresh=self.config.buy_win_thresh,
                    sell_win_thresh=self.config.sell_win_thresh,
                    mtf_bullish=mtf_bullish,
                )
                log.info(f"[{sym}] price={price:.4f} holding={holding_qty:.6f} "
                         f"signal={result.signal.value.upper()} — {result.reason}")

                if result.signal == Signal.BUY and holding_qty <= 0:
                    # Respect post-SL cooldown before any new entry
                    if self.risk_manager.is_in_cooldown(sym):
                        log.info(f"[{sym}] HOLD — post-SL cooldown active, skipping buy")
                    elif self.manual_mode:
                        try:
                            self.pending_orders.put_nowait({
                                "action": "BUY", "symbol": sym, "price": price,
                                "closes": closes, "highs": highs, "lows": lows,
                                "reason": result.reason,
                            })
                            log.info(f"[{sym}] [MANUAL] BUY signal — awaiting your confirmation")
                        except queue.Full:
                            pass
                    else:
                        self._execute_buy(sym, price, closes, highs, lows)

                elif result.signal == Signal.SELL and holding_qty > 0:
                    if self.manual_mode:
                        try:
                            self.pending_orders.put_nowait({
                                "action": "SELL", "symbol": sym, "price": price,
                                "reason": result.reason,
                            })
                            log.info(f"[{sym}] [MANUAL] SELL signal — awaiting your confirmation")
                        except queue.Full:
                            pass
                    else:
                        self._execute_sell(sym, price, "Strategy sell")
                        self.risk_manager.on_position_closed(sym)

                else:
                    log.info(f"[{sym}] HOLD — watching")

            except Exception as e:
                log.error(f"[{sym}] Cycle error: {e}")

    def _execute_buy(self, symbol: str, price: float, closes: list[float],
                     highs: list[float] | None = None,
                     lows:  list[float] | None = None) -> None:
        try:
            if self.config.is_sim:
                usdt_bal = self.portfolio.get_balance("USDT")
                step = 0.00001 if symbol.startswith("BTC") else 0.0001
                from core.exchange import LotSize
                lot = LotSize(min_qty=step, step_size=step, min_notional=0.0)
            elif self.config.mode == "testnet":
                # Use SQLite-tracked balance (starts from sim_principal) so
                # orders are not blocked by a depleted real testnet account.
                # Lot sizes are still fetched from the exchange for accuracy.
                usdt_bal = self.portfolio.get_balance("USDT")
                lot      = self.exchange.get_lot_size(symbol)
            else:
                usdt_bal = self.exchange.get_balance("USDT")
                lot      = self.exchange.get_lot_size(symbol)

            # ── ATR for dynamic stops and volatility-adjusted sizing ────────
            atr = 0.0
            if highs and lows:
                atr = calc_atr(highs, lows, closes)

            # ── Kelly Criterion sizing ──────────────────────────────────────
            # Only kicks in after ≥20 closed trades so early estimates are stable.
            # Falls back to config.order_pct when history is insufficient.
            kelly_fraction = 0.0
            vol_scalar     = 1.0
            try:
                stats = self.portfolio.get_stats()
                if stats.trade_count >= 20:
                    kelly_fraction = calc_kelly_fraction(
                        stats.win_rate, stats.avg_win_pct, stats.avg_loss_pct)
                    if kelly_fraction > 0:
                        log.info(
                            f"[{symbol}] Kelly fraction={kelly_fraction:.2%} "
                            f"(W={stats.win_rate:.0%} R={stats.avg_win_pct/max(stats.avg_loss_pct,1e-9):.2f})"
                        )
                # Volatility scalar: target ~2% ATR; scale down on high-vol
                if atr > 0 and price > 0:
                    atr_pct    = atr / price
                    vol_scalar = max(0.5, min(1.5, 0.02 / atr_pct))
            except Exception:
                pass

            qty = self.risk_manager.calc_position_size(
                usdt_bal, price, lot,
                kelly_fraction=kelly_fraction,
                vol_scalar=vol_scalar,
            )
            if qty <= 0:
                log.warning(f"[{symbol}] Buy qty is zero — skipping")
                return

            order_usdt = qty * price
            fee        = order_usdt * self.config.fee_rate
            mode_tag   = f"[{self.config.mode.upper()}]"

            if self.config.is_sim or self.config.mode == "testnet":
                self.portfolio.record_buy(symbol, qty, price, fee, self.config.strategy)
                log.info(f"[{symbol}] {mode_tag} BUY {qty:.6f} @${price:,.2f}  "
                         f"cost=${order_usdt:.2f}  fee=${fee:.4f}")
            else:
                if self.config.order_type == "LIMIT":
                    limit_price = price * (1.0 - self.config.limit_offset_pct)
                    r = self.exchange.place_limit_order(symbol, "BUY", qty, limit_price)
                elif self.config.order_type == "OCO":
                    tp_price = price * (1.0 + self.config.take_profit_pct)
                    sl_price = price * (1.0 - self.config.stop_loss_pct)
                    r = self.exchange.place_oco_order(symbol, "BUY", qty, tp_price, sl_price, sl_price * 0.999)
                else:
                    r = self.exchange.place_market_order(symbol, "BUY", qty)
                if not r or "orderId" not in r:
                    log.error(f"[{symbol}] Buy order failed: {r}")
                    return
                self.portfolio.record_buy(symbol, qty, price, fee, self.config.strategy)
                log.info(f"[{symbol}] {mode_tag} BUY filled #{r['orderId']}")

            self.risk_manager.on_position_opened(symbol, price)

            # Set ATR-based dynamic stops when OHLCV data is available
            if (atr > 0 and self.config.atr_sl_mult > 0 and self.config.atr_tp_mult > 0):
                self.risk_manager.set_dynamic_stops(
                    symbol, price, atr,
                    self.config.atr_tp_mult, self.config.atr_sl_mult,
                )

            self.notifications.send(NotificationEvent(
                event_type="buy", symbol=symbol, side="BUY",
                price=price, qty=qty, mode=self.config.mode,
            ))
        except Exception as e:
            log.error(f"[{symbol}] Buy execution error: {e}")

    def _execute_sell(self, symbol: str, price: float, reason: str) -> None:
        try:
            open_pos = self.portfolio.get_open_position(symbol)
            if not open_pos:
                log.warning(f"[{symbol}] No open position to sell")
                return

            qty      = open_pos.qty
            fee      = qty * price * self.config.fee_rate
            mode_tag = f"[{self.config.mode.upper()}]"

            if not self.config.is_sim and self.config.mode != "testnet":
                if self.config.order_type == "MARKET":
                    r = self.exchange.place_market_order(symbol, "SELL", qty)
                    if not r or "orderId" not in r:
                        log.error(f"[{symbol}] Sell order failed: {r}")
                        return
                    log.info(f"[{symbol}] {mode_tag} SELL filled #{r['orderId']}")

            trade = self.portfolio.record_sell(symbol, price, fee, reason)
            if trade:
                sign = "+" if (trade.pnl_usd or 0) >= 0 else ""
                log.info(f"[{symbol}] {mode_tag} SELL {qty:.6f} @${price:,.2f}  "
                         f"P&L {sign}${trade.pnl_usd:.2f} ({sign}{trade.pnl_pct*100:.2f}%) | {reason}")
                self.notifications.send(NotificationEvent(
                    event_type="sell", symbol=symbol, side="SELL",
                    price=price, qty=qty,
                    pnl_usd=trade.pnl_usd, pnl_pct=(trade.pnl_pct or 0) * 100,
                    reason=reason, mode=self.config.mode,
                ))
        except Exception as e:
            log.error(f"[{symbol}] Sell execution error: {e}")

    def _update_sentiment(self) -> float:
        """Fear & Greed Index (60%) blended with neutral technical (40%)."""
        fng = self._get_fear_greed()
        if fng is not None:
            return 0.6 * fng + 0.4 * 55.0
        return 55.0

    def _get_fear_greed(self) -> float | None:
        global _FNG_CACHE
        now = time.time()
        if _FNG_CACHE["value"] is not None and now - _FNG_CACHE["ts"] < _FNG_TTL:
            return float(_FNG_CACHE["value"])
        try:
            import requests
            r = requests.get("https://api.alternative.me/fng/", timeout=8)
            r.raise_for_status()
            val = int(r.json()["data"][0]["value"])
            _FNG_CACHE = {"value": val, "ts": now}
            log.info(f"Fear & Greed Index: {val}")
            return float(val)
        except Exception as e:
            log.warning(f"Fear & Greed fetch failed: {e}")
            return None

    # ── Manual trading ────────────────────────────────────────────────────────

    def execute_pending(self, order: dict) -> None:
        """Execute a queued pending order (called from GUI confirmation dialog)."""
        sym = order["symbol"]
        try:
            live = self.exchange.get_ticker_price(sym)
            price = live if live > 0 else order["price"]
        except Exception:
            price = order["price"]
        if order["action"] == "BUY":
            closes = order.get("closes") or [price] * 3
            self._execute_buy(sym, price, closes,
                              order.get("highs"), order.get("lows"))
        elif order["action"] == "SELL":
            self._execute_sell(sym, price, "Manual sell (confirmed)")
            self.risk_manager.on_position_closed(sym)

    def execute_manual_sell(self, symbol: str) -> None:
        """Immediately sell an open position regardless of auto/manual mode."""
        try:
            price = self.exchange.get_ticker_price(symbol)
        except Exception:
            pos = self.portfolio.get_open_position(symbol)
            price = pos.entry_price if pos else 0.0
        if price > 0:
            self._execute_sell(symbol, price, "Manual sell")
            self.risk_manager.on_position_closed(symbol)
        else:
            log.warning(f"[{symbol}] Manual sell skipped — could not get live price")


# ── Factory ──────────────────────────────────────────────────────────────────

def build_bot(cfg: TradingConfig, api_key: str = "",
              api_secret: str = "") -> tuple["TradingBot", Portfolio, NotificationManager]:
    """Construct and return bot + portfolio + notifier from a loaded config."""
    exchange  = BinanceClient(api_key, api_secret, cfg)
    portfolio = Portfolio(cfg.db_file, cfg.mode, cfg.sim_principal)
    risk_mgr  = RiskManager(cfg)
    notifier  = NotificationManager(cfg)
    bot       = TradingBot(cfg, exchange, portfolio, risk_mgr, notifier)
    return bot, portfolio, notifier


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    _default_cfg = str(Path(__file__).parent / "config.json")
    parser = argparse.ArgumentParser(description="Binance Trading Bot (headless)")
    parser.add_argument("--config", default=_default_cfg, help="Config JSON file")
    parser.add_argument("--mode", choices=["sim", "testnet", "live"], default=None,
                        help="Override trading mode")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.mode:
        cfg.mode = args.mode

    setup_logging(cfg.log_file)

    errors = validate_config(cfg)
    if errors:
        for e in errors:
            print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    # Load credentials
    api_key, api_secret = "", ""
    if cfg.mode != "sim":
        store     = CredentialStore(cfg.storage_dir)
        master_pw = os.getenv("MASTER_PASSWORD")
        if not master_pw and store.credentials_exist():
            master_pw = getpass.getpass("Master password: ")
        if master_pw and store.credentials_exist():
            store.set_master_password(master_pw)
            try:
                api_key, api_secret = store.load_credentials()
            except AuthenticationError as e:
                print(f"Authentication failed: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            api_key    = os.getenv("BINANCE_API_KEY", "")
            api_secret = os.getenv("BINANCE_API_SECRET", "")

    # Initialize components
    exchange  = BinanceClient(api_key, api_secret, cfg)
    portfolio = Portfolio(cfg.db_file, cfg.mode, cfg.sim_principal)
    risk_mgr  = RiskManager(cfg)
    notifier  = NotificationManager(cfg)
    bot       = TradingBot(cfg, exchange, portfolio, risk_mgr, notifier)

    # Graceful shutdown
    def _shutdown(sig, frame):
        log.info(f"Signal {sig} received — stopping bot...")
        bot.stop()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info(f"Starting bot: mode={cfg.mode}, strategy={cfg.strategy}, symbols={cfg.symbols}")
    bot.start()
    notifier.shutdown()


if __name__ == "__main__":
    main()
