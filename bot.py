import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
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
# SMC Engine v3 Core Logic
# ============================================================

def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    required = ["Open", "High", "Low", "Close", "Volume"]
    return df[required].dropna(subset=["Open", "High", "Low", "Close"])

def load_data(symbol: str, period_5m: str = "10d", period_1h: str = "60d"):
    import yfinance as yf
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    df5 = _flatten(yf.download(symbol, period=period_5m, interval="5m", progress=False, session=session, auto_adjust=False))
    df1 = _flatten(yf.download(symbol, period=period_1h, interval="1h", progress=False, session=session, auto_adjust=False))
    if df5.empty or df1.empty:
        raise RuntimeError("Failed to download market data.")
    for df in (df5, df1):
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
    return df5.sort_index(), df1.sort_index()

def htf_completed(df1h: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    return df1h[df1h.index + pd.Timedelta(hours=1) <= t]

def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()

def raw_swing_points(df: pd.DataFrame, w: int = 2):
    high, low = df["High"].values, df["Low"].values
    sh, sl = np.zeros(len(df), dtype=bool), np.zeros(len(df), dtype=bool)
    for i in range(w, len(df) - w):
        if high[i] == np.max(high[i - w:i + w + 1]): sh[i] = True
        if low[i] == np.min(low[i - w:i + w + 1]): sl[i] = True
    return pd.Series(sh, index=df.index), pd.Series(sl, index=df.index)

def confirmed_swing_indices(df: pd.DataFrame, w: int = 2, end_i: Optional[int] = None):
    sh, sl = raw_swing_points(df, w)
    if end_i is None: end_i = len(df) - 1
    max_pivot = end_i - w
    high_idx = [i for i in range(0, max_pivot + 1) if bool(sh.iloc[i])]
    low_idx = [i for i in range(0, max_pivot + 1) if bool(sl.iloc[i])]
    return high_idx, low_idx

def bullish_fvg(df: pd.DataFrame, i: int) -> bool:
    return i >= 2 and df["Low"].iloc[i] > df["High"].iloc[i - 2]

def bearish_fvg(df: pd.DataFrame, i: int) -> bool:
    return i >= 2 and df["High"].iloc[i] < df["Low"].iloc[i - 2]

@dataclass
class EngineParams:
    swing_w: int = 2
    atr_n: int = 14
    sweep_arm: int = 12
    disp_mult: float = 1.2
    sl_atr_mult: float = 0.30
    rr_min: float = 1.50
    bias_lookback: int = 5
    htf_swing_w: int = 3
    tp1_atr_fallback: float = 1.5
    tp2_atr_fallback: float = 1.0

@dataclass
class Signal:
    side: str
    signal_time: pd.Timestamp
    entry_ref: float
    sl: float
    tp1: float
    tp2: float
    rr_tp1: float
    rr_tp2: float
    atr_value: float
    sweep_price: float
    bos_level: float
    reason: str

class SMCEngineV3:
    def __init__(self, params: Optional[EngineParams] = None):
        self.p = params or EngineParams()

    def _htf_bias(self, df1h_c: pd.DataFrame) -> Optional[str]:
        p = self.p
        if len(df1h_c) < p.bias_lookback + 2: return None
        close = df1h_c["Close"]
        if close.iloc[-1] > close.iloc[-1 - p.bias_lookback]: return "BULL"
        if close.iloc[-1] < close.iloc[-1 - p.bias_lookback]: return "BEAR"
        return None

    def _last_confirmed_low(self, df5: pd.DataFrame, before_i: int) -> Optional[tuple]:
        _, lows = confirmed_swing_indices(df5, self.p.swing_w, before_i)
        lows = [x for x in lows if x < before_i]
        if not lows: return None
        k = lows[-1]
        return (k, float(df5["Low"].iloc[k]))

    def _last_confirmed_high(self, df5: pd.DataFrame, before_i: int) -> Optional[tuple]:
        highs, _ = confirmed_swing_indices(df5, self.p.swing_w, before_i)
        highs = [x for x in highs if x < before_i]
        if not highs: return None
        k = highs[-1]
        return (k, float(df5["High"].iloc[k]))

    def _find_sweep_low(self, df5: pd.DataFrame, i: int) -> Optional[tuple]:
        start = max(10, i - self.p.sweep_arm)
        for j in range(i, start - 1, -1):
            swing = self._last_confirmed_low(df5, j)
            if swing is None: continue
            k, liquidity = swing
            if float(df5["Low"].iloc[j]) < liquidity and float(df5["Close"].iloc[j]) > liquidity:
                return (j, k, liquidity, float(df5["Low"].iloc[j]))
        return None

    def _find_sweep_high(self, df5: pd.DataFrame, i: int) -> Optional[tuple]:
        start = max(10, i - self.p.sweep_arm)
        for j in range(i, start - 1, -1):
            swing = self._last_confirmed_high(df5, j)
            if swing is None: continue
            k, liquidity = swing
            if float(df5["High"].iloc[j]) > liquidity and float(df5["Close"].iloc[j]) < liquidity:
                return (j, k, liquidity, float(df5["High"].iloc[j]))
        return None

    def _bullish_bos(self, df5: pd.DataFrame, sweep_index: int, i: int, atr_value: float) -> Optional[float]:
        p = self.p
        highs, _ = confirmed_swing_indices(df5, p.swing_w, i)
        candidates = [x for x in highs if sweep_index < x < i]
        if not candidates: return None
        bos_index = candidates[-1]
        bos_level = float(df5["High"].iloc[bos_index])
        close, open_price = float(df5["Close"].iloc[i]), float(df5["Open"].iloc[i])
        body = abs(close - open_price)
        if close <= bos_level or close <= open_price or body < p.disp_mult * atr_value: return None
        if not (bullish_fvg(df5, i) or bullish_fvg(df5, i - 1)): return None
        return bos_level

    def _bearish_bos(self, df5: pd.DataFrame, sweep_index: int, i: int, atr_value: float) -> Optional[float]:
        p = self.p
        _, lows = confirmed_swing_indices(df5, p.swing_w, i)
        candidates = [x for x in lows if sweep_index < x < i]
        if not candidates: return None
        bos_index = candidates[-1]
        bos_level = float(df5["Low"].iloc[bos_index])
        close, open_price = float(df5["Close"].iloc[i]), float(df5["Open"].iloc[i])
        body = abs(close - open_price)
        if close >= bos_level or close >= open_price or body < p.disp_mult * atr_value: return None
        if not (bearish_fvg(df5, i) or bearish_fvg(df5, i - 1)): return None
        return bos_level

    def _tp1_long(self, df5: pd.DataFrame, i: int, entry: float, atr_value: float) -> float:
        highs, _ = confirmed_swing_indices(df5, self.p.swing_w, i)
        candidates = [float(df5["High"].iloc[x]) for x in highs if x < i and float(df5["High"].iloc[x]) > entry]
        return min(candidates) if candidates else entry + self.p.tp1_atr_fallback * atr_value

    def _tp1_short(self, df5: pd.DataFrame, i: int, entry: float, atr_value: float) -> float:
        _, lows = confirmed_swing_indices(df5, self.p.swing_w, i)
        candidates = [float(df5["Low"].iloc[x]) for x in lows if x < i and float(df5["Low"].iloc[x]) < entry]
        return max(candidates) if candidates else entry - self.p.tp1_atr_fallback * atr_value

    def _erl(self, df1h_c: pd.DataFrame, ref: float, side: str) -> Optional[float]:
        if len(df1h_c) < 20: return None
        p = self.p
        highs, lows = confirmed_swing_indices(df1h_c, p.htf_swing_w, len(df1h_c) - 1)
        if side == "LONG":
            candidates = [float(df1h_c["High"].iloc[x]) for x in highs if x < len(df1h_c) - 1 and float(df1h_c["High"].iloc[x]) > ref]
            return min(candidates) if candidates else None
        else:
            candidates = [float(df1h_c["Low"].iloc[x]) for x in lows if x < len(df1h_c) - 1 and float(df1h_c["Low"].iloc[x]) < ref]
            return max(candidates) if candidates else None

    def signal_at(self, df5_slice: pd.DataFrame, df1h_c: pd.DataFrame, t: pd.Timestamp) -> Optional[Signal]:
        p = self.p
        minimum = p.sweep_arm + p.swing_w * 2 + p.atr_n + 20
        if len(df5_slice) < minimum: return None
        i = len(df5_slice) - 1
        atr_value = float(atr(df5_slice, p.atr_n).iloc[i])
        if not np.isfinite(atr_value) or atr_value <= 0: return None
        bias = self._htf_bias(df1h_c)
        if bias is None: return None
        entry = float(df5_slice["Close"].iloc[i])

        if bias == "BULL":
            sweep = self._find_sweep_low(df5_slice, i)
            if sweep is not None:
                sweep_index, sweep_price = sweep[0], sweep[3]
                bos = self._bullish_bos(df5_slice, sweep_index, i, atr_value)
                if bos is not None:
                    sl = sweep_price - p.sl_atr_mult * atr_value
                    tp1 = self._tp1_long(df5_slice, i, entry, atr_value)
                    erl = self._erl(df1h_c, entry, "LONG")
                    tp2 = erl if (erl is not None and erl > tp1) else tp1 + p.tp2_atr_fallback * atr_value
                    risk = entry - sl
                    if risk <= 0: return None
                    rr1, rr2 = (tp1 - entry) / risk, (tp2 - entry) / risk
                    if rr2 < p.rr_min: return None
                    return Signal("LONG", t, entry, sl, tp1, tp2, rr1, rr2, atr_value, sweep_price, bos, "Bullish Sweep + BOS + Disp + FVG + HTF Bull")

        if bias == "BEAR":
            sweep = self._find_sweep_high(df5_slice, i)
            if sweep is not None:
                sweep_index, sweep_price = sweep[0], sweep[3]
                bos = self._bearish_bos(df5_slice, sweep_index, i, atr_value)
                if bos is not None:
                    sl = sweep_price + p.sl_atr_mult * atr_value
                    tp1 = self._tp1_short(df5_slice, i, entry, atr_value)
                    erl = self._erl(df1h_c, entry, "SHORT")
                    tp2 = erl if (erl is not None and erl < tp1) else tp1 - p.tp2_atr_fallback * atr_value
                    risk = sl - entry
                    if risk <= 0: return None
                    rr1, rr2 = (entry - tp1) / risk, (entry - tp2) / risk
                    if rr2 < p.rr_min: return None
                    return Signal("SHORT", t, entry, sl, tp1, tp2, rr1, rr2, atr_value, sweep_price, bos, "Bearish Sweep + BOS + Disp + FVG + HTF Bear")
        return None

def get_last_closed_5m(df5: pd.DataFrame) -> tuple:
    if len(df5) < 3: raise RuntimeError("Not enough 5M candles.")
    now = pd.Timestamp.now(tz="UTC")
    last_index = df5.index[-1]
    estimated_close = last_index + pd.Timedelta(minutes=5)
    if estimated_close <= now: return df5, last_index
    closed = df5.iloc[:-1]
    return closed, closed.index[-1]

# ============================================================
# Flask & Telegram Monitoring Loop
# ============================================================

def monitoring_loop():
    time.sleep(15)
    send_telegram_message(f"🚀 *تم تشغيل بوت القناص المؤسسي (SMC v3 Live)* ⏱ {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}")
    
    last_signal_time = None

    while True:
        try:
            df5, df1 = load_data(SYMBOL)
            df5_closed, t = get_last_closed_5m(df5)
            htf = htf_completed(df1, t)
            
            engine = SMCEngineV3()
            sig = engine.signal_at(df5_closed, htf, t)

            if sig and sig.signal_time != last_signal_time:
                side_emoji = "🟢 شـراء (LONG)" if sig.side == "LONG" else "🔴 بيع (SHORT)"
                alert_msg = f"""
🚨 *تنبيه قناص مؤسسي جديد (SMC v3)* 🚨
⏱ *الوقت:* {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

📌 *الاتجاه:* {side_emoji}
📍 *سعر الدخول المرجعي:* `{sig.entry_ref:.2f}` USD
🛑 *وقف الخسارة (SL):* `{sig.sl:.2f}` USD
🎯 *الهدف الأول (TP1):* `{sig.tp1:.2f}` USD
🚀 *الهدف الثاني (TP2):* `{sig.tp2:.2f}` USD
⚖️ *العائد للمخاطرة (RR):* `{sig.rr_tp2:.2f}`
📝 *السبب:* {sig.reason}
-----------------------------------
"""
                send_telegram_message(alert_msg)
                last_signal_time = sig.signal_time

        except Exception as e:
            print(f"Monitoring loop error: {e}")

        time.sleep(180)

@app.route("/")
def home():
    return "SMC Engine v3 Web Service is Running Live!"

if __name__ == "__main__":
    t = threading.Thread(target=monitoring_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
