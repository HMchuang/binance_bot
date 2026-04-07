# Binance Auto Trading Bot

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20windows-lightgrey)

A professional-grade automated cryptocurrency trading bot for Binance Spot with three interfaces, three operating modes, and an adaptive regime-aware strategy.

---

## Features

- **Three interfaces** — Tkinter GUI, Rich terminal UI, headless CLI
- **Three modes** — Simulator (no API keys), Testnet (paper-trade with real market data), Live
- **Regime-aware strategy** — detects trending vs ranging markets via ADX and applies the appropriate ruleset
- **Overbought entry guards** — hard-blocks new long entries when RSI/Stochastic signal extreme overbought conditions
- **ATR-based dynamic stops** — take-profit and stop-loss adapt to volatility at entry time
- **Trailing stop** — locks in profit by tracking the high-water mark and exiting on pullback
- **Kelly Criterion sizing** — optimal position sizing after 20+ closed trades, volatility-scaled
- **Risk management** — drawdown circuit breaker, post-SL cooldown, min-notional guard
- **Encrypted credential storage** — API keys encrypted with Fernet (PBKDF2 + AES-128-CBC)
- **Discord / Telegram notifications** — optional trade alerts
- **SQLite portfolio** — thread-safe trade history, positions, balances

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment (testnet / live only)

```bash
cp .env.example .env
# Edit .env and fill in your Binance API keys
```

### 3. Run

```bash
# GUI mode (recommended for interactive use)
python3 gui/app.py

# Terminal UI mode
python3 gui/app.py --GUI false

# Terminal UI with a specific config
python3 gui/app.py --GUI false --config config_sim.json

# Headless / server mode
python3 bot.py
python3 bot.py --mode testnet
python3 bot.py --config config_live.json
```

---

## Operating Modes

| Mode | Capital | Orders | API Keys | Use Case |
|---|---|---|---|---|
| `sim` | Virtual (SQLite, starts at `sim_principal`) | Simulated locally | Not required | Strategy testing, learning |
| `testnet` | Virtual (SQLite, starts at `sim_principal`) | Simulated locally with real lot sizes | Required (for market data) | Paper-trading with real prices and accurate lot sizes |
| `live` | Real Binance account balance | Sent to Binance exchange | Required | Production trading |

**sim vs testnet:** Both use virtual SQLite-tracked money. The only difference is that testnet fetches real lot-size constraints from the Binance API so quantities match what live trading would require. Neither sends real orders.

**testnet vs live:** Live sends real orders to Binance and uses your actual exchange balance.

> In **sim** and **testnet** modes, the USDT balance persists in SQLite across restarts. It resets to `sim_principal` only when there are no open positions.

