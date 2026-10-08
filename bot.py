#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMC Engine v2 — Sweep + BOS + Displacement + ATR-based risk
===========================================================
- يعمل بوضعين: backtest تاريخي أو live (إشارة لحظية قابلة للربط مع Telegram)
- لا توجد قيم ثابتة: الوقف والأهداف مبنية على ATR ومستويات الشارت نفسها
- لا lookahead: الإشارة تُحسب على شمعة مغلقة والدخول بشمعة التالية

التشغيل:
    python smc_engine_v2.py --backtest --symbol GC=F --period 3mo
    python smc_engine_v2.py --once --symbol GC=F
"""

import argparse
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    import yfinance as yf
except ImportError:
    yf = None


# ============================================================
# 1) جلب البيانات
# ============================================================

def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


def load_data(symbol: str, period_5m: str = "60d", period_1h: str = "1y"):
    """جلب فريمي 5M و1H مع معالجة الأعمدة والمناطق الزمنية."""
    if yf is None:
        raise RuntimeError("ثبّت yfinance أولاً: pip install yfinance")
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    df5 = _flatten(yf.download(symbol, period=period_5m, interval="5m",
                               progress=False, session=session))
    df1 = _flatten(yf.download(symbol, period=period_1h, interval="1h",
                               progress=False, session=session))
    if df5.empty or df1.empty:
        raise RuntimeError("فشل جلب البيانات من yfinance")
    for df in (df5, df1):
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
    return df5.sort_index(), df1.sort_index()


def htf_completed(df1h: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    """فريم 1H المغلق فقط قبل اللحظة t — منع تسريب الشمعة الجارية."""
    return df1h[df1h.index + pd.Timedelta(hours=1) <= t]


# ============================================================
# 2) مؤشرات مساعدة
# ============================================================

def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()


def swing_points(df: pd.DataFrame, w: int = 2):
    """قمم وقيعان محورية (fractal) — لا تنظر للمستقبل إطلاقاً."""
    sh = (df["High"] == df["High"].rolling(2 * w + 1, center=True).max())
    sl = (df["Low"] == df["Low"].rolling(2 * w + 1, center=True).min())
    return sh.fillna(False), sl.fillna(False)


def bullish_fvg(df: pd.DataFrame, i: int) -> bool:
    return df["Low"].iloc[i] > df["High"].iloc[i - 2]


def bearish_fvg(df: pd.DataFrame, i: int) -> bool:
    return df["High"].iloc[i] < df["Low"].iloc[i - 2]


# ============================================================
# 3) المحرك v2
# ============================================================

@dataclass
class Signal:
    side: str            # "LONG" / "SHORT"
    entry: float         # سعر مرجعي (الإغلاق عند الإشارة)
    sl: float
    tp1: float
    tp2: float
    rr: float
    reason: str
    time: pd.Timestamp


@dataclass
class EngineParams:
    swing_w: int = 2          # نافذة القمم/القيعان على 5M
    atr_n: int = 14
    sweep_lookback: int = 16  # كم شمعة 5M يشكّلون "المستوى المحمي" (~80 دقيقة)
    sweep_arm: int = 12       # أقصى عمر للـ Sweep قبل إلغائه (شموع)
    disp_mult: float = 1.2    # جسم شمعة الاندفاع >= 1.2 * ATR
    sl_atr_mult: float = 0.3  # وقف = قاع السحب - 0.3*ATR
    rr_min: float = 1.5       # فلتر الحد الأدنى للعائد/المخاطرة على TP2
    bias_lookback: int = 5    # HTF bias: إغلاق 1H مقابل إغلاق قبل 5 ساعات


class SMCEngineV2:
    """
    منطق الدخول (للشراء، والعكس للبيع):
      1) SWEEP  : كسر قاع محمي ثم إغلاق فوقه (سحب سيولة)
      2) BOS    : إغلاق فوق أعلى قمة محورية بعد السحب
      3) DISP   : شمعة الكسر جسمها >= disp_mult*ATR وتخلق FVG
      4) HTF    : اتجاه 1H مع الاتجاه
      5) RR     : (TP2-entry)/(entry-SL) >= rr_min وإلا لا صفقة
    """

    def __init__(self, p: EngineParams = None):
        self.p = p or EngineParams()

    # ---------- الخطوات ----------

    def _htf_bias(self, df1h_c: pd.DataFrame) -> str | None:
        p = self.p
        if len(df1h_c) < p.bias_lookback + 2:
            return None
        c = df1h_c["Close"]
        return "BULL" if c.iloc[-1] > c.iloc[-1 - p.bias_lookback] else "BEAR"

    def _find_sweep_low(self, df5: pd.DataFrame, i: int):
        """آخر سحب سيولة من القيعان ضمن نافذة sweep_arm شمعة."""
        p = self.p
        start = max(p.sweep_lookback + p.swing_w + 1, i - p.sweep_arm)
        for j in range(i, start - 1, -1):
            protected = df5["Low"].iloc[j - p.sweep_lookback:j].min()
            if df5["Low"].iloc[j] < protected and df5["Close"].iloc[j] > protected:
                return j, float(df5["Low"].iloc[j])
        return None, None

    def _find_sweep_high(self, df5: pd.DataFrame, i: int):
        p = self.p
        start = max(p.sweep_lookback + p.swing_w + 1, i - p.sweep_arm)
        for j in range(i, start - 1, -1):
            protected = df5["High"].iloc[j - p.sweep_lookback:j].max()
            if df5["High"].iloc[j] > protected and df5["Close"].iloc[j] < protected:
                return j, float(df5["High"].iloc[j])
        return None, None

    def _bos_bull(self, df5: pd.DataFrame, sh, i: int, j: int, a: float) -> tuple | None:
        """كسر هيكل صاعد: إغلاق i فوق أعلى قمة محورية بعد السحب j + اندفاع + FVG."""
        p = self.p
        highs = df5["High"].iloc[j + 1:i + 1][sh.iloc[j + 1:i + 1]]
        if highs.empty:
            return None
        bos_level = float(highs.max())
        if df5["Close"].iloc[i] <= bos_level:
            return None
        body = abs(df5["Close"].iloc[i] - df5["Open"].iloc[i])
        if body < p.disp_mult * a:
            return None
        if not (bullish_fvg(df5, i) or bullish_fvg(df5, i - 1)):
            return None
        return bos_level

    def _bos_bear(self, df5: pd.DataFrame, sl, i: int, j: int, a: float) -> tuple | None:
        p = self.p
        lows = df5["Low"].iloc[j + 1:i + 1][sl.iloc[j + 1:i + 1]]
        if lows.empty:
            return None
        bos_level = float(lows.min())
        if df5["Close"].iloc[i] >= bos_level:
            return None
        body = abs(df5["Close"].iloc[i] - df5["Open"].iloc[i])
        if body < p.disp_mult * a:
            return None
        if not (bearish_fvg(df5, i) or bearish_fvg(df5, i - 1)):
            return None
        return bos_level

    # ---------- الواجهة العامة ----------

    def signal_at(self, df5_slice: pd.DataFrame, df1h_c: pd.DataFrame,
                  t: pd.Timestamp) -> Signal | None:
        p = self.p
        if len(df5_slice) < p.sweep_lookback + 20:
            return None
        a = float(atr(df5_slice, p.atr_n).iloc[-1])
        if not np.isfinite(a) or a <= 0:
            return None
        sh, sl_ = swing_points(df5_slice, p.swing_w)
        i = len(df5_slice) - 1
        bias = self._htf_bias(df1h_c)
        entry = float(df5_slice["Close"].iloc[i])

        # ---------- شراء ----------
        if bias == "BULL":
            j, sweep_low = self._find_sweep_low(df5_slice, i)
            if j is not None:
                if self._bos_bull(df5_slice, sh, i, j, a):
                    sl = sweep_low - p.sl_atr_mult * a
                    swings_hi = df5_slice["High"][sh]
                    above = swings_hi[swings_hi > entry]
                    tp1 = float(above.min()) if len(above) else entry + 1.5 * a
                    erl = self._erl(df1h_c, entry, side="LONG")
                    tp2 = erl if erl and erl > tp1 else tp1 + 1.0 * a
                    rr = (tp2 - entry) / max(entry - sl, 1e-9)
                    if rr >= p.rr_min:
                        return Signal("LONG", entry, sl, tp1, tp2, rr,
                                      f"Sweep@{sweep_low:.2f} + BOS + Disp", t)

        # ---------- بيع ----------
        if bias == "BEAR":
            j, sweep_high = self._find_sweep_high(df5_slice, i)
            if j is not None:
                if self._bos_bear(df5_slice, sl_, i, j, a):
                    sl = sweep_high + p.sl_atr_mult * a
                    swings_lo = df5_slice["Low"][sl_]
                    below = swings_lo[swings_lo < entry]
                    tp1 = float(below.max()) if len(below) else entry - 1.5 * a
                    erl = self._erl(df1h_c, entry, side="SHORT")
                    tp2 = erl if erl and erl < tp1 else tp1 - 1.0 * a
                    rr = (entry - tp2) / max(sl - entry, 1e-9)
                    if rr >= p.rr_min:
                        return Signal("SHORT", entry, sl, tp1, tp2, rr,
                                      f"Sweep@{sweep_high:.2f} + BOS + Disp", t)
        return None

    @staticmethod
    def _erl(df1h_c: pd.DataFrame, ref: float, side: str) -> float | None:
        """أقرب سيولة خارجية من فريم 1H غير مُمسوحة بعد."""
        if df1h_c is None or len(df1h_c) < 20:
            return None
        sh, sl_ = swing_points(df1h_c, 3)
        if side == "LONG":
            cands = df1h_c["High"][sh]
            cands = cands[cands > ref]
            return float(cands.min()) if len(cands) else None
        cands = df1h_c["Low"][sl_]
        cands = cands[cands < ref]
        return float(cands.max()) if len(cands) else None


# ============================================================
# 4) المحاكاة التاريخية (Backtester)
# ============================================================

@dataclass
class Trade:
    side: str
    entry_time: pd.Timestamp
    entry: float
    sl: float
    tp1: float
    tp2: float
    exit_time: pd.Timestamp
    exit_price: float
    result_r: float     # العائد بوحدة المخاطرة (R)
    pnl_usd: float      # لكل 1 عقد ذهب (100 أونصة)


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)
    skipped: int = 0

    def metrics(self) -> dict:
        t = self.trades
        if not t:
            return {"trades": 0}
        wins = [x for x in t if x.result_r > 0]
        eq = np.cumsum([x.result_r for x in t])
        peak = np.maximum.accumulate(eq)
        dd = float((eq - peak).min())
        return {
            "trades": len(t),
            "win_rate": len(wins) / len(t),
            "avg_R": float(np.mean([x.result_r for x in t])),
            "total_R": float(eq[-1]),
            "max_dd_R": dd,
            "avg_usd": float(np.mean([x.pnl_usd for x in t])),
            "total_usd": float(np.sum([x.pnl_usd for x in t])),
        }


class Backtester:
    """
    - الإشارة على إغلاق الشمعة i، والدخول بسعر افتتاح الشمعة i+1
    - إذا لمَس الشمعة SL وTP1 معاً في نفس الشمعة نحتسب SL (محافظ)
    - الخروج عند TP1 فقط (محافظ). TP2 يُعرض كمستوى معلوماتي.
    """

    def __init__(self, df5: pd.DataFrame, df1h: pd.DataFrame,
                 params: EngineParams = None, warmup: int = 120):
        self.df5, self.df1h = df5, df1h
        self.eng = SMCEngineV2(params)
        self.warmup = warmup

    def run(self) -> BacktestResult:
        df5 = self.df5
        res = BacktestResult()
        pos: dict | None = None
        idx = df5.index

        for i in range(self.warmup, len(df5) - 1):
            bar = df5.iloc[i]
            t = idx[i]

            if pos:
                hit_sl = (bar["Low"] <= pos["sl"]) if pos["side"] == "LONG" else (bar["High"] >= pos["sl"])
                hit_tp = (bar["High"] >= pos["tp1"]) if pos["side"] == "LONG" else (bar["Low"] <= pos["tp1"])
                if hit_sl:
                    r = -1.0
                    exit_p = pos["sl"]
                elif hit_tp:
                    r = abs(pos["tp1"] - pos["entry"]) / abs(pos["entry"] - pos["sl"])
                    exit_p = pos["tp1"]
                else:
                    continue
                res.trades.append(Trade(
                    pos["side"], pos["entry_time"], pos["entry"], pos["sl"],
                    pos["tp1"], pos["tp2"], t, float(exit_p), r, r * 100 * abs(pos["entry"] - pos["sl"])))
                pos = None
                continue

            sig = self.eng.signal_at(df5.iloc[:i + 1],
                                     htf_completed(self.df1h, t), t)
            if sig is None:
                continue
            nxt = df5.iloc[i + 1]
            entry = float(nxt["Open"])
            # إعادة تثبيت المستويات على سعر الدخول الفعلي
            if sig.side == "LONG":
                if entry <= sig.sl or entry >= sig.tp1:
                    res.skipped += 1
                    continue
                risk = entry - sig.sl
                rr2 = (sig.tp2 - entry) / risk
                if rr2 < self.eng.p.rr_min:
                    res.skipped += 1
                    continue
            else:
                if entry >= sig.sl or entry <= sig.tp1:
                    res.skipped += 1
                    continue
                risk = sig.sl - entry
                rr2 = (entry - sig.tp2) / risk
                if rr2 < self.eng.p.rr_min:
                    res.skipped += 1
                    continue
            pos = {"side": sig.side, "entry_time": idx[i + 1], "entry": entry,
                   "sl": sig.sl, "tp1": sig.tp1, "tp2": sig.tp2}

        return res

    def plot(self, res: BacktestResult, out_png: str = "backtest_equity.png"):
        if not res.trades:
            print("لا توجد صفقات للرسم.")
            return
        eq = np.cumsum([x.result_r for x in res.trades])
        usd = np.cumsum([x.pnl_usd for x in res.trades])
        fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        ax[0].plot(eq, color="#2e86de", lw=1.5)
        ax[0].set_title("Equity Curve (R multiples)")
        ax[0].grid(alpha=0.3)
        ax[1].plot(usd, color="#27ae60", lw=1.5)
        ax[1].set_title("Cumulative PnL USD (1 contract = 100 oz)")
        ax[1].grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(out_png, dpi=130)
        print(f"تم حفظ منحنى الأداء: {out_png}")


# ============================================================
# 5) الوضع الحي (Live / Once)
# ============================================================

def run_once(symbol: str) -> dict:
    df5, df1 = load_data(symbol)
    t = df5.index[-1]
    eng = SMCEngineV2()
    sig = eng.signal_at(df5, htf_completed(df1, t), t)
    out = {
        "time_utc": str(t), "price": float(df5["Close"].iloc[-1]),
        "signal": None,
    }
    if sig:
        out["signal"] = {
            "side": sig.side, "entry_ref": round(sig.entry, 2),
            "sl": round(sig.sl, 2), "tp1": round(sig.tp1, 2),
            "tp2": round(sig.tp2, 2), "rr": round(sig.rr, 2),
            "reason": sig.reason,
        }
    return out


# ============================================================
# 6) التشغيل
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="SMC Engine v2")
    ap.add_argument("--symbol", default="GC=F")
    ap.add_argument("--period", default="3mo", help="فترة الـ backtest مثل 1mo / 3mo / 6mo")
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--plot", default="backtest_equity.png")
    args = ap.parse_args()

    params = EngineParams(rr_min=args.rr)

    if args.once:
        print(run_once(args.symbol))
        return

    if args.backtest:
        print(f"جلب البيانات {args.symbol} ...")
        df5, df1 = load_data(args.symbol)
        # تقييد فترة الـ backtest حسب طلب المستخدم (5M متاح ~60 يوم فقط)
        start = df5.index[-1] - pd.Timedelta(days={"1mo": 30, "3mo": 90, "6mo": 60}.get(args.period, 90))
        df5 = df5[df5.index >= start]
        bt = Backtester(df5, df1, params)
        res = bt.run()
        m = res.metrics()
        print("\n===== نتائج الـ Backtest =====")
        for k, v in m.items():
            if isinstance(v, float):
                print(f"{k:>10}: {v:,.2f}")
            else:
                print(f"{k:>10}: {v}")
        print(f"   skipped: {res.skipped}  (إشارات رُفضت بسبب فجوة الافتتاح أو RR)")
        bt.plot(res, args.plot)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
