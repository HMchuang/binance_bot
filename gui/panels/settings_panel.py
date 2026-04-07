"""
Left-side configuration panel — redesigned for clarity.

Key UX principles:
- Mode selection is 3 large toggle buttons with immediate visual feedback.
- A dynamic status banner tells the user exactly what action is needed next.
- Simulator mode is auto-ready — no "apply capital" step required.
- API key section shows one primary button; save/load is in a collapsible row.
- Strategy sliders are auto-applied when Start Bot is clicked if not done manually.
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable

from core.config import TradingConfig, save_config, validate_config
from utils.security import CredentialStore, AuthenticationError
from utils.logger import get_logger

BG = "#0e1117"; CARD = "#161b26"; BORDER = "#2a2f3e"; TEXT = "#e8eaf0"
MUTED = "#6b7280"; GREEN = "#22c55e"; RED = "#ef4444"; BLUE = "#3b82f6"
YELLOW = "#f59e0b"; PURPLE = "#7c3aed"; TEAL = "#0891b2"
ORANGE = "#f97316"

log = get_logger("settings_panel")

_REGIME_DESC = ("Detects market regime via ADX, then applies:\n"
                "Trending (ADX>25): trend-follow (RSI, MA, MACD, OBV)\n"
                "Ranging (ADX<20): mean-revert (RSI oversold, Stoch, BB)\n"
                "Needs 3+ bullish signals to enter.")


class SettingsPanel(tk.Frame):
    def __init__(self, parent: tk.Widget, config: TradingConfig,
                 on_start: Callable, on_stop: Callable, on_reset: Callable,
                 on_config_change: Callable[[TradingConfig], None],
                 credential_store: CredentialStore, **kwargs):
        super().__init__(parent, bg=BG, **kwargs)
        self._config    = config
        self._on_start  = on_start
        self._on_stop   = on_stop
        self._on_reset  = on_reset
        self._on_config_change = on_config_change
        self._store     = credential_store
        self._bot_running    = False
        self._keys_applied   = False   # True once Apply Keys clicked (testnet/live)
        self._strategy_dirty = True    # True until Apply Strategy clicked
        self._step_done      = [True, False, False, False]
        self._ui_ready       = False
        self._build()
        self._ui_ready = True
        self._refresh_ui()             # set initial state

    # ── Build ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        canvas = tk.Canvas(self, bg=BG, highlightthickness=0, width=330)
        sb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self._inner = tk.Frame(canvas, bg=BG)
        self._inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        p = self._inner
        self._build_mode(p)
        self._build_steps(p)
        self._build_status(p)
        self._build_sim_settings(p)
        self._build_api_settings(p)
        self._build_symbols(p)
        self._build_strategy(p)
        self._build_bot_control(p)
        self._build_account_overview(p)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _section_lbl(self, parent, text: str) -> tk.Label:
        lbl = tk.Label(parent, text=text, bg=BG, fg=MUTED,
                       font=("Helvetica", 9, "bold"), anchor="w")
        lbl.pack(fill="x", pady=(10, 2), padx=6)
        return lbl

    def _card(self, parent, **kw) -> tk.Frame:
        f = tk.Frame(parent, bg=CARD, padx=8, pady=6,
                     highlightthickness=1, highlightbackground=BORDER, **kw)
        f.pack(fill="x", padx=4, pady=(0, 6))
        return f

    def _slider(self, parent, label: str, attr: str, init_val: float,
                from_: float, to: float, res: float,
                suffix: str = "", tip: str = "") -> tk.DoubleVar:
        dv = tk.DoubleVar(value=init_val)
        setattr(self, f"_{attr}", dv)
        row = tk.Frame(parent, bg=CARD); row.pack(fill="x", pady=2)
        tk.Label(row, text=label, bg=CARD, fg=MUTED,
                 width=20, anchor="w", font=("Helvetica", 9)).pack(side="left")
        entry_var = tk.StringVar(value=f"{init_val:.1f}")
        entry = tk.Entry(row, textvariable=entry_var, bg=BG, fg=TEXT,
                         insertbackground=TEXT, relief="flat", highlightthickness=1,
                         highlightbackground=BORDER, width=6,
                         font=("Helvetica", 9, "bold"), justify="center")
        entry.pack(side="right")
        if suffix:
            tk.Label(row, text=suffix, bg=CARD, fg=MUTED,
                     font=("Helvetica", 9)).pack(side="right", padx=(0, 2))
        def _slider_moved(v): entry_var.set(f"{float(v):.1f}")
        scale = tk.Scale(row, variable=dv, from_=from_, to=to, resolution=res,
                         orient="horizontal", bg=CARD, fg=MUTED, troughcolor=BG,
                         highlightthickness=0, bd=0, sliderrelief="flat",
                         command=_slider_moved, length=100, showvalue=False)
        scale.pack(side="left")
        def _entry_commit(event=None):
            try:
                v = float(entry_var.get())
                v = max(from_, min(to, v))
                dv.set(round(v / res) * res)
                entry_var.set(f"{dv.get():.1f}")
            except ValueError:
                entry_var.set(f"{dv.get():.1f}")
        entry.bind("<Return>",   _entry_commit)
        entry.bind("<FocusOut>", _entry_commit)
        if tip:
            tk.Label(parent, text=f"  ↳ {tip}", bg=CARD, fg="#4b5563",
                     font=("Helvetica", 7), anchor="w").pack(fill="x", padx=4)
        dv.trace_add("write", lambda *_: self._on_strategy_slider_changed())
        return dv

    # ── Sections ─────────────────────────────────────────────────────────────

    def _build_mode(self, p) -> None:
        self._section_lbl(p, "① Trading Mode")
        card = self._card(p)
        self._mode_var = tk.StringVar(value=self._config.mode)

        btn_row = tk.Frame(card, bg=CARD)
        btn_row.pack(fill="x", pady=(2, 4))

        mode_defs = [
            ("sim",     "SIMULATOR", PURPLE),
            ("testnet", "TESTNET",   TEAL),
            ("live",    "LIVE ⚠",   RED),
        ]
        self._mode_btns: dict[str, tk.Button] = {}
        for val, label, color in mode_defs:
            b = tk.Button(btn_row, text=label, bg=CARD, fg=MUTED,
                          relief="flat", padx=8, pady=5, cursor="hand2",
                          font=("Helvetica", 9, "bold"),
                          command=lambda v=val: self._set_mode(v))
            b.pack(side="left", padx=(0, 4))
            self._mode_btns[val] = b

        # Per-mode help text (updated by _refresh_ui)
        self._mode_desc_lbl = tk.Label(card, text="", bg=CARD, fg=MUTED,
                                        font=("Helvetica", 8), justify="left",
                                        anchor="w", wraplength=290)
        self._mode_desc_lbl.pack(fill="x")

    def _build_steps(self, p) -> None:
        card = tk.Frame(p, bg=CARD, padx=8, pady=6,
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", padx=4, pady=(0, 4))
        labels = [
            "Select Trading Mode",
            "Configure Connection",
            "Apply Strategy Settings",
            "Start Bot",
        ]
        self._step_widgets: list[tuple[tk.Label, tk.Label]] = []
        for i, text in enumerate(labels):
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", pady=1)
            num_lbl = tk.Label(row, text=f"{'①②③④'[i]}", bg=CARD, fg=MUTED,
                               font=("Helvetica", 10, "bold"), width=3)
            num_lbl.pack(side="left")
            txt_lbl = tk.Label(row, text=text, bg=CARD, fg=MUTED,
                               font=("Helvetica", 9), anchor="w")
            txt_lbl.pack(side="left", fill="x", expand=True)
            chk_lbl = tk.Label(row, text="○", bg=CARD, fg=MUTED,
                               font=("Helvetica", 10, "bold"))
            chk_lbl.pack(side="right")
            self._step_widgets.append((txt_lbl, chk_lbl))
        self._update_steps()

    def _update_steps(self) -> None:
        for i, (txt_lbl, chk_lbl) in enumerate(self._step_widgets):
            done = self._step_done[i]
            chk_lbl.config(text="✓" if done else "○",
                           fg=GREEN if done else MUTED)
            txt_lbl.config(fg=TEXT if done else MUTED,
                           font=("Helvetica", 9, "bold") if done else ("Helvetica", 9))

    def _build_status(self, p) -> None:
        """Dynamic banner: tells the user exactly what action is needed next."""
        self._status_frame = tk.Frame(p, bg=CARD, padx=10, pady=8,
                                       highlightthickness=2,
                                       highlightbackground=BORDER)
        self._status_frame.pack(fill="x", padx=4, pady=(0, 6))
        self._status_icon = tk.Label(self._status_frame, text="●", bg=CARD,
                                      font=("Helvetica", 13, "bold"))
        self._status_icon.pack(side="left", padx=(0, 8))
        self._status_lbl = tk.Label(self._status_frame, text="", bg=CARD,
                                     fg=TEXT, font=("Helvetica", 9),
                                     justify="left", anchor="w", wraplength=250)
        self._status_lbl.pack(side="left", fill="x", expand=True)

    def _build_sim_settings(self, p) -> None:
        self._sim_section_lbl = self._section_lbl(p, "② Simulator Settings")
        self._sim_card = tk.Frame(p, bg=CARD, padx=8, pady=6,
                                   highlightthickness=1, highlightbackground=BORDER)
        self._sim_card.pack(fill="x", padx=4, pady=(0, 6))

        pr = tk.Frame(self._sim_card, bg=CARD); pr.pack(fill="x", pady=3)
        tk.Label(pr, text="Starting Capital $", bg=CARD, fg=MUTED,
                 width=18, anchor="w", font=("Helvetica", 10)).pack(side="left")
        self._principal_var = tk.StringVar(value=str(int(self._config.sim_principal)))
        tk.Entry(pr, textvariable=self._principal_var, bg=BG, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=0, highlightthickness=1,
                 highlightbackground=BORDER, width=12).pack(side="left")

        fee_row = tk.Frame(self._sim_card, bg=CARD); fee_row.pack(fill="x", pady=2)
        tk.Label(fee_row, text="Fee Rate %", bg=CARD, fg=MUTED,
                 width=18, anchor="w", font=("Helvetica", 9)).pack(side="left")
        self._sim_fee_var = tk.DoubleVar(value=round(self._config.fee_rate * 100, 2))
        fee_val = tk.Label(fee_row, bg=CARD, fg=TEXT, width=6,
                           font=("Helvetica", 9, "bold"))
        fee_val.pack(side="right")
        def _upd(v, lbl=fee_val): lbl.config(text=f"{float(v):.2f}%")
        tk.Scale(fee_row, variable=self._sim_fee_var, from_=0.0, to=1.0,
                 resolution=0.01, orient="horizontal", bg=CARD, fg=MUTED,
                 troughcolor=BG, highlightthickness=0, bd=0, sliderrelief="flat",
                 command=_upd, length=120).pack(side="left")
        _upd(self._config.fee_rate * 100)
        tk.Label(self._sim_card,
                 text="  ↳ These take effect when you click Start Bot.",
                 bg=CARD, fg="#4b5563", font=("Helvetica", 7), anchor="w").pack(fill="x")

        br = tk.Frame(self._sim_card, bg=CARD); br.pack(fill="x", pady=(6, 2))
        tk.Button(br, text="Reset Portfolio Now",
                  command=self._click_reset_sim,
                  bg="#374151", fg=TEXT, relief="flat", padx=8, pady=3,
                  cursor="hand2", font=("Helvetica", 9)).pack(side="left")

    def _build_api_settings(self, p) -> None:
        self._api_section_lbl = self._section_lbl(p, "② API Keys  (Testnet / Live)")
        self._api_card = tk.Frame(p, bg=CARD, padx=8, pady=8,
                                   highlightthickness=1, highlightbackground=BORDER)
        self._api_card.pack(fill="x", padx=4, pady=(0, 6))

        # Key field
        kr = tk.Frame(self._api_card, bg=CARD); kr.pack(fill="x", pady=3)
        tk.Label(kr, text="API Key", bg=CARD, fg=MUTED, width=10,
                 anchor="w", font=("Helvetica", 10)).pack(side="left")
        self._api_key_var = tk.StringVar()
        self._api_key_entry = tk.Entry(
            kr, textvariable=self._api_key_var,
            bg=BG, fg=TEXT, insertbackground=TEXT,
            relief="flat", highlightthickness=1, highlightbackground=BORDER,
            width=24, show="•")
        self._api_key_entry.pack(side="left", padx=(0, 4))
        tk.Button(kr, text="👁", bg=CARD, fg=MUTED, relief="flat",
                  cursor="hand2", font=("Helvetica", 9),
                  command=lambda: self._toggle_vis("key")).pack(side="left")

        # Secret field
        sr = tk.Frame(self._api_card, bg=CARD); sr.pack(fill="x", pady=3)
        tk.Label(sr, text="API Secret", bg=CARD, fg=MUTED, width=10,
                 anchor="w", font=("Helvetica", 10)).pack(side="left")
        self._api_secret_var = tk.StringVar()
        self._api_secret_entry = tk.Entry(
            sr, textvariable=self._api_secret_var,
            bg=BG, fg=TEXT, insertbackground=TEXT,
            relief="flat", highlightthickness=1, highlightbackground=BORDER,
            width=24, show="•")
        self._api_secret_entry.pack(side="left", padx=(0, 4))
        tk.Button(sr, text="👁", bg=CARD, fg=MUTED, relief="flat",
                  cursor="hand2", font=("Helvetica", 9),
                  command=lambda: self._toggle_vis("secret")).pack(side="left")

        tk.Label(self._api_card,
                 text="Testnet key: testnet.binance.vision → Login → API Management → HMAC key",
                 bg=CARD, fg="#4b5563", font=("Helvetica", 7),
                 justify="left", anchor="w", wraplength=290).pack(fill="x", pady=(2, 6))

        # Primary action row
        primary_row = tk.Frame(self._api_card, bg=CARD)
        primary_row.pack(fill="x", pady=(0, 4))
        self._apply_keys_btn = tk.Button(
            primary_row, text="✓  Apply Keys",
            command=self._apply_api,
            bg=BLUE, fg="white", relief="flat", padx=12, pady=5,
            cursor="hand2", font=("Helvetica", 10, "bold"))
        self._apply_keys_btn.pack(side="left", padx=(0, 6))
        tk.Button(primary_row, text="📂 Load from JSON",
                  command=self._load_from_json,
                  bg="#1e3a5f", fg=TEXT, relief="flat", padx=8, pady=5,
                  cursor="hand2", font=("Helvetica", 9)).pack(side="left")

        # Collapsible save/load encrypted row
        self._adv_expanded = tk.BooleanVar(value=False)
        adv_toggle = tk.Button(
            self._api_card, text="▸  Saved encrypted keys",
            command=self._toggle_adv_keys,
            bg=CARD, fg=MUTED, relief="flat", padx=0, pady=2,
            cursor="hand2", font=("Helvetica", 8), anchor="w")
        adv_toggle.pack(fill="x")
        self._adv_toggle_btn = adv_toggle

        self._adv_frame = tk.Frame(self._api_card, bg=CARD)
        # (not packed yet — shown on toggle)
        adv_inner = tk.Frame(self._adv_frame, bg=CARD)
        adv_inner.pack(fill="x", pady=(4, 0))
        for txt, cmd, color in [
            ("Save Encrypted", self._save_keys,  "#374151"),
            ("Load Encrypted", self._load_keys,  "#374151"),
            ("Clear Fields",   self._clear_keys, "#374151"),
        ]:
            tk.Button(adv_inner, text=txt, command=cmd, bg=color, fg=TEXT,
                      relief="flat", padx=6, pady=3, cursor="hand2",
                      font=("Helvetica", 8)).pack(side="left", padx=(0, 4))

    def _build_symbols(self, p) -> None:
        self._section_lbl(p, "③ Symbols to Trade")
        card = self._card(p)
        row = tk.Frame(card, bg=CARD); row.pack(fill="x", pady=2)
        self._symbols_var = tk.StringVar(value=", ".join(self._config.symbols))
        tk.Entry(row, textvariable=self._symbols_var, bg=BG, fg=TEXT,
                 insertbackground=TEXT, relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, width=28).pack(side="left", fill="x", expand=True)
        tk.Label(card, text="  Comma-separated, e.g. BTCUSDT, ETHUSDT, SOLUSDT",
                 bg=CARD, fg="#4b5563", font=("Helvetica", 7), anchor="w").pack(fill="x")

    def _build_strategy(self, p) -> None:
        self._section_lbl(p, "④ Strategy Settings")
        sf = self._card(p)
        sr = tk.Frame(sf, bg=CARD); sr.pack(fill="x", pady=(2, 4))
        tk.Label(sr, text="Strategy", bg=CARD, fg=MUTED,
                 width=16, anchor="w", font=("Helvetica", 10)).pack(side="left")
        tk.Label(sr, text="REGIME", bg="#0891b2", fg="white",
                 font=("Helvetica", 9, "bold"), padx=6, pady=2).pack(side="left")
        desc_f = tk.Frame(sf, bg="#0d1220", padx=6, pady=4)
        desc_f.pack(fill="x", pady=(0, 6))
        tk.Label(desc_f, text=_REGIME_DESC, bg="#0d1220", fg="#94a3b8",
                 font=("Helvetica", 8), justify="left",
                 anchor="w", wraplength=270).pack(fill="x")

        self._slider(sf, "Order Size %",    "order_pct", self._config.order_pct * 100,       5, 100, 5,   "%",
                     "% of available USDT per buy")
        self._slider(sf, "Take-Profit %",   "tp",        self._config.take_profit_pct * 100, 1,  50, 1,   "%",
                     "Auto-sell when position gains this much")
        self._slider(sf, "Stop-Loss %",     "sl",        self._config.stop_loss_pct * 100,   1,  50, 1,   "%",
                     "Auto-sell when position loses this much")
        self._slider(sf, "Trailing Stop %", "trail",     0.0,                                0,  20, 0.5, "%",
                     "0 = disabled. Trails below peak price")
        self._slider(sf, "Scan Interval (s)", "interval", self._config.loop_interval,        1, 300, 1,   "s",
                     "How often the bot scans")

        # Apply button — shows "✓ Applied" label after clicking
        apply_row = tk.Frame(sf, bg=CARD); apply_row.pack(fill="x", pady=(8, 2))
        tk.Button(apply_row, text="Apply Strategy",
                  command=self._apply_strategy,
                  bg="#6366f1", fg="white", relief="flat", padx=12, pady=5,
                  cursor="hand2", font=("Helvetica", 10, "bold")).pack(side="left")
        self._strategy_status_lbl = tk.Label(apply_row, text="",
                                              bg=CARD, fg=MUTED,
                                              font=("Helvetica", 9))
        self._strategy_status_lbl.pack(side="left", padx=(10, 0))

        self._build_notifications(p)

    def _build_notifications(self, p) -> None:
        self._section_lbl(p, "Notifications  (optional)")
        nf = self._card(p)
        def _field(label, attr, w=26):
            row = tk.Frame(nf, bg=CARD); row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=CARD, fg=MUTED, width=14,
                     anchor="w", font=("Helvetica", 9)).pack(side="left")
            var = tk.StringVar(value=getattr(self._config, attr) or "")
            setattr(self, f"_{attr}_var", var)
            tk.Entry(row, textvariable=var, bg=BG, fg=TEXT,
                     insertbackground=TEXT, relief="flat", highlightthickness=1,
                     highlightbackground=BORDER, width=w).pack(side="left")
        _field("Discord Webhook", "discord_webhook")
        _field("Telegram Token",  "telegram_token")
        _field("Telegram ChatID", "telegram_chat_id", 18)
        tk.Button(nf, text="Save Notification Settings",
                  command=self._save_notifications,
                  bg="#374151", fg=TEXT, relief="flat", padx=8, pady=3,
                  cursor="hand2", font=("Helvetica", 9)).pack(anchor="e", pady=(4, 0))

    def _build_bot_control(self, p) -> None:
        self._section_lbl(p, "⑤ Bot Control")
        ctrl = self._card(p)

        br = tk.Frame(ctrl, bg=CARD); br.pack(fill="x", pady=(4, 4))
        self._start_btn = tk.Button(br, text="▶  Start Bot",
                                    command=self._click_start,
                                    bg=GREEN, fg="white", relief="flat",
                                    padx=14, pady=8, cursor="hand2",
                                    font=("Helvetica", 12, "bold"))
        self._start_btn.pack(side="left", padx=(0, 8))
        self._stop_btn = tk.Button(br, text="■  Stop",
                                   command=self._click_stop,
                                   bg="#374151", fg=MUTED, relief="flat",
                                   padx=12, pady=8, cursor="hand2",
                                   font=("Helvetica", 12, "bold"),
                                   state="disabled")
        self._stop_btn.pack(side="left", padx=(0, 8))

        # "Why can't I start?" hint — only visible when blocked
        self._start_hint = tk.Label(ctrl, text="", bg=CARD, fg=YELLOW,
                                     font=("Helvetica", 8), anchor="w",
                                     wraplength=290)
        self._start_hint.pack(fill="x", pady=(0, 2))

    def _build_account_overview(self, p) -> None:
        self._section_lbl(p, "Account Overview")
        self._acc_card = self._card(p)
        self._acc_lbl: dict[str, tk.Label] = {}
        for key, label in [
            ("balance",  "USDT Balance"),
            ("value",    "Portfolio Value"),
            ("pnl",      "Total P&L"),
            ("fees",     "Total Fees"),
            ("wins",     "Win Rate"),
            ("trades",   "Total Trades"),
            ("drawdown", "Max Drawdown"),
        ]:
            row = tk.Frame(self._acc_card, bg=CARD); row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=CARD, fg=MUTED, width=16,
                     anchor="w", font=("Helvetica", 9)).pack(side="left")
            lbl = tk.Label(row, text="—", bg=CARD, fg=TEXT,
                           font=("Helvetica", 9, "bold"), anchor="w")
            lbl.pack(side="left")
            self._acc_lbl[key] = lbl

    # ── Mode switching ────────────────────────────────────────────────────────

    def _set_mode(self, mode: str) -> None:
        if not self._ui_ready:
            return
        if mode == "live":
            if not messagebox.askyesno(
                "⚠ Live Trading",
                "Switch to LIVE mode?\n\nThis will use REAL MONEY "
                "on your Binance account.\n\nAre you sure?"
            ):
                return
        self._mode_var.set(mode)
        # Switching mode invalidates credentials and restarts strategy state
        self._keys_applied   = False
        self._strategy_dirty = True
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        """Update all dynamic parts of the panel based on current mode & state."""
        if not self._ui_ready:
            return
        mode = self._mode_var.get()

        # ── Mode button highlight ──
        colors = {"sim": PURPLE, "testnet": TEAL, "live": RED}
        labels = {"sim": "SIMULATOR", "testnet": "TESTNET", "live": "LIVE ⚠"}
        for val, btn in self._mode_btns.items():
            if val == mode:
                btn.config(bg=colors[val], fg="white",
                           relief="raised", highlightthickness=0)
            else:
                btn.config(bg=CARD, fg=MUTED, relief="flat")

        # ── Mode description ──
        descs = {
            "sim":     "Virtual funds, real Binance prices.\nNo API key needed — ready to start.",
            "testnet": "Real orders on Binance's test network.\nFree testnet API key required.",
            "live":    "REAL MONEY on your Binance account.\nUse with extreme caution.",
        }
        self._mode_desc_lbl.config(text=descs[mode])

        # ── Show/hide sim vs api sections ──
        sim_visible = (mode == "sim")
        api_visible = (mode in ("testnet", "live"))

        # Hide both, then re-show whichever applies — keeps them positioned
        # right after the status banner (pack's `after=` preserves order).
        self._sim_section_lbl.pack_forget()
        self._sim_card.pack_forget()
        self._api_section_lbl.pack_forget()
        self._api_card.pack_forget()

        if sim_visible:
            self._sim_section_lbl.pack(fill="x", pady=(10, 2), padx=6,
                                        after=self._status_frame)
            self._sim_card.pack(fill="x", padx=4, pady=(0, 6),
                                after=self._sim_section_lbl)
        if api_visible:
            self._api_section_lbl.pack(fill="x", pady=(10, 2), padx=6,
                                        after=self._status_frame)
            self._api_card.pack(fill="x", padx=4, pady=(0, 6),
                                after=self._api_section_lbl)

        # ── Step checklist ──
        self._step_done[0] = True   # mode always selected
        self._step_done[1] = (mode == "sim") or self._keys_applied
        if not self._step_done[1]:
            self._step_done[2] = False
            self._step_done[3] = False
        self._update_steps()

        # ── Status banner ──
        self._update_status_banner()

        # ── Start button state ──
        self._update_start_btn()

    def _update_status_banner(self) -> None:
        mode = self._mode_var.get()
        if mode == "sim":
            self._status_frame.config(highlightbackground=GREEN)
            self._status_icon.config(text="✓", fg=GREEN)
            self._status_lbl.config(
                text="Simulator ready — no API key needed.\n"
                     "Adjust capital/strategy if desired, then click ▶ Start Bot.",
                fg=TEXT)
        elif not self._keys_applied:
            self._status_frame.config(highlightbackground=YELLOW)
            self._status_icon.config(text="⚠", fg=YELLOW)
            mode_name = "Testnet" if mode == "testnet" else "Live"
            self._status_lbl.config(
                text=f"{mode_name}: Enter your API Key + Secret above, "
                     f"then click  ✓ Apply Keys.",
                fg=TEXT)
        else:
            self._status_frame.config(highlightbackground=GREEN)
            self._status_icon.config(text="✓", fg=GREEN)
            self._status_lbl.config(
                text="API keys applied. Click ▶ Start Bot to begin trading.",
                fg=TEXT)

    def _update_start_btn(self) -> None:
        mode     = self._mode_var.get()
        blocked  = (mode != "sim" and not self._keys_applied)
        if self._bot_running:
            self._start_hint.config(text="")
            return
        if blocked:
            self._start_btn.config(bg="#374151", fg=MUTED)
            self._start_hint.config(
                text="⚠ Apply API keys first (step ② above).")
        else:
            self._start_btn.config(bg=GREEN, fg="white")
            self._start_hint.config(text="")

    def _toggle_adv_keys(self) -> None:
        if self._adv_expanded.get():
            self._adv_frame.pack_forget()
            self._adv_expanded.set(False)
            self._adv_toggle_btn.config(text="▸  Saved encrypted keys")
        else:
            self._adv_frame.pack(fill="x", pady=(2, 0))
            self._adv_expanded.set(True)
            self._adv_toggle_btn.config(text="▾  Saved encrypted keys")

    def _on_strategy_slider_changed(self) -> None:
        if not self._ui_ready:
            return
        self._strategy_dirty = True
        self._strategy_status_lbl.config(text="(not applied)", fg=MUTED)

    # ── Event handlers ────────────────────────────────────────────────────────

    def _toggle_vis(self, which: str) -> None:
        entry = self._api_key_entry if which == "key" else self._api_secret_entry
        entry.config(show="" if entry.cget("show") else "•")

    def _apply_principal_from_ui(self) -> None:
        """Read capital/fee fields and apply to config. Called silently at Start."""
        try:
            principal = float(self._principal_var.get().replace(",", ""))
            fee_rate  = self._sim_fee_var.get() / 100.0
            self._config.sim_principal = principal
            self._config.fee_rate      = fee_rate
            save_config(self._config)
            log.info(f"Simulator capital set — ${principal:,.0f}, fee {fee_rate*100:.2f}%")
        except ValueError:
            pass

    def _click_reset_sim(self) -> None:
        if self._bot_running:
            messagebox.showwarning("Bot Running", "Stop the bot before resetting.")
            return
        if messagebox.askyesno("Reset Portfolio",
                               "Reset simulator portfolio to current capital setting?"):
            self._apply_principal_from_ui()
            self._on_reset()
            log.info("Simulator portfolio reset")

    def _apply_api(self) -> None:
        key    = self._api_key_var.get().strip()
        secret = self._api_secret_var.get().strip()
        if not key or not secret:
            messagebox.showwarning("Missing Keys",
                                   "Enter both API key and secret first.")
            return
        self._config._api_key    = key
        self._config._api_secret = secret
        self._keys_applied       = True
        self._apply_keys_btn.config(text="✓  Keys Applied", bg="#15803d")
        self._on_config_change(self._config)
        self._refresh_ui()
        log.info("API keys applied")

    def _save_keys(self) -> None:
        key    = self._api_key_var.get().strip()
        secret = self._api_secret_var.get().strip()
        if not key or not secret:
            messagebox.showwarning("Missing Keys", "Enter both API key and secret first.")
            return
        pw = self._prompt_password("Set Master Password",
                                   confirm=not self._store.credentials_exist())
        if pw is None:
            return
        try:
            self._store.set_master_password(pw)
            self._store.save_credentials(key, secret)
            messagebox.showinfo("Saved", "Credentials encrypted and saved.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}")

    def _load_keys(self) -> None:
        if not self._store.credentials_exist():
            messagebox.showinfo("No Credentials", "No saved credentials found.")
            return
        pw = self._prompt_password("Master Password", confirm=False)
        if pw is None:
            return
        try:
            self._store.set_master_password(pw)
            key, secret = self._store.load_credentials()
            self._api_key_var.set(key)
            self._api_secret_var.set(secret)
            # Auto-apply after loading
            self._apply_api()
        except AuthenticationError as e:
            messagebox.showerror("Authentication Failed", str(e))

    def _clear_keys(self) -> None:
        self._api_key_var.set("")
        self._api_secret_var.set("")
        self._keys_applied = False
        self._apply_keys_btn.config(text="✓  Apply Keys", bg=BLUE)
        self._refresh_ui()

    def _load_from_json(self) -> None:
        from tkinter import filedialog
        import json
        path = filedialog.askopenfilename(
            title="Load API Keys from JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            key = (data.get("api_key") or data.get("apiKey") or
                   data.get("BINANCE_API_KEY") or data.get("key") or "").strip()
            secret = (data.get("api_secret") or data.get("apiSecret") or
                      data.get("BINANCE_API_SECRET") or data.get("secret") or "").strip()
            if not key or not secret:
                messagebox.showerror("Keys Not Found",
                    "Could not find api_key / api_secret in the JSON file.")
                return
            self._api_key_var.set(key)
            self._api_secret_var.set(secret)
            # Auto-apply after loading
            self._apply_api()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load JSON file:\n{e}")

    def _apply_strategy(self, silent: bool = False) -> None:
        trail_val = getattr(self, "_trail", tk.DoubleVar(value=0)).get()
        self._config.strategy          = "regime"
        self._config.order_pct         = self._order_pct.get() / 100
        self._config.take_profit_pct   = self._tp.get() / 100
        self._config.stop_loss_pct     = self._sl.get() / 100
        self._config.trailing_stop_pct = trail_val / 100 if trail_val > 0 else None
        self._config.loop_interval     = int(self._interval.get())
        syms = [s.strip().upper() for s in self._symbols_var.get().split(",")
                if s.strip()]
        if syms:
            self._config.symbols = syms
        errors = validate_config(self._config)
        if errors:
            if not silent:
                messagebox.showerror("Validation Error", "\n".join(errors))
            return False
        self._strategy_dirty = False
        self._step_done[2] = True
        self._update_steps()
        self._strategy_status_lbl.config(text="✓ Applied", fg=GREEN)
        save_config(self._config)
        if not silent:
            self._on_config_change(self._config)
            log.info(f"Strategy applied: {self._config.strategy}")
        return True

    def _save_notifications(self) -> None:
        self._config.discord_webhook  = self._discord_webhook_var.get().strip() or None
        self._config.telegram_token   = self._telegram_token_var.get().strip() or None
        self._config.telegram_chat_id = self._telegram_chat_id_var.get().strip() or None
        save_config(self._config)
        self._on_config_change(self._config)
        messagebox.showinfo("Saved", "Notification settings saved.")

    def _click_start(self) -> None:
        mode = self._mode_var.get()
        if mode == "live":
            if not messagebox.askyesno("⚠ LIVE TRADING",
                "You are about to start LIVE trading with REAL MONEY.\n\nAre you sure?"):
                return
        if mode != "sim" and not self._keys_applied:
            messagebox.showerror("API Keys Required",
                "Enter your API key + secret, then click  ✓ Apply Keys  before starting.")
            return
        # Apply capital/fee from UI (sim mode) — no separate button required
        if mode == "sim":
            self._apply_principal_from_ui()
        # Auto-apply strategy if sliders were changed but not applied
        if self._strategy_dirty:
            ok = self._apply_strategy(silent=True)
            if not ok:
                messagebox.showerror("Strategy Error",
                    "Strategy settings are invalid. Please fix them before starting.")
                return
        # Read symbols from field
        syms = [s.strip().upper() for s in self._symbols_var.get().split(",") if s.strip()]
        if syms:
            self._config.symbols = syms
        self._config.mode = mode
        self._start_btn.config(state="disabled", bg="#374151", fg=MUTED)
        self._stop_btn.config(state="normal", bg=RED, fg="white")
        self._start_hint.config(text="")
        self._bot_running  = True
        self._step_done[3] = True
        self._update_steps()
        self._on_start()

    def _click_stop(self) -> None:
        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled", bg="#374151", fg=MUTED)
        self._bot_running  = False
        self._step_done[3] = False
        self._keys_applied = False          # require re-apply after stop
        self._apply_keys_btn.config(text="✓  Apply Keys", bg=BLUE)
        self._refresh_ui()
        self._on_stop()

    def _click_reset(self) -> None:
        """Called from app.py on_reset — delegate to sim reset."""
        self._click_reset_sim()

    # ── Public API ────────────────────────────────────────────────────────────

    def update_account_stats(self, stats, usdt_balance: float = 0.0) -> None:
        if stats is None:
            return
        pnl_color = GREEN if stats.total_pnl_usd >= 0 else RED
        sign      = "+" if stats.total_pnl_usd >= 0 else ""
        self._acc_lbl["balance"].config(text=f"${usdt_balance:,.2f}")
        self._acc_lbl["value"].config(text=f"${stats.current_value:,.2f}")
        self._acc_lbl["pnl"].config(
            text=f"{sign}${stats.total_pnl_usd:,.2f} ({sign}{stats.total_pnl_pct*100:.2f}%)",
            fg=pnl_color)
        self._acc_lbl["fees"].config(text=f"${stats.total_fees:.4f}")
        self._acc_lbl["wins"].config(
            text=f"{stats.win_rate*100:.1f}% ({stats.wins}/{stats.trade_count})")
        self._acc_lbl["trades"].config(text=str(stats.trade_count))
        self._acc_lbl["drawdown"].config(text=f"{stats.max_drawdown_pct*100:.2f}%")

    def get_api_credentials(self) -> tuple[str, str]:
        return self._api_key_var.get().strip(), self._api_secret_var.get().strip()

    # ── Password dialog ───────────────────────────────────────────────────────

    def _prompt_password(self, title: str, confirm: bool = False) -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.configure(bg=CARD)
        dialog.resizable(False, False)
        dialog.grab_set()
        pw_var  = tk.StringVar()
        pw2_var = tk.StringVar()
        result  = [None]
        tk.Label(dialog, text="Master Password:", bg=CARD, fg=TEXT,
                 font=("Helvetica", 10)).grid(row=0, column=0, padx=12,
                                              pady=(12, 4), sticky="w")
        tk.Entry(dialog, textvariable=pw_var, show="•", bg=BG, fg=TEXT,
                 insertbackground=TEXT, relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, width=26).grid(
                     row=0, column=1, padx=(0, 12), pady=(12, 4))
        if confirm:
            tk.Label(dialog, text="Confirm:", bg=CARD, fg=TEXT,
                     font=("Helvetica", 10)).grid(row=1, column=0,
                                                   padx=12, pady=4, sticky="w")
            tk.Entry(dialog, textvariable=pw2_var, show="•", bg=BG, fg=TEXT,
                     insertbackground=TEXT, relief="flat", highlightthickness=1,
                     highlightbackground=BORDER, width=26).grid(
                         row=1, column=1, padx=(0, 12), pady=4)
        def _ok():
            pw = pw_var.get()
            if confirm and pw != pw2_var.get():
                messagebox.showerror("Mismatch", "Passwords do not match.",
                                     parent=dialog)
                return
            if len(pw) < 6:
                messagebox.showerror("Too Short",
                                     "Password must be at least 6 characters.",
                                     parent=dialog)
                return
            result[0] = pw
            dialog.destroy()
        row_n = 2 if confirm else 1
        br = tk.Frame(dialog, bg=CARD)
        br.grid(row=row_n, column=0, columnspan=2, pady=(4, 12))
        tk.Button(br, text="OK", command=_ok, bg=BLUE, fg="white",
                  relief="flat", padx=12, pady=4, cursor="hand2").pack(
                      side="left", padx=6)
        tk.Button(br, text="Cancel", command=dialog.destroy, bg="#374151",
                  fg=TEXT, relief="flat", padx=12, pady=4,
                  cursor="hand2").pack(side="left", padx=6)
        self.wait_window(dialog)
        return result[0]
