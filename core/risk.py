"""
Risk management: dynamic ATR-based stops, trailing stop, position sizing,
post-stop-loss cooldown, and drawdown circuit breaker.

RiskManager orchestrates all exit logic before strategy signals are checked.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

from utils.logger import get_logger

if TYPE_CHECKING:
    from core.config import TradingConfig
    from core.exchange import LotSize

log = get_logger("risk")


# ── Kelly Criterion (module-level, reusable) ──────────────────────────────────

def calc_kelly_fraction(win_rate: float, avg_win_pct: float, avg_loss_pct: float,
                        half_kelly: bool = True) -> float:
    """
    Kelly Criterion (Kelly 1956): optimal capital fraction to risk per trade.

    f* = W − (1 − W) / R
    where  W = win rate,  R = avg_win_pct / avg_loss_pct (reward/risk ratio).

    Half-Kelly (default) halves the fraction for safety — empirically reduces
    variance by ~75% while retaining ~75% of the Kelly growth rate.
    Clamped to [0.0, 0.50] so a single trade never exceeds 50% of capital.

    Returns 0.0 when trade history is insufficient (< 20 trades recommended).
    """
    if win_rate <= 0 or avg_loss_pct <= 0 or avg_win_pct <= 0:
        return 0.0
    r      = avg_win_pct / avg_loss_pct
    f_star = win_rate - (1.0 - win_rate) / r
    if half_kelly:
        f_star *= 0.5
    return max(0.0, min(0.50, float(f_star)))


@dataclass
class ExitSignal:
    reason: str
    trigger_price: float
    exit_type: Literal["tp", "sl", "trailing", "strategy"]


class TrailingStop:
    """Tracks high watermarks and triggers when price drops trail_pct below the peak."""

    def __init__(self, trail_pct: float):
        self.trail_pct = trail_pct
        self._high_watermarks: dict[str, float] = {}
        self._stop_levels:     dict[str, float] = {}

    def init_position(self, symbol: str, entry_price: float) -> None:
        self._high_watermarks[symbol] = entry_price
        self._stop_levels[symbol]     = entry_price * (1.0 - self.trail_pct)

    def update(self, symbol: str, current_price: float) -> bool:
        """Update high watermark. Returns True if trailing stop is triggered."""
        if symbol not in self._high_watermarks:
            return False
        if current_price > self._high_watermarks[symbol]:
            self._high_watermarks[symbol] = current_price
            self._stop_levels[symbol]     = current_price * (1.0 - self.trail_pct)
        return current_price <= self._stop_levels[symbol]

    def reset(self, symbol: str) -> None:
        self._high_watermarks.pop(symbol, None)
        self._stop_levels.pop(symbol, None)

    def get_stop_level(self, symbol: str) -> Optional[float]:
        return self._stop_levels.get(symbol)

    def get_high(self, symbol: str) -> Optional[float]:
        return self._high_watermarks.get(symbol)


class RiskManager:
    def __init__(self, config: "TradingConfig"):
        self.config   = config
        self._trailing: Optional[TrailingStop] = None
        if config.trailing_stop_pct:
            self._trailing = TrailingStop(config.trailing_stop_pct)

        # ATR-based dynamic TP/SL levels, keyed by symbol
        self._dynamic_tp: dict[str, float] = {}
        self._dynamic_sl: dict[str, float] = {}

        # Volatility-adjusted sizing scalar (updated per entry via set_volatility_scalar)
        self._vol_scalar: float = 1.0

        # Post-stop-loss cooldown: symbol → unix timestamp when cooldown expires
        self._cooldown_until: dict[str, float] = {}

    def on_position_opened(self, symbol: str, entry_price: float) -> None:
        if self._trailing:
            self._trailing.init_position(symbol, entry_price)

    def on_position_closed(self, symbol: str) -> None:
        if self._trailing:
            self._trailing.reset(symbol)
        self._dynamic_tp.pop(symbol, None)
        self._dynamic_sl.pop(symbol, None)

    def set_dynamic_stops(self, symbol: str, entry_price: float, atr: float,
                          tp_mult: float, sl_mult: float) -> None:
        """
        Store ATR-based TP and SL price levels for a position.

        ATR-adaptive stops avoid the two failure modes of fixed-pct stops:
          - Fixed stops too tight → stopped out by normal noise
          - Fixed stops too wide  → gives back too much profit before exiting

        Typical parameters: tp_mult=3.0, sl_mult=1.5 (2:1 reward/risk ratio).
        """
        if atr <= 0 or tp_mult <= 0 or sl_mult <= 0:
            return
        self._dynamic_tp[symbol] = entry_price + atr * tp_mult
        self._dynamic_sl[symbol] = entry_price - atr * sl_mult
        log.info(
            f"[{symbol}] ATR stops set — "
            f"entry=${entry_price:.4f} ATR={atr:.4f} "
            f"TP=${self._dynamic_tp[symbol]:.4f} (+{tp_mult}×ATR) "
            f"SL=${self._dynamic_sl[symbol]:.4f} (-{sl_mult}×ATR)"
        )

    def check_exit(self, symbol: str, current_price: float,
                   entry_price: float, holding_qty: float) -> Optional[ExitSignal]:
        """
        Returns ExitSignal if position should be closed, else None.

        Evaluation order:
          1. Trailing stop   (most dynamic — responds to price in real time)
          2. ATR-based TP/SL (adaptive to volatility at entry time)
          3. Fixed-pct TP/SL (fallback when ATR stops are not set)
        """
        if holding_qty <= 0 or entry_price <= 0:
            return None
        pnl_pct = (current_price - entry_price) / entry_price

        # 1. Trailing stop
        if self._trailing and self._trailing.update(symbol, current_price):
            high  = self._trailing.get_high(symbol)
            level = self._trailing.get_stop_level(symbol)
            return ExitSignal(
                reason=f"Trailing stop — high=${high:.2f}, stop=${level:.2f}",
                trigger_price=current_price,
                exit_type="trailing",
            )

        # 2. ATR-based dynamic TP/SL
        if symbol in self._dynamic_tp and current_price >= self._dynamic_tp[symbol]:
            return ExitSignal(
                reason=f"ATR take-profit ${self._dynamic_tp[symbol]:.4f} "
                       f"(+{pnl_pct * 100:.2f}%)",
                trigger_price=current_price,
                exit_type="tp",
            )
        if symbol in self._dynamic_sl and current_price <= self._dynamic_sl[symbol]:
            return ExitSignal(
                reason=f"ATR stop-loss ${self._dynamic_sl[symbol]:.4f} "
                       f"({pnl_pct * 100:.2f}%)",
                trigger_price=current_price,
                exit_type="sl",
            )

        # 3. Fixed-pct fallback (only when no dynamic stops are set)
        if symbol not in self._dynamic_tp:
            if pnl_pct >= self.config.take_profit_pct:
                return ExitSignal(
                    reason=f"Take-profit +{pnl_pct * 100:.2f}%",
                    trigger_price=current_price,
                    exit_type="tp",
                )
            if pnl_pct <= -self.config.stop_loss_pct:
                return ExitSignal(
                    reason=f"Stop-loss {pnl_pct * 100:.2f}%",
                    trigger_price=current_price,
                    exit_type="sl",
                )

        return None

    # ── Post-stop-loss cooldown ───────────────────────────────────────────────

    def get_stops(self, symbol: str) -> tuple[float | None, float | None]:
        """Return (tp_price, sl_price) for the symbol, or (None, None) if not set."""
        return (self._dynamic_tp.get(symbol), self._dynamic_sl.get(symbol))

    def on_stop_loss(self, symbol: str, cooldown_minutes: int = 60) -> None:
        """
        Record a stop-loss event and block new entries for `cooldown_minutes`.

        After a stop loss the market is typically still moving against the
        original thesis.  Entering immediately is one of the most common
        ways retail traders amplify losses ("revenge trading").
        """
        if cooldown_minutes <= 0:
            return
        expiry = time.time() + cooldown_minutes * 60
        self._cooldown_until[symbol] = expiry
        log.info(
            f"[{symbol}] Post-SL cooldown active for {cooldown_minutes} min "
            f"(until {time.strftime('%H:%M:%S', time.localtime(expiry))})"
        )

    def is_in_cooldown(self, symbol: str) -> bool:
        """Returns True if the symbol is still in its post-SL cooldown window."""
        expiry = self._cooldown_until.get(symbol)
        if expiry is None:
            return False
        if time.time() < expiry:
            return True
        del self._cooldown_until[symbol]
        return False

    # ── Position sizing ───────────────────────────────────────────────────────

    def set_volatility_scalar(self, atr_pct: float,
                               baseline_atr_pct: float = 0.02) -> None:
        """
        Volatility-adjusted position sizing.

        Scales position size down when current volatility (ATR%) is high and
        up when it is low, targeting a fixed dollar-risk per trade.

        atr_pct          = ATR / price  (current normalised volatility)
        baseline_atr_pct = neutral reference point (default 2% ATR)

        scalar = baseline / current, clamped to [0.5, 1.5].
        A 4% ATR halves the position; a 1% ATR would grow it (capped at 1.5×).
        """
        if atr_pct <= 0:
            self._vol_scalar = 1.0
            return
        self._vol_scalar = max(0.5, min(1.5, baseline_atr_pct / atr_pct))

    def calc_position_size(self, available_usdt: float, price: float,
                            lot_size: "LotSize",
                            kelly_fraction: float = 0.0,
                            vol_scalar: float = 1.0) -> float:
        """
        Calculate buy quantity rounded to exchange lot size.

        kelly_fraction > 0 overrides config.order_pct with the Kelly-optimal
        fraction (call calc_kelly_fraction() with portfolio stats to obtain it).
        vol_scalar adjusts size for current ATR volatility (from set_volatility_scalar).
        Both default to neutral values so existing call-sites need no change.
        """
        from core.exchange import BinanceClient
        base_pct   = kelly_fraction if kelly_fraction > 0 else self.config.order_pct
        eff_pct    = base_pct * vol_scalar
        order_usdt = available_usdt * eff_pct

        if order_usdt < lot_size.min_notional:
            # Buffer must cover: fee deduction + worst-case step-size floor loss.
            # After fee deduction and round_step (floor), the executed notional
            # can be up to one step_size * price below the gross order_usdt.
            # Formula: min_needed = (min_notional + step_loss) / (1 - fee_rate)
            step_loss  = lot_size.step_size * price
            min_needed = (lot_size.min_notional + step_loss) / (1.0 - self.config.fee_rate)
            if available_usdt >= min_needed:
                log.info(
                    f"Order ${order_usdt:.2f} below min notional "
                    f"${lot_size.min_notional:.2f} — bumping to minimum"
                )
                order_usdt = min_needed
            else:
                log.warning(
                    f"Insufficient balance ${available_usdt:.2f} to meet "
                    f"min notional ${lot_size.min_notional:.2f} — skipping"
                )
                return 0.0

        fee      = order_usdt * self.config.fee_rate
        net_usdt = order_usdt - fee
        if price <= 0:
            return 0.0
        raw_qty = net_usdt / price
        return BinanceClient.round_step(raw_qty, lot_size.step_size)

    def check_drawdown_limit(self, current_value: float, peak_value: float,
                              limit_pct: float = 0.20) -> bool:
        """Returns True if max drawdown exceeded — bot should pause trading."""
        if peak_value <= 0:
            return False
        return (peak_value - current_value) / peak_value >= limit_pct

    def update_config(self, config: "TradingConfig") -> None:
        self.config = config
        if config.trailing_stop_pct:
            self._trailing = TrailingStop(config.trailing_stop_pct)
        else:
            self._trailing = None
