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
# SMC Engine & Multi-TF Data Loader
# ============================================================

def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    required = ["Open", "High", "Low", "Close", "Volume"]
    return df[required].dropna(subset=["Open", "High", "Low", "Close"])

def load_data(symbol: str):
    import yfinance as yf
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    
    df5 = _flatten(yf.download(symbol, period="10d", interval="5m", progress=False, session=session, auto_adjust=False))
    df15 = _flatten(yf.download(symbol, period="20d", interval="15m", progress=False, session=session, auto_adjust=False))
    df1 = _flatten(yf.download(symbol, period="60d", interval="1h", progress=False, session=session, auto_adjust=False))
    
    if df5.empty or df15.empty or df1.empty:
        raise RuntimeError("Failed to download multi-tf market data.")
        
    for df in (df5, df15, df1):
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
            
    return df5.sort_index(), df15.sort_index(), df1.sort_index()

def htf_completed(df: pd.DataFrame, t: pd.Timestamp, tf_hours=1) -> pd.DataFrame:
    return df[df.index + pd.Timedelta(hours=tf_hours) <= t]

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

def get_market_bias(df: pd.DataFrame, lookback=5) -> str:
    if len(df) < lookback + 2: return "محايد"
    c = df["Close"]
    return "صاعد (BULL 📈)" if c.iloc[-1] > c.iloc[-1 - lookback] else "هابط (BEAR 📉)"

def extract_liquidity_levels(df5: pd.DataFrame, df1h: pd.DataFrame):
    ref_price = float(df5["Close"].iloc[-1])
    
    # 1. السيولة الخارجية (ERL) من الإطار العالي 1H
    h_idx_1h, l_idx_1h = confirmed_swing_indices(df1h, w=3)
    erl_highs = [float(df1h["High"].iloc[i]) for i in h_idx_1h if float(df1h["High"].iloc[i]) > ref_price]
    erl_lows = [float(df1h["Low"].iloc[i]) for i in l_idx_1h if float(df1h["Low"].iloc[i]) < ref_price]
    
    erl_h = min(erl_highs) if erl_highs else ref_price + 10.0
    erl_l = max(erl_lows) if erl_lows else ref_price - 10.0

    # 2. السيولة الداخلية (IRL) من الإطار الصغير 5M (محصورة داخل النطاق)
    h_idx_5m, l_idx_5m = confirmed_swing_indices(df5, w=2)
    irl_highs = [float(df5["High"].iloc[i]) for i in h_idx_5m if ref_price < float(df5["High"].iloc[i]) < erl_h]
    irl_lows = [float(df5["Low"].iloc[i]) for i in l_idx_5m if erl_l < float(df5["Low"].iloc[i]) < ref_price]
    
    irl_h = min(irl_highs) if irl_highs else ref_price + 3.0
    irl_l = max(irl_lows) if irl_lows else ref_price - 3.0

    # تحديد قُرب السعر
    dist_to_erl = min(abs(ref_price - erl_h), abs(ref_price - erl_l))
    dist_to_irl = min(abs(ref_price - irl_h), abs(ref_price - irl_l))
    
    proximity = "🎯 قريب جداً من السيولة الداخلية (IRL)" if dist_to_irl <= dist_to_erl else "🚀 قريب من مستويات السيولة الخارجية الكبرى (ERL)"

    return {
        "erl_high": erl_h, "erl_low": erl_l,
        "irl_high": irl_h, "irl_low": irl_l,
        "proximity": proximity
    }

def generate_periodic_report():
    try:
        df5, df15, df1 = load_data(SYMBOL)
        ref_price = float(df5["Close"].iloc[-1])
        
        bias_1h = get_market_bias(df1, lookback=5)
        bias_15m = get_market_bias(df15, lookback=5)
        bias_5m = get_market_bias(df5, lookback=5)
        
        liq = extract_liquidity_levels(df5, df1)
        
        report = f"""
📊 *التقرير المؤسسي الدوري للذهب (كل 15 دقيقة)* 📊
⏱ *الوقت (بغداد):* {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

*📍 السعر الفوري اللحظي:* `{ref_price:.2f}` USD

*📈 انحياز الأطر الزمنية:*
• إطار الساعة (1H): {bias_1h}
• إطار الربع ساعة (15M): {bias_15m}
• إطار الـ 5 دقائق (5M): {bias_5m}

*💧 مستويات السيولة بالأرقام:*
• السيولة الخارجية (ERL): `[قاع: {liq['erl_low']:.2f} — قمة: {liq['erl_high']:.2f}]`
• السيولة الداخلية (IRL): `[قاع: {liq['irl_low']:.2f} — قمة: {liq['irl_high']:.2f}]`

*🔍 حالة التمركز والقُرب:*
{liq['proximity']}
-----------------------------------
"""
        send_telegram_message(report)
    except Exception as e:
        print(f"Report generation error: {e}")

# ============================================================
# Monitoring Loop (Periodic Reports + Signal Alerts)
# ============================================================

def monitoring_loop():
    time.sleep(15)
    send_telegram_message(f"🚀 *تم تشغيل بوت القناص المؤسسي (مع التقارير الدورية كل 15 دقيقة)* ⏱ {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}")
    
    last_report_time = 0
    last_signal_time = None

    while True:
        try:
            current_time = time.time()
            # إرسال تقرير كل 15 دقيقة (900 ثانية)
            if current_time - last_report_time >= 900:
                generate_periodic_report()
                last_report_time = current_time

            # فحص الإشارات اللحظية
            df5, df15, df1 = load_data(SYMBOL)
            # (يمكن إضافة محرك الإشارات هنا أو الفحص المباشر)

        except Exception as e:
            print(f"Monitoring loop error: {e}")

        time.sleep(60) # فحص الحلقات كل دقيقة

@app.route("/")
def home():
    return "SMC Engine v3 Periodic Reporting Service is Running!"

if __name__ == "__main__":
    t = threading.Thread(target=monitoring_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
