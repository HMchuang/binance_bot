"""
Matplotlib candlestick chart with volume, RSI, and MACD subplots.
Displays two symbols simultaneously (default: BTCUSDT + ETHUSDT).

Refresh strategy:
  - Ticker price  : fetched on every cycle (as fast as API responds)
  - Full klines   : every 3 s per symbol (heavier OHLCV batch call)
  - Next cycle is queued immediately after each draw completes,
    so the chart updates as fast as the API allows.
"""
import threading
import time
import tkinter as tk
from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.gridspec as gridspec

from core.indicators import (
    calc_rsi_series, calc_macd_series,
    calc_bollinger_series, calc_ma_series,
)
from utils.logger import get_logger

matplotlib.rcParams.update({
    "font.size":        13,
    "axes.titlesize":   15,
    "axes.labelsize":   13,
    "xtick.labelsize":  11,
    "ytick.labelsize":  11,
    "legend.fontsize":  11,
    "font.family":      "sans-serif",
})

BG = "#0e1117"; CARD = "#161b26"; TEXT = "#e8eaf0"; MUTED = "#6b7280"
GREEN = "#22c55e"; RED = "#ef4444"; BLUE = "#3b82f6"; YELLOW = "#f59e0b"
ORANGE = "#f97316"
CHART_BG = "#0e1117"; CHART_GRID = "#1e2433"

log = get_logger("chart_panel")

INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]

_KLINES_TTL    = 3.0   # seconds before re-fetching full OHLCV
_MIN_DELAY_MS  = 100   # minimum ms between refresh cycles (safety throttle)


