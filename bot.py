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
TD_URL = "https://api.twelvedata.com"

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
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print("Telegram API Error Response:", response.text)
        return response.status_code == 200
    except Exception as e:
        print("Telegram Error:", e)
        return False

# ============================================================
# ADVANCED TWELVE DATA API WRAPPER WITH ERROR INSPECTION
# ============================================================

def call_twelve_data(endpoint, params):
    url = f"{TD_URL}/{endpoint}"
    params["apikey"] = TWELVE_DATA_API_KEY
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 429:
                print(f"Rate limit hit (429) on {endpoint}. Sleeping for 30s...")
                time.sleep(30)
                continue
            r.raise_for_status()
            data = r.json()
            
            # التحقق إذا أرجع الموقع خطأ داخلي (مثل تجاوز الحد)
            if isinstance(data, dict) and (data.get("status") == "error" or "code" in data and data["code"] != 200):
                err_msg = data.get("message", "Unknown API Error")
                print(f"Twelve Data Internal Error: {err_msg}")
                if data.get("code") == 429:
                    time.sleep(30)
                    continue
                return data
            return data
        except Exception as e:
            print(f"API Connection Error on {endpoint}: {e}")
            time.sleep(10)
    return None

def get_live_gold():
    try:
        data = call_twelve_data("price", {"symbol": SYMBOL})
        if not data or not isinstance(data, dict) or "price" not in data:
            return None
        return float(data["price"])
    except Exception as e:
        print("LIVE PRICE ERROR:", e)
        return None

def get_candles(interval, outputsize=300):
    data = call_twelve_data("time_series", {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": outputsize,
        "format": "JSON"
    })
    if not data or not isinstance(data, dict):
        raise ValueError("فشل الاتصال بمزود البيانات أو انتهت مهلة الطلب.")
    
    if "values" not in data:
        err_msg = data.get("message", "تم تجاوز الحد المسموح أو خطأ في الـ API")
        raise ValueError(f"Twelve Data Error: {err_msg}")
    
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

# ============================================================
# SMART BIAS FUNCTION (1H & 5M) WITH MOMENTUM & BREAKOUT CHECK
# ============================================================

def get_smart_bias(df):
    if len(df) < 30: return "NEUTRAL (محايد)"
    
    highs, lows = confirmed_swings(df, SWING_W)
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"]:
            return "BULLISH (صاعد)"
        if highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"]:
            return "BEARISH (هابط)"
            
    recent_slice = df.iloc[-25:-1]
    if not recent_slice.empty:
        recent_high = recent_slice["high"].max()
        recent_low = recent_slice["low"].min()
        curr = df.iloc[-1]
        
        atr_series = calculate_atr(df, ATR_LEN)
        atr_val = float(atr_series.iloc[-1]) if not atr_series.empty and not pd.isna(atr_series.iloc[-1]) else 2.0
        body = abs(curr["close"] - curr["open"])
        
        if curr["close"] > recent_high and body >= (atr_val * 1.0):
            return "BULLISH (صاعد)"
        if curr["close"] < recent_low and body >= (atr_val * 1.0):
            return "BEARISH (هابط)"

    return "NEUTRAL (محايد)"

def detect_fvg(df):
    if len(df) < 3: return None
    a, b, c = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if c["low"] > a["high"]:
        return {"type": "BULLISH", "low": a["high"], "high": c["low"]}
    if c["high"] < a["low"]:
        return {"type": "BEARISH", "low": c["high"], "high": a["low"]}
    return None

def has_displacement(df, atr_value):
    if len(df) < 20 or pd.isna(atr_value): return False
    candle = df.iloc[-1]
    body = abs(candle["close"] - candle["open"])
    return body >= (atr_value * 1.2)

# ============================================================
# ORDER BLOCK DETECTION (OB)
# ============================================================

