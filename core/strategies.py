"""
Strategy classes. Each strategy is stateless per call.

Two strategies are available:
  - RegimeAwareStrategy ("regime") — recommended. Detects whether the market
    is trending or ranging, then applies the appropriate ruleset with multi-
    factor confluence requirements. Requires OHLCV data (highs/lows/volumes
    passed as kwargs).
  - WinRateStrategy ("winrate") — original threshold strategy, kept for
    backward compatibility.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

from core.indicators import (
    calc_win_chance,
    calc_rsi_wilder,
    calc_macd_current,
    calc_bollinger_current,
    calc_ma,
    calc_adx,
    calc_stochastic,
    calc_obv_slope,
    calc_volume_ratio,
    calc_rsi_divergence,
    calc_hurst_exponent,
    calc_permutation_entropy,
    calc_vwap,
)
from utils.logger import get_logger

log = get_logger("strategies")


class Signal(Enum):
    BUY  = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class SignalResult:
    signal: Signal
    reason: str
    confidence: float  # 0.0 – 1.0


class BaseStrategy:
    def __init__(self, params: dict):
        self.params = params

    def get_signal(self, symbol: str, closes: list[float], holding: float,
                   sentiment: float = 55.0, **kwargs) -> SignalResult:
        raise NotImplementedError

    @classmethod
    def get_default_params(cls) -> dict:
        return {}

    @classmethod
    def get_param_grid(cls) -> dict:
        """For optimizer: {"param_name": [v1, v2, ...]}"""
        return {}


# ── WinRate (legacy) ─────────────────────────────────────────────────────────

class WinRateStrategy(BaseStrategy):
    """Original win-chance threshold strategy. Kept for backward compatibility.
    BUY  when win_chance >= buy_win_thresh.
    SELL when win_chance <= sell_win_thresh.
    """

    @classmethod
    def get_default_params(cls) -> dict:
        return {"buy_win_thresh": 60, "sell_win_thresh": 35}

    @classmethod
    def get_param_grid(cls) -> dict:
        return {
            "buy_win_thresh":  [55, 60, 65, 70],
            "sell_win_thresh": [25, 30, 35, 40],
        }

    def get_signal(self, symbol: str, closes: list[float], holding: float,
                   sentiment: float = 55.0, **kwargs) -> SignalResult:
        buy_t  = kwargs.get("buy_win_thresh",  self.params.get("buy_win_thresh",  60))
        sell_t = kwargs.get("sell_win_thresh", self.params.get("sell_win_thresh", 35))
        win    = calc_win_chance(closes, sentiment)

        if holding <= 0:
            if win >= buy_t:
                return SignalResult(Signal.BUY,
                                    f"Win={win}% >= {buy_t}% — buy signal",
                                    round(win / 100, 2))
            return SignalResult(Signal.HOLD,
                                f"Win={win}% < {buy_t}% — below threshold", 0.0)
        else:
            if win <= sell_t:
                return SignalResult(Signal.SELL,
                                    f"Win={win}% <= {sell_t}% — sell signal",
                                    round(1.0 - win / 100, 2))
            return SignalResult(Signal.HOLD,
                                f"Win={win}% > {sell_t}% — holding", 0.0)


# ── RegimeAware (primary) ────────────────────────────────────────────────────

class RegimeAwareStrategy(BaseStrategy):
    """
    Multi-factor regime-aware strategy — designed for reliable win rate.

    The core insight: RSI mean-reversion works in sideways markets; trend-
    following works in trending markets.  Using the wrong ruleset in the wrong
    regime is the main cause of false signals in simple strategies.

    Regime detection
    ─────────────────
    ADX < 20  → RANGING   — use RSI/Stochastic mean-reversion + Bollinger
    ADX > 25  → TRENDING  — use MA alignment, DI direction, MACD momentum
    20–25     → TRANSITION — require +1 extra confluence factor before entering

    Entry requires CONFLUENCE — multiple independent factors must agree.
    A single indicator firing never triggers a trade.

    Buy factors (TRENDING)
    ──────────────────────
    1. +DI > -DI  (trend is pointing up)                         [required]
    2. Price above MA50  (still in the trend structure)
    3. MA20 > MA50  (medium-term uptrend confirmed)
    4. RSI 40–65  (healthy pullback — not overbought, not collapsed)
    5. MACD histogram > 0  (momentum confirming)
    6. OBV slope > 0  (volume flow agreeing with price)
    7. Volume ratio >= threshold  (activity confirms the move)
    8. No bearish divergence

    Buy factors (RANGING)
    ──────────────────────
    1. RSI < rsi_oversold  (oversold reading)
    2. Stoch %K < stoch_oversold AND %K > %D  (stochastic turning up)
    3. Price at or below lower Bollinger Band
    4. Bullish RSI divergence  (double weight — highest quality signal)
    5. High volume on a down bar  (capitulation / exhaustion)
    6. Fear & Greed < 25  (extreme fear = contrarian buy)

    Exit via strategy (supplement to RiskManager TP/SL/trailing)
    ─────────────────────────────────────────────────────────────
    The RiskManager handles hard exits (ATR-stop, fixed TP/SL, trailing stop).
    The strategy provides a "soft exit" when the trade thesis breaks down —
    e.g., a ranging trade that reached the upper band, or a trending trade where
    the trend has clearly reversed.  A strategy SELL does NOT fire unless
    bearish factors outnumber bullish factors AND meet the sell threshold.
    """

    TRENDING    = "TREND"
    RANGING     = "RANGE"
    TRANSITION  = "TRANS"

    @classmethod
    def get_default_params(cls) -> dict:
        return {
            # Regime thresholds
            "adx_trend":            25,    # ADX above this → trending
            "adx_range":            20,    # ADX below this → ranging
            # RSI
            "rsi_oversold":         38,    # ranging buy trigger
            "rsi_overbought":       65,    # ranging soft-sell trigger
            "rsi_trend_healthy_lo": 40,    # trending: RSI floor (healthy pullback)
            "rsi_trend_healthy_hi": 68,    # trending: RSI ceiling (not overbought)
            # Stochastic
            "stoch_oversold":       25,
            "stoch_overbought":     75,
            # Volume
            "volume_ratio_min":     1.15,  # minimum volume multiplier to confirm entry
            # Confluence thresholds
            "min_confluence":       3,     # bullish factors required for buy (trending)
            "min_confluence_range": 3,     # bullish factors required for buy (ranging)
            # Sell: bearish factors required for strategy sell
            "sell_confluence":      2,
        }

    @classmethod
    def get_param_grid(cls) -> dict:
        return {
            "adx_trend":            [22, 25, 28],
            "adx_range":            [18, 20, 22],
            "rsi_oversold":         [33, 36, 38, 40],
            "rsi_overbought":       [62, 65, 68],
            "min_confluence":       [3, 4],
            "min_confluence_range": [2, 3],
            "volume_ratio_min":     [1.0, 1.1, 1.2],
        }

    def get_signal(self, symbol: str, closes: list[float], holding: float,
                   sentiment: float = 55.0, **kwargs) -> SignalResult:

        # ── Pull OHLCV kwargs (falls back to closes when not provided) ──────
        highs   = kwargs.get("highs",   closes)
        lows    = kwargs.get("lows",    closes)
        volumes = kwargs.get("volumes", [1.0] * len(closes))

        if len(closes) < 50:
            return SignalResult(Signal.HOLD, "Insufficient data (<50 bars)", 0.0)

        # ── Merge params ──────────────────────────────────────────────────────
        p = {**self.get_default_params(), **self.params}

        # ── Compute indicators ────────────────────────────────────────────────
        price    = closes[-1]
        rsi      = calc_rsi_wilder(closes)
        macd_r   = calc_macd_current(closes)
        bb       = calc_bollinger_current(closes)
        adx_r    = calc_adx(highs, lows, closes)
        stoch    = calc_stochastic(highs, lows, closes)
        obv_sl   = calc_obv_slope(closes, volumes)
        vol_r    = calc_volume_ratio(volumes)
        ma20     = calc_ma(closes, 20)
        ma50     = calc_ma(closes, 50)
        diverge  = calc_rsi_divergence(closes)

        # ── Quantitative model indicators ─────────────────────────────────────
        # Hurst Exponent — confirms whether price is statistically trending or
        # mean-reverting, cross-checking the ADX regime label
        hurst = calc_hurst_exponent(closes)

        # Permutation Entropy — measures market randomness/noise
        # High PE (>0.85) = chaotic, raise confluence threshold by +1
        pe = calc_permutation_entropy(closes)

        # VWAP — institutional price benchmark; directional bias indicator
        vwap_val = calc_vwap(highs, lows, closes, volumes)

        # Multi-Timeframe confirmation (passed in from bot.py if configured)
        mtf_bullish = kwargs.get("mtf_bullish", None)  # True/False/None

        # ── Regime ────────────────────────────────────────────────────────────
        if adx_r.adx >= p["adx_trend"]:
            regime = self.TRENDING
        elif adx_r.adx <= p["adx_range"]:
            regime = self.RANGING
        else:
            regime = self.TRANSITION

        # ── Factor collection ─────────────────────────────────────────────────
        bullish: list[str] = []
        bearish: list[str] = []

        if regime == self.TRENDING:
            # Hard overbought guard — TREND regime can sustain high RSI, but
            # entering when RSI > 72 or Stoch > 85 means chasing a move that
            # is statistically likely to mean-revert before hitting TP.
            # If already holding, let RiskManager handle the exit; don't block.
            if holding <= 0 and (rsi > 72 or stoch.k > 85):
                return SignalResult(
                    Signal.HOLD,
                    (f"[{regime} ADX={adx_r.adx:.0f} H={hurst:.2f} PE={pe:.2f}] "
                     f"RSI={rsi:.0f} Stoch={stoch.k:.0f} "
                     f"— overbought entry blocked in TREND regime"),
                    0.0,
                )

            # ── Trending: trade WITH the trend, don't fight it ─────────────
            # Direction (required signal — DI+ must lead)
            if adx_r.plus_di > adx_r.minus_di:
                bullish.append(f"+DI{adx_r.plus_di:.0f}>{adx_r.minus_di:.0f}")
            else:
                bearish.append(f"-DI{adx_r.minus_di:.0f}>+DI{adx_r.plus_di:.0f}")

            # Price vs MA50 (trend structure)
            if price > ma50:
                bullish.append("P>MA50")
            else:
                bearish.append("P<MA50")

            # MA alignment (intermediate trend)
            if ma20 > ma50:
                bullish.append("MA20>MA50")
            else:
                bearish.append("MA20<MA50")

            # RSI — healthy pullback zone (not overbought, not broken)
            if p["rsi_trend_healthy_lo"] <= rsi <= p["rsi_trend_healthy_hi"]:
                bullish.append(f"RSI{rsi:.0f}healthy")
            elif rsi > 72:
                bearish.append(f"RSI{rsi:.0f}OB")
            elif rsi < 35:
                bearish.append(f"RSI{rsi:.0f}collapsed")

            # MACD momentum
            if macd_r.histogram > 0:
                bullish.append("MACD+")
            else:
                bearish.append("MACD-")

            # Volume flow (OBV)
            if obv_sl > 0.05:
                bullish.append("OBV↑")
            elif obv_sl < -0.05:
                bearish.append("OBV↓")

            # Volume activity
            if vol_r >= p["volume_ratio_min"]:
                bullish.append(f"Vol×{vol_r:.1f}")

            # Sentiment — only flag extreme greed as a warning in trending markets.
            # FearBuy removed: F&G panic during a trend often signals a reversal,
            # not a dip. The other 7 trend factors are sufficient for entry.
            if sentiment > 82:
                bearish.append("GreedSell")

            # Divergence — bearish divergence in a trend is a high-quality exit
            if diverge == -1:
                bearish.append("BearDiv")
                bearish.append("BearDiv×2")  # double weight — strong signal
            elif diverge == 1:
                bullish.append("BullDiv")

            # VWAP — institutional benchmark; price above = net buy pressure
            if price > vwap_val:
                bullish.append("AboveVWAP")
            else:
                bearish.append("BelowVWAP")

            # Hurst: only flag when very clearly anti-persistent (H < 0.38)
            # Avoids false negatives from the noisy 0.4–0.5 zone
            if hurst < 0.38:
                bearish.append(f"Hurst{hurst:.2f}MR")

            buy_conf  = p["min_confluence"]
            sell_conf = p["sell_confluence"]

        elif regime == self.RANGING:
            # ── Ranging: mean-reversion — buy oversold, sell overbought ────
            bb_range = bb.upper - bb.lower
            bb_pct   = ((price - bb.lower) / bb_range) if bb_range > 0 else 0.5

            # RSI oversold/overbought
            if rsi < p["rsi_oversold"]:
                bullish.append(f"RSI{rsi:.0f}OS")
            elif rsi > p["rsi_overbought"]:
                bearish.append(f"RSI{rsi:.0f}OB")

            # Stochastic turning up from oversold
            if stoch.k < p["stoch_oversold"] and stoch.k > stoch.d:
                bullish.append(f"Stoch{stoch.k:.0f}↑")
            elif stoch.k > p["stoch_overbought"] and stoch.k < stoch.d:
                bearish.append(f"Stoch{stoch.k:.0f}↓")

            # Bollinger Band position
            if bb_pct < 0.12:
                bullish.append("AtLowerBB")
            elif bb_pct > 0.88:
                bearish.append("AtUpperBB")

            # Divergence — highest quality mean-reversion signal
            if diverge == 1:
                bullish.append("BullDiv")
                bullish.append("BullDiv×2")  # double weight
            elif diverge == -1:
                bearish.append("BearDiv")
                bearish.append("BearDiv×2")

            # Volume spike on a down bar = capitulation / exhaustion
            last_move_down = len(closes) >= 2 and closes[-1] < closes[-2]
            if vol_r > 1.5 and last_move_down and rsi < 45:
                bullish.append(f"CapitVol×{vol_r:.1f}")

            # Fear & Greed extremes (contrarian signals)
            if sentiment < 25:
                bullish.append("ExtremeFear")
            elif sentiment > 78:
                bearish.append("ExtremeGreed")

            # OBV as secondary confirmation
            if obv_sl > 0.08:
                bullish.append("OBV↑")
            elif obv_sl < -0.08:
                bearish.append("OBV↓")

            # VWAP — in ranging markets, price below VWAP is a confirmatory buy signal
            # (buying below fair value, targeting mean-reversion back toward VWAP)
            # Not used as a bearish gate here — RSI/BB/Stoch already handle overbought
            if price < vwap_val:
                bullish.append("BelowVWAP_MR")

            # Hurst: only flag when very clearly persistent (H > 0.65)
            # This means the "range" may actually be a breakout — avoid fading it
            if hurst > 0.65:
                bearish.append(f"Hurst{hurst:.2f}TR")

            buy_conf  = p["min_confluence_range"]
            sell_conf = p["sell_confluence"]

        else:
            # ── Transitional (ADX 20–25): mixed signal set ─────────────────
            # Widen the factor pool so there are enough independent signals to
            # evaluate.  Keep each factor separate — don't require two conditions
            # to fire simultaneously (that collapses the effective pool).

            # Hard overbought guard — TRANS regime is uncertain; entering on
            # overbought RSI/Stoch is exactly the wrong time (chasing the top).
            # If already holding, let RiskManager handle the exit; don't block.
            if holding <= 0 and (rsi > 65 or stoch.k > 80):
                return SignalResult(
                    Signal.HOLD,
                    (f"[TRANS ADX={adx_r.adx:.0f}] RSI={rsi:.0f} Stoch={stoch.k:.0f} "
                     f"— overbought entry blocked in TRANS regime"),
                    0.0,
                )

            # Directional indicators — split so each fires independently
            if adx_r.plus_di > adx_r.minus_di:
                bullish.append(f"+DI{adx_r.plus_di:.0f}>{adx_r.minus_di:.0f}")
            else:
                bearish.append(f"-DI{adx_r.minus_di:.0f}>+DI{adx_r.plus_di:.0f}")

            if ma20 > ma50:
                bullish.append("MA20>MA50")
            else:
                bearish.append("MA20<MA50")

            if price > ma50:
                bullish.append("P>MA50")
            else:
                bearish.append("P<MA50")

            # RSI — healthy pullback zone or oversold
            if rsi < 48:
                bullish.append(f"RSI{rsi:.0f}low")
            elif rsi > 65:
                bearish.append(f"RSI{rsi:.0f}OB")

            # Stochastic — in transition, confirm momentum direction
            if stoch.k < 35 and stoch.k > stoch.d:
                bullish.append(f"Stoch{stoch.k:.0f}↑")
            elif stoch.k > 70 and stoch.k < stoch.d:
                bearish.append(f"Stoch{stoch.k:.0f}↓")

            # MACD momentum
            if macd_r.histogram > 0:
                bullish.append("MACD+")
            else:
                bearish.append("MACD-")

            # Volume flow
            if obv_sl > 0.08:
                bullish.append("OBV↑")
            elif obv_sl < -0.08:
                bearish.append("OBV↓")

            # VWAP directional bias
            if price > vwap_val:
                bullish.append("AboveVWAP")
            else:
                bearish.append("BelowVWAP")

            # Divergence
            if diverge == 1:
                bullish.append("BullDiv")
            elif diverge == -1:
                bearish.append("BearDiv")

            buy_conf  = p["min_confluence"]      # same as TRENDING (3)
            sell_conf = p["sell_confluence"]

        # ── Decision ──────────────────────────────────────────────────────────
        nb    = len(bullish)
        nbe   = len(bearish)
        total = max(nb + nbe, 1)
        conf  = round(nb / total, 2)

        header = (f"[{regime} ADX={adx_r.adx:.0f} H={hurst:.2f} PE={pe:.2f}] "
                  f"RSI={rsi:.0f} Stoch={stoch.k:.0f} VWAP={'↑' if price > vwap_val else '↓'} "
                  f"Vol×{vol_r:.1f} OBV={obv_sl:+.2f} "
                  f"bull={nb} bear={nbe}")

        if holding <= 0:
            # MTF veto: higher-timeframe trend is bearish → block long entries
            if mtf_bullish is not None and not mtf_bullish:
                return SignalResult(
                    Signal.HOLD,
                    f"{header} | MTF bearish — long entries blocked",
                    0.0,
                )
            # Entry: need enough bullish factors AND bullish must outnumber bearish
            if nb >= buy_conf and nb > nbe:
                factors = ", ".join(bullish[:5])
                return SignalResult(
                    Signal.BUY,
                    f"{header} | {factors}",
                    conf,
                )
            return SignalResult(Signal.HOLD, header, 0.0)

        else:
            # Exit: bearish factors must dominate AND meet sell threshold.
            # Require margin ≥ 2 (nbe > nb + 1) to avoid whipsaw exits caused by
            # a single noisy factor (e.g., VWAP) flipping between scans.
            if nbe >= sell_conf and nbe > nb + 1:
                factors = ", ".join(bearish[:5])
                return SignalResult(
                    Signal.SELL,
                    f"{header} | {factors}",
                    round(1.0 - conf, 2),
                )
            return SignalResult(Signal.HOLD, header, 0.0)


# ── Factory ──────────────────────────────────────────────────────────────────

class StrategyFactory:
    _REGISTRY: dict[str, type[BaseStrategy]] = {
        "regime":  RegimeAwareStrategy,
        "winrate": WinRateStrategy,
    }

    @classmethod
    def get_strategy(cls, name: str = "regime", params: dict | None = None) -> BaseStrategy:
        klass = cls._REGISTRY.get(name)
        if klass is None:
            log.warning(f"Unknown strategy '{name}', using RegimeAwareStrategy")
            klass = RegimeAwareStrategy
        merged = {**klass.get_default_params(), **(params or {})}
        return klass(merged)
