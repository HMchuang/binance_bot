"""
All technical indicators as pure, stateless functions.
No side effects, no logging, no external calls.
Takes lists or numpy arrays; returns scalars or lists.
"""
from __future__ import annotations
from dataclasses import dataclass

import numpy as np


# ── Result dataclasses ──────────────────────────────────────────────────────

@dataclass
class MACDResult:
    macd: float
    signal: float
    histogram: float


@dataclass
class BBResult:
    upper: float
    middle: float
    lower: float


@dataclass
class MACDSeries:
    macd: list
    signal: list
    histogram: list


@dataclass
class BBSeries:
    upper: list
    middle: list
    lower: list


@dataclass
class ADXResult:
    adx: float       # trend strength: <20=ranging, >25=trending
    plus_di: float   # bullish directional indicator
    minus_di: float  # bearish directional indicator


@dataclass
class StochResult:
    k: float   # %K — fast line
    d: float   # %D — signal line (SMA of %K)


# ── RSI (simple — kept for backward compatibility) ───────────────────────────

def calc_rsi(closes: list[float], period: int = 14) -> float:
    """Simple RSI using average gain/loss over the last `period` bars."""
    if len(closes) < period + 1:
        return 50.0
    arr = np.array(closes, dtype=float)
    d   = np.diff(arr)
    g   = np.where(d > 0, d, 0.0)
    l   = np.where(d < 0, -d, 0.0)
    ag  = np.mean(g[-period:])
    al  = np.mean(l[-period:])
    if al == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + ag / al), 2)


def calc_rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    """RSI for every position in the series (None where not enough data)."""
    result: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return result
    arr = np.array(closes, dtype=float)
    for i in range(period, len(closes)):
        sl = arr[i - period: i + 1]
        d  = np.diff(sl)
        g  = np.where(d > 0, d, 0.0)
        l  = np.where(d < 0, -d, 0.0)
        ag = np.mean(g)
        al = np.mean(l)
        result[i] = 100.0 if al == 0 else round(100.0 - 100.0 / (1.0 + ag / al), 2)
    return result


# ── RSI (Wilder's smoothed — more stable) ────────────────────────────────────

def calc_rsi_wilder(closes: list[float], period: int = 14) -> float:
    """
    Wilder's smoothed RSI.
    Seeds from a simple-average for the first period, then uses the
    exponential-style 1/period smoothing Wilder originally specified.
    More stable than the simple-average version — fewer whipsaws.
    """
    if len(closes) < period + 1:
        return 50.0
    arr    = np.array(closes, dtype=float)
    deltas = np.diff(arr)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = float(np.mean(gains[:period]))
    avg_l  = float(np.mean(losses[:period]))
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + avg_g / avg_l), 2)


def calc_rsi_wilder_series(closes: list[float], period: int = 14) -> list[float | None]:
    """
    Wilder's RSI for the full price series — O(n).
    Uses rolling Wilder smoothing after seeding with a simple average.
    """
    n = len(closes)
    result: list[float | None] = [None] * n
    if n < period + 1:
        return result
    arr    = np.array(closes, dtype=float)
    deltas = np.diff(arr)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = float(np.mean(gains[:period]))
    avg_l  = float(np.mean(losses[:period]))
    result[period] = 100.0 if avg_l == 0 else round(100.0 - 100.0 / (1.0 + avg_g / avg_l), 2)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        val   = 100.0 if avg_l == 0 else round(100.0 - 100.0 / (1.0 + avg_g / avg_l), 2)
        result[i + 1] = val
    return result


# ── EMA ─────────────────────────────────────────────────────────────────────

def calc_ema_series(data: list[float | None], period: int) -> list[float | None]:
    """Exponential moving average series. None values are skipped."""
    k      = 2.0 / (period + 1)
    result: list[float | None] = []
    for v in data:
        if v is None:
            result.append(None)
        elif not result or result[-1] is None:
            result.append(float(v))
        else:
            result.append(float(v) * k + result[-1] * (1.0 - k))
    return result


def _ema_numpy(arr: np.ndarray, period: int) -> np.ndarray:
    """Fast EMA on a plain numpy array (no None values)."""
    k = 2.0 / (period + 1)
    result = np.empty(len(arr), dtype=float)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = arr[i] * k + result[i - 1] * (1.0 - k)
    return result


