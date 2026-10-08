import os
import threading
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from flask import Flask

app = Flask(__name__)

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "7bf250b4b655456c805478936ebed10a")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOL = "XAU/USD"
TD_URL = "https://api.twelvedata.com/time_series"

SWING_W = 3
ATR_LEN = 14
ATR_SL_MULT = 1.5
ATR_TP_MULT = 2.0
RR_MIN = 1.5

def get_baghdad_time():
    baghdad_tz = timezone(timedelta(hours=3))
    return datetime.now(baghdad_tz)

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram Token or Chat ID missing.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print("Telegram Error:", e)
        return False

# ============================================================
# TWELVE DATA API FUNCTIONS
# ============================================================

def get_live_gold():
    try:
        params = {"symbol": SYMBOL, "apikey": TWELVE_DATA_API_KEY}
        r = requests.get("https://api.twelvedata.com/price", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if "price" not in data:
            return None
        return float(data["price"])
    except Exception as e:
        print("LIVE PRICE ERROR:", e)
        return None

def get_candles(interval, outputsize=300):
    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON"
    }
    r = requests.get(TD_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "values" not in data:
        raise ValueError(f"Twelve Data Error: {data}")
    
    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime").set_index("datetime")
    return df

def remove_incomplete_candle(df, minutes):
    if df.empty: return df
    now = pd.Timestamp.now(tz="UTC")
    last_time = df.index[-1]
    if (now - last_time).total_seconds() / 60 < minutes:
        df = df.iloc[:-1]
    return df

def calculate_atr(df, length=14):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(length).mean()

def confirmed_swings(df, w=3):
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    for i in range(w, len(df) - w):
        if h[i] > np.max(h[i-w:i]) and h[i] > np.max(h[i+1:i+w+1]):
            highs.append({"index": i, "time": df.index[i], "price": h[i]})
        if l[i] < np.min(l[i-w:i]) and l[i] < np.min(l[i+1:i+w+1]):
            lows.append({"index": i, "time": df.index[i], "price": l[i]})
    return highs, lows

def get_htf_bias(df):
    if len(df) < 30: return "NEUTRAL"
    highs, lows = confirmed_swings(df, SWING_W)
    if len(highs) < 2 or len(lows) < 2: return "NEUTRAL"
    
    if highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"]:
        return "BULLISH (صاعد 📈)"
    if highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"]:
        return "BEARISH (هابط 📉)"
    return "NEUTRAL (محايد)"

# ============================================================
# PERIODIC REPORT & MONITORING LOOP
# ============================================================

def generate_periodic_report():
    try:
        price = get_live_gold()
        df5 = remove_incomplete_candle(get_candles("5min", 200), 5)
        df1h = remove_incomplete_candle(get_candles("1h", 150), 60)
        
        bias_1h = get_htf_bias(df1h)
        bias_5m = get_htf_bias(df5)
        
        highs_1h, lows_1h = confirmed_swings(df1h, SWING_W)
        highs_5m, lows_5m = confirmed_swings(df5, SWING_W)
        
        erl_high = highs_1h[-1]["price"] if highs_1h else (price + 10 if price else 0)
        erl_low = lows_1h[-1]["price"] if lows_1h else (price - 10 if price else 0)
        
        irl_high = highs_5m[-1]["price"] if highs_5m else (price + 4 if price else 0)
        irl_low = lows_5m[-1]["price"] if lows_5m else (price - 4 if price else 0)
        
        proximity = "🎯 السعر قريب من السيولة الداخلية (IRL)" if price and abs(price - irl_high) < abs(price - erl_high) else "🚀 السعر متجه نحو السيولة الخارجية الكبرى (ERL)"

        report = f"""
📊 *التقرير المؤسسي الدوري (XAU/USD)* 📊
⏱ *الوقت (بغداد):* {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

*📍 السعر اللحظي:* `{price:.2f}` USD if price else "غير متوفر"

*📈 اتجاه الأطر الزمنية:*
• إطار الساعة (1H): {bias_1h}
• إطار الـ 5 دقائق (5M): {bias_5m}

*💧 مستويات السيولة بالأرقام:*
• السيولة الخارجية (ERL - 1H): `[{erl_low:.2f} — {erl_high:.2f}]`
• السيولة الداخلية (IRL - 5M): `[{irl_low:.2f} — {irl_high:.2f}]`

*🔍 حالة القُرب:*
{proximity}
-----------------------------------
"""
        send_telegram_message(report)
    except Exception as e:
        print("Periodic Report Error:", e)

def monitoring_loop():
    time.sleep(15)
    send_telegram_message(f"🚀 *تم تشغيل بوت SMC v4 (Twelve Data) مع التقارير الدورية* ⏱ {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}")
    
    last_report_time = 0

    while True:
        try:
            current_time = time.time()
            # إرسال تقرير دوري كل 15 دقيقة (900 ثانية)
            if current_time - last_report_time >= 900:
                generate_periodic_report()
                last_report_time = current_time

        except Exception as e:
            print("Monitoring loop error:", e)

        time.sleep(60)

@app.route("/")
def home():
    return "SMC Engine v4 Twelve Data Service is Running Live!"

if __name__ == "__main__":
    t = threading.Thread(target=monitoring_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