### Testnet API Keys
1. Visit [testnet.binance.vision](https://testnet.binance.vision)
2. Log in with GitHub
3. Generate API Key under *API Management*
4. Paste into the GUI or `.env`

---

## Minimum Recommended Balance

Based on the default `order_pct=0.30` (30% per trade), ATR TP ~0.4% (2×ATR on 15m candles), ATR SL ~0.3% (1.5×ATR), and `fee_rate=0.1%` per order:

| Balance | Position (30%) | Net TP gain | Net SL loss | Verdict |
|---|---|---|---|---|
| $50 | $15 | ~$0.03 | ~$0.05 | Fees dominate — not viable |
| $100 | $30 | ~$0.06 | ~$0.09 | Marginal |
| $500 | $150 | +$0.30 | −$0.45 | Practical minimum |
| $1,000 | $300 | +$0.60 | −$0.90 | Comfortable — survives losing streaks |

**Math per trade (15m candles, BTC ~$69K):**
- Win: `position × ~0.4% ATR TP − 0.2% round-trip fee`
- Loss: `position × ~0.3% ATR SL + 0.2% round-trip fee`
- At ~55% win rate with $1,000: expected value ≈ +$0.04 per trade

Individual trade P&L is small on 15m — the edge comes from **trade frequency** (many trades per day) and compounding. Trailing stop also locks in profits on larger moves beyond the TP target.

**Recommendation: start with $500–$1,000.** Below $500, fee drag and the 20% drawdown circuit breaker together make consistent compounding very difficult.

> ATR varies by asset and timeframe. These figures use typical 15m BTC ATR (~0.2% of price). Actual TP/SL distances will differ.

---

## Config Files

Four ready-made configs are provided. Pass them with `--config`:

| File | Mode | Symbols | Candles | Scan Rate | Order Type |
|---|---|---|---|---|---|
| `config.json` | testnet | BTC, ETH | 15m | 60s | MARKET |
| `config_sim.json` | sim | BTC, ETH, BNB, SOL | 15m | 10s | MARKET |
| `config_testnet.json` | testnet | BTC, ETH | 15m | 60s | MARKET |
| `config_live.json` | live | BTC, ETH | 15m | 60s | MARKET |

`config.json` is the GUI default.

### Key parameters

| Parameter | Description | sim | testnet | live |
|---|---|---|---|---|
| `order_pct` | Fraction of USDT balance per trade | 25% | 30% | 30% |
| `atr_tp_mult` | Take-profit = entry + N × ATR | 2.0 | 2.0 | 2.0 |
| `atr_sl_mult` | Stop-loss = entry − N × ATR | 1.5 | 1.5 | 1.5 |
| `trailing_stop_pct` | Trailing stop — % below peak before exit | 1.0% | 1.0% | 1.0% |
| `take_profit_pct` | Fixed-pct TP fallback (ATR overrides) | 1.5% | 1.5% | 1.5% |
| `stop_loss_pct` | Fixed-pct SL fallback (ATR overrides) | 1.0% | 1.0% | 1.0% |
| `sell_confluence` | Bearish factors required for strategy sell | 2 | 2 | 2 |
| `cooldown_minutes` | Re-entry block after SL hit | 30 | 60 | 60 |
| `loop_interval` | Seconds between scans | 10 | 60 | 60 |
| `kline_interval` | Candle timeframe | 15m | 15m | 15m |

> `take_profit_pct` and `stop_loss_pct` are **fallbacks only** — used when ATR cannot be calculated (< 2 candles). The ATR multipliers take priority in normal operation.

---

## Trading Strategy — Regime Aware

The bot uses **RegimeAwareStrategy**, which detects the current market condition via ADX before deciding what to do.

### Step 1 — Detect regime

| ADX Value | Regime | Approach |
|---|---|---|
| > 25 | Trending | Trend-following |
| 20–25 | Transitional | Cautious trend-following with stricter overbought guard |
| < 20 | Ranging | Mean-reversion |

Entry requires **confluence** — multiple independent factors must agree. A single indicator never triggers a trade.

### Step 2 — Overbought entry guards

Both TRENDING and TRANSITIONAL regimes hard-block new long entries when indicators signal an overbought market. If already holding, RiskManager handles the exit — these guards only prevent new entries.

| Regime | Block condition |
|---|---|
| TRENDING | RSI > 72 **or** Stochastic %K > 85 |
| TRANSITIONAL | RSI > 65 **or** Stochastic %K > 80 |

### Step 3 — Entry signals

**Trending market (ADX > 25):**

Entry fires when ≥ 3 bullish factors AND bullish count > bearish count (after overbought guard passes).

| Factor | Bullish condition | Bearish condition |
|---|---|---|
| DI direction | +DI > −DI | −DI > +DI |
| Price vs MA50 | Price > MA50 | Price < MA50 |
| MA alignment | MA20 > MA50 | MA20 < MA50 |
| RSI healthy range | RSI 40–68 | RSI > 72 (overbought) or RSI < 35 (collapsed) |
| MACD histogram | > 0 | ≤ 0 |
| OBV slope | > 0.05 | < −0.05 |
| Volume activity | Ratio ≥ 1.15× | — |
| VWAP | Price above VWAP | Price below VWAP |
| Sentiment | — | Fear & Greed > 82 (extreme greed) |
| RSI divergence | Bullish divergence | Bearish divergence (double-weighted) |
| Hurst Exponent | — | H < 0.38 (anti-persistent — trend less reliable) |

**Ranging market (ADX < 20):**

Entry fires when ≥ 3 bullish factors AND bullish count > bearish count.

| Factor | Bullish condition | Bearish condition |
|---|---|---|
| RSI | < 38 (oversold) | > 65 (overbought) |
| Stochastic | %K < 25 AND %K turning up | %K > 75 AND %K turning down |
| Bollinger Band | Price at lower BB (bb_pct < 0.12) | Price at upper BB (bb_pct > 0.88) |
| RSI divergence | Bullish divergence (double-weighted) | Bearish divergence (double-weighted) |
| Capitulation | Volume spike on down bar with RSI < 45 | — |
| Sentiment | Fear & Greed < 25 (extreme fear) | Fear & Greed > 78 (extreme greed) |
| OBV slope | > 0.08 | < −0.08 |
| VWAP | Price below VWAP (buying below fair value) | — |
| Hurst Exponent | — | H > 0.65 (persistent — range may be breaking out) |

**Transitional market (ADX 20–25):**

Uses a broader factor set combining elements from both regimes. Same confluence threshold as TRENDING (3 factors). The key difference is the stricter overbought guard (RSI > 65 or Stoch > 80) that blocks entries at lower extreme levels.

Factors: +DI vs −DI, MA20 vs MA50, Price vs MA50, RSI < 48 (bullish) / RSI > 65 (bearish), Stoch < 35 turning up (bullish) / Stoch > 70 turning down (bearish), MACD histogram, OBV slope (threshold 0.08), VWAP, RSI divergence.

### Step 4 — Exit

| Exit type | Trigger | Priority |
|---|---|---|
| Trailing stop | Price drops `trailing_stop_pct` below the high-water mark | 1st (most dynamic) |
| ATR take-profit | Price reaches `entry + atr_tp_mult × ATR` | 2nd |
| ATR stop-loss | Price drops to `entry − atr_sl_mult × ATR` | 2nd |
| Fixed-pct TP/SL | Fallback when ATR stops not set | 3rd |
| Strategy sell | Bearish factors ≥ `sell_confluence` (2) AND outnumber bullish by ≥ 2 | Last |

### Safety mechanisms

| Mechanism | Behaviour |
|---|---|
| Drawdown circuit breaker | Halts all new entries if portfolio drops >20% from `sim_principal` |
| Post-SL cooldown | Blocks re-entry into same symbol for `cooldown_minutes` after a stop-loss |
| Fear & Greed blend | Sentiment = 60% Fear & Greed Index + 40% neutral (55) baseline; cached 5 min |
| MTF veto | Optional higher-timeframe MA check blocks long entries when broader trend is down |
| Min-notional guard | Position is skipped if calculated size falls below Binance min notional |

---

## Position Sizing

### Default: fixed fraction

`order_usdt = available_usdt × order_pct`

### After 20+ trades: Kelly Criterion

```
f* = W − (1 − W) / R
```
where W = win rate, R = avg_win / avg_loss. Half-Kelly is used (×0.5) to reduce variance. Clamped to [0%, 50%] of capital.

### Volatility scalar

`scalar = 0.02 / (ATR / price)`, clamped to [0.5, 1.5].

High volatility (ATR > 2% of price) scales position down; low volatility scales it up. Both Kelly and scalar are applied together: `effective_pct = kelly_or_order_pct × vol_scalar`.

---

## Quantitative Indicators

### Hurst Exponent (R/S Analysis)

Computed via Rescaled Range (R/S) analysis over log-returns at geometric window sizes.

| H value | Interpretation | Strategy use |
|---|---|---|
| > 0.55 | Persistent / trending | Trend-following signals are more reliable |
| ≈ 0.5 | Random walk | Low signal reliability |
| < 0.45 | Anti-persistent / mean-reverting | RSI/BB reversals work best |

In trending regime: H < 0.38 → adds a bearish confluence factor (trend may not be reliable).
In ranging regime: H > 0.65 → adds a bearish confluence factor (range may be breaking out — avoid fade).

### Permutation Entropy (Bandt & Pompe)

Measures ordinal pattern complexity of the close price series (order = 3).

| PE value | Interpretation |
|---|---|
| < 0.70 | Structured market — indicators are reliable |
| 0.70–0.85 | Moderate noise |
| > 0.85 | High noise — market is near-random |

Displayed in every log line as `PE=X.XX`. Used as a market-quality diagnostic; does not directly modify entry thresholds.

### VWAP

Volume-weighted mean of the typical price `(H+L+C)/3` over the 100-candle window.

- Above VWAP in trending/transitional regime → net institutional buy pressure (bullish)
- Below VWAP in ranging regime → buying below fair value, targeting mean-reversion (bullish)

### Fear & Greed Index

Fetched from `api.alternative.me/fng/` every 5 minutes. Blended into sentiment as:
`sentiment = 0.6 × F&G + 0.4 × 55.0`

Used as a factor in RANGING (extreme fear < 25 = bullish, extreme greed > 78 = bearish) and in TRENDING (extreme greed > 82 = bearish only).

### Multi-Timeframe (MTF) Filter

`is_mtf_bullish()` checks MA20 > MA50 AND price > MA50 on the higher timeframe. Controlled by `mtf_interval` in config (`null` = disabled). When enabled, a bearish reading on the higher TF vetoes all long entries.

---

## Architecture

```
TradingConfig (config.json)
  └─ TradingBot (bot.py)
       ├─ BinanceClient    (core/exchange.py)      REST API, HMAC auth, rate limiting, no 4xx retries
       ├─ Portfolio        (core/portfolio.py)     SQLite: positions, trades, balances (thread-safe)
       ├─ RiskManager      (core/risk.py)          TP/SL/trailing stop, Kelly sizing, cooldown
       ├─ StrategyFactory  (core/strategies.py)    BUY / SELL / HOLD signals
       ├─ Indicators       (core/indicators.py)    Pure functions: RSI, EMA, MACD, BB, ADX, Stoch, ATR, OBV, VWAP, Hurst, PE
       └─ NotificationMgr  (core/notifications.py) Discord / Telegram, non-blocking thread
```

The GUI (`gui/app.py`) and Terminal UI (`gui/terminal_ui.py`) run `TradingBot` in a background thread and communicate via `logger.LOG_QUEUE` (drained every 400ms). Portfolio stats are refreshed in a separate daemon thread to avoid blocking the UI.

### Main loop order (per symbol, per cycle)

```
1. Fetch OHLCV (100 candles) + patch last close with live tick price
2. Optional MTF filter (higher-timeframe trend check)
3. Drawdown circuit breaker check (>20% from principal → skip new entries)
4. Risk exits evaluated first: trailing stop → ATR TP/SL → fixed-pct fallback
5. Post-SL cooldown check (blocks new entries after a stop-loss)
6. Strategy signal (BUY / SELL / HOLD)
```

---

## Interfaces

### GUI (`python3 gui/app.py`)

Full Tkinter desktop application:
- Mode selector (Simulator / Testnet / Live)
- API key management with encrypted storage
- Strategy sliders (order size, TP %, SL %, trailing stop, scan interval)
- Live candlestick chart (price, volume, RSI, MACD)
- Market scanner — regime, ADX, RSI, Stochastic, BB position per symbol
- Open positions with live unrealized P&L, TP/SL price levels
- Scrolling trade log
- Account overview (USDT balance, portfolio value, total P&L, win rate, drawdown)
- **AUTO / MANUAL trading mode toggle** — switch between fully automated trading (⚡ AUTO) and signal-only mode (✋ MANUAL) at any time without restarting

### Terminal UI (`python3 gui/app.py --GUI false`)

Rich terminal dashboard — suitable for servers with a display:

```
┌─ ⠋ Binance Auto Trading Bot [TESTNET]  strategy=regime  next scan in 8s ──────┐
│  Account Stats              │  Trade Log                                        │
│  USDT Balance  $7,003.04    │  21:59:55 | [ETHUSDT] signal=BUY [RANGE] RSI..  │
│  Portfolio     $10,011.22   │  21:59:55 | [ETHUSDT] [TESTNET] BUY 1.463200    │
│  Total P&L     +$11.22      │                                                   │
│  Win Rate      60.0%        │                                                   │
│  Last scan     3s ago       │                                                   │
├─ Latest Signals ────────────┤                                                   │
│  BTCUSDT  HOLD  [RANGE]     │                                                   │
│  ETHUSDT  HOLD  [RANGE]     │                                                   │
├─ Open Positions ────────────┤                                                   │
│  ETHUSDT  1.4632  entry=..  │                                                   │
└─────────────────────────────┴───────────────────────────────────────────────────┘
```

### Headless (`python3 bot.py`)

No display required. All output goes to `bot.log`. Suitable for cron jobs or remote servers.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in as needed:

```bash
# Required for testnet / live mode
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# Optional — avoids interactive password prompt on headless runs
MASTER_PASSWORD=your_master_password_here

# Optional — trade notifications
DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

> Never commit your `.env` file. It is listed in `.gitignore`.

---

## Security

- API keys are **never stored in plain text** — encrypted with Fernet (PBKDF2 + AES-128-CBC) under a master password; the encrypted store lives in `storage_dir` (`~/.binance_bot` by default)
- `saved_config.json` stores GUI session state (principal, fee). **Do not store API keys here** — use `.env` or the encrypted store
- `sim` and `testnet` modes never send orders to the exchange — safe for testing without risk
- For live trading, use **read + spot trading permissions only** — never enable withdrawals on the API key

---

## Notifications

Configure Discord or Telegram in the GUI (Notifications section) or via `.env`. Alerts fire on:
- BUY filled
- SELL filled (includes P&L)
- Stop-loss triggered
- Take-profit triggered

---

## File Structure

```
binance_bot/
├── bot.py                    # Headless entry point
├── gui/
│   ├── app.py                # GUI + terminal entry point
│   ├── terminal_ui.py        # Rich terminal dashboard
│   └── panels/
│       ├── settings_panel.py # Config, API keys, strategy sliders
│       ├── chart_panel.py    # Candlestick chart (OHLCV + indicators)
│       ├── scanner_panel.py  # Market scanner (regime-aware signals)
│       ├── positions_panel.py# Open positions + unrealized P&L + TP/SL levels
│       └── log_panel.py      # Scrolling trade log
├── core/
│   ├── config.py             # TradingConfig dataclass + load/save/validate
│   ├── exchange.py           # Binance REST API wrapper (no 4xx retries, fresh timestamp per retry)
│   ├── portfolio.py          # SQLite portfolio (positions, trades, balances) — sim+testnet tracked
│   ├── risk.py               # TP/SL/trailing/cooldown/circuit breaker/Kelly sizing
│   ├── strategies.py         # RegimeAwareStrategy + WinRateStrategy (legacy)
│   ├── indicators.py         # RSI, MACD, BB, ADX, Stoch, ATR, OBV, VWAP, Hurst, PE (pure functions)
│   ├── notifications.py      # Discord + Telegram webhooks (non-blocking)
│   └── backtester.py         # Backtest engine (not yet exposed in UI)
├── utils/
│   ├── logger.py             # Rotating file log (10 MB × 5) + LOG_QUEUE for GUI
│   └── security.py           # Fernet credential encryption
├── config.json               # GUI default config (testnet, BTC+ETH, 15m)
├── config_sim.json           # Simulator template (BTC+ETH+BNB+SOL, 15m)
├── config_testnet.json       # Testnet template (BTC+ETH, 15m)
├── config_live.json          # Live trading template (BTC+ETH, 15m, MARKET orders)
├── generate_manual.py        # Regenerates Binance_Bot_Operation_Manual.pdf (requires reportlab)
├── requirements.txt
└── .env.example
```

---

## Dependencies

```
requests>=2.31.0        # Binance REST API
python-dotenv>=1.0.0    # .env loading
numpy>=1.26.0           # Indicator calculations
matplotlib>=3.8.0       # GUI charts
cryptography>=42.0.0    # Credential encryption
scipy>=1.12.0           # Backtesting metrics
rich>=13.0.0            # Terminal UI
reportlab>=3.6.0        # PDF manual generation (generate_manual.py only)
```

---

## Contributing

Bug reports and pull requests are welcome. For major changes, open an issue first to discuss what you'd like to change.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Disclaimer

This software is for **educational and research purposes**. Cryptocurrency trading carries significant financial risk. Past performance in simulator or testnet mode does not guarantee future live results. Never trade with funds you cannot afford to lose entirely.