class ChartPanel(tk.Frame):
    def __init__(self, parent: tk.Widget, exchange, **kwargs):
        super().__init__(parent, bg=BG, **kwargs)
        self._exchange    = exchange
        self._symbol      = "BTCUSDT"
        self._symbol2     = "ETHUSDT"
        self._interval    = "1m"
        self._fetching    = False
        self._klines_cache: dict = {}   # (sym, iv) -> (timestamp, [ohlcv])
        self._live_price:  float = 0.0
        self._live_price2: float = 0.0
        self._build()
        self._fetch_and_draw()   # kick off; self-rescheduling via _draw's finally

    # ── Build ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        ctrl = tk.Frame(self, bg=CARD, pady=6)
        ctrl.pack(fill="x", padx=4)

        tk.Label(ctrl, text="Symbol 1:", bg=CARD, fg=MUTED,
                 font=("Helvetica", 11)).pack(side="left", padx=(10, 2))
        self._sym_var = tk.StringVar(value=self._symbol)
        sym_e = ttk.Entry(ctrl, textvariable=self._sym_var, width=10,
                          font=("Helvetica", 11))
        sym_e.pack(side="left", padx=(0, 4))
        sym_e.bind("<Return>", lambda e: self._apply_symbols())

        tk.Label(ctrl, text="Symbol 2:", bg=CARD, fg=MUTED,
                 font=("Helvetica", 11)).pack(side="left", padx=(6, 2))
        self._sym2_var = tk.StringVar(value=self._symbol2)
        sym2_e = ttk.Entry(ctrl, textvariable=self._sym2_var, width=10,
                           font=("Helvetica", 11))
        sym2_e.pack(side="left", padx=(0, 8))
        sym2_e.bind("<Return>", lambda e: self._apply_symbols())

        tk.Label(ctrl, text="Interval:", bg=CARD, fg=MUTED,
                 font=("Helvetica", 11)).pack(side="left", padx=(0, 2))
        self._iv_var = tk.StringVar(value=self._interval)
        iv_cb = ttk.Combobox(ctrl, textvariable=self._iv_var,
                              values=INTERVALS, state="readonly", width=5,
                              font=("Helvetica", 11))
        iv_cb.pack(side="left", padx=(0, 10))
        iv_cb.bind("<<ComboboxSelected>>", lambda e: self._apply_symbols())

        # Price labels — right side (ETH outermost so BTC is closer to center)
        self._change2_lbl = tk.Label(ctrl, text="", bg=CARD, fg=MUTED,
                                      font=("Helvetica", 11))
        self._change2_lbl.pack(side="right", padx=(0, 10))
        self._price2_lbl = tk.Label(ctrl, text="—", bg=CARD, fg=ORANGE,
                                     font=("Helvetica", 13, "bold"))
        self._price2_lbl.pack(side="right", padx=(2, 0))
        self._sym2_lbl = tk.Label(ctrl, text="ETH:", bg=CARD, fg=ORANGE,
                                   font=("Helvetica", 11))
        self._sym2_lbl.pack(side="right", padx=(12, 2))

        self._change_lbl = tk.Label(ctrl, text="", bg=CARD, fg=MUTED,
                                     font=("Helvetica", 11))
        self._change_lbl.pack(side="right", padx=(0, 4))
        self._price_lbl = tk.Label(ctrl, text="—", bg=CARD, fg=TEXT,
                                    font=("Helvetica", 13, "bold"))
        self._price_lbl.pack(side="right", padx=(2, 0))
        self._sym1_lbl = tk.Label(ctrl, text="BTC:", bg=CARD, fg=TEXT,
                                   font=("Helvetica", 11))
        self._sym1_lbl.pack(side="right", padx=(12, 2))

        # Figure: BTC price / ETH price / Volume / RSI / MACD
        self._fig    = Figure(figsize=(10, 8), facecolor=CHART_BG)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        self._canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)

        gs = gridspec.GridSpec(5, 1, height_ratios=[3, 2, 1, 1, 1],
                               hspace=0.05, figure=self._fig)
        self._ax_btc  = self._fig.add_subplot(gs[0])
        self._ax_eth  = self._fig.add_subplot(gs[1], sharex=self._ax_btc)
        self._ax_vol  = self._fig.add_subplot(gs[2], sharex=self._ax_btc)
        self._ax_rsi  = self._fig.add_subplot(gs[3], sharex=self._ax_btc)
        self._ax_macd = self._fig.add_subplot(gs[4], sharex=self._ax_btc)

    # ── Public API ───────────────────────────────────────────────────────────

    def _apply_symbols(self) -> None:
        s1 = self._sym_var.get().upper().strip()
        s2 = self._sym2_var.get().upper().strip()
        iv = self._iv_var.get()
        if s1 and s1 != self._symbol:
            self._klines_cache.pop((self._symbol, self._interval), None)
            self._symbol = s1
        if s2 and s2 != self._symbol2:
            self._klines_cache.pop((self._symbol2, self._interval), None)
            self._symbol2 = s2
        self._interval = iv
        self._sym_var.set(self._symbol)
        self._sym2_var.set(self._symbol2)
        self._sym1_lbl.config(text=f"{self._symbol[:3]}:")
        self._sym2_lbl.config(text=f"{self._symbol2[:3]}:")
        self._fetch_and_draw()

    def set_symbol_interval(self, symbol: str, interval: str) -> None:
        self._klines_cache.pop((self._symbol, interval), None)
        self._symbol   = symbol
        self._interval = interval
        self._sym_var.set(symbol)
        self._iv_var.set(interval)
        self._fetch_and_draw()

    def update_exchange(self, exchange) -> None:
        self._exchange = exchange

    # ── Refresh loop ─────────────────────────────────────────────────────────

    def _fetch_and_draw(self) -> None:
        if self._fetching:
            return
        self._fetching = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        data1 = data2 = None
        scheduled = False
        try:
            now = time.time()

            # Live ticker prices (always fetch — fast path)
            try:
                live1 = self._exchange.get_ticker_price(self._symbol)
                self._live_price = live1
            except Exception:
                live1 = self._live_price

            try:
                live2 = self._exchange.get_ticker_price(self._symbol2)
                self._live_price2 = live2
            except Exception:
                live2 = self._live_price2

            # Full OHLCV for symbol 1 (slow path — cached)
            key1 = (self._symbol, self._interval)
            cached1 = self._klines_cache.get(key1)
            if cached1 and now - cached1[0] < _KLINES_TTL:
                data1 = list(cached1[1])
            else:
                raw1 = self._exchange.get_klines_ohlcv(
                    self._symbol, self._interval, 120)
                if raw1:
                    self._klines_cache[key1] = (now, raw1)
                    data1 = list(raw1)

            # Full OHLCV for symbol 2 (slow path — cached)
            key2 = (self._symbol2, self._interval)
            cached2 = self._klines_cache.get(key2)
            if cached2 and now - cached2[0] < _KLINES_TTL:
                data2 = list(cached2[1])
            else:
                raw2 = self._exchange.get_klines_ohlcv(
                    self._symbol2, self._interval, 120)
                if raw2:
                    self._klines_cache[key2] = (now, raw2)
                    data2 = list(raw2)

            # Patch last forming candle with live price
            for data, live in ((data1, live1), (data2, live2)):
                if data and live:
                    last = data[-1]
                    data[-1] = {
                        "o": last["o"],
                        "h": max(last["h"], live),
                        "l": min(last["l"], live),
                        "c": live,
                        "v": last["v"],
                    }

            if data1 is not None or data2 is not None:
                self.after(0, self._draw, data1 or [], data2 or [])
                scheduled = True

        except Exception as e:
            log.warning(f"Chart fetch error: {e}")
        finally:
            self._fetching = False
            if not scheduled:
                # No data drawn — retry after a short delay
                self.after(_MIN_DELAY_MS * 5, self._fetch_and_draw)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self, ohlcv1: list[dict], ohlcv2: list[dict]) -> None:
        try:
            all_axes = (self._ax_btc, self._ax_eth,
                        self._ax_vol, self._ax_rsi, self._ax_macd)
            for ax in all_axes:
                ax.cla()
                ax.set_facecolor(CHART_BG)
                ax.tick_params(colors=MUTED, labelsize=11)
                for spine in ax.spines.values():
                    spine.set_color(CHART_GRID)
                ax.yaxis.tick_right()

            w = 0.6

            # ── Symbol 1 (BTC) price chart ────────────────────────────────
            if ohlcv1:
                opens1  = [d["o"] for d in ohlcv1]
                closes1 = [d["c"] for d in ohlcv1]
                vols1   = [d["v"] for d in ohlcv1]
                live1   = closes1[-1]
                color1  = GREEN if live1 >= opens1[-1] else RED

                self._draw_candles(self._ax_btc, ohlcv1, w)
                self._draw_bb_ma(self._ax_btc, closes1)
                self._draw_live_line(self._ax_btc, live1, color1)

                self._ax_btc.set_ylabel("Price", color=TEXT, fontsize=12)
                self._ax_btc.legend(loc="upper left", fancybox=False,
                                    framealpha=0.2, labelcolor=TEXT, fontsize=10)
                self._ax_btc.grid(color=CHART_GRID, linewidth=0.4, zorder=0)
                self._ax_btc.set_title(
                    f"{self._symbol}  ·  {self._interval}",
                    color=TEXT, fontsize=13, pad=6)
                plt.setp(self._ax_btc.get_xticklabels(), visible=False)

                # Price label top-bar
                self._price_lbl.config(text=f"${live1:,.2f}")
                if len(closes1) >= 2:
                    chg = (closes1[-1] - closes1[-2]) / closes1[-2] * 100
                    self._change_lbl.config(
                        text=f"{chg:+.2f}%", fg=GREEN if chg >= 0 else RED)

                # Volume (from primary symbol)
                for i, (o, c, v) in enumerate(zip(opens1, closes1, vols1)):
                    self._ax_vol.bar(i, v,
                                     color=GREEN if c >= o else RED,
                                     width=w, linewidth=0)
                self._ax_vol.set_ylabel("Vol", color=TEXT, fontsize=11)
                self._ax_vol.grid(color=CHART_GRID, linewidth=0.4)
                plt.setp(self._ax_vol.get_xticklabels(), visible=False)

                # RSI (from primary symbol)
                rsi_vals  = calc_rsi_series(closes1)
                valid_rsi = [(i, v) for i, v in enumerate(rsi_vals) if v is not None]
                if valid_rsi:
                    xi, yv = zip(*valid_rsi)
                    self._ax_rsi.plot(xi, yv, color=YELLOW, lw=1.2)
                self._ax_rsi.axhline(70, color=RED,   lw=0.8, linestyle="--", alpha=0.7)
                self._ax_rsi.axhline(30, color=GREEN, lw=0.8, linestyle="--", alpha=0.7)
                self._ax_rsi.axhline(50, color=MUTED, lw=0.5, linestyle=":",  alpha=0.5)
                self._ax_rsi.set_ylim(0, 100)
                self._ax_rsi.set_ylabel("RSI", color=TEXT, fontsize=11)
                self._ax_rsi.grid(color=CHART_GRID, linewidth=0.4)
                plt.setp(self._ax_rsi.get_xticklabels(), visible=False)

                # MACD (from primary symbol)
                ms      = calc_macd_series(closes1)
                valid_h = [(i, v) for i, v in enumerate(ms.histogram) if v is not None]
                if valid_h:
                    xi, yv   = zip(*valid_h)
                    colors_h = [GREEN if v >= 0 else RED for v in yv]
                    self._ax_macd.bar(xi, yv, color=colors_h, width=w, linewidth=0)
                valid_s = [(i, v) for i, v in enumerate(ms.signal) if v is not None]
                if valid_s:
                    xi, yv = zip(*valid_s)
                    self._ax_macd.plot(xi, yv, color=YELLOW, lw=1.0)
                self._ax_macd.axhline(0, color=MUTED, lw=0.5)
                self._ax_macd.set_ylabel("MACD", color=TEXT, fontsize=11)
                self._ax_macd.grid(color=CHART_GRID, linewidth=0.4)

            # ── Symbol 2 (ETH) price chart ────────────────────────────────
            if ohlcv2:
                opens2  = [d["o"] for d in ohlcv2]
                closes2 = [d["c"] for d in ohlcv2]
                live2   = closes2[-1]
                color2  = GREEN if live2 >= opens2[-1] else RED

                self._draw_candles(self._ax_eth, ohlcv2, w)
                self._draw_live_line(self._ax_eth, live2, color2)

                self._ax_eth.set_ylabel("Price", color=TEXT, fontsize=11)
                self._ax_eth.grid(color=CHART_GRID, linewidth=0.4, zorder=0)
                self._ax_eth.set_title(
                    f"{self._symbol2}  ·  {self._interval}",
                    color=ORANGE, fontsize=11, pad=4)
                plt.setp(self._ax_eth.get_xticklabels(), visible=False)

                # Price label top-bar
                self._price2_lbl.config(text=f"${live2:,.2f}")
                if len(closes2) >= 2:
                    chg2 = (closes2[-1] - closes2[-2]) / closes2[-2] * 100
                    self._change2_lbl.config(
                        text=f"{chg2:+.2f}%", fg=GREEN if chg2 >= 0 else RED)

            self._canvas.draw_idle()

        except Exception as e:
            log.warning(f"Chart draw error: {e}")
        finally:
            # Self-reschedule: next fetch begins right after this draw
            self.after(_MIN_DELAY_MS, self._fetch_and_draw)

    # ── Drawing helpers ───────────────────────────────────────────────────────

    def _draw_candles(self, ax, ohlcv: list[dict], w: float) -> None:
        for i, d in enumerate(ohlcv):
            o, h, l, c = d["o"], d["h"], d["l"], d["c"]
            color = GREEN if c >= o else RED
            ax.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=1)
            ax.bar(i, abs(c - o) or (h - l) * 0.01,
                   bottom=min(o, c), color=color,
                   width=w, zorder=2, linewidth=0)

    def _draw_bb_ma(self, ax, closes: list[float]) -> None:
        bb    = calc_bollinger_series(closes)
        valid = [(i, u, l2, m)
                 for i, (u, l2, m) in enumerate(zip(bb.upper, bb.lower, bb.middle))
                 if u is not None]
        if valid:
            xi, u_v, l_v, m_v = zip(*valid)
            ax.fill_between(xi, l_v, u_v, alpha=0.07, color=BLUE)
            ax.plot(xi, u_v, color=BLUE,   lw=0.8, alpha=0.5)
            ax.plot(xi, l_v, color=BLUE,   lw=0.8, alpha=0.5, label="BB")
            ax.plot(xi, m_v, color=YELLOW, lw=0.8, alpha=0.5,
                    linestyle="--", label="MA20")
        ma50 = calc_ma_series(closes, 50)
        valid_ma = [(i, v) for i, v in enumerate(ma50) if v is not None]
        if valid_ma:
            xi, yv = zip(*valid_ma)
            ax.plot(xi, yv, color="#a78bfa", lw=1.0, alpha=0.7, label="MA50")

    def _draw_live_line(self, ax, price: float, color: str) -> None:
        ax.axhline(y=price, color=color, lw=1.2,
                   linestyle="--", alpha=0.85, zorder=6)
        ax.annotate(
            f" ${price:,.2f} ",
            xy=(1.0, price),
            xycoords=("axes fraction", "data"),
            fontsize=11, fontweight="bold",
            color="white", va="center", ha="left",
            bbox=dict(boxstyle="round,pad=0.3",
                      facecolor=color, edgecolor="none", alpha=0.92),
            zorder=10, annotation_clip=False,
        )
