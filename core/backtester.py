"""
Backtesting engine + strategy optimizer.
Fetches historical OHLCV from Binance, caches to CSV, replays through strategy.
"""
from __future__ import annotations
import csv
import itertools
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import numpy as np

from core.indicators import calc_rsi, calc_macd_current, calc_bollinger_current, calc_ma, calc_win_chance
from core.strategies import StrategyFactory, Signal
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.exchange import BinanceClient

log = get_logger("backtester")


@dataclass
class BacktestConfig:
    symbol:           str
    interval:         str   = "1h"
    start_date:       str   = "2024-01-01"
    end_date:         str   = ""        # empty = today
    initial_capital:  float = 10_000.0
    fee_rate:         float = 0.001
    strategy_name:    str   = "winrate"
    strategy_params:  dict  = field(default_factory=dict)
    take_profit_pct:  float = 0.10
    stop_loss_pct:    float = 0.05
    order_pct:        float = 0.20


@dataclass
class TradeRecord:
    timestamp:     datetime
    side:          str
    price:         float
    qty:           float
    fee:           float
    pnl_usd:       float
    pnl_pct:       float
    reason:        str
    capital_after: float


@dataclass
class BacktestResult:
    symbol:          str
    strategy:        str
    params:          dict
    start_date:      str
    end_date:        str
    initial_capital: float
    final_capital:   float
    total_return_pct: float
    sharpe_ratio:    float
    max_drawdown_pct: float
    win_rate:        float
    total_trades:    int
    winning_trades:  int
    profit_factor:   float
    equity_curve:    list[float]
    trade_log:       list[TradeRecord]
    interval:        str


