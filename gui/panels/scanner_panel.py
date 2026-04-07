"""Market scanner table. Updates every 10 seconds via background thread."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from datetime import datetime

from core.indicators import (
    calc_rsi_wilder, calc_macd_current, calc_bollinger_current,
    calc_ma, calc_adx, calc_stochastic, calc_obv_slope, calc_volume_ratio, calc_vwap,
)
from utils.logger import get_logger

BG = "#0e1117"; CARD = "#161b26"; TEXT = "#e8eaf0"; MUTED = "#6b7280"
GREEN = "#22c55e"; RED = "#ef4444"; BLUE = "#3b82f6"; YELLOW = "#f59e0b"

log = get_logger("scanner_panel")

SCANNER_COLS = ("Symbol", "Price", "24h Chg", "RSI", "ADX", "Regime", "BB Pos", "Stoch", "Signal", "B/Bear", "Gap")


class ScannerPanel(tk.Frame):
    def __init__(self, parent: tk.Widget, exchange, symbols: list[str],
                 sentiment_getter=None, kline_interval: str = "15m", **kwargs):
        super().__init__(parent, bg=CARD, **kwargs)
        self._exchange         = exchange
        self._symbols          = list(symbols)
        self._sentiment_getter = sentiment_getter or (lambda: 55.0)
        self._kline_interval   = kline_interval
        self._queue: queue.Queue = queue.Queue()
        self._running = True
        self._build()
        threading.Thread(target=self._worker_loop, daemon=True).start()
        self._render_loop()

    def _build(self) -> None:
        tk.Label(self, text="Market Scanner", bg=CARD, fg=TEXT,
                 font=("Helvetica", 11, "bold"), anchor="w").pack(
                     fill="x", padx=8, pady=(6, 2))

        style = ttk.Style()
        style.configure("Scan.Treeview", background="#0d1220", foreground=TEXT,
                        fieldbackground="#0d1220", rowheight=24, font=("Courier", 9))
        style.configure("Scan.Treeview.Heading", background="#1e2433", foreground=TEXT,
                        font=("Helvetica", 9, "bold"), relief="flat")
        style.map("Scan.Treeview", background=[("selected", "#2a3555")])

        frame = tk.Frame(self, bg=CARD)
        frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._tree = ttk.Treeview(frame, columns=SCANNER_COLS,
                                   show="headings", style="Scan.Treeview")
        self._col_weights = [2.0, 2.0, 1.5, 1.0, 1.0, 1.5, 1.5, 1.0, 1.5, 1.2, 1.8]
        for col in SCANNER_COLS:
            self._tree.heading(col, text=col)
            self._tree.column(col, width=80, anchor="center", minwidth=40, stretch=True)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        frame.bind("<Configure>", self._on_frame_resize)
        self._tree.tag_configure("buy",    foreground=GREEN)
        self._tree.tag_configure("sell",   foreground=RED)
        self._tree.tag_configure("hold",   foreground=MUTED)
        self._tree.tag_configure("close",  foreground=YELLOW)   # close to buy trigger
        self._tree.tag_configure("bear",   foreground="#f97316") # bearish domination

        self._row_ids: dict[str, str] = {}
        for sym in self._symbols:
            iid = self._tree.insert("", "end",
                                     values=(sym,) + ("…",) * (len(SCANNER_COLS) - 1))
            self._row_ids[sym] = iid

        self._last_lbl = tk.Label(self, text="", bg=CARD, fg="#4b5563",
                                   font=("Helvetica", 8))
        self._last_lbl.pack(fill="x", padx=8, pady=(0, 2))
        self._build_legend()
        self._build_readiness_bars()

    def _build_legend(self) -> None:
        """Compact legend explaining Signal, B/Bear, and Gap column values."""
        LEGEND_BG = "#0d1117"
        DIM       = "#6b7280"
        MONO      = ("Courier", 8)

        outer = tk.Frame(self, bg=CARD, pady=2)
        outer.pack(fill="x", padx=4, pady=(0, 4))

        # ── Title row ─────────────────────────────────────────────────────────
        tk.Label(outer, text="Signal Legend", bg=CARD, fg=DIM,
                 font=("Helvetica", 8, "bold"), anchor="w").pack(
                     fill="x", padx=4, pady=(0, 2))

        inner = tk.Frame(outer, bg=LEGEND_BG, bd=0)
        inner.pack(fill="x", padx=2)

        # ── Row 1: Signal colours ─────────────────────────────────────────────
        r1 = tk.Frame(inner, bg=LEGEND_BG)
        r1.pack(fill="x", padx=6, pady=(4, 1))
        tk.Label(r1, text="Signal: ", bg=LEGEND_BG, fg=DIM, font=MONO).pack(side="left")
        for label, color, tip in [
            ("BUY",  GREEN,     "≥3 bull factors AND bull > bear"),
            ("SELL", RED,       "≥2 bear factors AND bear > bull"),
            ("HOLD", MUTED,     "Conditions not yet met — watching"),
        ]:
            tk.Label(r1, text=f" {label} ", bg=LEGEND_BG, fg=color,
                     font=("Courier", 8, "bold")).pack(side="left")
            tk.Label(r1, text=f"= {tip}    ", bg=LEGEND_BG, fg=DIM,
                     font=MONO).pack(side="left")

        # ── Row 2: B/Bear column ──────────────────────────────────────────────
        r2 = tk.Frame(inner, bg=LEGEND_BG)
        r2.pack(fill="x", padx=6, pady=1)
        tk.Label(r2, text="B/Bear: ", bg=LEGEND_BG, fg=DIM, font=MONO).pack(side="left")
        tk.Label(r2, text="bullish / bearish factor count  ",
                 bg=LEGEND_BG, fg=TEXT, font=MONO).pack(side="left")
        tk.Label(r2, text="e.g.  ", bg=LEGEND_BG, fg=DIM, font=MONO).pack(side="left")
        for sample, color in [("4/3", GREEN), ("3/4", RED), ("4/4", YELLOW)]:
            tk.Label(r2, text=f"{sample}  ", bg=LEGEND_BG, fg=color,
                     font=("Courier", 8, "bold")).pack(side="left")

        # ── Row 3: Gap column ─────────────────────────────────────────────────
        r3 = tk.Frame(inner, bg=LEGEND_BG)
        r3.pack(fill="x", padx=6, pady=1)
        tk.Label(r3, text="Gap:    ", bg=LEGEND_BG, fg=DIM, font=MONO).pack(side="left")
        for label, color, tip in [
            ("✓ ready",    GREEN,     "All buy conditions met — waiting for bot cycle"),
            ("+2 abs",     YELLOW,    "Bull leads bear but needs 2 more absolute factors"),
            ("+2 (tied)",  YELLOW,    "Bull = bear; need 2 more to break the tie"),
            ("bear+3",     "#f97316", "Bear dominates by 3 — strongly bearish"),
        ]:
            tk.Label(r3, text=f" {label} ", bg=LEGEND_BG, fg=color,
                     font=("Courier", 8, "bold")).pack(side="left")
            tk.Label(r3, text=f"= {tip}   ", bg=LEGEND_BG, fg=DIM,
                     font=MONO).pack(side="left")

        # ── Row 4: Regime note ────────────────────────────────────────────────
        r4 = tk.Frame(inner, bg=LEGEND_BG)
        r4.pack(fill="x", padx=6, pady=(1, 4))
        tk.Label(r4, text="Regime: ", bg=LEGEND_BG, fg=DIM, font=MONO).pack(side="left")
        for label, color, tip in [
            ("TREND", BLUE,   "ADX > 25 — trend-following rules, need 3+ bull"),
            ("RANGE", GREEN,  "ADX < 20 — mean-reversion rules, need 3+ bull"),
            ("TRANS", YELLOW, "ADX 20–25 — transitional, need 4+ bull"),
        ]:
            tk.Label(r4, text=f" {label} ", bg=LEGEND_BG, fg=color,
                     font=("Courier", 8, "bold")).pack(side="left")
            tk.Label(r4, text=f"= {tip}   ", bg=LEGEND_BG, fg=DIM,
                     font=MONO).pack(side="left")

    # ── Readiness bars ────────────────────────────────────────────────────────

    _MAX_FACTORS = 12   # normalisation scale for the bar (covers all regimes)
    _BAR_H       = 16   # canvas height in pixels
    _BAR_BG      = "#111827"
    _BULL_CLR    = "#22c55e"   # green  — bull factors
    _BEAR_CLR    = "#ef4444"   # red    — bear factors
    _GAP_CLR     = "#1e293b"   # dark   — gap between bull and threshold
    _THRESH_CLR  = "#f59e0b"   # yellow — buy-threshold marker line

    def _build_readiness_bars(self) -> None:
        """One horizontal bar per symbol showing buy-readiness at a glance."""
        self._bar_latest: dict[str, dict] = {}
        self._bar_canvases: dict[str, tk.Canvas] = {}
        self._bar_labels:   dict[str, tk.Label]  = {}

        outer = tk.Frame(self, bg=CARD)
        outer.pack(fill="x", padx=4, pady=(0, 6))
        self._bars_outer = outer

        # ── Header row with mini colour legend ────────────────────────────────
        hdr = tk.Frame(outer, bg=CARD)
        hdr.pack(fill="x", padx=4, pady=(2, 2))
        tk.Label(hdr, text="Buy Readiness", bg=CARD, fg=MUTED,
                 font=("Helvetica", 8, "bold"), anchor="w").pack(side="left")
        for swatch, label in [
            (self._BULL_CLR, "Bull"), (self._BEAR_CLR, "Bear"),
            (self._GAP_CLR,  "Gap"),  (self._THRESH_CLR, "Threshold"),
        ]:
            tk.Label(hdr, text="  ■", bg=CARD, fg=swatch,
                     font=("Courier", 8, "bold")).pack(side="left")
            tk.Label(hdr, text=label, bg=CARD, fg=MUTED,
                     font=("Courier", 8)).pack(side="left")

        # ── One row per symbol ────────────────────────────────────────────────
        self._bars_rows_frame = tk.Frame(outer, bg=CARD)
        self._bars_rows_frame.pack(fill="x")
        self._rebuild_bar_rows()

    def _rebuild_bar_rows(self) -> None:
        """Destroy and recreate bar rows when symbol list changes."""
        for w in self._bars_rows_frame.winfo_children():
            w.destroy()
        self._bar_canvases.clear()
        self._bar_labels.clear()

        for sym in self._symbols:
            row = tk.Frame(self._bars_rows_frame, bg=CARD)
            row.pack(fill="x", padx=4, pady=2)

            tk.Label(row, text=sym.replace("USDT", ""), bg=CARD, fg=TEXT,
                     font=("Courier", 8, "bold"), width=5, anchor="w").pack(side="left")

            c = tk.Canvas(row, height=self._BAR_H, bg=self._BAR_BG,
                          highlightthickness=1, highlightbackground="#2a3555")
            c.pack(side="left", fill="x", expand=True, padx=(4, 6))
            c.bind("<Configure>", lambda e, s=sym: self._redraw_bar(s))
            self._bar_canvases[sym] = c

            lbl = tk.Label(row, text="…", bg=CARD, fg=MUTED,
                           font=("Courier", 8), width=18, anchor="w")
            lbl.pack(side="left")
            self._bar_labels[sym] = lbl

    def _redraw_bar(self, sym: str) -> None:
        """Redraw bar for sym using the latest cached data (called on resize too)."""
        data = self._bar_latest.get(sym)
        if data:
            self._update_bar(sym, data["nb"], data["nbe"], data["buy_conf"], data["sig"])

    def _update_bar(self, sym: str, nb: int, nbe: int,
                    buy_conf: int, sig: str) -> None:
        """Draw the readiness bar and update the status label."""
        # Cache for redraws
        self._bar_latest[sym] = {"nb": nb, "nbe": nbe, "buy_conf": buy_conf, "sig": sig}

        c = self._bar_canvases.get(sym)
        lbl = self._bar_labels.get(sym)
        if not c or not lbl:
            return

        W = c.winfo_width()
        H = c.winfo_height() or self._BAR_H
        if W < 10:
            return  # not yet laid out — <Configure> will fire later

        c.delete("all")
        M = self._MAX_FACTORS

        def px(factors: float) -> int:
            return max(0, min(W, round(factors / M * W)))

        # Background (gap zone between bull and bear)
        c.create_rectangle(0, 0, W, H, fill=self._BAR_BG, outline="")

        # Bull section — green fill from left up to nb (capped at buy_conf visually)
        bull_end = px(nb)
        if bull_end > 0:
            # Shade beyond threshold slightly darker to show overshoot
            cap = px(buy_conf)
            if bull_end <= cap:
                c.create_rectangle(0, 2, bull_end, H - 2,
                                   fill=self._BULL_CLR, outline="")
            else:
                c.create_rectangle(0, 2, cap, H - 2,
                                   fill=self._BULL_CLR, outline="")
                c.create_rectangle(cap, 2, bull_end, H - 2,
                                   fill="#16a34a", outline="")   # darker green overshoot

        # Bear section — red fill from right
        bear_start = W - px(nbe)
        if bear_start < W:
            c.create_rectangle(bear_start, 2, W, H - 2,
                               fill=self._BEAR_CLR, outline="")

        # Gap zone (between bull end and bear start) — dim fill
        gap_l = bull_end
        gap_r = bear_start
        if gap_l < gap_r:
            c.create_rectangle(gap_l, 2, gap_r, H - 2,
                               fill=self._GAP_CLR, outline="")

        # Threshold marker — vertical dashed line at buy_conf position
        tx = px(buy_conf)
        c.create_line(tx, 0, tx, H, fill=self._THRESH_CLR, width=2, dash=(4, 3))

        # Overlap warning: bull and bear bars overlap when nb + nbe > MAX_FACTORS
        # draw a white divider between them so they don't visually merge
        if bull_end > bear_start and bull_end > 0:
            c.create_line(bull_end, 2, bull_end, H - 2, fill="#ffffff", width=1)

        # ── Status label ──────────────────────────────────────────────────────
        if sig == "BUY":
            txt, clr = "✓ BUY ready", GREEN
        elif nb >= buy_conf and nb > nbe:
            txt, clr = "✓ ready (next cycle)", GREEN
        elif nb > nbe:
            txt, clr = f"need +{buy_conf - nb} more bull", YELLOW
        elif nb == nbe:
            txt, clr = f"tied — need +{buy_conf - nb}", YELLOW
        else:
            txt, clr = f"bear leads by {nbe - nb}", "#f97316"
        lbl.config(text=txt, fg=clr)

    def _on_frame_resize(self, event: tk.Event) -> None:
        total = event.width - 18
        if total < 200:
            return
        total_w = sum(self._col_weights)
        for col, weight in zip(SCANNER_COLS, self._col_weights):
            self._tree.column(col, width=max(40, int(total * weight / total_w)))

    def _worker_loop(self) -> None:
        while self._running:
            for sym in list(self._symbols):
                try:
                    ohlcv = self._exchange.get_klines_ohlcv(sym, self._kline_interval, 100)
                    if len(ohlcv) < 30:
                        continue

                    closes  = [b["c"] for b in ohlcv]
                    highs   = [b["h"] for b in ohlcv]
                    lows    = [b["l"] for b in ohlcv]
                    volumes = [b["v"] for b in ohlcv]

                    # Patch last candle with live price
                    try:
                        live = self._exchange.get_ticker_price(sym)
                        if live > 0:
                            closes[-1] = live
                            highs[-1]  = max(highs[-1], live)
                            lows[-1]   = min(lows[-1],  live)
                    except Exception:
                        pass

                    ticker    = self._exchange.get_ticker_24hr(sym)
                    price     = closes[-1]
                    chg_pct   = float(ticker.get("priceChangePercent", 0)) if ticker else 0.0
                    sentiment = self._sentiment_getter()

                    rsi      = calc_rsi_wilder(closes)
                    adx_r    = calc_adx(highs, lows, closes)
                    adx      = adx_r.adx
                    stoch_r  = calc_stochastic(highs, lows, closes)
                    stoch_k  = stoch_r.k
                    bb_r     = calc_bollinger_current(closes)
                    macd_r   = calc_macd_current(closes)
                    obv_sl   = calc_obv_slope(closes, volumes)
                    vol_r    = calc_volume_ratio(volumes)
                    vwap_val = calc_vwap(highs, lows, closes, volumes)
                    ma20     = calc_ma(closes, 20)
                    ma50     = calc_ma(closes, 50)

                    # Regime detection (mirrors RegimeAwareStrategy)
                    if adx > 25:
                        regime = "TREND"
                    elif adx < 20:
                        regime = "RANGE"
                    else:
                        regime = "TRANS"

                    # Bull/bear factor counts — mirrors RegimeAwareStrategy logic
                    bullish: list[str] = []
                    bearish: list[str] = []

                    if regime == "TREND":
                        if adx_r.plus_di > adx_r.minus_di:
                            bullish.append("+DI")
                        else:
                            bearish.append("-DI")
                        if price > ma50:
                            bullish.append("P>MA50")
                        else:
                            bearish.append("P<MA50")
                        if ma20 > ma50:
                            bullish.append("MA20>MA50")
                        else:
                            bearish.append("MA20<MA50")
                        if 40 <= rsi <= 68:
                            bullish.append("RSI")
                        elif rsi > 72:
                            bearish.append("RSI_OB")
                        elif rsi < 35:
                            bearish.append("RSI_col")
                        if macd_r.histogram > 0:
                            bullish.append("MACD+")
                        else:
                            bearish.append("MACD-")
                        if obv_sl > 0.05:
                            bullish.append("OBV↑")
                        elif obv_sl < -0.05:
                            bearish.append("OBV↓")
                        if vol_r >= 1.15:
                            bullish.append("Vol")
                        if sentiment < 30:
                            bullish.append("Fear")
                        elif sentiment > 82:
                            bearish.append("Greed")
                        if price > vwap_val:
                            bullish.append("VWAP↑")
                        else:
                            bearish.append("VWAP↓")
                        buy_conf = 3

                    elif regime == "RANGE":
                        bb_range = bb_r.upper - bb_r.lower
                        bb_pct   = ((price - bb_r.lower) / bb_range) if bb_range > 0 else 0.5
                        if rsi < 38:
                            bullish.append("RSI_OS")
                        elif rsi > 65:
                            bearish.append("RSI_OB")
                        if stoch_r.k < 25 and stoch_r.k > stoch_r.d:
                            bullish.append("Stoch↑")
                        elif stoch_r.k > 75 and stoch_r.k < stoch_r.d:
                            bearish.append("Stoch↓")
                        if bb_pct < 0.12:
                            bullish.append("LoBB")
                        elif bb_pct > 0.88:
                            bearish.append("HiBB")
                        if sentiment < 25:
                            bullish.append("Fear")
                        elif sentiment > 78:
                            bearish.append("Greed")
                        if obv_sl > 0.08:
                            bullish.append("OBV↑")
                        elif obv_sl < -0.08:
                            bearish.append("OBV↓")
                        if price < vwap_val:
                            bullish.append("VWAP_MR")
                        buy_conf = 3

                    else:  # TRANS
                        if adx_r.plus_di > adx_r.minus_di:
                            bullish.append("+DI")
                        else:
                            bearish.append("-DI")
                        if ma20 > ma50:
                            bullish.append("MA20>MA50")
                        else:
                            bearish.append("MA20<MA50")
                        if price > ma50:
                            bullish.append("P>MA50")
                        else:
                            bearish.append("P<MA50")
                        if rsi < 48:
                            bullish.append("RSI_lo")
                        elif rsi > 65:
                            bearish.append("RSI_OB")
                        if macd_r.histogram > 0:
                            bullish.append("MACD+")
                        else:
                            bearish.append("MACD-")
                        if obv_sl > 0.08:
                            bullish.append("OBV↑")
                        elif obv_sl < -0.08:
                            bearish.append("OBV↓")
                        if price > vwap_val:
                            bullish.append("VWAP↑")
                        else:
                            bearish.append("VWAP↓")
                        buy_conf = 4

                    nb  = len(bullish)
                    nbe = len(bearish)

                    # Signal based on actual confluence (consistent with strategy)
                    if nb >= buy_conf and nb > nbe:
                        sig = "BUY"
                    elif nbe >= 2 and nbe > nb:
                        sig = "SELL"
                    else:
                        sig = "HOLD"

                    # Gap: how many more bullish factors to trigger a buy
                    if sig == "BUY":
                        gap_str = "✓ ready"
                        row_tag = "buy"
                    elif nb > nbe:
                        # Bullish leads but not enough absolute factors
                        gap_str = f"+{buy_conf - nb} abs"
                        row_tag = "close"
                    elif nb == nbe:
                        gap_str = f"+{buy_conf - nb} (tied)"
                        row_tag = "hold"
                    else:
                        gap_str = f"bear+{nbe - nb}"
                        row_tag = "bear" if sig == "SELL" else "hold"

                    bb_range = bb_r.upper - bb_r.lower
                    bb_str   = f"{(price - bb_r.lower) / bb_range * 100:.0f}%" if bb_range > 0 else "N/A"

                    self._queue.put({
                        "symbol":   sym,
                        "price":    f"${price:,.4f}",
                        "change":   f"{chg_pct:+.2f}%",
                        "rsi":      f"{rsi:.1f}",
                        "adx":      f"{adx:.1f}",
                        "regime":   regime,
                        "bb_pos":   bb_str,
                        "stoch":    f"{stoch_k:.0f}",
                        "signal":   sig,
                        "bb_bear":  f"{nb}/{nbe}",
                        "gap":      gap_str,
                        "tag":      row_tag,
                        "nb":       nb,
                        "nbe":      nbe,
                        "buy_conf": buy_conf,
                    })
                except Exception as e:
                    log.debug(f"Scanner {sym}: {e}")
            time.sleep(10)

    def _render_loop(self) -> None:
        try:
            for _ in range(20):
                data = self._queue.get_nowait()
                sym  = data["symbol"]
                if sym in self._row_ids:
                    iid = self._row_ids[sym]
                    self._tree.item(iid, values=(
                        sym, data["price"], data["change"],
                        data["rsi"], data["adx"], data["regime"],
                        data["bb_pos"], data["stoch"], data["signal"],
                        data["bb_bear"], data["gap"],
                    ), tags=(data["tag"],))
                    self._update_bar(sym, data["nb"], data["nbe"],
                                     data["buy_conf"], data["signal"])
        except queue.Empty:
            pass
        self._last_lbl.config(
            text=f"Last update: {datetime.now().strftime('%H:%M:%S')}")
        self.after(400, self._render_loop)

    def update_symbols(self, symbols: list[str]) -> None:
        self._symbols = list(symbols)
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._row_ids = {}
        for sym in self._symbols:
            iid = self._tree.insert("", "end",
                                     values=(sym,) + ("…",) * (len(SCANNER_COLS) - 1))
            self._row_ids[sym] = iid
        self._bar_latest.clear()
        self._rebuild_bar_rows()

    def update_exchange(self, exchange) -> None:
        self._exchange = exchange

    def update_kline_interval(self, interval: str) -> None:
        self._kline_interval = interval
