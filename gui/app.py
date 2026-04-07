#!/usr/bin/env python3
"""
Binance Trading Bot — GUI Entry Point.

Run from the project root:
  python gui/app.py
  python -m gui.app
"""
from __future__ import annotations
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import TradingConfig, load_config, save_config
from core.exchange import BinanceClient
from core.portfolio import Portfolio
from core.risk import RiskManager
from core.notifications import NotificationManager
from core.strategies import StrategyFactory
from utils.logger import setup_logging, get_logger
from utils.security import CredentialStore

from gui.panels.settings_panel import SettingsPanel
from gui.panels.chart_panel import ChartPanel
from gui.panels.scanner_panel import ScannerPanel
from gui.panels.positions_panel import PositionsPanel
from gui.panels.log_panel import LogPanel

BG = "#0e1117"; CARD = "#161b26"; TEXT = "#e8eaf0"; MUTED = "#6b7280"
GREEN = "#22c55e"; RED = "#ef4444"

log = get_logger("app")


def _import_trading_bot():
    from bot import TradingBot
    return TradingBot


class BotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Binance Auto Trading Bot  |  Simulator / Testnet / Live")
        self.configure(bg=BG)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        win_w = max(1280, int(sw * 0.92))
        win_h = max(800,  int(sh * 0.92))
        self.geometry(f"{win_w}x{win_h}+{(sw - win_w)//2}+{(sh - win_h)//2}")
        self.minsize(1100, 700)

        # Load config + init services
        _cfg_path = Path(__file__).parent.parent / "config.json"
        self._config    = load_config(str(_cfg_path))
        self._store     = CredentialStore(self._config.storage_dir)
        self._exchange  = BinanceClient("", "", self._config)
        self._portfolio = Portfolio(self._config.db_file, self._config.mode,
                                    self._config.sim_principal)
        self._risk_mgr  = RiskManager(self._config)
        self._notifier  = NotificationManager(self._config)
        self._bot_instance = None
        self._bot_thread: threading.Thread | None = None
        self._sentiment: float = 55.0
        self._manual_mode  = False
        self._last_trade_count = 0   # tracks closed trades for notification
        # Sash proportions — preserved across window resizes and updated on drag
        self._sash_ratio: float = 0.27   # main left/right
        self._v_ratio:    float = 0.55   # chart (top) vs bottom row
        self._h_ratio:    float = 0.57   # scanner+log vs positions
        self._sl_ratio:   float = 0.55   # scanner vs log

        setup_logging(self._config.log_file)
        self._build_ui()
        self._schedule_stats_refresh()
        self._schedule_pending_check()
        log.info("Binance Trading Bot GUI started")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Top bar
        top = tk.Frame(self, bg=CARD, pady=6)
        top.pack(fill="x", side="top")
        tk.Label(top, text="Binance Auto Trading Bot",
                 bg=CARD, fg=TEXT, font=("Helvetica", 14, "bold")).pack(
                     side="left", padx=16)
        self._mode_badge = tk.Label(top, text="SIMULATOR", bg="#7c3aed", fg="white",
                                     font=("Helvetica", 9, "bold"), padx=8, pady=3)
        self._mode_badge.pack(side="left", padx=4)
        self._env_lbl = tk.Label(top, text="● Simulation mode — no real funds at risk",
                                  bg=CARD, fg="#a78bfa", font=("Helvetica", 10))
        self._env_lbl.pack(side="left", padx=8)
        self._status_lbl = tk.Label(top, text="● Stopped", bg=CARD, fg=RED,
                                     font=("Helvetica", 11, "bold"))
        self._status_lbl.pack(side="left", padx=8)
        self._fee_lbl = tk.Label(
            top,
            text=f"Binance Spot  fee {self._config.fee_rate*100:.1f}%/order",
            bg=CARD, fg=MUTED, font=("Helvetica", 10))
        self._fee_lbl.pack(side="right", padx=16)

        # Manual / Auto toggle
        self._manual_var = tk.BooleanVar(value=False)
        self._manual_btn = tk.Button(
            top, text="⚡ AUTO", bg="#22c55e", fg="white",
            font=("Helvetica", 9, "bold"), relief="flat",
            padx=8, pady=3, cursor="hand2",
            command=self._toggle_manual_mode)
        self._manual_btn.pack(side="right", padx=(0, 6))

        # Trade-close notification banner (hidden by default)
        self._alert_frame = tk.Frame(self, bg="#1a3a1a", pady=5)
        self._alert_lbl   = tk.Label(
            self._alert_frame, text="", bg="#1a3a1a", fg=GREEN,
            font=("Helvetica", 10, "bold"), anchor="w", padx=12)
        self._alert_lbl.pack(side="left", fill="x", expand=True)
        tk.Button(
            self._alert_frame, text="✕", bg="#1a3a1a", fg=MUTED,
            relief="flat", font=("Helvetica", 10), cursor="hand2",
            command=self._hide_alert).pack(side="right", padx=8)

        # ── Main paned layout: left settings | right content ─────────────────
        paned = tk.PanedWindow(self, orient="horizontal", bg=BG, sashwidth=5)
        paned.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        left_frame  = tk.Frame(paned, bg=BG)
        right_frame = tk.Frame(paned, bg=BG)
        paned.add(left_frame,  minsize=280)
        paned.add(right_frame, minsize=600)
        self._paned = paned

        # Left: settings
        self._settings = SettingsPanel(
            left_frame,
            config           = self._config,
            on_start         = self._on_bot_start,
            on_stop          = self._on_bot_stop,
            on_reset         = self._on_reset,
            on_config_change = self._on_config_change,
            credential_store = self._store,
        )
        self._settings.pack(fill="both", expand=True)

        # ── Right: single-view multi-pane layout ──────────────────────────────
        # Outer vertical split: chart (top) | bottom row
        self._v_paned = tk.PanedWindow(right_frame, orient="vertical",
                                        bg=BG, sashwidth=4)
        self._v_paned.pack(fill="both", expand=True)

        # Top: Chart
        chart_frame = tk.Frame(self._v_paned, bg=BG)
        self._chart = ChartPanel(chart_frame, exchange=self._exchange)
        self._chart.pack(fill="both", expand=True)

        # Bottom row: horizontal split — scanner+log (left) | positions (right)
        self._h_paned = tk.PanedWindow(self._v_paned, orient="horizontal",
                                        bg=BG, sashwidth=4)

        # Bottom-left: scanner (top) + log (bottom)
        self._sl_paned = tk.PanedWindow(self._h_paned, orient="vertical",
                                         bg=BG, sashwidth=4)
        self._scanner = ScannerPanel(
            self._sl_paned,
            exchange         = self._exchange,
            symbols          = self._config.symbols,
            sentiment_getter = lambda: self._sentiment,
            kline_interval   = self._config.kline_interval,
        )
        self._log = LogPanel(self._sl_paned)
        self._sl_paned.add(self._scanner, minsize=100)
        self._sl_paned.add(self._log,     minsize=70)

        # Bottom-right: positions
        pos_frame = tk.Frame(self._h_paned, bg=BG)
        self._positions = PositionsPanel(
            pos_frame, portfolio=self._portfolio, exchange=self._exchange,
            risk_manager=self._risk_mgr,
            on_manual_sell=self._on_manual_sell)
        self._positions.pack(fill="both", expand=True)

        self._h_paned.add(self._sl_paned, minsize=340)
        self._h_paned.add(pos_frame,      minsize=240)

        self._v_paned.add(chart_frame,    minsize=260)
        self._v_paned.add(self._h_paned,  minsize=170)

        # Wire sash resize tracking for all paned windows
        self._paned.bind(   "<ButtonRelease-1>", self._on_sash_release)
        self._v_paned.bind( "<ButtonRelease-1>", self._on_sash_release)
        self._h_paned.bind( "<ButtonRelease-1>", self._on_sash_release)
        self._sl_paned.bind("<ButtonRelease-1>", self._on_sash_release)
        self.bind("<Configure>", self._on_win_resize)
        self.after(300, self._init_sash)

        # Status bar
        self._statusbar = tk.Label(
            self, anchor="w", padx=10,
            text="Ready  |  Select a mode and click ▶ Start Bot to begin.",
            bg="#080b12", fg=MUTED, font=("Helvetica", 9))
        self._statusbar.pack(fill="x", side="bottom", ipady=3)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Sash auto-resize ──────────────────────────────────────────────────────

    def _init_sash(self) -> None:
        w = self._paned.winfo_width()
        if w < 10:
            self.after(100, self._init_sash)
            return
        self._paned.sash_place(0, int(w * self._sash_ratio), 0)
        h = self._v_paned.winfo_height()
        if h > 10:
            self._v_paned.sash_place(0, 0, int(h * self._v_ratio))
        w2 = self._h_paned.winfo_width()
        if w2 > 10:
            self._h_paned.sash_place(0, int(w2 * self._h_ratio), 0)
        h2 = self._sl_paned.winfo_height()
        if h2 > 10:
            self._sl_paned.sash_place(0, 0, int(h2 * self._sl_ratio))

    def _on_win_resize(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        for paned, attr, orient in (
            (self._paned,    "_sash_ratio", "h"),
            (self._v_paned,  "_v_ratio",   "v"),
            (self._h_paned,  "_h_ratio",   "h"),
            (self._sl_paned, "_sl_ratio",  "v"),
        ):
            try:
                if orient == "h":
                    dim = paned.winfo_width()
                    if dim > 10:
                        paned.sash_place(0, int(dim * getattr(self, attr)), 0)
                else:
                    dim = paned.winfo_height()
                    if dim > 10:
                        paned.sash_place(0, 0, int(dim * getattr(self, attr)))
            except Exception:
                pass

    def _on_sash_release(self, event: tk.Event) -> None:
        src = event.widget
        mapping = {
            id(self._paned):    ("_sash_ratio", "h"),
            id(self._v_paned):  ("_v_ratio",    "v"),
            id(self._h_paned):  ("_h_ratio",    "h"),
            id(self._sl_paned): ("_sl_ratio",   "v"),
        }
        entry = mapping.get(id(src))
        if not entry:
            return
        attr, orient = entry
        try:
            coord = src.sash_coord(0)
            if orient == "h":
                dim = src.winfo_width()
                if dim > 0:
                    setattr(self, attr, coord[0] / dim)
            else:
                dim = src.winfo_height()
                if dim > 0:
                    setattr(self, attr, coord[1] / dim)
        except Exception:
            pass

    # ── Bot lifecycle ─────────────────────────────────────────────────────────

    def _on_bot_start(self) -> None:
        cfg = self._config
        api_key, api_secret = self._settings.get_api_credentials()
        if cfg.mode != "sim" and (not api_key or not api_secret):
            messagebox.showerror("Missing API Keys",
                                 "Enter API key and secret for testnet/live mode.")
            return

        # Reinitialize components with current config + credentials
        self._exchange  = BinanceClient(api_key, api_secret, cfg)
        self._portfolio = Portfolio(cfg.db_file, cfg.mode, cfg.sim_principal)
        # Only reset sim/testnet if no open positions exist.
        # Resetting on every start would wipe positions after a GUI restart.
        if cfg.mode in ("sim", "testnet"):
            if not self._portfolio.get_open_positions():
                self._portfolio.reset_mode(cfg.sim_principal)
        self._risk_mgr  = RiskManager(cfg)
        self._notifier.update_config(cfg)

        TradingBot = _import_trading_bot()
        strategy = StrategyFactory.get_strategy(cfg.strategy, cfg.strategy_params)
        self._bot_instance = TradingBot(
            cfg, self._exchange, self._portfolio, self._risk_mgr, self._notifier)
        self._bot_instance.strategy = strategy

        self._bot_thread = threading.Thread(
            target=self._bot_instance.start, daemon=True, name="bot-loop")
        self._bot_thread.start()

        # Update UI
        mode_bg   = {"sim": "#7c3aed", "testnet": "#0891b2", "live": "#ef4444"}
        mode_text = {"sim": "SIMULATOR", "testnet": "TESTNET", "live": "LIVE ⚠"}
        self._mode_badge.config(
            text=mode_text.get(cfg.mode, ""),
            bg=mode_bg.get(cfg.mode, BG))
        self._status_lbl.config(text="● Running", fg=GREEN)
        self._statusbar.config(
            text=f"Bot running  |  Mode: {cfg.mode.upper()}  |  "
                 f"Strategy: {cfg.strategy}  |  Symbols: {', '.join(cfg.symbols)}")
        self._chart.update_exchange(self._exchange)
        self._scanner.update_symbols(cfg.symbols)
        self._scanner.update_exchange(self._exchange)
        self._scanner.update_kline_interval(cfg.kline_interval)
        self._positions.update_exchange(self._exchange)
        self._positions.update_portfolio(self._portfolio)
        self._positions.update_risk_manager(self._risk_mgr)
        self._fee_lbl.config(
            text=f"Binance Spot  fee {cfg.fee_rate*100:.1f}%/order")
        # Apply current manual mode to new bot instance
        self._bot_instance.manual_mode = self._manual_mode
        log.info(f"Bot started — mode={cfg.mode}, strategy={cfg.strategy}, "
                 f"symbols={cfg.symbols}")

    def _on_bot_stop(self) -> None:
        if self._bot_instance:
            self._bot_instance.stop()
        self._status_lbl.config(text="● Stopped", fg=RED)
        self._statusbar.config(text="Bot stopped.")
        log.info("Bot stopped by user")

    def _on_reset(self) -> None:
        self._portfolio.reset_sim(self._config.sim_principal)

    def _on_config_change(self, new_config: TradingConfig) -> None:
        self._config = new_config
        self._fee_lbl.config(
            text=f"Binance Spot  fee {new_config.fee_rate*100:.1f}%/order")
        # If bot is running, restart it with new config
        if self._bot_instance:
            self._bot_instance.stop()
            self.after(500, self._on_bot_start)

    # ── Manual mode ───────────────────────────────────────────────────────────

    def _toggle_manual_mode(self) -> None:
        self._manual_mode = not self._manual_mode
        if self._manual_mode:
            self._manual_btn.config(text="✋ MANUAL", bg="#f59e0b")
        else:
            self._manual_btn.config(text="⚡ AUTO",   bg="#22c55e")
        if self._bot_instance:
            self._bot_instance.manual_mode = self._manual_mode
        mode_label = "MANUAL — confirm each trade" if self._manual_mode else "AUTO — strategy executes automatically"
        log.info(f"Trading mode switched to {mode_label}")

    def _on_manual_sell(self, symbol: str) -> None:
        """Called from PositionsPanel when user clicks Sell Selected."""
        if self._bot_instance:
            self._bot_instance.execute_manual_sell(symbol)
        else:
            log.warning(f"Manual sell requested for {symbol} but bot is not running")

    # ── Pending order confirmation ─────────────────────────────────────────────

    def _schedule_pending_check(self) -> None:
        self._check_pending_orders()
        self.after(600, self._schedule_pending_check)

    def _check_pending_orders(self) -> None:
        if not self._bot_instance or not self._bot_instance.manual_mode:
            return
        try:
            order = self._bot_instance.pending_orders.get_nowait()
            self.after(0, self._show_pending_dialog, order)
        except queue.Empty:
            pass

    def _show_pending_dialog(self, order: dict) -> None:
        sym    = order["symbol"]
        action = order["action"]
        price  = order["price"]
        reason = order.get("reason", "")
        msg = (f"Strategy signal:  {action} {sym}\n"
               f"Signal price:  ${price:,.4f}\n"
               f"Reason:  {reason}\n\n"
               f"Execute this trade now?")
        if messagebox.askyesno(f"Manual {action} — {sym}", msg):
            threading.Thread(
                target=self._bot_instance.execute_pending,
                args=(order,), daemon=True).start()
        else:
            log.info(f"[{sym}] Manual {action} dismissed by user")

    # ── Trade-close notification banner ───────────────────────────────────────

    def _show_alert(self, text: str, color: str = GREEN) -> None:
        self._alert_lbl.config(text=text, fg=color)
        self._alert_frame.config(bg="#1a3a1a" if color == GREEN else "#3a1a1a")
        self._alert_lbl.config(bg="#1a3a1a" if color == GREEN else "#3a1a1a")
        self._alert_frame.pack(fill="x", after=self.children.get("!frame", None))
        # Find the top bar and insert banner right below it
        self._alert_frame.pack(fill="x", side="top", before=self._paned)
        self.after(10000, self._hide_alert)

    def _hide_alert(self) -> None:
        self._alert_frame.pack_forget()

    # ── Stats refresh ─────────────────────────────────────────────────────────

    def _schedule_stats_refresh(self) -> None:
        self._refresh_stats()
        self.after(5000, self._schedule_stats_refresh)

    def _refresh_stats(self) -> None:
        threading.Thread(target=self._do_refresh_stats, daemon=True).start()

    def _do_refresh_stats(self) -> None:
        try:
            if self._bot_instance is not None:
                self._sentiment = self._bot_instance._sentiment

            prices: dict[str, float] = {}
            for sym in self._config.symbols:
                try:
                    prices[sym] = self._exchange.get_ticker_price(sym)
                except Exception:
                    pass

            # Real USDT balance: exchange API for live only.
            # Sim and testnet both track spending in SQLite starting from sim_principal.
            if self._config.mode == "live":
                try:
                    usdt_bal = self._exchange.get_balance("USDT")
                except Exception:
                    usdt_bal = self._portfolio.get_balance("USDT")
            else:
                usdt_bal = self._portfolio.get_balance("USDT")

            stats = self._portfolio.get_stats(prices or None, usdt_balance=usdt_bal)
            try:
                self.after(0, self._settings.update_account_stats, stats, usdt_bal)
            except RuntimeError:
                pass

            # Detect newly closed trades and show notification banner
            if stats.trade_count > self._last_trade_count:
                self._last_trade_count = stats.trade_count
                try:
                    history = self._portfolio.get_trade_history(limit=1)
                    sells   = [t for t in history if t.side == "SELL"]
                    if sells:
                        t      = sells[0]
                        pnl    = t.pnl_usd or 0.0
                        pnl_p  = (t.pnl_pct or 0.0) * 100
                        sign   = "+" if pnl >= 0 else ""
                        color  = GREEN if pnl >= 0 else RED
                        reason = (t.reason or "").replace("\n", " ")
                        msg    = (f"✓ Position Closed — {t.symbol}  "
                                  f"Qty: {t.qty:.6f}  "
                                  f"Exit: ${t.price:,.4f}  "
                                  f"P&L: {sign}${pnl:.2f} ({sign}{pnl_p:.2f}%)  "
                                  f"[{reason}]")
                        self.after(0, self._show_alert, msg, color)
                        self.after(0, self._positions.flash_closed_tab)
                except Exception:
                    pass
        except Exception as e:
            log.debug(f"Stats refresh error: {e}")

    # ── Window close ──────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        if self._bot_instance:
            self._bot_instance.stop()
        self._notifier.shutdown()
        self.destroy()


def _run_terminal(args) -> None:
    """Start the bot in terminal/TUI mode (no Tkinter)."""
    import getpass
    from pathlib import Path

    from core.config import load_config, validate_config
    from utils.logger import setup_logging, get_logger
    from utils.security import AuthenticationError, CredentialStore
    from bot import build_bot
    from gui.terminal_ui import TerminalUI
    log = get_logger("app")

    _default_cfg = str(Path(__file__).parent.parent / "config.json")
    cfg_path = args.config or _default_cfg
    cfg = load_config(cfg_path)
    if args.mode:
        cfg.mode = args.mode

    setup_logging(cfg.log_file)

    errors = validate_config(cfg)
    if errors:
        for e in errors:
            print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    import os
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

    log.info(
        f"Config loaded: {cfg_path} | mode={cfg.mode} "
        f"symbols={cfg.symbols} loop_interval={cfg.loop_interval}s"
    )

    bot, portfolio, notifier = build_bot(cfg, api_key, api_secret)

    if cfg.mode in ("sim", "testnet"):
        if not portfolio.get_open_positions():
            portfolio.reset_mode(cfg.sim_principal)
            print(f"[{cfg.mode.upper()}] Portfolio reset — ${cfg.sim_principal:,.2f} USDT")

    TerminalUI(bot, portfolio, notifier).run()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Binance Trading Bot")
    parser.add_argument("--GUI", default="true", choices=["true", "false"],
                        help="Launch the GUI (default: true). Pass 'false' for terminal-only mode.")
    parser.add_argument("--config", default=None, help="Config JSON file (headless mode only)")
    parser.add_argument("--mode", choices=["sim", "testnet", "live"], default=None,
                        help="Override trading mode (headless mode only)")
    args = parser.parse_args()

    if args.GUI.lower() == "false":
        _run_terminal(args)
        return

    app = BotApp()
    app.mainloop()


if __name__ == "__main__":
    main()