def detect_order_block(df, atr_value):
    if len(df) < 10 or pd.isna(atr_value) or atr_value <= 0:
        return None
    i = len(df) - 1
    curr = df.iloc[i]
    body = abs(curr["close"] - curr["open"])
    if body < (atr_value * 1.2):
        return None

    if curr["close"] > curr["open"]:
        for j in range(i - 1, max(0, i - 6), -1):
            c = df.iloc[j]
            if c["close"] < c["open"]:
                return {
                    "type": "BULLISH_OB",
                    "low": float(c["low"]),
                    "high": float(c["high"])
                }
    elif curr["close"] < curr["open"]:
        for j in range(i - 1, max(0, i - 6), -1):
            c = df.iloc[j]
            if c["close"] > c["open"]:
                return {
                    "type": "BEARISH_OB",
                    "low": float(c["low"]),
                    "high": float(c["high"])
                }
    return None

def detect_sweep(df, highs, lows):
    if len(df) < 2: return None
    candle = df.iloc[-1]
    high, low, close = candle["high"], candle["low"], candle["close"]
    recent_low = lows[-1]["price"] if lows else None
    recent_high = highs[-1]["price"] if highs else None

    if recent_low is not None and low < recent_low and close > recent_low:
        return {"type": "SELL_SIDE", "level": recent_low}
    if recent_high is not None and high > recent_high and close < recent_high:
        return {"type": "BUY_SIDE", "level": recent_high}
    return None

def detect_bos_or_cisd(df, highs, lows, sweep):
    if sweep is None: return None
    close = df.iloc[-1]["close"]
    if sweep["type"] == "SELL_SIDE" and highs:
        structure_high = highs[-1]["price"]
        if close > structure_high:
            return {"direction": "LONG", "level": structure_high}
    if sweep["type"] == "BUY_SIDE" and lows:
        structure_low = lows[-1]["price"]
        if close < structure_low:
            return {"direction": "SHORT", "level": structure_low}
    return None

def generate_signal(df5, df1h, htf_bias):
    if len(df5) < 50 or len(df1h) < 30: return None
    highs_5m, lows_5m = confirmed_swings(df5, SWING_W)
    highs_1h, lows_1h = confirmed_swings(df1h, SWING_W)
    
    sweep = detect_sweep(df5, highs_5m, lows_5m)
    if sweep is None: return None
    
    bos = detect_bos_or_cisd(df5, highs_5m, lows_5m, sweep)
    if bos is None: return None

    atr_series = calculate_atr(df5, ATR_LEN)
    atr_value = float(atr_series.iloc[-1])
    if pd.isna(atr_value) or not has_displacement(df5, atr_value):
        return None

    direction = bos["direction"]
    if direction == "LONG" and "BULLISH" not in htf_bias: return None
    if direction == "SHORT" and "BEARISH" not in htf_bias: return None

    entry = float(df5.iloc[-1]["close"])
    
    if direction == "LONG":
        sl = entry - atr_value * ATR_SL_MULT
        tp1 = entry + atr_value * ATR_TP_MULT
        erl_highs = [h["price"] for h in highs_1h if h["price"] > entry] if highs_1h else []
        tp2 = min(erl_highs) if erl_highs else (entry + atr_value * ATR_TP_MULT * 2)
    else:
        sl = entry + atr_value * ATR_SL_MULT
        tp1 = entry - atr_value * ATR_TP_MULT
        erl_lows = [l["price"] for l in lows_1h if l["price"] < entry] if lows_1h else []
        tp2 = max(erl_lows) if erl_lows else (entry - atr_value * ATR_TP_MULT * 2)

    risk = abs(entry - sl)
    reward = abs(tp2 - entry)
    rr = (reward / risk) if risk > 0 else 0
    if rr < RR_MIN: return None

    ob = detect_order_block(df5, atr_value)
    fvg = detect_fvg(df5)

    return {
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
        "atr": atr_value,
        "sweep": sweep["type"],
        "sweep_level": sweep["level"],
        "bos": bos["level"],
        "ob": ob,
        "fvg": fvg
    }

# ============================================================
# PERIODIC REPORT & MONITORING LOOP (24/7)
# ============================================================

