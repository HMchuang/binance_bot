"""
Single source of truth for all runtime configuration.
TradingConfig replaces the old global CONFIG dict.
Credentials are NOT stored here — use utils/security.py.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

TESTNET_URL = "https://testnet.binance.vision"
LIVE_URL    = "https://api.binance.com"


@dataclass
class TradingConfig:
    # Mode
    mode: Literal["sim", "testnet", "live"] = "sim"
    # Symbols — dynamic list, not hardcoded
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    # Strategy
    strategy: str = "regime"
    strategy_params: dict = field(default_factory=dict)
    # Risk
    order_pct: float = 0.20
    take_profit_pct: float = 0.10
    stop_loss_pct: float = 0.05
    trailing_stop_pct: float | None = None  # None = disabled
    buy_win_thresh: int = 60
    sell_win_thresh: int = 35
    # Execution
    order_type: Literal["MARKET", "LIMIT", "OCO"] = "MARKET"
    limit_offset_pct: float = 0.002  # for LIMIT: buy X% below market price
    # Timing
    loop_interval: int = 60
    kline_interval: str = "15m"
    # Simulator
    sim_principal: float = 10_000.0
    fee_rate: float = 0.001
    # Paths
    log_file: str = "bot.log"
    db_file: str = "portfolio.db"
    storage_dir: str = "~/.binance_bot"
    # ATR-based dynamic stops (0 = disabled, use fixed-pct instead)
    atr_tp_mult: float = 3.0   # take-profit = entry + atr_tp_mult × ATR14
    atr_sl_mult: float = 1.5   # stop-loss   = entry - atr_sl_mult × ATR14
    # Post-stop-loss cooldown: do not re-enter a symbol for this many minutes
    cooldown_minutes: int = 60
    # Multi-Timeframe filter: higher-TF interval for trend confirmation (None = disabled)
    # e.g. set "4h" when running on "1h" candles to confirm the 4h trend is bullish
    mtf_interval: str | None = None
    # Notifications (loaded from env if not set explicitly)
    discord_webhook: str | None = field(default=None)
    telegram_token: str | None = field(default=None)
    telegram_chat_id: str | None = field(default=None)

    def __post_init__(self):
        if self.discord_webhook is None:
            self.discord_webhook = os.getenv("DISCORD_WEBHOOK") or None
        if self.telegram_token is None:
            self.telegram_token = os.getenv("TELEGRAM_TOKEN") or None
        if self.telegram_chat_id is None:
            self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID") or None

    @property
    def base_url(self) -> str:
        return TESTNET_URL if self.mode == "testnet" else LIVE_URL

    @property
    def is_sim(self) -> bool:
        return self.mode == "sim"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


# Fields safe to persist to config.json (no secrets)
_SAFE_FIELDS = {
    "mode", "symbols", "strategy", "strategy_params",
    "order_pct", "take_profit_pct", "stop_loss_pct", "trailing_stop_pct",
    "buy_win_thresh", "sell_win_thresh", "order_type", "limit_offset_pct",
    "loop_interval", "kline_interval", "sim_principal", "fee_rate",
    "log_file", "db_file", "storage_dir",
    "atr_tp_mult", "atr_sl_mult", "cooldown_minutes", "mtf_interval",
}


def load_config(config_file: str = "config.json") -> TradingConfig:
    """Load non-secret config from JSON, with defaults fallback.

    Relative paths for log_file and db_file are resolved against the directory
    that contains config.json, not the process working directory. This keeps
    log and database files next to the config regardless of where the app is
    launched from.
    """
    data: dict = {}
    config_path = Path(config_file).resolve()
    project_dir = config_path.parent
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                data = json.load(f)
        except Exception:
            pass
    safe_data = {k: v for k, v in data.items() if k in _SAFE_FIELDS}
    cfg = TradingConfig(**safe_data)
    # Resolve relative file paths against the project directory
    for attr in ("log_file", "db_file"):
        val = getattr(cfg, attr)
        if val and not Path(val).is_absolute():
            setattr(cfg, attr, str(project_dir / val))
    return cfg


def save_config(cfg: TradingConfig, config_file: str = "config.json") -> None:
    """Save non-secret fields to JSON. Never writes API keys."""
    all_data = asdict(cfg)
    safe_data = {k: v for k, v in all_data.items() if k in _SAFE_FIELDS}
    with open(config_file, "w") as f:
        json.dump(safe_data, f, indent=2)


def validate_config(cfg: TradingConfig) -> list[str]:
    """Returns list of validation error strings. Empty list means valid."""
    errors: list[str] = []
    if not cfg.symbols:
        errors.append("symbols list cannot be empty")
    if not 0 < cfg.order_pct <= 1:
        errors.append("order_pct must be between 0 and 1")
    if not 0 < cfg.take_profit_pct <= 1:
        errors.append("take_profit_pct must be between 0 and 1")
    if not 0 < cfg.stop_loss_pct <= 1:
        errors.append("stop_loss_pct must be between 0 and 1")
    if cfg.trailing_stop_pct is not None and not 0 < cfg.trailing_stop_pct <= 1:
        errors.append("trailing_stop_pct must be between 0 and 1 if set")
    if cfg.loop_interval < 1:
        errors.append("loop_interval must be >= 1 second")
    return errors