# ── MACD ────────────────────────────────────────────────────────────────────

def calc_macd_current(closes: list[float], fast: int = 12, slow: int = 26, signal_p: int = 9) -> MACDResult:
    """Current MACD values (last bar)."""
    if len(closes) < slow:
        return MACDResult(0.0, 0.0, 0.0)
    arr  = np.array(closes, dtype=float)
    e12  = _ema_numpy(arr, fast)
    e26  = _ema_numpy(arr, slow)
    macd = e12 - e26
    sig  = _ema_numpy(macd, signal_p)
    hist = macd - sig
    return MACDResult(
        macd=round(float(macd[-1]), 6),
        signal=round(float(sig[-1]), 6),
        histogram=round(float(hist[-1]), 6),
    )


def calc_macd_series(closes: list[float], fast: int = 12, slow: int = 26, signal_p: int = 9) -> MACDSeries:
    """Full MACD series (lists with None where not enough data)."""
    e12_s  = calc_ema_series(closes, fast)
    e26_s  = calc_ema_series(closes, slow)
    macd_s = [a - b if a is not None and b is not None else None
              for a, b in zip(e12_s, e26_s)]
    sig_s  = calc_ema_series(macd_s, signal_p)
    hist_s = [a - b if a is not None and b is not None else None
              for a, b in zip(macd_s, sig_s)]
    return MACDSeries(macd=macd_s, signal=sig_s, histogram=hist_s)


# ── Bollinger Bands ──────────────────────────────────────────────────────────

def calc_bollinger_current(closes: list[float], n: int = 20, mult: float = 2.0) -> BBResult:
    """Current Bollinger Band values."""
    if len(closes) < n:
        p = closes[-1] if closes else 0.0
        return BBResult(upper=p, middle=p, lower=p)
    w   = np.array(closes[-n:], dtype=float)
    mid = float(np.mean(w))
    std = float(np.std(w))
    return BBResult(
        upper=round(mid + mult * std, 4),
        middle=round(mid, 4),
        lower=round(mid - mult * std, 4),
    )


def calc_bollinger_series(closes: list[float], n: int = 20, mult: float = 2.0) -> BBSeries:
    """Full Bollinger Band series."""
    upper: list[float | None] = []
    middle: list[float | None] = []
    lower: list[float | None] = []
    for i in range(len(closes)):
        if i < n - 1:
            upper.append(None); middle.append(None); lower.append(None)
        else:
            w   = np.array(closes[i - n + 1: i + 1], dtype=float)
            mid = float(np.mean(w))
            std = float(np.std(w))
            upper.append(round(mid + mult * std, 4))
            middle.append(round(mid, 4))
            lower.append(round(mid - mult * std, 4))
    return BBSeries(upper=upper, middle=middle, lower=lower)


# ── Simple MA ───────────────────────────────────────────────────────────────

def calc_ma(closes: list[float], n: int) -> float:
    if len(closes) < n:
        return float(closes[-1]) if closes else 0.0
    return round(float(np.mean(closes[-n:])), 4)


def calc_ma_series(closes: list[float], n: int) -> list[float | None]:
    return [
        None if i < n - 1
        else round(float(np.mean(closes[i - n + 1: i + 1])), 4)
        for i in range(len(closes))
    ]


# ── ATR ─────────────────────────────────────────────────────────────────────

def calc_atr(highs: list[float], lows: list[float], closes: list[float],
             period: int = 14) -> float:
    """Average True Range — used for trailing stop and dynamic TP/SL sizing."""
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)
    return float(np.mean(trs[-period:]))


# ── ADX ─────────────────────────────────────────────────────────────────────

