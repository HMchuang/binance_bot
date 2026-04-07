# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Bot

```bash
# Install dependencies
pip install -r requirements.txt

# GUI mode (preferred for interactive use)
python3 gui/app.py

# Terminal UI mode (no Tkinter window)
python3 gui/app.py --GUI false
python3 gui/app.py --GUI false --config config_sim.json

# Headless/server mode
python3 bot.py              # loads config.json, prompts for master password
python3 bot.py --mode testnet
python3 bot.py --config config_live.json
```

**No test suite exists.** Use Simulator mode for manual testing (no API keys needed).

## Architecture

```
TradingConfig (config.json) → TradingBot (bot.py)
  ├─ BinanceClient (core/exchange.py)     — REST API, HMAC auth, rate limiting
  ├─ Portfolio (core/portfolio.py)        — SQLite: positions, trades, balances
  ├─ RiskManager (core/risk.py)           — TP/SL/trailing stop, Kelly sizing, cooldown
  ├─ StrategyFactory (core/strategies.py) — returns BUY/SELL/HOLD signals
  ├─ Indicators (core/indicators.py)      — pure functions: RSI, EMA, MACD, BB, ADX, Stoch, ATR, OBV, VWAP, Hurst, PE
  └─ NotificationManager (core/notifications.py) — Discord/Telegram, non-blocking
```

The GUI (`gui/app.py`) and Terminal UI (`gui/terminal_ui.py`) run `TradingBot` in a background thread and communicate via `logger.LOG_QUEUE` (drained every 400ms). Portfolio stats are refreshed in a separate daemon thread to avoid blocking the UI.

`bot_gui.py` is the original monolithic 3500-line file — still functional but legacy. All new work goes into `bot.py` + `gui/app.py`.

## Operating Modes

| Mode | Orders | Balance source |
|---|---|---|
| `sim` | Simulated locally (SQLite only) | SQLite — starts at `sim_principal`, persists across restarts |
| `testnet` | Simulated locally (SQLite only, real lot sizes from API) | SQLite — starts at `sim_principal`, persists across restarts |
| `live` | Real orders sent to Binance exchange | Real Binance exchange balance |

**testnet is paper-trading:** `_execute_buy`/`_execute_sell` skip the exchange order API and record fills directly in SQLite, same as `sim`. The only difference between `sim` and `testnet` is that `testnet` fetches real `LOT_SIZE` filters from the Binance API so position quantities match live requirements.

## Core Trading Logic

Two strategies are available via `strategy` in config:

**`"regime"` (default) — RegimeAwareStrategy:**
Detects market regime using ADX, then applies the appropriate ruleset:

- **Trending (ADX > 25):** trend-following. New entries are hard-blocked when RSI > 72 or Stoch > 85 (overbought guard). Otherwise collects bullish/bearish factors: +DI vs −DI, Price vs MA50, MA20 vs MA50, RSI in 40–68 range, MACD histogram, OBV slope, volume ratio, VWAP direction, sentiment (F&G > 82 = bearish), RSI divergence, Hurst. Needs ≥ 3 bullish AND bullish > bearish.

- **Ranging (ADX < 20):** mean-reversion. Collects factors: RSI oversold/overbought, Stochastic turning up from oversold, price at lower/upper BB, RSI divergence (double-weighted), capitulation volume, Fear & Greed extremes, OBV slope, VWAP, Hurst. Needs ≥ 3 bullish AND bullish > bearish.

- **Transitional (ADX 20–25):** cautious trend-following. New entries are hard-blocked when RSI > 65 or Stoch > 80 (stricter guard than TRENDING). Uses a broader factor set combining directional and momentum indicators. Same confluence threshold as TRENDING (≥ 3 bullish AND bullish > bearish).

- Exits via strategy SELL only when bearish factors ≥ `sell_confluence` (default 2) AND bearish outnumber bullish by ≥ 2. Hard exits (trailing stop → ATR TP/SL → fixed-pct fallback) are handled by `RiskManager` before strategy is consulted.

- Permutation Entropy (PE) is computed and logged as a market-noise diagnostic but does **not** modify the entry threshold in the current implementation.

**`"winrate"` (legacy) — WinRateStrategy:**
Buys when composite score ≥ `buy_win_thresh` (60), sells when ≤ `sell_win_thresh` (35).

**Main loop order (per symbol):** fetch OHLCV → patch last candle close with live tick → MTF filter → drawdown check → risk exits (trailing → ATR TP/SL → fixed-pct) → post-SL cooldown check → strategy signal.

