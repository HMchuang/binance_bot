"""
Terminal UI for the trading bot using `rich`.
Displays live positions table, account stats, and a scrolling trade log.
Run via:  python gui/app.py --GUI false
"""
from __future__ import annotations
import queue
import re
import signal
import threading
import time
from datetime import datetime

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from utils.logger import LOG_QUEUE

_MAX_LOG_LINES  = 200
_DISPLAY_LINES  = 20
_PRICE_INTERVAL = 15   # seconds between background price refreshes
_SPINNER        = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class TerminalUI:
    def __init__(self, bot, portfolio, notifier):
        self._bot       = bot
        self._portfolio = portfolio
        self._notifier  = notifier
        self._config    = bot.config
        self._exchange  = bot.exchange
        self._log_lines: list[str] = []
        self._console   = Console()
        self._stop      = threading.Event()

        # Price cache — updated by background thread
        self._prices: dict[str, float] = {}
        self._prices_lock    = threading.Lock()
        self._prices_updated = "—"
        self._usdt_balance: float | None = None   # real exchange balance (live/testnet)


        # Heartbeat / scan tracking
        self._tick          = 0          # increments every render, drives spinner
        self._last_scan_ts: datetime | None = None
        self._last_activity: datetime | None = None

        # Latest signal per symbol parsed from log
        self._last_signals: dict[str, str] = {}              # sym -> one-line summary
        self._last_bull_bear: dict[str, tuple[int, int]] = {} # sym -> (bull, bear)

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self) -> None:
        bot_thread = threading.Thread(
            target=self._bot.start, daemon=True, name="bot-loop")
        bot_thread.start()

        price_thread = threading.Thread(
            target=self._price_worker, daemon=True, name="price-fetch")
        price_thread.start()

        def _sig(sig, frame):
            self._bot.stop()
            self._stop.set()

        signal.signal(signal.SIGINT,  _sig)
        signal.signal(signal.SIGTERM, _sig)

        with Live(
            self._render(),
            refresh_per_second=2,
            console=self._console,
            screen=True,
        ) as live:
            while not self._stop.is_set() and not self._bot._stop_event.is_set():
                self._drain_log()
                self._tick += 1
                live.update(self._render())
                time.sleep(0.5)
            self._drain_log()
            live.update(self._render())

        bot_thread.join(timeout=5)
        self._notifier.shutdown()
        self._console.print("[green]Bot stopped.[/green]")

    # ── Price worker ──────────────────────────────────────────────────────────

    def _price_worker(self) -> None:
        while not self._stop.is_set() and not self._bot._stop_event.is_set():
            prices: dict[str, float] = {}
            for sym in self._config.symbols:
                try:
                    p = self._exchange.get_ticker_price(sym)
                    if p > 0:
                        prices[sym] = p
                except Exception:
                    pass
            try:
                for pos in self._portfolio.get_open_positions():
                    if pos.symbol not in prices:
                        try:
                            p = self._exchange.get_ticker_price(pos.symbol)
                            if p > 0:
                                prices[pos.symbol] = p
                        except Exception:
                            pass
            except Exception:
                pass
            if prices:
                with self._prices_lock:
                    self._prices = prices
                    self._prices_updated = datetime.now().strftime("%H:%M:%S")
            if self._config.mode == "live":
                try:
                    bal = self._exchange.get_balance("USDT")
                    with self._prices_lock:
                        self._usdt_balance = bal
                except Exception:
                    pass
            time.sleep(_PRICE_INTERVAL)

    def _get_prices(self) -> dict[str, float]:
        with self._prices_lock:
            return dict(self._prices)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self) -> Layout:
        layout = Layout(name="root")
        layout.split_column(
            Layout(self._make_header(),  name="header", size=3),
            Layout(name="body"),
        )
        layout["body"].split_row(
            Layout(name="left",  ratio=2),
            Layout(name="right", ratio=3),
        )
        layout["right"].split_column(
            Layout(self._make_log(),          name="log",    ratio=3),
            Layout(self._make_closed_trades(), name="closed", ratio=2),
        )
        layout["left"].split_column(
            Layout(self._make_stats(),     name="stats",     size=11),
            Layout(self._make_signals(),   name="signals",   size=10),
            Layout(self._make_positions(), name="positions"),
        )
        return layout

    def _make_header(self) -> Panel:
        cfg    = self._config
        mode   = cfg.mode.upper()
        color  = {"SIM": "purple", "TESTNET": "cyan", "LIVE": "red"}.get(mode, "white")
        spin   = _SPINNER[self._tick % len(_SPINNER)]
        now    = datetime.now()

        # Countdown to next scan
        if self._last_scan_ts:
            elapsed  = (now - self._last_scan_ts).total_seconds()
            remaining = max(0, self._config.loop_interval - elapsed)
            countdown = f"  next scan in [bold]{int(remaining)}s[/bold]"
        else:
            countdown = "  waiting for first scan…"

        manual_tag = "  [bold yellow] MANUAL [/bold yellow]" if getattr(self._bot, "manual_mode", False) else ""

        t = Text()
        t.append(f" {spin} ", style="bold green")
        t.append("Binance Auto Trading Bot  ", style="bold white")
        t.append(f" {mode} ", style=f"bold white on {color}")
        if getattr(self._bot, "manual_mode", False):
            t.append("  ✋ MANUAL ", style="bold yellow on dark_orange")
        t.append(f"   strategy={cfg.strategy}  symbols={', '.join(cfg.symbols)}", style="dim")
        t.append(countdown, style="cyan")
        t.append(f"   {now.strftime('%Y-%m-%d  %H:%M:%S')}", style="dim")
        return Panel(t, box=box.HORIZONTALS, style="bold")

    def _make_stats(self) -> Panel:
        try:
            prices = self._get_prices()
            with self._prices_lock:
                cached_usdt = self._usdt_balance
            if self._config.mode == "live":
                usdt_bal = cached_usdt if cached_usdt is not None else 0.0
            else:
                usdt_bal = self._portfolio.get_balance("USDT")
            stats = self._portfolio.get_stats(prices or None, usdt_balance=usdt_bal)

            pnl_color = "green" if stats.total_pnl_usd >= 0 else "red"
            pnl_sign  = "+" if stats.total_pnl_usd >= 0 else ""
            now       = datetime.now()

            # Last scan age
            if self._last_scan_ts:
                age_s    = int((now - self._last_scan_ts).total_seconds())
                scan_ago = f"{age_s}s ago  ({self._last_scan_ts.strftime('%H:%M:%S')})"
            else:
                scan_ago = "—"

            # Last activity age
            if self._last_activity:
                act_s    = int((now - self._last_activity).total_seconds())
                act_str  = f"{act_s}s ago  ({self._last_activity.strftime('%H:%M:%S')})"
            else:
                act_str  = "—"

            t = Table(box=None, show_header=False, padding=(0, 1), expand=True)
            t.add_column("key",   style="dim",   no_wrap=True)
            t.add_column("value", style="white", no_wrap=True)
            t.add_row("USDT Balance",    f"[bold]${usdt_bal:,.2f}[/bold]")
            t.add_row("Portfolio Value", f"[bold]${stats.current_value:,.2f}[/bold]")
            t.add_row("Total P&L",
                      f"[bold {pnl_color}]{pnl_sign}${stats.total_pnl_usd:,.2f}"
                      f"  ({pnl_sign}{stats.total_pnl_pct*100:.2f}%)[/bold {pnl_color}]")
            t.add_row("Total Fees",     f"[dim]${stats.total_fees:.4f}[/dim]")
            t.add_row("Trades / Wins",  f"{stats.trade_count} / {stats.wins}")
            t.add_row("Win Rate",       f"{stats.win_rate*100:.1f}%")
            t.add_row("Max Drawdown",
                      f"[{'red' if stats.max_drawdown_pct > 0.1 else 'dim'}]"
                      f"{stats.max_drawdown_pct*100:.2f}%[/]")
            t.add_row("Last scan",      f"[dim]{scan_ago}[/dim]")
            t.add_row("Last activity",  f"[dim]{act_str}[/dim]")
            t.add_row("Prices at",      f"[dim]{self._prices_updated}[/dim]")
        except Exception as e:
            t = Text(f"Stats error: {e}", style="red")

        return Panel(t, title="[bold cyan]Account Stats[/bold cyan]", box=box.ROUNDED)

    def _make_signals(self) -> Panel:
        """Latest signal per symbol, parsed from recent log lines."""
        tbl = Table(box=box.SIMPLE, expand=True, show_edge=False)
        tbl.add_column("Symbol",  style="bold white", no_wrap=True)
        tbl.add_column("Signal",  no_wrap=True)
        tbl.add_column("B/Bear",  justify="center", no_wrap=True)
        tbl.add_column("Bar",     no_wrap=True)
        tbl.add_column("Gap",     no_wrap=True)
        tbl.add_column("Detail",  style="dim", overflow="fold")

        for sym in self._config.symbols:
            raw = self._last_signals.get(sym, "")
            if not raw:
                tbl.add_row(sym, "[dim]—[/dim]", "—", "[dim]——————[/dim]", "—", "")
                continue

            # Signal keyword
            m   = re.search(r"signal=(\w+)", raw)
            sig = m.group(1).upper() if m else "?"
            color = {"BUY": "bold green", "SELL": "bold red"}.get(sig, "dim white")

            # Bull/bear counts + readiness bar
            bb  = self._last_bull_bear.get(sym)
            if bb:
                nb, nbe = bb
                bb_str = f"{nb}/{nbe}"
                rm = re.search(r"\[(\w+) ADX", raw)
                regime   = rm.group(1) if rm else "TREND"
                buy_conf = 4 if regime == "TRANS" else 3

                # Unicode bar: green=bull scored, dim=gap to threshold, red=bear
                BAR_W  = buy_conf + min(nbe, 6)   # total display width
                b_fill = min(nb,  buy_conf)
                b_gap  = max(0, buy_conf - nb)
                b_bear = min(nbe, 6)
                bar  = f"[green]{'█' * b_fill}[/green]"
                bar += f"[dim]{'░' * b_gap}[/dim]"
                bar += f"[red]{'█' * b_bear}[/red]"

                if sig == "BUY":
                    gap_str = "[green]✓ ready[/green]"
                elif nb > nbe:
                    gap_str = f"[yellow]+{buy_conf - nb} needed[/yellow]"
                elif nb == nbe:
                    gap_str = f"[yellow]+{buy_conf - nb} tied[/yellow]"
                else:
                    gap_str = f"[red]bear+{nbe - nb}[/red]"
            else:
                bb_str  = "—"
                bar     = "[dim]——————[/dim]"
                gap_str = "—"

            detail = raw.split(" — ", 1)[1] if " — " in raw else ""
            tbl.add_row(sym, f"[{color}]{sig}[/{color}]",
                        bb_str, bar, gap_str, detail)

        return Panel(tbl, title="[bold cyan]Latest Signals[/bold cyan]", box=box.ROUNDED)

    def _make_positions(self) -> Panel:
        tbl = Table(box=box.SIMPLE, expand=True, show_edge=False)
        tbl.add_column("Symbol",   style="bold white", no_wrap=True)
        tbl.add_column("Qty",      justify="right")
        tbl.add_column("Entry $",  justify="right")
        tbl.add_column("Live $",   justify="right")
        tbl.add_column("Unreal.",  justify="right")
        tbl.add_column("SL←  →TP", justify="left", no_wrap=True)
        tbl.add_column("→ TP",     justify="right")
        tbl.add_column("→ SL",     justify="right")
        tbl.add_column("Since",    style="dim", justify="right")

        try:
            prices    = self._get_prices()
            positions = self._portfolio.get_open_positions()
            risk_mgr  = getattr(self._bot, "risk_manager", None)
            for pos in positions:
                live_price = prices.get(pos.symbol, pos.entry_price)
                unreal     = (live_price - pos.entry_price) * pos.qty
                unreal_str = f"{'+'if unreal>=0 else ''}{unreal:.2f}"
                unreal_col = "green" if unreal >= 0 else "red"
                age        = datetime.now() - pos.entry_time
                h, rem     = divmod(int(age.total_seconds()), 3600)
                m          = rem // 60
                age_str    = f"{h}h{m:02d}m" if h else f"{m}m"

                # TP / SL distance labels
                tp_str   = "[dim]—[/dim]"
                sl_str   = "[dim]—[/dim]"
                bar_str  = "[dim]——————————[/dim]"
                tp = sl  = None
                if risk_mgr is not None and live_price > 0:
                    tp, sl = risk_mgr.get_stops(pos.symbol)
                    if tp is not None:
                        pct = (tp - live_price) / live_price * 100
                        tp_str = f"[green]+{pct:.1f}%[/green]"
                    if sl is not None:
                        pct = (sl - live_price) / live_price * 100
                        sl_str = f"[red]{pct:.1f}%[/red]"

                    # Range bar: SL on left, TP on right, | = entry, ● = current
                    if tp is not None and sl is not None and tp > sl:
                        BAR_W = 10
                        total = tp - sl
                        ei = max(0, min(BAR_W, round((pos.entry_price - sl) / total * BAR_W)))
                        ci = max(0, min(BAR_W, round((live_price     - sl) / total * BAR_W)))
                        if live_price >= pos.entry_price:
                            # Profit: green fill from entry to current
                            left  = "░" * ei
                            fill  = "█" * max(0, ci - ei)
                            right = "░" * max(0, BAR_W - ci)
                            bar_str = (f"[dim]{left}┃[/dim]"
                                       f"[green]{fill}[/green]"
                                       f"[dim]{right}[/dim]")
                        else:
                            # Loss: red fill from current to entry
                            left  = "░" * ci
                            fill  = "█" * max(0, ei - ci)
                            right = "░" * max(0, BAR_W - ei)
                            bar_str = (f"[dim]{left}[/dim]"
                                       f"[red]{fill}┃[/red]"
                                       f"[dim]{right}[/dim]")

                tbl.add_row(
                    pos.symbol,
                    f"{pos.qty:.6f}",
                    f"${pos.entry_price:,.4f}",
                    f"${live_price:,.4f}",
                    f"[{unreal_col}]{unreal_str}[/{unreal_col}]",
                    bar_str,
                    tp_str,
                    sl_str,
                    age_str,
                )
            if not positions:
                tbl.add_row("[dim]—[/dim]", "", "", "", "", "", "", "", "")
        except Exception as e:
            tbl.add_row(f"[red]{e}[/red]", "", "", "", "", "", "", "", "")

        return Panel(tbl, title="[bold cyan]Open Positions[/bold cyan]", box=box.ROUNDED)

    def _make_closed_trades(self) -> Panel:
        """Recent closed positions with P&L summary."""
        tbl = Table(box=box.SIMPLE, expand=True, show_edge=False)
        tbl.add_column("Symbol",   style="bold white", no_wrap=True)
        tbl.add_column("Qty",      justify="right")
        tbl.add_column("Entry $",  justify="right")
        tbl.add_column("Exit $",   justify="right")
        tbl.add_column("P&L $",    justify="right")
        tbl.add_column("P&L %",    justify="right")
        tbl.add_column("Reason",   style="dim", overflow="fold")
        tbl.add_column("Closed",   style="dim", justify="right")

        try:
            history = self._portfolio.get_trade_history(limit=50)
            sells   = [t for t in history if t.side == "SELL"][:8]
            for t in sells:
                pnl   = t.pnl_usd or 0.0
                pnl_p = (t.pnl_pct or 0.0) * 100
                color = "green" if pnl >= 0 else "red"
                sign  = "+" if pnl >= 0 else ""
                entry_price = t.price / (1.0 + (t.pnl_pct or 0.0)) if t.pnl_pct else 0.0
                ts    = (t.timestamp.strftime("%m/%d %H:%M")
                         if hasattr(t.timestamp, "strftime") else str(t.timestamp))
                reason = (t.reason or "—")[:18]
                tbl.add_row(
                    t.symbol,
                    f"{t.qty:.4f}",
                    f"${entry_price:,.2f}" if entry_price else "—",
                    f"${t.price:,.2f}",
                    f"[{color}]{sign}${pnl:.2f}[/{color}]",
                    f"[{color}]{sign}{pnl_p:.2f}%[/{color}]",
                    reason,
                    ts,
                )
            if not sells:
                tbl.add_row("[dim]No closed trades yet[/dim]", "", "", "", "", "", "", "")
        except Exception as e:
            tbl.add_row(f"[red]{e}[/red]", "", "", "", "", "", "", "")

        return Panel(tbl, title="[bold cyan]Closed Trades[/bold cyan]", box=box.ROUNDED)

    def _make_log(self) -> Panel:
        lines = self._log_lines[-_DISPLAY_LINES:]
        t = Text()
        for line in lines:
            if "ERROR" in line:
                t.append(line + "\n", style="bold red")
            elif "WARNING" in line or "WARN" in line:
                t.append(line + "\n", style="yellow")
            elif " BUY " in line:
                t.append(line + "\n", style="bold green")
            elif " SELL " in line or "EXIT" in line:
                t.append(line + "\n", style="bold red")
            elif "signal=BUY" in line:
                t.append(line + "\n", style="green")
            elif "signal=SELL" in line:
                t.append(line + "\n", style="red")
            else:
                t.append(line + "\n", style="dim white")
        return Panel(t, title="[bold cyan]Trade Log[/bold cyan]", box=box.ROUNDED)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _drain_log(self) -> None:
        try:
            while True:
                line = LOG_QUEUE.get_nowait()
                self._log_lines.append(line)
                self._last_activity = datetime.now()
                self._parse_log_line(line)
        except queue.Empty:
            pass
        if len(self._log_lines) > _MAX_LOG_LINES:
            self._log_lines = self._log_lines[-_MAX_LOG_LINES:]

    def _parse_log_line(self, line: str) -> None:
        """Extract scan timestamps, per-symbol signals, and bull/bear counts from log lines."""
        # "--- Scanning HH:MM:SS ---"
        if "--- Scanning" in line:
            self._last_scan_ts = datetime.now()
            return
        # "[BTCUSDT] price=... signal=... — ..."
        m = re.search(r"\[(\w+USDT)\] price=[\d.]+ holding=[\d.]+ signal=\w+", line)
        if m:
            sym = m.group(1)
            after = line.split(f"[{sym}] ", 1)[-1]
            self._last_signals[sym] = after.strip()
            # Parse bull/bear counts: "bull=4 bear=4"
            bb = re.search(r"bull=(\d+) bear=(\d+)", line)
            if bb:
                self._last_bull_bear[sym] = (int(bb.group(1)), int(bb.group(2)))
