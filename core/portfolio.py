"""
SQLite-backed portfolio tracker. Thread-safe via threading.Lock.
Replaces all global dicts: sim_portfolio, entry_prices, trade_stats.
Positions and trades persist across bot restarts.
"""
from __future__ import annotations
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from utils.logger import get_logger

log = get_logger("portfolio")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    qty           REAL    NOT NULL,
    entry_price   REAL    NOT NULL,
    entry_time    TEXT    NOT NULL,
    mode          TEXT    NOT NULL,
    strategy      TEXT    NOT NULL,
    trailing_high REAL,
    is_open       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT    NOT NULL,
    side         TEXT    NOT NULL,
    qty          REAL    NOT NULL,
    price        REAL    NOT NULL,
    fee          REAL    NOT NULL,
    pnl_usd      REAL,
    pnl_pct      REAL,
    reason       TEXT,
    timestamp    TEXT    NOT NULL,
    mode         TEXT    NOT NULL,
    position_id  INTEGER
);

CREATE TABLE IF NOT EXISTS balances (
    asset      TEXT NOT NULL,
    qty        REAL NOT NULL,
    mode       TEXT NOT NULL DEFAULT 'sim',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (asset, mode)
);
"""


@dataclass
class Position:
    id: int
    symbol: str
    qty: float
    entry_price: float
    entry_time: datetime
    mode: str
    strategy: str
    trailing_high: Optional[float]
    is_open: bool


@dataclass
class Trade:
    id: int
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    pnl_usd: Optional[float]
    pnl_pct: Optional[float]
    reason: Optional[str]
    timestamp: datetime
    mode: str
    position_id: Optional[int]


@dataclass
class PortfolioStats:
    principal: float
    current_value: float
    trade_count: int
    wins: int
    total_fees: float
    win_rate: float
    total_pnl_usd: float
    total_pnl_pct: float
    peak_value: float
    max_drawdown_pct: float
    avg_win_pct: float = 0.0   # average winning trade return % (for Kelly Criterion)
    avg_loss_pct: float = 0.0  # average losing trade loss % (absolute, for Kelly Criterion)


def _parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.now()


def _row_to_position(row) -> Position:
    return Position(
        id=row[0], symbol=row[1], qty=row[2], entry_price=row[3],
        entry_time=_parse_dt(row[4]), mode=row[5], strategy=row[6],
        trailing_high=row[7], is_open=bool(row[8]),
    )


def _row_to_trade(row) -> Trade:
    return Trade(
        id=row[0], symbol=row[1], side=row[2], qty=row[3], price=row[4],
        fee=row[5], pnl_usd=row[6], pnl_pct=row[7], reason=row[8],
        timestamp=_parse_dt(row[9]), mode=row[10], position_id=row[11],
    )


class Portfolio:
    def __init__(self, db_path: str, mode: str, principal: float = 10_000.0):
        self._db_path   = db_path
        self._mode      = mode
        self._principal = principal
        self._lock      = threading.Lock()
        self._conn      = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_CREATE_SQL)
        self._conn.commit()
        # Ensure USDT balance is initialised for sim and testnet modes.
        # Both modes track spending in SQLite so Portfolio Value can be computed
        # without hitting the exchange API (live mode reads the real balance instead).
        if mode in ("sim", "testnet"):
            with self._lock:
                cur = self._conn.execute(
                    "SELECT qty FROM balances WHERE asset=? AND mode=?", ("USDT", mode))
                row = cur.fetchone()
                if row is None:
                    self._conn.execute(
                        "INSERT INTO balances(asset, qty, mode, updated_at) VALUES(?,?,?,?)",
                        ("USDT", principal, mode, datetime.now().isoformat()))
                    self._conn.commit()

    # ── Position management ──────────────────────────────────────────────────

    def open_position(self, symbol: str, qty: float, entry_price: float,
                      strategy: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO positions(symbol,qty,entry_price,entry_time,mode,strategy,trailing_high,is_open)"
                " VALUES(?,?,?,?,?,?,?,1)",
                (symbol, qty, entry_price, datetime.now().isoformat(),
                 self._mode, strategy, entry_price))
            self._conn.commit()
            return cur.lastrowid

    def close_position(self, position_id: int, exit_price: float, reason: str) -> Trade:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM positions WHERE id=?", (position_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Position {position_id} not found")
            pos = _row_to_position(row)
            pnl_usd = (exit_price - pos.entry_price) * pos.qty
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price if pos.entry_price else 0
            fee     = exit_price * pos.qty * 0.001  # approximate; caller may override via record_sell
            self._conn.execute("UPDATE positions SET is_open=0 WHERE id=?", (position_id,))
            cur2 = self._conn.execute(
                "INSERT INTO trades(symbol,side,qty,price,fee,pnl_usd,pnl_pct,reason,timestamp,mode,position_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (pos.symbol, "SELL", pos.qty, exit_price, fee,
                 round(pnl_usd, 4), round(pnl_pct, 6), reason,
                 datetime.now().isoformat(), self._mode, position_id))
            self._conn.commit()
            return Trade(
                id=cur2.lastrowid, symbol=pos.symbol, side="SELL",
                qty=pos.qty, price=exit_price, fee=fee,
                pnl_usd=round(pnl_usd, 4), pnl_pct=round(pnl_pct, 6),
                reason=reason, timestamp=datetime.now(),
                mode=self._mode, position_id=position_id,
            )

    def get_open_positions(self, mode: str | None = None) -> list[Position]:
        m = mode or self._mode
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM positions WHERE is_open=1 AND mode=? ORDER BY entry_time",
                (m,))
            return [_row_to_position(r) for r in cur.fetchall()]

    def get_open_position(self, symbol: str) -> Position | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM positions WHERE symbol=? AND mode=? AND is_open=1"
                " ORDER BY entry_time DESC LIMIT 1",
                (symbol, self._mode))
            row = cur.fetchone()
            return _row_to_position(row) if row else None

    def update_trailing_high(self, symbol: str, price: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE positions SET trailing_high=? WHERE symbol=? AND mode=? AND is_open=1",
                (price, symbol, self._mode))
            self._conn.commit()

    # ── Balance (sim mode) ───────────────────────────────────────────────────

    def get_balance(self, asset: str) -> float:
        with self._lock:
            cur = self._conn.execute(
                "SELECT qty FROM balances WHERE asset=? AND mode=?", (asset, self._mode))
            row = cur.fetchone()
            return float(row[0]) if row else 0.0

    def set_balance(self, asset: str, qty: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO balances(asset,qty,mode,updated_at) VALUES(?,?,?,?)"
                " ON CONFLICT(asset,mode) DO UPDATE SET qty=excluded.qty, updated_at=excluded.updated_at",
                (asset, qty, self._mode, datetime.now().isoformat()))
            self._conn.commit()

    def reset_sim(self, principal: float) -> None:
        self.reset_mode(principal)

    def reset_mode(self, principal: float) -> None:
        """Full reset for the current mode: wipes all trades, positions, and
        non-USDT balances, then sets USDT to `principal`. Works for sim/testnet/live."""
        with self._lock:
            self._conn.execute("DELETE FROM trades     WHERE mode=?", (self._mode,))
            self._conn.execute("DELETE FROM positions  WHERE mode=?", (self._mode,))
            self._conn.execute(
                "DELETE FROM balances WHERE mode=? AND asset != 'USDT'",
                (self._mode,))
            self._conn.execute(
                "INSERT INTO balances(asset,qty,mode,updated_at) VALUES(?,?,?,?)"
                " ON CONFLICT(asset,mode) DO UPDATE SET qty=excluded.qty, updated_at=excluded.updated_at",
                ("USDT", principal, self._mode, datetime.now().isoformat()))
            self._conn.commit()
            self._principal = principal
        log.info(f"[{self._mode}] Portfolio reset — ${principal:,.0f} USDT")

    # ── Convenience: record_buy / record_sell ────────────────────────────────

    def record_buy(self, symbol: str, qty: float, price: float,
                   fee: float, strategy: str) -> int:
        """Open position + update tracked USDT/base balances (sim and testnet modes)."""
        position_id = self.open_position(symbol, qty, price, strategy)
        if self._mode in ("sim", "testnet"):
            base     = symbol.replace("USDT", "")
            cost     = qty * price + fee
            usdt_bal = self.get_balance("USDT")
            base_bal = self.get_balance(base)
            self.set_balance("USDT", max(0.0, usdt_bal - cost))
            self.set_balance(base,  base_bal + qty)
        # Record buy trade
        with self._lock:
            self._conn.execute(
                "INSERT INTO trades(symbol,side,qty,price,fee,pnl_usd,pnl_pct,reason,timestamp,mode,position_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (symbol, "BUY", qty, price, fee, None, None, "buy",
                 datetime.now().isoformat(), self._mode, position_id))
            self._conn.commit()
        return position_id

    def record_sell(self, symbol: str, price: float, fee: float, reason: str) -> Trade | None:
        """Close open position + update tracked balances (sim and testnet modes)."""
        pos = self.get_open_position(symbol)
        if not pos:
            log.warning(f"record_sell: no open position for {symbol}")
            return None
        pnl_usd = (price - pos.entry_price) * pos.qty - fee
        pnl_pct = (price - pos.entry_price) / pos.entry_price if pos.entry_price else 0
        with self._lock:
            self._conn.execute("UPDATE positions SET is_open=0 WHERE id=?", (pos.id,))
            cur = self._conn.execute(
                "INSERT INTO trades(symbol,side,qty,price,fee,pnl_usd,pnl_pct,reason,timestamp,mode,position_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (symbol, "SELL", pos.qty, price, fee,
                 round(pnl_usd, 4), round(pnl_pct, 6), reason,
                 datetime.now().isoformat(), self._mode, pos.id))
            self._conn.commit()
        if self._mode in ("sim", "testnet"):
            base     = symbol.replace("USDT", "")
            proceeds = pos.qty * price - fee
            usdt_bal = self.get_balance("USDT")
            self.set_balance("USDT", usdt_bal + proceeds)
            self.set_balance(base,  0.0)
        return Trade(
            id=cur.lastrowid, symbol=symbol, side="SELL",
            qty=pos.qty, price=price, fee=fee,
            pnl_usd=round(pnl_usd, 4), pnl_pct=round(pnl_pct, 6),
            reason=reason, timestamp=datetime.now(),
            mode=self._mode, position_id=pos.id,
        )

    # ── Analytics ────────────────────────────────────────────────────────────

    def get_trade_history(self, limit: int = 100,
                          mode: str | None = None) -> list[Trade]:
        m = mode or self._mode
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM trades WHERE mode=? ORDER BY timestamp DESC LIMIT ?",
                (m, limit))
            return [_row_to_trade(r) for r in cur.fetchall()]

    def get_stats(self, current_prices: dict[str, float] | None = None,
                  usdt_balance: float | None = None) -> PortfolioStats:
        with self._lock:
            trades_cur = self._conn.execute(
                "SELECT side, fee, pnl_usd, pnl_pct FROM trades WHERE mode=?",
                (self._mode,))
            rows = trades_cur.fetchall()

        total_fees  = sum(r[1] for r in rows if r[1])
        sell_trades = [r for r in rows if r[0] == "SELL"]
        trade_count = len(sell_trades)
        wins        = sum(1 for r in sell_trades if r[2] and r[2] > 0)
        total_pnl   = sum(r[2] for r in sell_trades if r[2]) - total_fees
        win_rate    = wins / trade_count if trade_count else 0.0

        # Average win/loss percentages for Kelly Criterion sizing
        win_pcts  = [r[3] for r in sell_trades if r[2] and r[2] > 0  and r[3] is not None]
        loss_pcts = [abs(r[3]) for r in sell_trades if r[2] and r[2] <= 0 and r[3] is not None]
        avg_win_pct  = sum(win_pcts)  / len(win_pcts)  if win_pcts  else 0.0
        avg_loss_pct = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0.0

        # Current portfolio value = free USDT + market value of open positions.
        # Caller should pass the real exchange balance in live/testnet mode because
        # set_balance() only runs in sim mode (SQLite balance is always 0 otherwise).
        usdt_bal = usdt_balance if usdt_balance is not None else self.get_balance("USDT")
        open_pos = self.get_open_positions()
        current_value = usdt_bal
        for pos in open_pos:
            cp = (current_prices or {}).get(pos.symbol, pos.entry_price)
            current_value += pos.qty * cp

        # Rough peak/drawdown from equity snapshots
        peak_value = max(self._principal, current_value)
        max_dd = max(0.0, (peak_value - current_value) / peak_value) if peak_value > 0 else 0.0

        return PortfolioStats(
            principal=self._principal,
            current_value=round(current_value, 2),
            trade_count=trade_count,
            wins=wins,
            total_fees=round(total_fees, 4),
            win_rate=round(win_rate, 4),
            total_pnl_usd=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl / self._principal, 4) if self._principal else 0.0,
            peak_value=round(peak_value, 2),
            max_drawdown_pct=round(max_dd, 4),
            avg_win_pct=round(avg_win_pct, 4),
            avg_loss_pct=round(avg_loss_pct, 4),
        )

    def get_pnl_series(self) -> list[tuple[datetime, float]]:
        """Cumulative P&L over time from closed trades."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT timestamp, pnl_usd FROM trades WHERE mode=? AND side='SELL'"
                " ORDER BY timestamp ASC", (self._mode,))
            rows = cur.fetchall()
        cumulative = 0.0
        result = []
        for ts_str, pnl in rows:
            if pnl:
                cumulative += pnl
                result.append((_parse_dt(ts_str), round(cumulative, 2)))
        return result