def calc_adx(highs: list[float], lows: list[float], closes: list[float],
             period: int = 14) -> ADXResult:
    """
    Average Directional Index with +DI and -DI.

    adx < 20   → ranging market  (RSI mean-reversion works best)
    adx > 25   → trending market (trend-following works best)
    plus_di > minus_di → bullish trend direction
    """
    n = len(closes)
    if n < period * 2:
        return ADXResult(adx=0.0, plus_di=0.0, minus_di=0.0)
    h = np.array(highs,  dtype=float)
    l = np.array(lows,   dtype=float)
    c = np.array(closes, dtype=float)

    tr      = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        tr[i]       = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        up          = h[i] - h[i - 1]
        down        = l[i - 1] - l[i]
        if up > down and up > 0:
            plus_dm[i] = up
        if down > up and down > 0:
            minus_dm[i] = down

    # Wilder smoothing: seed = sum of first `period` values, then rolling
    def _wilder(arr: np.ndarray) -> np.ndarray:
        s = np.zeros(n)
        s[period] = float(np.sum(arr[1: period + 1]))
        for i in range(period + 1, n):
            s[i] = s[i - 1] - s[i - 1] / period + arr[i]
        return s

    smooth_tr  = _wilder(tr)
    smooth_pdm = _wilder(plus_dm)
    smooth_mdm = _wilder(minus_dm)

    _z = np.zeros(n)
    pdi = np.divide(100.0 * smooth_pdm, smooth_tr, out=_z.copy(), where=smooth_tr > 0)
    mdi = np.divide(100.0 * smooth_mdm, smooth_tr, out=_z.copy(), where=smooth_tr > 0)

    dxsum  = pdi + mdi
    dx     = np.divide(100.0 * np.abs(pdi - mdi), dxsum, out=_z.copy(), where=dxsum > 0)

    # ADX = Wilder average of DX, seeded at bar 2*period-1
    adx_arr = np.zeros(n)
    start   = period * 2 - 1
    if start < n:
        adx_arr[start] = float(np.mean(dx[period: period * 2]))
        for i in range(start + 1, n):
            adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx[i]) / period

    return ADXResult(
        adx=round(float(adx_arr[-1]), 2),
        plus_di=round(float(pdi[-1]), 2),
        minus_di=round(float(mdi[-1]), 2),
    )


# ── Stochastic Oscillator ───────────────────────────────────────────────────

def calc_stochastic(highs: list[float], lows: list[float], closes: list[float],
                    k_period: int = 14, d_period: int = 3) -> StochResult:
    """
    Stochastic oscillator %K and %D.
    %K < 20 → oversold, %K > 80 → overbought.
    %K crossing above %D from below → bullish turn.
    """
    if len(closes) < k_period + d_period:
        return StochResult(k=50.0, d=50.0)
    h = np.array(highs,  dtype=float)
    l = np.array(lows,   dtype=float)
    c = np.array(closes, dtype=float)

    k_vals: list[float] = []
    for i in range(k_period - 1, len(c)):
        lo = float(np.min(l[i - k_period + 1: i + 1]))
        hi = float(np.max(h[i - k_period + 1: i + 1]))
        k_vals.append(50.0 if hi == lo else 100.0 * (c[i] - lo) / (hi - lo))

    k = k_vals[-1]
    d = float(np.mean(k_vals[-d_period:])) if len(k_vals) >= d_period else k
    return StochResult(k=round(k, 2), d=round(d, 2))


# ── OBV slope ────────────────────────────────────────────────────────────────

def calc_obv_slope(closes: list[float], volumes: list[float],
                   ema_period: int = 14) -> float:
    """
    On-Balance Volume slope relative to its own EMA, normalised to [-1, +1].
    Positive  → OBV above its EMA (volume flow is bullish — smart money buying).
    Negative  → OBV below its EMA (volume flow is bearish — distribution).
    """
    if len(closes) < ema_period + 2 or len(volumes) < ema_period + 2:
        return 0.0
    c = np.array(closes,  dtype=float)
    v = np.array(volumes, dtype=float)

    obv = np.zeros(len(c))
    for i in range(1, len(c)):
        if c[i] > c[i - 1]:
            obv[i] = obv[i - 1] + v[i]
        elif c[i] < c[i - 1]:
            obv[i] = obv[i - 1] - v[i]
        else:
            obv[i] = obv[i - 1]

    obv_ema = _ema_numpy(obv, ema_period)
    diff    = obv[-1] - obv_ema[-1]
    scale   = max(abs(obv_ema[-1]), 1.0)
    return float(np.clip(diff / scale, -1.0, 1.0))


# ── Volume ratio ─────────────────────────────────────────────────────────────

def calc_volume_ratio(volumes: list[float], period: int = 20) -> float:
    """
    Current bar volume relative to the N-period average (excluding current bar).
    > 1.5 → significant volume activity (confirms breakouts / capitulations).
    """
    if len(volumes) < period + 1:
        return 1.0
    avg = float(np.mean(volumes[-period - 1: -1]))
    if avg <= 0:
        return 1.0
    return round(float(volumes[-1]) / avg, 2)


