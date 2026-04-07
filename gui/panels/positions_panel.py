"""
Positions panel — tabbed view of Open Positions and Closed Trades.

Open tab:  live unrealized P&L, TP/SL bar, manual Sell button.
Closed tab: recent trade history with P&L, duration, exit reason.
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from typing import Callable, Optional

from utils.logger import get_logger

BG = "#0e1117"; CARD = "#161b26"; TEXT = "#e8eaf0"; MUTED = "#6b7280"
GREEN = "#22c55e"; RED = "#ef4444"; YELLOW = "#f59e0b"

log = get_logger("positions_panel")

OPEN_COLS   = ("Symbol", "Qty", "Entry $", "Current $", "P&L %",
               "SL←  →TP", "Value $", "TP $", "SL $", "Strategy", "Opened")
CLOSED_COLS = ("Symbol", "Qty", "Entry $", "Exit $", "P&L $",
               "P&L %", "Fee $", "Reason", "Duration", "Closed At")


class PositionsPanel(tk.Frame):
    def __init__(self, parent: tk.Widget, portfolio, exchange,
                 risk_manager=None,
                 on_manual_sell: Optional[Callable[[str], None]] = None,
                 **kwargs):
        super().__init__(parent, bg=CARD, **kwargs)
        self._portfolio     = portfolio
        self._exchange      = exchange
        self._risk_mgr      = risk_manager
        self._on_manual_sell = on_manual_sell   # callback(symbol)
        self._build()
        self._schedule_refresh()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        # Notebook style
        style.configure("Pos.TNotebook",        background=CARD, borderwidth=0)
        style.configure("Pos.TNotebook.Tab",    background="#1e2433", foreground=MUTED,
                        padding=[10, 4], font=("Helvetica", 9, "bold"))
        style.map("Pos.TNotebook.Tab",
                  background=[("selected", CARD)],
                  foreground=[("selected", TEXT)])

        # Treeview style
        style.configure("Pos.Treeview",
                        background=CARD, foreground=TEXT,
                        fieldbackground=CARD, rowheight=22,
                        font=("Helvetica", 9))
        style.configure("Pos.Treeview.Heading",
                        background="#1e2433", foreground=TEXT,
                        font=("Helvetica", 9, "bold"), relief="flat")
        style.map("Pos.Treeview", background=[("selected", "#2a3555")])

        self._nb = ttk.Notebook(self, style="Pos.TNotebook")
        self._nb.pack(fill="both", expand=True)

        # ── Tab 1: Open Positions ─────────────────────────────────────────────
        open_frame = tk.Frame(self._nb, bg=CARD)
        self._nb.add(open_frame, text="Open Positions")
        self._build_open_tab(open_frame)

        # ── Tab 2: Closed Trades ──────────────────────────────────────────────
        closed_frame = tk.Frame(self._nb, bg=CARD)
        self._nb.add(closed_frame, text="Closed Trades")
        self._build_closed_tab(closed_frame)

    def _build_open_tab(self, parent: tk.Frame) -> None:
        hdr = tk.Frame(parent, bg=CARD)
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(hdr, text="Open Positions", bg=CARD, fg=TEXT,
                 font=("Helvetica", 11, "bold"), anchor="w").pack(side="left")

        # Manual Sell button — sells the selected row's symbol
        self._sell_btn = tk.Button(
            hdr, text="Sell Selected", bg=RED, fg="white",
            relief="flat", padx=8, pady=2, cursor="hand2",
            font=("Helvetica", 9, "bold"),
            command=self._sell_selected)
        self._sell_btn.pack(side="right", padx=(4, 0))
        tk.Button(hdr, text="Refresh", bg="#374151", fg=TEXT, relief="flat",
                  padx=6, pady=2, cursor="hand2", font=("Helvetica", 9),
                  command=self._refresh).pack(side="right")

        frame = tk.Frame(parent, bg=CARD)
        frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._open_tree = ttk.Treeview(frame, columns=OPEN_COLS, show="headings",
                                        style="Pos.Treeview", height=6)
        col_widths = [75, 90, 105, 105, 62, 105, 80, 130, 130, 65, 115]
        for col, w in zip(OPEN_COLS, col_widths):
            self._open_tree.heading(col, text=col)
            self._open_tree.column(col, width=w, anchor="center", minwidth=w, stretch=False)
        vsb = ttk.Scrollbar(frame, orient="vertical",   command=self._open_tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self._open_tree.xview)
        self._open_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._open_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._open_tree.tag_configure("profit", foreground=GREEN)
        self._open_tree.tag_configure("loss",   foreground=RED)
        self._open_tree.tag_configure("flat",   foreground=TEXT)

        self._summary_lbl = tk.Label(parent, text="", bg=CARD, fg=MUTED,
                                      font=("Helvetica", 9), anchor="w")
        self._summary_lbl.pack(fill="x", padx=8, pady=(0, 4))

    def _build_closed_tab(self, parent: tk.Frame) -> None:
        hdr = tk.Frame(parent, bg=CARD)
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(hdr, text="Closed Trades", bg=CARD, fg=TEXT,
                 font=("Helvetica", 11, "bold"), anchor="w").pack(side="left")
        tk.Button(hdr, text="Refresh", bg="#374151", fg=TEXT, relief="flat",
                  padx=6, pady=2, cursor="hand2", font=("Helvetica", 9),
                  command=self._refresh_closed).pack(side="right")

        frame = tk.Frame(parent, bg=CARD)
        frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._closed_tree = ttk.Treeview(frame, columns=CLOSED_COLS, show="headings",
                                          style="Pos.Treeview", height=6)
        col_widths = [75, 90, 105, 105, 80, 68, 65, 125, 65, 115]
        for col, w in zip(CLOSED_COLS, col_widths):
            self._closed_tree.heading(col, text=col)
            self._closed_tree.column(col, width=w, anchor="center", minwidth=w, stretch=False)
        vsb = ttk.Scrollbar(frame, orient="vertical",   command=self._closed_tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self._closed_tree.xview)
        self._closed_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._closed_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._closed_tree.tag_configure("profit", foreground=GREEN)
        self._closed_tree.tag_configure("loss",   foreground=RED)

        self._closed_summary_lbl = tk.Label(parent, text="", bg=CARD, fg=MUTED,
                                             font=("Helvetica", 9), anchor="w")
        self._closed_summary_lbl.pack(fill="x", padx=8, pady=(0, 4))

    # ── Manual sell ───────────────────────────────────────────────────────────

    def _sell_selected(self) -> None:
        sel = self._open_tree.selection()
        if not sel:
            messagebox.showinfo("No Selection", "Select a position row first.")
            return
        vals = self._open_tree.item(sel[0], "values")
        if not vals:
            return
        symbol = vals[0]
        if not messagebox.askyesno(
                "Manual Sell",
                f"Sell {symbol} at current market price?\n\nThis will close the position immediately."):
            return
        if self._on_manual_sell:
            threading.Thread(target=self._on_manual_sell, args=(symbol,), daemon=True).start()

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        threading.Thread(target=self._fetch_and_render_open, daemon=True).start()

    def _refresh_closed(self) -> None:
        threading.Thread(target=self._fetch_and_render_closed, daemon=True).start()

    def _fetch_and_render_open(self) -> None:
        try:
            positions = self._portfolio.get_open_positions()
            price_map = {}
            for pos in positions:
                try:
                    price_map[pos.symbol] = self._exchange.get_ticker_price(pos.symbol)
                except Exception:
                    price_map[pos.symbol] = pos.entry_price
            try:
                self.after(0, self._render_open, positions, price_map)
            except RuntimeError:
                pass
        except Exception as e:
            log.error(f"Open positions refresh error: {e}")

    def _fetch_and_render_closed(self) -> None:
        try:
            trades = self._portfolio.get_trade_history(limit=50)
            # Only SELL trades = closed positions
            sells = [t for t in trades if t.side == "SELL"]
            try:
                self.after(0, self._render_closed, sells)
            except RuntimeError:
                pass
        except Exception as e:
            log.error(f"Closed trades refresh error: {e}")

    def _render_open(self, positions, price_map: dict) -> None:
        for item in self._open_tree.get_children():
            self._open_tree.delete(item)
        total_value = 0.0
        total_pnl   = 0.0
        for pos in positions:
            curr    = price_map.get(pos.symbol, pos.entry_price)
            pnl_pct = (curr - pos.entry_price) / pos.entry_price * 100 if pos.entry_price else 0
            value   = pos.qty * curr
            pnl_usd = value - pos.qty * pos.entry_price
            total_value += value
            total_pnl   += pnl_usd
            tag = "profit" if pnl_pct > 0 else ("loss" if pnl_pct < 0 else "flat")
            et_str = (pos.entry_time.strftime("%m/%d %H:%M")
                      if hasattr(pos.entry_time, "strftime") else str(pos.entry_time))

            tp_str  = "—"
            sl_str  = "—"
            bar_str = "░░░░░|░░░░░"
            if self._risk_mgr is not None:
                tp, sl = self._risk_mgr.get_stops(pos.symbol)
                if tp is not None and curr > 0:
                    pct_to_tp = (tp - curr) / curr * 100
                    tp_str = f"${tp:,.2f} (+{pct_to_tp:.1f}%)"
                if sl is not None and curr > 0:
                    pct_to_sl = (sl - curr) / curr * 100
                    sl_str = f"${sl:,.2f} ({pct_to_sl:.1f}%)"
                if tp is not None and sl is not None and tp > sl and curr > 0:
                    BAR_W = 10
                    total = tp - sl
                    ei = max(0, min(BAR_W, round((pos.entry_price - sl) / total * BAR_W)))
                    ci = max(0, min(BAR_W, round((curr            - sl) / total * BAR_W)))
                    chars = ["░"] * BAR_W
                    if curr >= pos.entry_price:
                        for i in range(ei, ci):
                            chars[i] = "█"
                    else:
                        for i in range(ci, ei):
                            chars[i] = "▒"
                    if 0 <= ei < BAR_W:
                        chars[ei] = "|"
                    bar_str = "".join(chars)

            self._open_tree.insert("", "end", values=(
                pos.symbol,
                f"{pos.qty:.6f}",
                f"${pos.entry_price:,.4f}",
                f"${curr:,.4f}",
                f"{pnl_pct:+.2f}%",
                bar_str,
                f"${value:,.2f}",
                tp_str,
                sl_str,
                pos.strategy,
                et_str,
            ), tags=(tag,))

        count    = len(positions)
        pnl_sign = "+" if total_pnl >= 0 else ""
        self._summary_lbl.config(
            text=f"{count} open position{'s' if count != 1 else ''}  |  "
                 f"Total value: ${total_value:,.2f}  |  "
                 f"Unrealized P&L: {pnl_sign}${total_pnl:,.2f}",
            fg=GREEN if total_pnl >= 0 else RED,
        )
        # Update tab title with count
        self._nb.tab(0, text=f"Open Positions ({count})")

    def _render_closed(self, sells) -> None:
        for item in self._closed_tree.get_children():
            self._closed_tree.delete(item)

        total_pnl  = 0.0
        total_fees = 0.0
        wins       = 0

        for t in sells:
            pnl    = t.pnl_usd or 0.0
            pnl_p  = (t.pnl_pct or 0.0) * 100
            fee    = t.fee or 0.0
            total_pnl  += pnl
            total_fees += fee
            if pnl > 0:
                wins += 1
            tag = "profit" if pnl > 0 else "loss"

            # Duration: need buy trade with same position_id
            duration_str = "—"
            try:
                # We store buy trades too; look up by position_id
                buy_trades = [b for b in self._portfolio.get_trade_history(limit=200)
                              if b.side == "BUY" and b.position_id == t.position_id]
                if buy_trades:
                    delta = t.timestamp - buy_trades[0].timestamp
                    secs  = int(delta.total_seconds())
                    if secs < 3600:
                        duration_str = f"{secs // 60}m"
                    elif secs < 86400:
                        duration_str = f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
                    else:
                        duration_str = f"{secs // 86400}d{(secs % 86400) // 3600}h"
            except Exception:
                pass

            ts_str = (t.timestamp.strftime("%m/%d %H:%M")
                      if hasattr(t.timestamp, "strftime") else str(t.timestamp))

            # Reconstruct entry price from pnl_pct: entry = exit / (1 + pnl_pct)
            entry_price = t.price / (1.0 + (t.pnl_pct or 0.0)) if t.pnl_pct is not None else 0.0

            self._closed_tree.insert("", "end", values=(
                t.symbol,
                f"{t.qty:.6f}",
                f"${entry_price:,.4f}" if entry_price else "—",
                f"${t.price:,.4f}",
                f"{'+'if pnl>=0 else ''}{pnl:.2f}",
                f"{pnl_p:+.2f}%",
                f"${fee:.4f}",
                (t.reason or "—")[:20],
                duration_str,
                ts_str,
            ), tags=(tag,))

        count    = len(sells)
        wr       = f"{wins/count*100:.0f}%" if count else "—"
        sign     = "+" if total_pnl >= 0 else ""
        self._closed_summary_lbl.config(
            text=f"{count} closed trade{'s' if count != 1 else ''}  |  "
                 f"Win rate: {wr}  |  "
                 f"Total P&L: {sign}${total_pnl:.2f}  |  "
                 f"Total fees: ${total_fees:.4f}",
            fg=GREEN if total_pnl >= 0 else RED,
        )
        self._nb.tab(1, text=f"Closed Trades ({count})")

    def _schedule_refresh(self) -> None:
        self._refresh()
        self._refresh_closed()
        self.after(5000, self._schedule_refresh)

    # ── External updates ──────────────────────────────────────────────────────

    def update_exchange(self, exchange) -> None:
        self._exchange = exchange

    def update_portfolio(self, portfolio) -> None:
        self._portfolio = portfolio
        self._refresh()
        self._refresh_closed()

    def update_risk_manager(self, risk_manager) -> None:
        self._risk_mgr = risk_manager

    def flash_closed_tab(self) -> None:
        """Switch to the Closed Trades tab briefly to highlight a new close."""
        self._nb.select(1)
        self._refresh_closed()
