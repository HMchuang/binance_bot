"""Color-tagged scrolling trade log. Drains LOG_QUEUE every 400ms."""
import queue
import tkinter as tk
from tkinter import scrolledtext

from utils.logger import LOG_QUEUE, get_logger

BG = "#0e1117"; CARD = "#161b26"; TEXT = "#e8eaf0"; MUTED = "#6b7280"
GREEN = "#22c55e"; RED = "#ef4444"; YELLOW = "#f59e0b"; BLUE = "#3b82f6"

log = get_logger("log_panel")


class LogPanel(tk.Frame):
    MAX_LINES = 500

    def __init__(self, parent: tk.Widget, **kwargs):
        super().__init__(parent, bg=CARD, **kwargs)
        self._build()
        self._poll()

    def _build(self) -> None:
        header = tk.Frame(self, bg=CARD)
        header.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(header, text="Trade Log", bg=CARD, fg=TEXT,
                 font=("Helvetica", 11, "bold"), anchor="w").pack(side="left")
        tk.Button(header, text="Clear", bg="#374151", fg=TEXT, relief="flat",
                  padx=6, pady=2, cursor="hand2", font=("Helvetica", 9),
                  command=self.clear).pack(side="right")

        self._text = scrolledtext.ScrolledText(
            self, bg="#080b12", fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=0, font=("Courier", 9),
            state="disabled", wrap="none",
        )
        self._text.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        bold = ("Courier", 9, "bold")
        norm = ("Courier", 9)
        self._text.tag_config("buy",         foreground="#93c5fd", background="#1e3a5f", font=bold)
        self._text.tag_config("sell_profit", foreground="#86efac", background="#14532d", font=bold)
        self._text.tag_config("sell_loss",   foreground="#fca5a5", background="#7f1d1d", font=bold)
        self._text.tag_config("tp",          foreground="#bbf7d0", background="#166534", font=norm)
        self._text.tag_config("sl",          foreground="#fecaca", background="#991b1b", font=norm)
        self._text.tag_config("warning",     foreground="#fef08a", background="#713f12", font=norm)
        self._text.tag_config("error",       foreground="white",   background="#991b1b", font=bold)
        self._text.tag_config("info",        foreground=MUTED,     font=norm)
        self._text.tag_config("scan",        foreground="#4b5563",  font=norm)

    def _poll(self) -> None:
        try:
            for _ in range(50):
                msg = LOG_QUEUE.get_nowait()
                self._append(msg)
        except queue.Empty:
            pass
        self.after(400, self._poll)

    def _append(self, msg: str) -> None:
        tag = self._classify_tag(msg)
        self._text.config(state="normal")
        self._text.insert("end", msg + "\n", tag)
        lines = int(self._text.index("end-1c").split(".")[0])
        if lines > self.MAX_LINES:
            self._text.delete("1.0", f"{lines - self.MAX_LINES}.0")
        self._text.see("end")
        self._text.config(state="disabled")

    def _classify_tag(self, msg: str) -> str:
        m = msg.lower()
        if "buy" in m and ("[sim]" in m or "[testnet]" in m or "[live]" in m):
            return "buy"
        if "sell filled" in m:
            return "sell_profit" if ("profit" in m or "+" in m) else "sell_loss"
        if "take-profit" in m: return "tp"
        if "stop-loss" in m or "trailing stop" in m: return "sl"
        if "warning" in m or "warn" in m: return "warning"
        if "error" in m: return "error"
        if "scanning" in m or "waiting" in m or "watching" in m: return "scan"
        return "info"

    def clear(self) -> None:
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.config(state="disabled")

    def append_message(self, msg: str) -> None:
        self._append(msg)