# ── RSI divergence ───────────────────────────────────────────────────────────

def calc_rsi_divergence(closes: list[float], lookback: int = 30) -> int:
    """
    Detects regular RSI divergence over the last `lookback` candles.

    Returns:
      +1  bullish divergence — price lower low while RSI higher low.
           Signals hidden buying demand; often precedes reversals from oversold.
      -1  bearish divergence — price higher high while RSI lower high.
           Signals hidden selling pressure; often precedes reversals from overbought.
       0  no clear divergence.
    """
    n = len(closes)
    if n < lookback + 15:
        return 0

    rsi_s = calc_rsi_wilder_series(closes, 14)

    # Align price and RSI windows, filtering out leading Nones
    pairs = [
        (closes[i], rsi_s[i])
        for i in range(max(0, n - lookback), n)
        if rsi_s[i] is not None
    ]
    if len(pairs) < lookback // 2:
        return 0

    mid = len(pairs) // 2
    p_early, r_early = [x[0] for x in pairs[:mid]], [x[1] for x in pairs[:mid]]
    p_late,  r_late  = [x[0] for x in pairs[mid:]], [x[1] for x in pairs[mid:]]

    # Bullish: price makes lower low while RSI makes higher low
    if min(p_late) < min(p_early) * 0.998 and min(r_late) > min(r_early) + 3.0:
        return 1

    # Bearish: price makes higher high while RSI makes lower high
    if max(p_late) > max(p_early) * 1.002 and max(r_late) < max(r_early) - 3.0:
        return -1

    return 0


# ── Hurst Exponent (R/S Analysis) ────────────────────────────────────────────

def calc_hurst_exponent(closes: list[float], min_window: int = 8) -> float:
    """
    Hurst Exponent via Rescaled Range (R/S) analysis (Hurst 1951).

    H > 0.55 → persistent/trending series — trend-following signals are reliable.
    H < 0.45 → anti-persistent/mean-reverting series — RSI/BB reversals work best.
    H ≈ 0.5  → random walk — low signal reliability, raise entry bar.

    Cross-checks the ADX regime label against the statistical structure of
    log-returns so the strategy avoids misclassifying noisy chops as trends.
    Returns 0.5 (random walk) when insufficient data.
    """
    n = len(closes)
    if n < min_window * 4:
        return 0.5

    arr     = np.array(closes, dtype=float)
    log_ret = np.log(arr[1:] / np.maximum(arr[:-1], 1e-10))
    m       = len(log_ret)
    max_win = m // 2

    # Geometric progression of window sizes
    windows: list[int] = []
    w = min_window
    while w <= max_win:
        windows.append(w)
        w = max(w + 1, int(w * 1.6))

    if len(windows) < 3:
        return 0.5

    log_n_vals: list[float] = []
    log_rs_vals: list[float] = []
    for w in windows:
        rs_sub: list[float] = []
        for start in range(0, m - w + 1, w):
            sub  = log_ret[start: start + w]
            mean = np.mean(sub)
            dev  = np.cumsum(sub - mean)
            r    = float(np.max(dev) - np.min(dev))
            s    = float(np.std(sub, ddof=1))
            if s > 0:
                rs_sub.append(r / s)
        if rs_sub:
            log_n_vals.append(np.log(w))
            log_rs_vals.append(np.log(float(np.mean(rs_sub))))

    if len(log_n_vals) < 3:
        return 0.5

    h = float(np.polyfit(log_n_vals, log_rs_vals, 1)[0])
    return round(float(np.clip(h, 0.0, 1.0)), 3)


# ── Permutation Entropy (Bandt & Pompe 2002) ──────────────────────────────────

