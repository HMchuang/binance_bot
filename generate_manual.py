#!/usr/bin/env python3
"""
Generates Binance_Bot_Operation_Manual.pdf — v4.0
Run from the project root: python3 generate_manual.py
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY

# ── Palette ───────────────────────────────────────────────────────────────────
NAVY    = colors.HexColor("#0f1b2d")
BLUE    = colors.HexColor("#1d4ed8")
LBLUE   = colors.HexColor("#2563eb")
TEAL    = colors.HexColor("#0891b2")
RED     = colors.HexColor("#dc2626")
ORANGE  = colors.HexColor("#ea580c")
GREEN   = colors.HexColor("#16a34a")
YELLOW  = colors.HexColor("#d97706")
LGRAY   = colors.HexColor("#f1f5f9")
MGRAY   = colors.HexColor("#cbd5e1")
DGRAY   = colors.HexColor("#334155")
BLACK   = colors.HexColor("#0f172a")
WHITE   = colors.white

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

# ── Styles ────────────────────────────────────────────────────────────────────
base = getSampleStyleSheet()

def _s(name, **kw):
    return ParagraphStyle(name, **kw)

H1 = _s("H1", fontName="Helvetica-Bold", fontSize=16, textColor=BLUE,
         spaceAfter=4, spaceBefore=14, leading=20)
H2 = _s("H2", fontName="Helvetica-Bold", fontSize=12, textColor=DGRAY,
         spaceAfter=3, spaceBefore=10, leading=15)
BODY = _s("BODY", fontName="Helvetica", fontSize=9.5, textColor=BLACK,
          spaceAfter=4, leading=14, alignment=TA_JUSTIFY)
BODY_L = _s("BODY_L", fontName="Helvetica", fontSize=9.5, textColor=BLACK,
            spaceAfter=3, leading=14, alignment=TA_LEFT)
SMALL = _s("SMALL", fontName="Helvetica", fontSize=8, textColor=DGRAY,
           spaceAfter=3, leading=11, alignment=TA_LEFT)
SMALL_I = _s("SMALL_I", fontName="Helvetica-Oblique", fontSize=8,
             textColor=DGRAY, spaceAfter=3, leading=11)
CODE = _s("CODE", fontName="Courier", fontSize=8.5, textColor=BLACK,
          backColor=LGRAY, spaceAfter=4, leading=12,
          leftIndent=8, rightIndent=8, spaceBefore=2)
WARN = _s("WARN", fontName="Helvetica-Bold", fontSize=9, textColor=RED,
          spaceAfter=4, leading=13, leftIndent=6)
NOTE = _s("NOTE", fontName="Helvetica-Oblique", fontSize=8.2, textColor=DGRAY,
          spaceAfter=4, leading=12, leftIndent=6)
BULLET = _s("BULLET", fontName="Helvetica", fontSize=9.5, textColor=BLACK,
            spaceAfter=2, leading=14, leftIndent=14, firstLineIndent=-10)
COVER_TITLE = _s("CT", fontName="Helvetica-Bold", fontSize=32, textColor=WHITE,
                 alignment=TA_CENTER, leading=38)
COVER_SUB   = _s("CS", fontName="Helvetica", fontSize=13, textColor=MGRAY,
                 alignment=TA_CENTER, leading=18)

# ── Table helpers ─────────────────────────────────────────────────────────────
def _tbl(data, col_widths, style_extra=None):
    ts = TableStyle([
        ("BACKGROUND",  (0,0), (-1,0), NAVY),
        ("TEXTCOLOR",   (0,0), (-1,0), WHITE),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("TEXTCOLOR",   (0,1), (-1,-1), BLACK),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, LGRAY]),
        ("GRID",        (0,0), (-1,-1), 0.4, MGRAY),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",  (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0),(-1,-1), 4),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING",(0,0), (-1,-1), 5),
    ])
    if style_extra:
        for s in style_extra:
            ts.add(*s)
    return Table(data, colWidths=col_widths, style=ts, repeatRows=1)

def b(txt):  return f"<b>{txt}</b>"
def i(txt):  return f"<i>{txt}</i>"
def c(txt, col="#1d4ed8"): return f'<font color="{col}">{txt}</font>'
def bullet(txt): return Paragraph(f"• {txt}", BULLET)

def h1(txt): return Paragraph(txt, H1)
def h2(txt): return Paragraph(txt, H2)
def body(txt): return Paragraph(txt, BODY)
def body_l(txt): return Paragraph(txt, BODY_L)
def small(txt): return Paragraph(txt, SMALL)
def small_i(txt): return Paragraph(txt, SMALL_I)
def code(txt): return Paragraph(txt, CODE)
def warn(txt): return Paragraph(f"⚠ {txt}", WARN)
def note(txt): return Paragraph(f"{i('Note:')} {txt}", NOTE)
def sp(h=4):  return Spacer(1, h * mm)
def hr(): return HRFlowable(width="100%", thickness=0.5, color=MGRAY, spaceAfter=4)

# ── Page template ─────────────────────────────────────────────────────────────
FOOTER_TXT = "For educational and research purposes only. Cryptocurrency trading involves significant financial risk."

def _header_footer(canvas, doc):
    canvas.saveState()
    if doc.page > 1:
        # Header bar
        canvas.setFillColor(NAVY)
        canvas.rect(0, PAGE_H - 14*mm, PAGE_W, 14*mm, fill=1, stroke=0)
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(MARGIN, PAGE_H - 9*mm,
                          "Binance Auto Trading Bot — Operation Manual v4.0")
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 9*mm,
                               f"Page {doc.page}")
        # Footer
        canvas.setFillColor(DGRAY)
        canvas.setFont("Helvetica-Oblique", 7.5)
        canvas.drawCentredString(PAGE_W / 2, 10*mm, FOOTER_TXT)
    canvas.restoreState()

# ── Content builder ───────────────────────────────────────────────────────────
def build_content():
    story = []

    # ── COVER PAGE ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 40*mm))
    # Dark header box
    cover_data = [[Paragraph("BINANCE AUTO TRADING BOT", COVER_TITLE)]]
    cover_tbl = Table(cover_data, colWidths=[PAGE_W - 2*MARGIN])
    cover_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), NAVY),
        ("TOPPADDING",  (0,0), (-1,-1), 18),
        ("BOTTOMPADDING",(0,0),(-1,-1), 18),
    ]))
    story.append(cover_tbl)
    story.append(sp(4))
    story.append(Paragraph("Operation Manual", COVER_SUB))
    story.append(Paragraph("Version 4.0 — 2026-04-07", COVER_SUB))
    story.append(sp(10))

    # Mode badges
    badge_data = [["SIM", "TESTNET", "LIVE"]]
    badge_tbl = Table(badge_data, colWidths=[(PAGE_W - 2*MARGIN)/3]*3)
    badge_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,0), colors.HexColor("#7c3aed")),
        ("BACKGROUND", (1,0), (1,0), colors.HexColor("#0891b2")),
        ("BACKGROUND", (2,0), (2,0), colors.HexColor("#dc2626")),
        ("TEXTCOLOR",  (0,0), (-1,-1), WHITE),
        ("FONTNAME",   (0,0), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 13),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("TOPPADDING", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING",(0,0),(-1,-1), 10),
    ]))
    story.append(badge_tbl)
    story.append(sp(12))

    features = [
        "Regime-aware strategy (Trending / Ranging / Transitional)",
        "Overbought entry guards — RSI/Stochastic hard-blocks prevent chasing tops",
        "ATR-based dynamic take-profit and stop-loss (15m candles)",
        "Trailing stop — locks in profit by tracking the high-water mark",
        "Kelly Criterion position sizing after 20+ trades",
        "Auto / Manual trading mode toggle — confirm each trade in Manual mode",
        "Drawdown circuit breaker + post-SL cooldown",
        "Three interfaces: GUI, Terminal UI, Headless CLI",
        "Encrypted API key storage (Fernet / AES-128-CBC)",
    ]
    for f in features:
        story.append(Paragraph(f"✓  {f}", BODY_L))

    story.append(sp(10))
    disclaimer_s = ParagraphStyle("disc", fontName="Helvetica-Oblique",
                                  fontSize=8, textColor=DGRAY,
                                  borderColor=MGRAY, borderWidth=0.5,
                                  borderPadding=6, leading=12,
                                  alignment=TA_LEFT)
    story.append(Paragraph(
        "This software is for educational and research purposes. "
        "Cryptocurrency trading involves significant financial risk. "
        "Never trade with funds you cannot afford to lose entirely.",
        disclaimer_s))
    story.append(PageBreak())

    # ── TABLE OF CONTENTS ─────────────────────────────────────────────────────
    story.append(h1("Table of Contents"))
    story.append(hr())
    toc = [
        ("1.",  "Quick Start"),
        ("2.",  "Operating Modes"),
        ("3.",  "Setup Steps"),
        ("4.",  "Configuration Parameters"),
        ("5.",  "Trading Strategy — Regime Aware"),
        ("6.",  "Risk Management"),
        ("7.",  "Position Sizing"),
        ("8.",  "Minimum Recommended Balance"),
        ("9.",  "Bot Control & Trading Modes"),
        ("10.", "Account Overview Panel"),
        ("11.", "Charts"),
        ("12.", "Market Scanner"),
        ("13.", "API Key Security"),
        ("14.", "Fee Calculation"),
        ("15.", "Notifications"),
        ("16.", "Frequently Asked Questions"),
    ]
    for num, title in toc:
        story.append(Paragraph(f"{b(num)}  {title}", BODY_L))
    story.append(PageBreak())

    # ── 1. QUICK START ────────────────────────────────────────────────────────
    story.append(h1("1. Quick Start"))
    story.append(hr())
    story.append(body("Install dependencies and launch the bot in three commands:"))
    story.append(sp(1))
    for line in [
        "pip install -r requirements.txt",
        "",
        "# Recommended: GUI desktop app",
        "python3 gui/app.py",
        "",
        "# Terminal dashboard (no Tkinter needed)",
        "python3 gui/app.py --GUI false",
        "",
        "# Headless server mode",
        "python3 bot.py",
        "python3 bot.py --mode testnet",
        "python3 bot.py --config config_live.json",
    ]:
        story.append(code(line if line else " "))
    story.append(body(
        "In <b>Simulator mode</b> no API keys are needed — the bot uses virtual money "
        "tracked in a local SQLite database. This is the recommended starting point."))

    story.append(sp(3))
    story.append(h2("Config files included"))
    cfg_data = [
        ["File", "Mode", "Symbols", "Candles", "Scan rate", "Order type"],
        ["config.json",         "testnet", "BTC, ETH",         "15m", "60s",  "MARKET"],
        ["config_sim.json",     "sim",     "BTC, ETH, BNB, SOL","15m","10s",  "MARKET"],
        ["config_testnet.json", "testnet", "BTC, ETH",         "15m", "60s",  "MARKET"],
        ["config_live.json",    "live",    "BTC, ETH",         "15m", "60s",  "MARKET"],
    ]
    cw = [90, 48, 95, 42, 48, 55]
    story.append(_tbl(cfg_data, [x*mm for x in cw]))
    story.append(note("config.json is the GUI default. Pass a different file with --config."))

    # ── 2. OPERATING MODES ────────────────────────────────────────────────────
    story.append(sp(2))
    story.append(h1("2. Operating Modes"))
    story.append(hr())
    mode_data = [
        ["Mode", "Capital", "Orders", "API Keys", "Use Case"],
        ["SIM",     "Virtual (SQLite, $1,000)",    "Simulated locally",                    "Not required",        "Strategy testing, learning"],
        ["TESTNET", "Virtual (SQLite, $1,000)",    "Simulated locally\n(real lot sizes)",  "Required (mkt data)", "Paper-trade with real prices"],
        ["LIVE",    "Real Binance balance",         "Sent to Binance\nexchange",            "Required",            "Production trading"],
    ]
    story.append(_tbl(mode_data, [x*mm for x in [35,80,70,65,82]]))
    story.append(sp(1))
    story.append(h2("SIM vs TESTNET"))
    story.append(body(
        "Both modes use virtual money stored in SQLite — no real orders are ever sent. "
        "The only difference is that <b>testnet</b> fetches real LOT_SIZE constraints from "
        "the Binance API, so position quantities match what live trading would require. "
        "This makes testnet the ideal final check before going live."))
    story.append(h2("TESTNET vs LIVE"))
    story.append(body(
        "<b>Live</b> mode sends real orders to Binance and reads your actual exchange balance. "
        "All other logic (strategy, risk management, stops) is identical across all three modes."))
    story.append(h2("Balance persistence"))
    story.append(body(
        "In SIM and TESTNET modes, the USDT balance persists in SQLite across restarts — "
        "long-running simulations survive interruptions. The balance resets to "
        "<b>sim_principal</b> (default $1,000) only when there are no open positions at startup."))
    story.append(h2("Testnet API Keys"))
    for line in [
        "• Visit testnet.binance.vision and log in with GitHub",
        "• Click Generate HMAC_SHA256 Key under API Management",
        "• Paste the key and secret into the GUI Settings panel or into .env",
    ]:
        story.append(bullet(line[2:]))
    story.append(note(
        "Testnet keys only allow market-data calls. Since sim and testnet never send "
        "real orders, the keys are used solely to fetch accurate lot sizes."))
    story.append(PageBreak())

    # ── 3. SETUP STEPS ────────────────────────────────────────────────────────
    story.append(h1("3. Setup Steps"))
    story.append(hr())

    steps = [
        ("Step 1 — Select Trading Mode",
         "Choose SIM, TESTNET, or LIVE from the three large mode buttons at the top of the "
         "Settings panel. SIM requires no further credentials."),
        ("Step 2 — Apply API Keys (Testnet / Live)",
         "Paste your Binance API Key and Secret into the provided fields. Click "
         "<b>✓ Apply Keys</b> to activate them, or <b>Save Encrypted</b> to store them "
         "under a master password. In SIM mode skip this step entirely."),
        ("Step 3 — Review Strategy Settings",
         "Adjust Order Size %, ATR TP/SL multipliers, trailing stop, scan interval, and "
         "candle timeframe as needed. Click <b>Apply Strategy</b> to confirm. "
         "The defaults work well for testing."),
        ("Step 4 — Start Bot",
         "Click the green <b>▶ Start Bot</b> button. The bot begins scanning at the "
         "configured interval. Open positions, P&amp;L, and live signals appear in the "
         "respective panels."),
    ]
    for title, desc in steps:
        story.append(KeepTogether([h2(title), body(desc), sp(1)]))

    story.append(warn(
        "For LIVE trading, verify your API key has Spot Trading enabled and "
        "Withdrawals disabled. Never share your API secret."))

    # ── 4. CONFIGURATION PARAMETERS ──────────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("4. Configuration Parameters"))
    story.append(hr())
    story.append(body(
        "All settings live in the active JSON config file. "
        "Most can also be adjusted in the GUI Settings panel at runtime."))
    story.append(sp(2))

    param_data = [
        ["Parameter", "Description", "SIM", "Testnet", "Live"],
        ["order_pct",         "Fraction of USDT balance per trade",          "25%",    "30%",    "30%"],
        ["atr_tp_mult",       "Take-profit = entry + N × ATR",               "2.0",    "2.0",    "2.0"],
        ["atr_sl_mult",       "Stop-loss = entry − N × ATR",                 "1.5",    "1.5",    "1.5"],
        ["trailing_stop_pct", "Trailing stop — % below peak (null=off)",     "1.0%",   "1.0%",   "1.0%"],
        ["take_profit_pct",   "Fixed-pct TP fallback (ATR overrides)",       "1.5%",   "1.5%",   "1.5%"],
        ["stop_loss_pct",     "Fixed-pct SL fallback (ATR overrides)",       "1.0%",   "1.0%",   "1.0%"],
        ["sell_confluence",   "Bearish factors needed for strategy sell",     "2",      "2",      "2"],
        ["cooldown_minutes",  "Re-entry block after SL hit",                 "30",     "60",     "60"],
        ["loop_interval",     "Seconds between scans",                        "10",     "60",     "60"],
        ["kline_interval",    "Candle timeframe",                             "15m",    "15m",    "15m"],
        ["order_type",        "MARKET / LIMIT / OCO",                        "MARKET", "MARKET", "MARKET"],
        ["fee_rate",          "Applied to each order",                        "0.10%",  "0.10%",  "0.10%"],
        ["sim_principal",     "Virtual starting balance",                     "$1,000", "$1,000", "n/a"],
        ["mtf_interval",      "Higher-TF filter (null = disabled)",           "null",   "null",   "null"],
    ]
    cw2 = [80, 115, 36, 40, 35]
    story.append(_tbl(param_data, [x*mm for x in cw2]))
    story.append(note(
        "take_profit_pct and stop_loss_pct are fallbacks only. When ATR data is available "
        "(normal operation), the atr_tp_mult / atr_sl_mult multipliers override them."))

    story.append(sp(3))
    story.append(h2("Order types"))
    order_data = [
        ["Type",   "Behaviour"],
        ["MARKET", "Fills immediately at the best available price. Default for all modes."],
        ["LIMIT",  "Places a buy order at price × (1 − limit_offset_pct). Reduces slippage."],
        ["OCO",    "Places a combined take-profit limit + stop-loss order bracket."],
    ]
    story.append(_tbl(order_data, [40*mm, 280*mm]))

    # ── 5. STRATEGY ───────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("5. Trading Strategy — Regime Aware"))
    story.append(hr())
    story.append(body(
        "The default strategy is <b>RegimeAwareStrategy</b>. It first detects the current "
        "market regime via ADX (Average Directional Index), then applies a different set of "
        "entry rules for each regime. This avoids the most common failure mode of "
        "single-strategy systems: using trend-following rules in choppy markets or "
        "mean-reversion rules in strong trends."))

    story.append(sp(2))
    story.append(h2("Step 1 — Detect regime"))
    reg_data = [
        ["ADX Value", "Regime",       "Approach",            "Entry threshold"],
        ["> 25",      "Trending",     "Trend-following",     "3+ bullish factors AND bullish > bearish"],
        ["20–25",     "Transitional", "Cautious trending",   "3+ bullish factors AND bullish > bearish"],
        ["< 20",      "Ranging",      "Mean-reversion",      "3+ bullish factors AND bullish > bearish"],
    ]
    story.append(_tbl(reg_data, [x*mm for x in [30,44,55,190]]))

    story.append(sp(2))
    story.append(h2("Step 2 — Overbought entry guards"))
    story.append(body(
        "Both TRENDING and TRANSITIONAL regimes hard-block new long entries when indicators "
        "signal extreme overbought conditions. This prevents buying near local tops when a "
        "mean-reversion is statistically likely before the TP is reached. "
        "If already holding, the bot lets RiskManager handle the exit — "
        "these guards only affect new entries."))
    story.append(sp(1))
    ob_data = [
        ["Regime",       "Block condition (new entries only)"],
        ["TRENDING",     "RSI > 72  OR  Stochastic %K > 85"],
        ["TRANSITIONAL", "RSI > 65  OR  Stochastic %K > 80  (stricter)"],
        ["RANGING",      "No hard block — RSI/Stoch are individual bearish factors"],
    ]
    story.append(_tbl(ob_data, [55*mm, 270*mm]))

    story.append(sp(2))
    story.append(h2("Step 3 — Entry signals"))
    story.append(h2("Trending market (ADX > 25):"))
    story.append(body("Entry fires when ≥ 3 bullish factors AND bullish count > bearish count."))
    trend_data = [
        ["Factor",           "Bullish condition",                    "Bearish condition"],
        ["DI direction",     "+DI > −DI",                            "−DI > +DI"],
        ["Price vs MA50",    "Price > MA50",                         "Price < MA50"],
        ["MA alignment",     "MA20 > MA50",                          "MA20 < MA50"],
        ["RSI healthy",      "RSI 40–68",                            "RSI > 72 (OB) or RSI < 35 (collapsed)"],
        ["MACD histogram",   "> 0",                                  "≤ 0"],
        ["OBV slope",        "> 0.05",                               "< −0.05"],
        ["Volume activity",  "Ratio ≥ 1.15×",                        "—"],
        ["VWAP",             "Price above VWAP",                     "Price below VWAP"],
        ["Sentiment",        "—",                                    "Fear & Greed > 82 (extreme greed)"],
        ["RSI divergence",   "Bullish divergence",                   "Bearish divergence (double-weighted)"],
        ["Hurst Exponent",   "—",                                    "H < 0.38 (anti-persistent)"],
    ]
    story.append(_tbl(trend_data, [x*mm for x in [48, 105, 125]]))

    story.append(sp(2))
    story.append(h2("Ranging market (ADX < 20):"))
    story.append(body("Entry fires when ≥ 3 bullish factors AND bullish count > bearish count."))
    range_data = [
        ["Factor",           "Bullish condition",                    "Bearish condition"],
        ["RSI",              "< 38 (oversold)",                      "> 65 (overbought)"],
        ["Stochastic",       "%K < 25 AND turning up above %D",      "%K > 75 AND turning down"],
        ["Bollinger Band",   "Price at lower BB (bb_pct < 0.12)",    "Price at upper BB (bb_pct > 0.88)"],
        ["RSI divergence",   "Bullish div (double-weighted)",         "Bearish div (double-weighted)"],
        ["Capitulation vol", "Spike on down bar + RSI < 45",         "—"],
        ["Sentiment",        "Fear & Greed < 25 (extreme fear)",     "Fear & Greed > 78"],
        ["OBV slope",        "> 0.08",                               "< −0.08"],
        ["VWAP",             "Price below VWAP (buying below fair value)", "—"],
        ["Hurst Exponent",   "—",                                    "H > 0.65 (persistent — avoid fade)"],
    ]
    story.append(_tbl(range_data, [x*mm for x in [48, 105, 125]]))

    story.append(sp(2))
    story.append(h2("Transitional market (ADX 20–25):"))
    story.append(body(
        "Uses a broader factor set combining directional and momentum indicators from both "
        "regimes. Same confluence threshold as TRENDING (3+ bullish). The key difference is "
        "a stricter overbought guard (RSI > 65 or Stoch > 80 blocks new entries)."))
    trans_data = [
        ["Factor",           "Bullish condition",                    "Bearish condition"],
        ["DI direction",     "+DI > −DI",                            "−DI > +DI"],
        ["MA alignment",     "MA20 > MA50",                          "MA20 < MA50"],
        ["Price vs MA50",    "Price > MA50",                         "Price < MA50"],
        ["RSI",              "RSI < 48 (pullback zone)",             "RSI > 65 (overbought)"],
        ["Stochastic",       "%K < 35 AND turning up above %D",      "%K > 70 AND turning down below %D"],
        ["MACD histogram",   "> 0",                                  "≤ 0"],
        ["OBV slope",        "> 0.08",                               "< −0.08"],
        ["VWAP",             "Price above VWAP",                     "Price below VWAP"],
        ["RSI divergence",   "Bullish divergence",                   "Bearish divergence"],
    ]
    story.append(_tbl(trans_data, [x*mm for x in [48, 105, 125]]))

    story.append(sp(2))
    story.append(h2("Step 4 — Exit signals (in priority order)"))
    exit_data = [
        ["Priority", "Exit type",      "Trigger"],
        ["1st",      "Trailing stop",  "Price drops trailing_stop_pct% below the high-water mark since entry"],
        ["2nd",      "ATR take-profit","Price reaches entry + atr_tp_mult × ATR"],
        ["2nd",      "ATR stop-loss",  "Price drops to entry − atr_sl_mult × ATR"],
        ["3rd",      "Fixed-pct TP/SL","Fallback when ATR stops were not set at entry"],
        ["Last",     "Strategy sell",  "Bearish factors ≥ sell_confluence (2) AND outnumber bullish by ≥ 2"],
    ]
    story.append(_tbl(exit_data, [x*mm for x in [28, 50, 240]]))

    story.append(sp(2))
    story.append(h2("Quantitative indicators used"))
    quant_data = [
        ["Indicator",            "What it measures",                          "Strategy use"],
        ["Hurst Exponent (H)",   "Price series memory — H > 0.5 = trending,\nH < 0.5 = mean-reverting",
                                 "Adds bearish factor when H disagrees with regime\n(H<0.38 in TREND, H>0.65 in RANGE)"],
        ["Permutation Entropy",  "Ordinal pattern complexity — high PE = noisy market",
                                 "Logged as diagnostic (PE=X.XX in every log line).\nDoes not modify entry thresholds."],
        ["VWAP",                 "Volume-weighted avg price over 100 candles",
                                 "Bullish above VWAP (trending) or below VWAP (ranging, targeting mean-reversion)"],
        ["MTF filter",           "MA20 > MA50 on the higher timeframe",
                                 "Vetoes all long entries when the bigger-picture trend is down"],
    ]
    story.append(_tbl(quant_data, [x*mm for x in [46, 110, 122]]))

    story.append(sp(2))
    story.append(h2("Legacy strategy"))
    story.append(body(
        'Set <b>"strategy": "winrate"</b> in config to use the legacy WinRateStrategy. '
        "It buys when a composite score ≥ buy_win_thresh (60) and sells when ≤ "
        "sell_win_thresh (35). This strategy is kept for compatibility but the regime "
        "strategy significantly outperforms it."))

    # ── 6. RISK MANAGEMENT ────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("6. Risk Management"))
    story.append(hr())

    story.append(h2("ATR-based dynamic stops"))
    story.append(body(
        "On every entry, the bot calculates ATR (Average True Range) over the last 100 "
        "candles and sets per-symbol TP and SL price levels:"))
    story.append(sp(1))
    story.append(Paragraph("<b>TP price</b> = entry + atr_tp_mult × ATR", BODY_L))
    story.append(Paragraph("<b>SL price</b> = entry − atr_sl_mult × ATR", BODY_L))
    story.append(sp(1))
    story.append(body(
        "These dynamic stops adapt to actual market volatility. On 15m candles, BTC ATR is "
        "typically $100–130 (≈0.2% of price), giving TP ≈ +0.4% and SL ≈ −0.3% — "
        "tight enough to trigger on realistic intraday moves. On 1h candles ATR is "
        "3–4× wider, so stops rarely trigger in consolidating markets."))
    story.append(note(
        "If ATR cannot be computed (fewer than 2 candles), the bot falls back to "
        "take_profit_pct and stop_loss_pct as fixed percentages."))

    story.append(sp(2))
    story.append(h2("Trailing stop"))
    story.append(body(
        "When trailing_stop_pct is set (default 1.0%), the bot tracks the high-water mark "
        "price since entry. If the current price drops more than trailing_stop_pct below "
        "that peak, the position is closed. The trailing stop is checked before ATR stops "
        "and locks in profit on any upward run beyond the TP level."))

    story.append(sp(2))
    story.append(h2("Post-SL cooldown"))
    story.append(body(
        "After a stop-loss is triggered, the bot blocks re-entry into the same symbol for "
        "<b>cooldown_minutes</b>. This prevents revenge-trading into continued adverse moves "
        "— one of the most common ways retail traders amplify losses."))
    story.append(sp(1))
    cooldown_data = [
        ["Mode",    "cooldown_minutes"],
        ["SIM",     "30"],
        ["TESTNET", "60"],
        ["LIVE",    "60"],
    ]
    story.append(_tbl(cooldown_data, [60*mm, 60*mm]))

    story.append(sp(2))
    story.append(h2("Drawdown circuit breaker"))
    story.append(body(
        "If the portfolio value drops more than <b>20%</b> from the starting principal "
        "(sim_principal), the bot stops opening new positions. It continues monitoring and "
        "managing existing positions. This protects against catastrophic loss during "
        "sustained adverse market conditions."))

    story.append(sp(2))
    story.append(h2("Safety mechanisms summary"))
    safety_data = [
        ["Mechanism",         "Trigger",                              "Action"],
        ["Drawdown CB",       "Portfolio < principal × 0.80",        "Block all new entries"],
        ["Post-SL cooldown",  "Stop-loss triggered on any symbol",   "Block that symbol for cooldown_minutes"],
        ["Min-notional guard","Calculated position < Binance minimum","Skip trade, log warning"],
        ["Trailing stop",     "Price falls trail_pct% from peak",    "Close position"],
        ["Overbought guard",  "RSI/Stoch exceed regime thresholds",  "Block new entry (hold existing)"],
        ["MTF veto",          "Higher-TF MA bearish (when configured)","Block long entries"],
    ]
    story.append(_tbl(safety_data, [x*mm for x in [50, 90, 90]]))

    # ── 7. POSITION SIZING ────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("7. Position Sizing"))
    story.append(hr())

    story.append(h2("Default: fixed fraction"))
    story.append(body("By default each trade uses a fixed fraction of available USDT balance:"))
    story.append(Paragraph("<b>order_usdt</b> = available_usdt × order_pct", BODY_L))
    story.append(body(
        "With order_pct = 0.30 (30%) and a $1,000 balance, each trade uses $300."))

    story.append(sp(2))
    story.append(h2("After 20+ trades: Kelly Criterion"))
    story.append(body(
        "Once the bot has closed 20 or more trades, it switches to "
        "<b>Half-Kelly sizing</b> for better long-run compounding:"))
    story.append(Paragraph("<b>f*</b> = W − (1 − W) / R", BODY_L))
    story.append(body(
        "where W = win rate and R = average win % ÷ average loss %. "
        "Half-Kelly applies f* × 0.5 to reduce variance by ~75% while retaining "
        "~75% of the theoretical growth rate. Clamped to [0%, 50%] of capital so "
        "a single trade never risks more than half the portfolio."))

    story.append(sp(2))
    story.append(h2("Volatility scalar"))
    story.append(body(
        "The position size is further scaled by current volatility relative to a 2% baseline:"))
    story.append(Paragraph("<b>scalar</b> = 0.02 / (ATR / price), clamped to [0.5, 1.5]", BODY_L))
    story.append(body(
        "When ATR is 4% of price, scalar = 0.5 (half position). "
        "When ATR is 1%, scalar = 1.5 (50% larger position). "
        "This targets a roughly constant dollar-risk per trade regardless of "
        "how volatile the market currently is."))
    story.append(Paragraph("<b>effective_pct</b> = (kelly_or_order_pct) × vol_scalar", BODY_L))

    # ── 8. MINIMUM RECOMMENDED BALANCE ───────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("8. Minimum Recommended Balance"))
    story.append(hr())
    story.append(body(
        "Based on order_pct = 30%, ATR TP ≈ 0.4% (2×ATR on 15m candles), "
        "ATR SL ≈ 0.3% (1.5×ATR), and fee_rate = 0.1% (0.2% round-trip):"))
    story.append(sp(1))

    bal_data = [
        ["Balance", "Position (30%)", "Net TP gain", "Net SL loss", "Verdict"],
        ["$50",     "$15",            "~$0.03",      "~$0.05",      "Fees dominate — not viable"],
        ["$100",    "$30",            "~$0.06",      "~$0.09",      "Marginal"],
        ["$500",    "$150",           "+$0.54",      "−$0.60",      "Practical minimum"],
        ["$1,000",  "$300",           "+$1.08",      "−$1.20",      "Comfortable — survives losing streaks"],
        ["$5,000",  "$1,500",         "+$5.40",      "−$6.00",      "Meaningful compounding"],
    ]
    story.append(_tbl(bal_data, [x*mm for x in [38, 45, 38, 40, 80]]))
    story.append(sp(2))
    story.append(h2("Math per trade (15m candles, BTC ~$69K)"))
    story.append(Paragraph("<b>Win:</b> position × ~0.4% ATR TP − 0.2% round-trip fee", BODY_L))
    story.append(Paragraph("<b>Loss:</b> position × ~0.3% ATR SL + 0.2% round-trip fee", BODY_L))
    story.append(sp(1))
    story.append(body(
        "Individual trade P&amp;L is small on 15m — the edge comes from "
        "<b>trade frequency</b> (multiple trades per day) and compounding over time. "
        "The trailing stop also captures additional profit on larger moves beyond the TP."))
    story.append(sp(1))
    story.append(warn(
        "Below $500, the 20% drawdown circuit breaker can trigger after just 4–5 "
        "losses in a row, halting the bot before it can recover. "
        "Start with at least $500 to give the strategy room to work."))
    story.append(note("ATR varies by asset and timeframe. Figures use typical 15m BTC ATR (~0.2% of price)."))

    # ── 9. BOT CONTROL & TRADING MODES ───────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("9. Bot Control & Trading Modes"))
    story.append(hr())

    ctrl_data = [
        ["Button / Control",       "Action",                               "Notes"],
        ["▶ Start Bot (green)",    "Begins scanning and trading",          "Disabled while already running"],
        ["■ Stop (gray)",          "Halts the scan loop",                  "Open positions are NOT closed"],
        ["Reset Portfolio (yellow)","Clears position tracking + resets balance","Does not cancel real exchange orders"],
        ["⚡ AUTO (top bar)",      "Strategy executes trades automatically", "Green badge — default mode"],
        ["✋ MANUAL (top bar)",    "Strategy signals queue for confirmation","Yellow badge — you confirm each trade"],
    ]
    story.append(_tbl(ctrl_data, [x*mm for x in [55, 80, 95]]))

    story.append(sp(3))
    story.append(h2("Auto mode vs Manual mode"))
    story.append(body(
        "The <b>⚡ AUTO / ✋ MANUAL</b> toggle in the top bar switches the bot between "
        "two execution modes at any time — even while running:"))
    story.append(sp(1))
    story.append(bullet(
        "<b>AUTO</b> — strategy signals execute immediately without user confirmation. "
        "The bot trades fully autonomously. This is the default."))
    story.append(bullet(
        "<b>MANUAL</b> — when a BUY or SELL signal fires, a confirmation dialog pops up "
        "showing the symbol, signal price, and reason. You choose to confirm or dismiss. "
        "Useful for reviewing strategy decisions before they execute."))
    story.append(sp(1))
    story.append(note(
        "Manual SELL is always available in the Open Positions panel regardless of mode. "
        "Click the row and use the Sell button to exit any position immediately."))

    story.append(sp(2))
    story.append(h2("Changing mode while the bot is running"))
    story.append(body(
        "Selecting a different mode while the bot is running triggers a confirmation dialog. "
        "<b>Confirm</b> to stop the bot, wipe session state, and apply the new mode. "
        "<b>Cancel</b> to keep the bot running with the current mode unchanged."))

    story.append(sp(2))
    story.append(h2("Changing strategy while the bot is running"))
    story.append(body(
        "Strategy changes (via Apply Strategy) take effect on the next scan cycle without "
        "stopping the bot. Existing open positions are not closed automatically. "
        "Use Reset Portfolio for a completely clean strategy switch."))

    # ── 10. ACCOUNT OVERVIEW ─────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("10. Account Overview Panel"))
    story.append(hr())
    acct_data = [
        ["Field",          "Description"],
        ["USDT Balance",   "Available USDT not currently invested in positions"],
        ["Portfolio Value","USDT Balance + all coin holdings valued at current live prices"],
        ["Total P&L",      "Portfolio Value minus starting principal (fees deducted)"],
        ["Return %",       "(Portfolio Value / Principal − 1) × 100%"],
        ["Win Rate",       "Profitable closed trades ÷ total closed trades"],
        ["Trade Count",    "Total buy orders executed in this session"],
        ["Max Drawdown",   "(Peak portfolio value − current value) / peak value"],
        ["Total Fees",     "Sum of all buy and sell fees paid this session"],
    ]
    story.append(_tbl(acct_data, [55*mm, 265*mm]))
    story.append(note(
        "Portfolio Value updates in real-time using live tick prices. "
        "In SIM and TESTNET modes, USDT Balance comes from the SQLite-tracked balance. "
        "In LIVE mode it is read directly from your Binance account."))

    # ── 11. CHARTS ────────────────────────────────────────────────────────────
    story.append(sp(3))
    story.append(h1("11. Charts"))
    story.append(hr())
    chart_data = [
        ["Panel",  "Content"],
        ["Price",  "Candlesticks + MA20 (orange) + MA50 (pink) + Bollinger Bands (green/cyan)"],
        ["Volume", "Green bars (up candle), red bars (down candle)"],
        ["RSI",    "RSI-14 (purple), overbought line at 70 (red), oversold line at 30 (green)"],
        ["MACD",   "Histogram (green = bullish, red = bearish) + MACD line (blue) + Signal (orange)"],
    ]
    story.append(_tbl(chart_data, [30*mm, 290*mm]))
    story.append(sp(2))
    story.append(h2("Controls"))
    for b_item in [
        "Symbol selector: BTCUSDT, ETHUSDT (and any others configured)",
        "Candle period: 1m, 5m, 15m, 1h",
        "Live price updates every 2 seconds",
        "Full chart refresh every 30 seconds",
    ]:
        story.append(bullet(b_item))

    # ── 12. MARKET SCANNER ────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("12. Market Scanner"))
    story.append(hr())
    story.append(body(
        "The scanner runs independently (refreshes every 10 seconds) and shows the current "
        "regime assessment and indicator values for every configured symbol."))
    story.append(sp(1))
    scanner_data = [
        ["Column",  "Description"],
        ["Symbol",  "Trading pair"],
        ["Price",   "Current live price"],
        ["24h Chg", "Price change in the last 24 hours"],
        ["RSI",     "Current RSI-14 value"],
        ["ADX",     "ADX value used for regime detection"],
        ["Regime",  "TREND / RANGE / TRANS based on ADX"],
        ["BB Pos",  "Price position within Bollinger Bands as % (0%=lower band, 100%=upper band)"],
        ["Stoch",   "Stochastic %K value"],
        ["Signal",  "BUY / SELL / HOLD — current strategy output"],
        ["B/Bear",  "Bullish / bearish confluence factor counts (e.g. 4/3 = 4 bull, 3 bear)"],
        ["Gap",     "Distance to buy trigger: '✓ ready', '+2 abs' (need 2 more bull), 'bear+3' (bear leads by 3)"],
    ]
    story.append(_tbl(scanner_data, [28*mm, 292*mm]))
    story.append(note(
        "The scanner signal shows what the strategy would do right now. The bot only acts "
        "on it during its own scan cycle (every loop_interval seconds). "
        "Hurst and Permutation Entropy are computed by the strategy internally but are not "
        "displayed as scanner columns — they appear in the trade log output."))

    # ── 13. API KEY SECURITY ──────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("13. API Key Security"))
    story.append(hr())
    for b_item in [
        "API keys are encrypted with <b>Fernet (PBKDF2 + AES-128-CBC)</b> under your master password",
        "Keys are stored in <b>~/.binance_bot/</b> (outside the project folder) — not in saved_config.json",
        "SIM and TESTNET modes never place real orders — safe to use with testnet-only keys",
        "For LIVE trading, enable <b>Spot Trading only</b> — never enable Withdrawals on the API key",
        "Restrict your live API key to your IP address on the Binance key management page",
        "Never commit .env or any file containing your secret key to version control",
        "Never share your API secret — Binance support will never ask for it",
    ]:
        story.append(bullet(b_item))
    story.append(sp(1))
    story.append(warn(
        "A compromised API key with withdrawal permissions could result in total loss of "
        "funds. Always restrict permissions to Spot Trading only."))

    # ── 14. FEE CALCULATION ───────────────────────────────────────────────────
    story.append(sp(3))
    story.append(h1("14. Fee Calculation"))
    story.append(hr())
    fee_mode_data = [
        ["Mode",     "Fee rate",                         "Configurable"],
        ["SIM",      "0.10% (default, matches Binance standard)", "Yes — edit fee_rate in config"],
        ["TESTNET",  "0.10%",                            "Yes — edit fee_rate in config"],
        ["LIVE",     "0.10% (Binance Spot standard)",    "Match your actual Binance tier"],
    ]
    story.append(_tbl(fee_mode_data, [x*mm for x in [28, 115, 80]]))
    story.append(sp(2))
    story.append(h2("Round-trip example at 0.10% fee rate"))
    fee_ex_data = [
        ["Step",                       "Amount"],
        ["Buy $1,000 of ETH",          "Fee = $1.00 → $999.00 of ETH purchased"],
        ["Sell at $1,050 (+5%)",       "Fee = $1.05 → $1,048.95 USDT received"],
        ["Total fees paid",            "$2.05"],
        ["Net profit",                 "$48.95 (+4.90% on original $1,000)"],
        ["Break-even price move needed","+ 0.20% (to cover both buy and sell fee)"],
    ]
    story.append(_tbl(fee_ex_data, [100*mm, 120*mm]))
    story.append(note(
        "BNB holders on Binance receive a 25% fee discount, effectively 0.075% per order. "
        "Set fee_rate=0.00075 in your config if you use BNB to pay fees."))

    # ── 15. NOTIFICATIONS ────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("15. Notifications"))
    story.append(hr())
    story.append(body(
        "The bot can send trade alerts to Discord or Telegram. Alerts are sent "
        "asynchronously in a background thread so they never delay the trading loop."))
    story.append(sp(2))
    story.append(h2("Supported events"))
    for ev in ["BUY filled — symbol, quantity, price, mode",
               "SELL filled — symbol, quantity, price, P&L, reason",
               "Stop-loss triggered",
               "Take-profit triggered"]:
        story.append(bullet(ev))
    story.append(sp(2))
    story.append(h2("Setup via .env"))
    for line in [
        "# Discord",
        "DISCORD_WEBHOOK=https://discord.com/api/webhooks/...",
        "",
        "# Telegram",
        "TELEGRAM_TOKEN=your_bot_token",
        "TELEGRAM_CHAT_ID=your_chat_id",
    ]:
        story.append(code(line if line else " "))
    story.append(sp(1))
    story.append(h2("Setup via GUI"))
    story.append(body(
        "Enter webhook URLs in the <b>Notifications</b> section of the Settings panel "
        "(bottom of the left sidebar). Click <b>Save Notification Settings</b> to persist."))
    story.append(note(
        "If both Discord and Telegram are configured, alerts are sent to both. "
        "A failed notification does not affect trading."))

    # ── 16. FAQ ───────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(h1("16. Frequently Asked Questions"))
    story.append(hr())

    faqs = [
        ("The bot shows BUY signal but never places an order.",
         "Check the log for 'Buy qty is zero' or 'Insufficient balance'. Ensure your balance "
         "is above the min-notional threshold (~$50 minimum, $500 recommended). "
         "Also check if a post-SL cooldown is active."),

        ("Signal is always HOLD — the bot never enters a trade.",
         "The regime strategy requires 3+ bullish confluence factors. In a strong downtrend "
         "or very noisy market, this is intentional — the bot waits for a high-quality setup. "
         "Also check if the overbought guard is active: log lines containing "
         "'overbought entry blocked' mean RSI or Stochastic is too high to enter safely."),

        ("USDT Balance shows $0 or wrong amount.",
         "In SIM/TESTNET, the balance is read from SQLite. If you just started the bot for "
         "the first time, it initialises to sim_principal ($1,000). If the balance looks "
         "wrong, check portfolio.db has not been manually edited."),

        ("400 Bad Request errors from exchange.",
         "4xx errors are client-side and are not retried — the bot logs the error and moves on. "
         "Common causes: invalid symbol, quantity below lot-size minimum. In TESTNET mode, "
         "no real orders are sent so 400 errors should not occur from order endpoints."),

        ("Will open positions close when I click Stop?",
         "No. Stop halts the scan loop but leaves all position tracking intact. "
         "The bot will manage existing positions on the next Start. "
         "Use Reset Portfolio to discard all position state "
         "(does not cancel real exchange orders)."),

        ("Where are the API keys stored?",
         "Encrypted in ~/.binance_bot/ under your master password. They are not written "
         "to the project folder, config files, or saved_config.json."),

        ("How do I add more trading pairs?",
         'Edit the "symbols" list in your config JSON: '
         '["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]. '
         "Each symbol is scanned independently every loop_interval seconds."),

        ("Where are trade logs stored?",
         "In bot.log (rotating, 10 MB per file, 5 backups). "
         "The Trade Log tab in the GUI shows the same entries in real-time."),

        ("How do I switch from testnet to live?",
         "Stop the bot, select LIVE in the mode selector, enter your live API keys, and "
         "click Start Bot. Or pass --mode live and --config config_live.json on the CLI."),

        ("What happens to open positions if I reset the portfolio?",
         "In SIM/TESTNET, positions are discarded and balance resets to sim_principal. "
         "In LIVE, the bot's internal tracking is cleared but real orders already submitted "
         "to Binance continue to exist on the exchange — you must cancel them manually "
         "via the Binance interface."),

        ("Why did the bot enter a trade but immediately stall with no exit?",
         "If using 1h candles, the ATR SL can be $500+ below entry — too wide to trigger "
         "in normal intraday consolidation. Use 15m candles (default) for ATR stops that "
         "are tight enough (~$150–200 for BTC) to respond to real price moves."),

        ("What is the trailing stop and how does it help?",
         "The trailing stop tracks the highest price since entry. If price drops "
         "trailing_stop_pct% below that peak, the position closes. "
         "This locks in profit when price runs up before the ATR TP is reached, "
         "and prevents giving back gains in a gradual reversal."),
    ]

    for q, a in faqs:
        story.append(KeepTogether([
            Paragraph(f"<b>Q: {q}</b>", BODY_L),
            Paragraph(f"<b>A:</b> {a}", BODY),
            sp(2),
        ]))

    return story


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    import os
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "Binance_Bot_Operation_Manual.pdf")
    doc = SimpleDocTemplate(
        out,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=22*mm, bottomMargin=18*mm,
        title="Binance Auto Trading Bot — Operation Manual v4.0",
        author="Binance Auto Trading Bot",
    )
    doc.build(build_content(), onFirstPage=_header_footer,
              onLaterPages=_header_footer)
    print(f"Generated: {out}")


if __name__ == "__main__":
    main()
