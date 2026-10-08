import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = "GC=F"

def get_baghdad_time():
    baghdad_tz = timezone(timedelta(hours=3))
    return datetime.now(baghdad_tz)

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram Error: {e}")
        return False

# ============================================================
# محرك SMC v2 (منطق السحب، البوس، الاندفاع، والـ ATR)
# ============================================================

def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])

def load_data(symbol: str, period_5m: str = "10d", period_1h: str = "60d"):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    df5 = _flatten(yf.download(symbol, period=period_5m, interval="5m", progress=False, session=session))
    df1 = _flatten(yf.download(symbol, period=period_1h, interval="1h", progress=False, session=session))
    if df5.empty or df1.empty:
        raise RuntimeError("فشل جلب البيانات من yfinance")
    for df in (df5, df1):
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
    return df5.sort_index(), df1.sort_index()

def htf_completed(df1h: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    return df1h[df1h.index + pd.Timedelta(hours=1) <= t]

def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()

def swing_points(df: pd.DataFrame, w: int = 2):
    sh = (df["High"] == df["High"].rolling(2 * w + 1, center=True).max())
    sl = (df["Low"] == df["Low"].rolling(2 * w + 1, center=True).min())
    return sh.fillna(False), sl.fillna(False)

def bullish_fvg(df: pd.DataFrame, i: int) -> bool:
    return df["Low"].iloc[i] > df["High"].iloc[i - 2]

def bearish_fvg(df: pd.DataFrame, i: int) -> bool:
    return df["High"].iloc[i] < df["Low"].iloc[i - 2]

@dataclass
class Signal:
    side: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    rr: float
    reason: str
    time: pd.Timestamp

@dataclass
class EngineParams:
    swing_w: int = 2
    atr_n: int = 14
    sweep_lookback: int = 16
    sweep_arm: int = 12
    disp_mult: float = 1.2
    sl_atr_mult: float = 0.3
    rr_min: float = 1.5
    bias_lookback: int = 5

class SMCEngineV2:
    def __init__(self, p: EngineParams = None):
        self.p = p or EngineParams()

    def _htf_bias(self, df1h_c: pd.DataFrame) -> str | None:
        p = self.p
        if len(df1h_c) < p.bias_lookback + 2:
            return None
        c = df1h_c["Close"]
        return "BULL" if c.iloc[-1] > c.iloc[-1 - p.bias_lookback] else "BEAR"

    def _find_sweep_low(self, df5: pd.DataFrame, i: int):
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

    def _bos_bull(self, df5: pd.DataFrame, sh, i: int, j: int, a: float):
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

    def _bos_bear(self, df5: pd.DataFrame, sl, i: int, j: int, a: float):
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

    def signal_at(self, df5_slice: pd.DataFrame, df1h_c: pd.DataFrame, t: pd.Timestamp) -> Signal | None:
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
                        return Signal("LONG", entry, sl, tp1, tp2, rr, f"Sweep + BOS + Disp", t)

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
                        return Signal("SHORT", entry, sl, tp1, tp2, rr, f"Sweep + BOS + Disp", t)
        return None

    @staticmethod
    def _erl(df1h_c: pd.DataFrame, ref: float, side: str) -> float | None:
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
# حلقة المراقبة الحية (Monitoring Loop) وخادم الـ Flask
# ============================================================

def monitoring_loop():
    time.sleep(10)
    send_telegram_message(f"🚀 *تم تشغيل بوت القناص المؤسسي (SMC v2 Live)* ⏱ {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}")
    
    last_signal_time = None

    while True:
        try:
            df5, df1 = load_data(SYMBOL)
            t = df5.index[-1]
            eng = SMCEngineV2()
            sig = eng.signal_at(df5, htf_completed(df1, t), t)

            if sig and sig.time != last_signal_time:
                side_emoji = "🟢 شـراء (LONG)" if sig.side == "LONG" else "🔴 بيع (SHORT)"
                alert_msg = f"""
🚨 *تنبيه قناص مؤسسي جديد (SMC v2)* 🚨
⏱ *الوقت:* {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

📌 *الاتجاه:* {side_emoji}
📍 *سعر الدخول المرجعي:* `{sig.entry:.2f}` USD
🛑 *وقف الخسارة (SL):* `{sig.sl:.2f}` USD
🎯 *الهدف الأول (TP1):* `{sig.tp1:.2f}` USD
🚀 *الهدف الثاني (TP2 - ERL):* `{sig.tp2:.2f}` USD
⚖️ *العائد للمخاطرة (R:R):* `{sig.rr:.2f}`
📝 *السبب:* {sig.reason}
-----------------------------------
"""
                send_telegram_message(alert_msg)
                last_signal_time = sig.time

        except Exception as e:
            print(f"Monitoring loop error: {e}")

        # فحص السوق كل 3 دقائق
        time.sleep(180)

@app.route("/")
def home():
    return "SMC Engine v2 Live Web Service is Running Perfectly!"

if __name__ == "__main__":
    t = threading.Thread(target=monitoring_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