## Configuration

**`config.json`** — runtime settings (mode, symbols, thresholds, intervals, file paths).

**`.env`** — API keys and webhook URLs (copy from `.env.example`):
```
BINANCE_API_KEY, BINANCE_API_SECRET, DISCORD_WEBHOOK, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
```

**`saved_config.json`** — auto-saved GUI session state (principal, fee). **Do not store API keys here** — they belong in `.env` or the encrypted credential store.

## Key Implementation Details

- **SQLite** (`portfolio.db`): three tables — `positions`, `trades`, `balances`. All DB access in `core/portfolio.py` is thread-safe (single connection + `threading.Lock`).
- **Balance tracking**: `record_buy`/`record_sell` in `portfolio.py` update SQLite USDT and base-asset balances for both `sim` and `testnet` modes. `live` mode reads balance directly from exchange API.
- **`get_stats(usdt_balance=...)`**: accepts an optional `usdt_balance` override so callers can inject the correct balance (portfolio or exchange) without coupling Portfolio to the exchange client.
- **Credential encryption**: `utils/security.py` uses Fernet (PBKDF2 + AES-128-CBC). Master password prompted at startup in headless mode. Encrypted store is in `storage_dir` (`~/.binance_bot`).
- **Logging**: rotating file handler (10 MB, 5 backups) + `LOG_QUEUE` for GUI. Get a logger via `get_logger(name)` from `utils/logger.py`.
- **`core/indicators.py`** is pure functions with no state — safe to call from any thread. Uses `calc_rsi_wilder` everywhere (Wilder's smoothed RSI). Simple RSI kept only for compatibility.
- **`_run_cycle` fetches full OHLCV** (`get_klines_ohlcv`, 100 candles). Highs, lows, and volumes are passed to the strategy. The last candle's close is patched with the live tick price (and high/low updated accordingly) so indicators respond to real-time movement.
- **ATR-based stops**: `RiskManager.set_dynamic_stops()` stores per-symbol TP/SL price levels on entry. `check_exit()` checks trailing → ATR TP/SL → fixed-pct fallback. Fallback only fires when no dynamic stops are set (i.e., ATR was unavailable at entry). Config: `atr_tp_mult` (default 2.0) and `atr_sl_mult` (default 1.5). R:R = 1.33:1.
- **Trailing stop**: `RiskManager` tracks a per-symbol high-water mark. Once price drops `trailing_stop_pct` below the peak, it exits. Takes priority over ATR stops (checked first). Default: 1.0% for sim/testnet/live (see config files).
- **Position sizing**: `calc_position_size()` uses `order_pct` by default, switches to Kelly Criterion (`calc_kelly_fraction`) after 20+ closed trades. Kelly is further scaled by a volatility scalar (`baseline_atr_pct / current_atr_pct`, clamped 0.5–1.5).
- **Post-SL cooldown**: after a stop-loss, `RiskManager.on_stop_loss()` blocks re-entry for `cooldown_minutes` (30 min sim, 60 min testnet, 60 min live).
- **kline_interval**: All configs (`config.json`, `config_testnet.json`, `config_live.json`) use `15m` candles. `config_sim.json` also uses `15m`. The 15m timeframe gives smaller ATR values → tighter ATR stops that trigger on realistic intraday moves. On 1h candles, ATR is ~3–4× wider and stops rarely trigger in consolidating markets.
- **Drawdown circuit breaker**: if portfolio value drops >20% from `sim_principal`, the bot skips new entries for that symbol. Implemented in `_run_cycle` via `portfolio.get_stats()`.
- **4xx HTTP errors**: `exchange._request()` returns `None` immediately on any 4xx response — no retry. 5xx errors retry with exponential backoff (up to 5 attempts). Timestamp and signature are refreshed on every attempt so retries never fail due to a stale nonce.
- **`core/backtester.py`** implements backtest + parameter grid search but is not exposed via any CLI or GUI entry point yet.
- The `kline_interval` config key sets the candle timeframe sent to the API. The bot always uses the last 100 candles for signal generation regardless of interval.
- **Sell margin**: strategy SELL requires `nbe >= sell_confluence AND nbe > nb + 1` — bearish factors must outnumber bullish by at least 2, preventing whipsaw exits from a single noisy factor flip.
- **Fear & Greed**: blended as `sentiment = 0.6 × F&G + 0.4 × 55.0`, cached for 5 minutes (`_FNG_TTL = 300`). Falls back to neutral (55.0) on fetch failure.