def generate_periodic_report():
    try:
        price = get_live_gold()
        if price is None:
            price = 0.0
        time.sleep(8)
            
        df5 = remove_incomplete_candle(get_candles("5min", 200), 5)
        time.sleep(8)
        
        df1h = remove_incomplete_candle(get_candles("1h", 150), 60)
        
        bias_1h = get_smart_bias(df1h)
        bias_5m = get_smart_bias(df5)
        
        highs_1h, lows_1h = confirmed_swings(df1h, SWING_W)
        highs_5m, lows_5m = confirmed_swings(df5, SWING_W)
        
        erl_high = highs_1h[-1]["price"] if highs_1h else (price + 10 if price else 0)
        erl_low = lows_1h[-1]["price"] if lows_1h else (price - 10 if price else 0)
        
        irl_high = highs_5m[-1]["price"] if highs_5m else (price + 4 if price else 0)
        irl_low = lows_5m[-1]["price"] if lows_5m else (price - 4 if price else 0)
        
        if price > 0:
            dist_erl = min(abs(price - erl_high), abs(price - erl_low))
            dist_irl = min(abs(price - irl_high), abs(price - irl_low))
            proximity = "السعر قريب من السيولة الداخلية (IRL)" if dist_irl <= dist_erl else "السعر متجه نحو السيولة الخارجية الكبرى (ERL)"
        else:
            proximity = "غير متوفر"

        report = f"""
[التقرير المؤسسي الدوري XAU/USD]
الوقت (بغداد): {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

السعر الفوري اللحظي: {price:.2f} USD

انحياز الأطر الزمنية (الذكي):
- إطار الساعة (1H): {bias_1h}
- إطار الـ 5 دقائق (5M): {bias_5m}

مستويات السيولة بالأرقام:
- السيولة الخارجية (ERL - 1H): [قاع: {erl_low:.2f} -- قمة: {erl_high:.2f}]
- السيولة الداخلية (IRL - 5M): [قاع: {irl_low:.2f} -- قمة: {irl_high:.2f}]

حالة القُرب:
{proximity}
-----------------------------------
"""
        send_telegram_message(report)
    except Exception as e:
        error_msg = f"خطأ في إنشاء التقرير الدوري: {str(e)}"
        print("Periodic Report Error:", e)
        send_telegram_message(error_msg)

def monitoring_loop():
    time.sleep(5)
    send_telegram_message("تم تشغيل بوت SMC v4 (حماية واكتشاف أخطاء Twelve Data مفعلة)")
    
    print("Sending instant startup report with safe delays...")
    generate_periodic_report()
    
    last_report_time = time.time()
    last_signal_time = None

    while True:
        try:
            current_time = time.time()
            if current_time - last_report_time >= 900:
                generate_periodic_report()
                last_report_time = current_time

            df5 = remove_incomplete_candle(get_candles("5min", 200), 5)
            time.sleep(8)
            df1h = remove_incomplete_candle(get_candles("1h", 150), 60)
            htf_bias = get_smart_bias(df1h)
            
            signal = generate_signal(df5, df1h, htf_bias)
            if signal:
                sig_key = (signal["direction"], round(signal["entry"], 2))
                if sig_key != last_signal_time:
                    ob_info = f"{signal['ob']['type']} [{signal['ob']['low']:.2f} - {signal['ob']['high']:.2f}]" if signal["ob"] else "غير متوفر"
                    alert_msg = f"""
[تنبيه Model #1 - أحدث السيولة ERL]
الوقت: {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

الاتجاه: {signal['direction']}
الدخول: {signal['entry']:.2f}
وقف الخسارة: {signal['sl']:.2f}
الهدف الأول: {signal['tp1']:.2f}
الهدف الثاني (سيولة ERL): {signal['tp2']:.2f}
العائد للمخاطرة: 1:{signal['rr']:.2f}
الأوردر بلوك: {ob_info}
-----------------------------------
"""
                    send_telegram_message(alert_msg)
                    last_signal_time = sig_key

        except Exception as e:
            print("Monitoring loop error:", e)

        time.sleep(150)

@app.route("/")
def home():
    return "SMC Advanced Error-Handled Engine is Running Live!"

if __name__ == "__main__":
    t = threading.Thread(target=monitoring_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