class Backtester:
    def __init__(self, exchange: Optional["BinanceClient"] = None,
                 data_dir: str = "backtest_data"):
        self.exchange = exchange
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

    def run(self, config: BacktestConfig) -> BacktestResult:
        ohlcv = self.fetch_or_load(config.symbol, config.interval,
                                   config.start_date, config.end_date)
        if not ohlcv:
            raise ValueError(f"No data for {config.symbol} {config.interval}")
        return self._simulate(ohlcv, config)

    def fetch_or_load(self, symbol: str, interval: str,
                      start_date: str, end_date: str) -> list[dict]:
        end_str  = end_date or datetime.now().strftime("%Y-%m-%d")
        filename = f"{symbol}_{interval}_{start_date}_{end_str}.csv"
        cache    = self.data_dir / filename

        if cache.exists():
            log.info(f"Loading cached data: {filename}")
            return self._load_csv(cache)

        if self.exchange is None:
            raise ValueError(f"No exchange client and no cached data for {symbol}")

        log.info(f"Fetching historical data: {symbol} {interval} {start_date} → {end_str}")
        start_ms = self._date_to_ms(start_date)
        end_ms   = self._date_to_ms(end_str) if end_str else None
        candles  = self.exchange.get_klines_historical(symbol, interval, start_ms, end_ms)
        if candles:
            self._save_csv(cache, candles)
        return candles

    def _simulate(self, ohlcv: list[dict], cfg: BacktestConfig) -> BacktestResult:
        strategy    = StrategyFactory.get_strategy(cfg.strategy_name, cfg.strategy_params)
        capital     = cfg.initial_capital
        holding_qty = 0.0
        entry_price = 0.0
        trade_log:  list[TradeRecord] = []
        equity:     list[float]       = []
        wins        = 0

        for i, candle in enumerate(ohlcv):
            closes = [c["c"] for c in ohlcv[:i + 1]]
            price  = candle["c"]
            equity.append(capital + holding_qty * price)

            if len(closes) < 30:
                continue

            # Check fixed SL/TP on open position
            if holding_qty > 0 and entry_price > 0:
                pnl_pct = (price - entry_price) / entry_price
                exit_reason: Optional[str] = None
                if pnl_pct >= cfg.take_profit_pct:
                    exit_reason = f"Take-profit +{pnl_pct*100:.2f}%"
                elif pnl_pct <= -cfg.stop_loss_pct:
                    exit_reason = f"Stop-loss {pnl_pct*100:.2f}%"
                if exit_reason:
                    gross   = holding_qty * price
                    fee     = gross * cfg.fee_rate
                    net     = gross - fee
                    pnl_usd = net - holding_qty * entry_price
                    capital += net
                    if pnl_usd > 0:
                        wins += 1
                    ts = datetime.fromtimestamp(candle["t"] / 1000) if "t" in candle else datetime.now()
                    trade_log.append(TradeRecord(
                        timestamp=ts, side="sell", price=price,
                        qty=holding_qty, fee=fee, pnl_usd=round(pnl_usd, 4),
                        pnl_pct=round(pnl_pct, 4), reason=exit_reason,
                        capital_after=round(capital, 2),
                    ))
                    holding_qty = 0.0
                    entry_price = 0.0
                    continue

            # Get strategy signal
            result = strategy.get_signal("BT", closes, holding_qty)

            if result.signal == Signal.BUY and holding_qty <= 0:
                order_usdt = capital * cfg.order_pct
                fee        = order_usdt * cfg.fee_rate
                net_usdt   = order_usdt - fee
                if net_usdt > 0 and price > 0:
                    qty         = net_usdt / price
                    capital    -= order_usdt
                    holding_qty = qty
                    entry_price = price
                    ts = datetime.fromtimestamp(candle["t"] / 1000) if "t" in candle else datetime.now()
                    trade_log.append(TradeRecord(
                        timestamp=ts, side="buy", price=price,
                        qty=qty, fee=fee, pnl_usd=0.0, pnl_pct=0.0,
                        reason=result.reason, capital_after=round(capital, 2),
                    ))

            elif result.signal == Signal.SELL and holding_qty > 0:
                gross    = holding_qty * price
                fee      = gross * cfg.fee_rate
                net      = gross - fee
                pnl_usd  = net - holding_qty * entry_price
                pnl_pct2 = (price - entry_price) / entry_price if entry_price else 0
                capital += net
                if pnl_usd > 0:
                    wins += 1
                ts = datetime.fromtimestamp(candle["t"] / 1000) if "t" in candle else datetime.now()
                trade_log.append(TradeRecord(
                    timestamp=ts, side="sell", price=price,
                    qty=holding_qty, fee=fee, pnl_usd=round(pnl_usd, 4),
                    pnl_pct=round(pnl_pct2, 4), reason=result.reason,
                    capital_after=round(capital, 2),
                ))
                holding_qty = 0.0
                entry_price = 0.0

        # Close any open position at end
        if holding_qty > 0:
            final_price = ohlcv[-1]["c"]
            gross   = holding_qty * final_price
            fee     = gross * cfg.fee_rate
            capital += gross - fee

        final_capital = round(capital, 2)
        total_return  = (final_capital - cfg.initial_capital) / cfg.initial_capital
        sell_trades   = [t for t in trade_log if t.side == "sell"]
        total_trades  = len(sell_trades)
        win_rate      = wins / total_trades if total_trades else 0.0
        gross_profit  = sum(t.pnl_usd for t in sell_trades if t.pnl_usd > 0)
        gross_loss    = abs(sum(t.pnl_usd for t in sell_trades if t.pnl_usd < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        start_str = ohlcv[0]["t"] if ohlcv and "t" in ohlcv[0] else cfg.start_date
        end_str   = ohlcv[-1]["t"] if ohlcv and "t" in ohlcv[-1] else cfg.end_date

        return BacktestResult(
            symbol=cfg.symbol, strategy=cfg.strategy_name, params=cfg.strategy_params,
            start_date=str(start_str), end_date=str(end_str),
            initial_capital=cfg.initial_capital, final_capital=final_capital,
            total_return_pct=round(total_return * 100, 2),
            sharpe_ratio=round(self._calc_sharpe(equity), 3),
            max_drawdown_pct=round(self._calc_max_drawdown(equity) * 100, 2),
            win_rate=round(win_rate, 4),
            total_trades=total_trades, winning_trades=wins,
            profit_factor=round(profit_factor, 3),
            equity_curve=equity, trade_log=trade_log,
            interval=cfg.interval,
        )

    def _calc_sharpe(self, equity: list[float]) -> float:
        if len(equity) < 2:
            return 0.0
        arr     = np.array(equity, dtype=float)
        returns = np.diff(arr) / arr[:-1]
        std     = float(np.std(returns))
        if std == 0:
            return 0.0
        return float(np.mean(returns) / std * np.sqrt(365))

    def _calc_max_drawdown(self, equity: list[float]) -> float:
        if not equity:
            return 0.0
        peak = equity[0]; max_dd = 0.0
        for v in equity:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _date_to_ms(date_str: str) -> int:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return int(dt.timestamp() * 1000)

    def _save_csv(self, path: Path, candles: list[dict]) -> None:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["t", "o", "h", "l", "c", "v"])
            writer.writeheader()
            writer.writerows(candles)

    def _load_csv(self, path: Path) -> list[dict]:
        candles = []
        with open(path, "r") as f:
            for row in csv.DictReader(f):
                candles.append({
                    "t": int(row["t"]), "o": float(row["o"]),
                    "h": float(row["h"]), "l": float(row["l"]),
                    "c": float(row["c"]), "v": float(row["v"]),
                })
        return candles


class StrategyOptimizer:
    def __init__(self, backtester: Backtester):
        self.backtester = backtester

    def grid_search(self, base_config: BacktestConfig,
                    param_grid: dict) -> list[BacktestResult]:
        """
        Run backtest for every combination of params.
        Returns all results sorted by Sharpe ratio descending.
        """
        keys  = list(param_grid.keys())
        vals  = list(param_grid.values())
        combos = list(itertools.product(*vals))
        results: list[BacktestResult] = []
        total = len(combos)
        log.info(f"Optimizer: {total} combinations to test")
        for idx, combo in enumerate(combos, 1):
            params = dict(zip(keys, combo))
            cfg    = BacktestConfig(
                symbol=base_config.symbol, interval=base_config.interval,
                start_date=base_config.start_date, end_date=base_config.end_date,
                initial_capital=base_config.initial_capital,
                fee_rate=base_config.fee_rate,
                strategy_name=base_config.strategy_name,
                strategy_params={**base_config.strategy_params, **params},
                take_profit_pct=base_config.take_profit_pct,
                stop_loss_pct=base_config.stop_loss_pct,
                order_pct=base_config.order_pct,
            )
            try:
                result = self.backtester.run(cfg)
                results.append(result)
                log.info(f"  [{idx}/{total}] params={params} sharpe={result.sharpe_ratio:.3f} "
                         f"return={result.total_return_pct:.1f}%")
            except Exception as e:
                log.warning(f"  [{idx}/{total}] params={params} failed: {e}")
        results.sort(key=lambda r: r.sharpe_ratio, reverse=True)
        return results

    def save_results(self, results: list[BacktestResult],
                     output_file: str = "optimization_results.csv") -> None:
        if not results:
            return
        fields = ["strategy", "params", "total_return_pct", "sharpe_ratio",
                  "max_drawdown_pct", "win_rate", "total_trades", "profit_factor"]
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in results:
                writer.writerow({
                    "strategy":         r.strategy,
                    "params":           str(r.params),
                    "total_return_pct": r.total_return_pct,
                    "sharpe_ratio":     r.sharpe_ratio,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "win_rate":         r.win_rate,
                    "total_trades":     r.total_trades,
                    "profit_factor":    r.profit_factor,
                })
        log.info(f"Optimizer results saved to {output_file}")

    def best_params(self, results: list[BacktestResult]) -> dict:
        if not results:
            return {}
        return results[0].params