def calc_permutation_entropy(closes: list[float], order: int = 3) -> float:
    """
    Permutation Entropy (Bandt & Pompe 2002).

    Measures the complexity (randomness) of a price series via ordinal patterns.
    0.0 = perfectly predictable  →  high PE = more reliable signals.
    1.0 = completely random      →  raise entry bar, reduce position size.

    PE < 0.70 → structured market, indicators are reliable.
    PE > 0.85 → high noise, require +1 extra confluence factor.
    """
    import math as _math
    from collections import Counter

    n = len(closes)
    if n < order + 10:
        return 1.0  # insufficient data — assume random

    arr     = np.array(closes, dtype=float)
    counter: Counter = Counter()
    for i in range(n - order + 1):
        pattern = tuple(np.argsort(arr[i: i + order], kind="stable"))
        counter[pattern] += 1

    total = sum(counter.values())
    probs = [c / total for c in counter.values()]
    max_ent = _math.log2(_math.factorial(order))
    entropy = -sum(p * _math.log2(p) for p in probs if p > 0)

    return round(entropy / max_ent if max_ent > 0 else 1.0, 3)


# ── VWAP ─────────────────────────────────────────────────────────────────────

def calc_vwap(highs: list[float], lows: list[float],
              closes: list[float], volumes: list[float]) -> float:
    """
    Volume Weighted Average Price (VWAP).

    Institutional traders benchmark order quality against VWAP.  Sustained
    trading above VWAP signals net buy pressure; below signals sell pressure.

    Uses typical price (H+L+C)/3 × volume, summed over the full input series.
    Returns last close when data is unavailable.
    """
    if not closes or not volumes:
        return closes[-1] if closes else 0.0
    typical = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]
    total_vol = sum(volumes)
    if total_vol <= 0:
        return closes[-1]
    return round(sum(t * v for t, v in zip(typical, volumes)) / total_vol, 6)


# ── Multi-Timeframe trend filter ──────────────────────────────────────────────

def is_mtf_bullish(higher_tf_closes: list[float]) -> bool:
    """
    Multi-Timeframe (MTF) trend filter.

    Checks whether the higher-timeframe trend is bullish before allowing
    long entries on a lower timeframe.  This prevents buying a local oversold
    bounce while the bigger picture trend is clearly down.

    Returns True (bullish / permissive) when:
      • MA20 > MA50 on the higher TF  (medium-term uptrend confirmed)
      • price > MA50 on the higher TF  (still above trend structure)

    Returns True when data is insufficient (warm-up guard — avoid blocking).
    """
    if len(higher_tf_closes) < 50:
        return True
    price = higher_tf_closes[-1]
    ma20  = calc_ma(higher_tf_closes, 20)
    ma50  = calc_ma(higher_tf_closes, 50)
    return bool(ma20 > ma50 and price > ma50)


# ── Win Chance score (legacy — used by WinRateStrategy) ─────────────────────

def calc_win_chance(closes: list[float], sentiment: float = 55.0) -> int:
    """
    Composite score 0-100 estimating the probability of a profitable trade.
    Uses Wilder RSI for improved stability vs the original simple-average version.

    All components are continuous so the score reacts to every price tick:

      Base                                50
      RSI (continuous, ±12):
        RSI=50 →  0,  RSI=20 → +12,  RSI=80 → -12
      BB position (continuous, ±10):
        price at lower band → +10,  at middle → 0,  at upper → -10
      MACD hist direction (binary ±5):
        histogram > 0 → +5,  histogram < 0 → -5
      MA trend (binary ±7):
        MA20 > MA50 → +7,  MA20 < MA50 → -7
      Sentiment (continuous, ±8):
        sentiment=100 → +8,  sentiment=50 → 0,  sentiment=0 → -8

    Clamped to [15, 90].
    """
    if len(closes) < 20:
        return 50
    rsi    = calc_rsi_wilder(closes)       # improved: Wilder vs simple-average
    macd_r = calc_macd_current(closes)
    bb_r   = calc_bollinger_current(closes)
    ma20   = calc_ma(closes, 20)
    ma50   = calc_ma(closes, 50)
    price  = closes[-1]
    score  = 50.0

    score += (50.0 - rsi) / 50.0 * 12.0

    bb_range = bb_r.upper - bb_r.lower
    if bb_range > 0:
        bb_pct = (price - bb_r.middle) / (bb_range / 2.0)
        bb_pct = max(-2.0, min(2.0, bb_pct))
        score += -bb_pct * 8.0

    score += 5.0 if macd_r.histogram > 0 else -5.0
    score += 7.0 if ma20 > ma50 else -7.0
    score += -(sentiment - 50.0) / 50.0 * 8.0

    return max(15, min(90, round(score)))
