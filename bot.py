import os
import time
import threading
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timezone, timedelta
from flask import Flask

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = "GC=F"

# معامل التصحيح الدقيق للسعر والقمة والقاع
PRICE_OFFSET = -27.5  

# دالة الحصول على وقت بغداد الحالي (UTC+3)
def get_baghdad_time():
    baghdad_tz = timezone(timedelta(hours=3))
    return datetime.now(baghdad_tz).strftime('%Y-%m-%d %H:%M')

class TrueInstitutionalEngine:
    def __init__(self, df_1h, df_5m):
        self.df_1h = df_1h
        self.df_5m = df_5m

    def get_market_bias(self):
        df_1h = self.df_1h
        df_5m = self.df_5m
        
        if len(df_1h) < 10 or len(df_5m) < 10:
            return "محايد", "محايد"

        close_1h = df_1h['Close']
        sma_1h = close_1h.rolling(window=5).mean().iloc[-1]
        
        if close_1h.iloc[-1] > sma_1h and close_1h.iloc[-1] > close_1h.iloc[-5]:
            bias_1h = "صاعد (Bullish 📈)"
        else:
            bias_1h = "هابط (Bearish 📉)"

        close_5m = df_5m['Close']
        sma_5m = close_5m.rolling(window=5).mean().iloc[-1]
        recent_trend = close_5m.iloc[-1] - close_5m.iloc[-6]
        
        if close_5m.iloc[-1] > sma_5m and recent_trend > 0:
            bias_5m = "صاعد (Bullish ⚡)"
        else:
            bias_5m = "هابط (Bearish ⚡)"
            
        return bias_1h, bias_5m

    def analyze_true_market_structure(self):
        df = self.df_5m
        if len(df) < 10:
            return "بيانات غير كافية", 0.0, 0, 0, 0, 0

        window = 2
        recent_highs = []
        recent_lows = []

        for i in range(window, len(df) - window):
            is_swing_high = True
            is_swing_low = True
            
            for j in range(1, window + 1):
                if df['High'].iloc[i] < df['High'].iloc[i-j] or df['High'].iloc[i] < df['High'].iloc[i+j]:
                    is_swing_high = False
                if df['Low'].iloc[i] > df['Low'].iloc[i-j] or df['Low'].iloc[i] > df['Low'].iloc[i+j]:
                    is_swing_low = False
                    
            if is_swing_high:
                recent_highs.append(df['High'].iloc[i])
            if is_swing_low:
                recent_lows.append(df['Low'].iloc[i])

        current_close = df['Close'].iloc[-1]
        
        erl_high = self.df_1h['High'].max()
        erl_low = self.df_1h['Low'].min()
        
        irl_high = df['High'].iloc[-5:].max()
        irl_low = df['Low'].iloc[-5:].min()

        structure_desc = "🔄 حركة داخلية ضمن النطاق (No Structural Break)"
        broken_level = 0.0

        if recent_highs:
            last_swing_high = recent_highs[-1]
            if current_close > last_swing_high:
                broken_level = float(last_swing_high)
                structure_desc = f"✅ كسر هيكل صاعد حقيقي (True BOS Bullish) فوق القمة: `{broken_level:.2f}`"

        if recent_lows and broken_level == 0.0:
            last_swing_low = recent_lows[-1]
            if current_close < last_swing_low:
                broken_level = float(last_swing_low)
                structure_desc = f"✅ كسر هيكل هابط حقيقي (True BOS Bearish) تحت القاع: `{broken_level:.2f}`"

        return structure_desc, broken_level, erl_high, erl_low, irl_high, irl_low

    def execute_precision_strategy(self):
        bias_1h, bias_5m = self.get_market_bias()
        structure_desc, broken_level, erl_high, erl_low, irl_high, irl_low = self.analyze_true_market_structure()
        
        current_price = float(self.df_5m['Close'].iloc[-1])
        equilibrium = (erl_high + erl_low) / 2
        
        zone = "Discount (منطقة خصم - مسموح الشراء 🟢)" if current_price < equilibrium else "Premium (منطقة تضخم - مسموح البيع 🔴)"
        
        signal = "⏳ مراقبة دقيقة لترتيب الاتجاه والسيولة..."
        trade_type = "غير محدد"
        tp1, tp2, suggested_sl = 0, 0, 0
        
        if "صاعد" in bias_1h and "صاعد" in bias_5m and "True BOS Bullish" in structure_desc:
            trade_type = "🟢 صفقة شراء (STRONG BUY)"
            signal = "إشارة شراء قناصة مؤكدة - توافق تام في الاتجاهين الصاعدين!"
            tp1 = irl_high if irl_high > current_price else current_price + 3.0
            tp2 = erl_high if erl_high > tp1 else tp1 + 5.0
            suggested_sl = (broken_level - 1.5) if (broken_level > 0 and broken_level < current_price) else current_price - 4.0
            
        elif "هابط" in bias_1h and "هابط" in bias_5m and "True BOS Bearish" in structure_desc:
            trade_type = "🔴 صفقة بيع (STRONG SELL)"
            signal = "إشارة بيع قناصة مؤكدة - توافق تام في الاتجاهين الهابطين!"
            tp1 = irl_low if irl_low < current_price else current_price - 3.0
            tp2 = erl_low if erl_low < tp1 else tp1 - 5.0
            suggested_sl = (broken_level + 1.5) if (broken_level > 0 and broken_level > current_price) else current_price + 4.0
            
        else:
            signal = f"👁️ الحالة التشغيلية: الاتجاه العام (1H: {bias_1h} | 5m: {bias_5m})"

        return {
            "price": current_price,
            "bias_1h": bias_1h,
            "bias_5m": bias_5m,
            "zone": zone,
            "structure": structure_desc,
            "trade_type": trade_type,
            "signal": signal,
            "tp1": tp1,
            "tp2": tp2,
            "sl": suggested_sl,
            "irl_high": irl_high,
            "irl_low": irl_low,
            "erl_high": erl_high,
            "erl_low": erl_low
        }

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except:
        return False

def get_multi_timeframe_data():
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        
        df_1h = yf.download(SYMBOL, period="5d", interval="1h", progress=False, session=session)
        df_5m = yf.download(SYMBOL, period="1d", interval="5m", progress=False, session=session)
        
        if df_1h.empty or df_5m.empty:
            return None, None, 0, "فشل جلب البيانات"

        for df in [df_1h, df_5m]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

        for df in [df_1h, df_5m]:
            df['Close'] = df['Close'] + PRICE_OFFSET
            df['Low'] = df['Low'] + PRICE_OFFSET
            df['High'] = (df['High'] + PRICE_OFFSET) - 3.0

        current_price = float(df_5m['Close'].iloc[-1])
        prev_price = float(df_5m['Close'].iloc[-2])
        change_pct = ((current_price - prev_price) / prev_price) * 100
        
        return df_1h, df_5m, change_pct, None
    except Exception as e:
        return None, None, 0, str(e)

def analyze_market_and_generate_report():
    df_1h, df_5m, change_pct, error = get_multi_timeframe_data()
    if error: return f"⚠️ خطأ: {error}"

    engine = TrueInstitutionalEngine(df_1h, df_5m)
    res = engine.execute_precision_strategy()
    
    targets_block = ""
    if res['tp1'] > 0:
        targets_block = f"""
🎯 *مستويات التنفيذ والمخاطرة:*
• 🛑 وقف الخسارة: `{res['sl']:.2f}` USD
• 🎯 الهدف الأول (TP1): `{res['tp1']:.2f}` USD
• 🚀 الهدف الثاني (TP2): `{res['tp2']:.2f}` USD"""

    report_text = f"""
🧠 *التقرير الدوري المفصل (كل 15 دقيقة)* 🧠
⏱ *الوقت (توقيت بغداد):* {get_baghdad_time()}

*📍 السعر الحالي:* `{res['price']:.2f}` USD ({change_pct:+.2f}%)
*📈 اتجاه فريم الساعة (1H Bias):* {res['bias_1h']}
*⚡ اتجاه فريم الـ 5 دقائق (5m Bias):* {res['bias_5m']}
*🗺 النطاق الاستراتيجي:* {res['zone']}
*📊 مستويات السيولة الرقمية:*
  - سيولة داخلية (IRL): [`{res['irl_low']:.2f}` - `{res['irl_high']:.2f}`]
  - سيولة خارجية (ERL): [`{res['erl_low']:.2f}` - `{res['erl_high']:.2f}`]
*⚡ تقييم الهيكل:* {res['structure']}
{targets_block}

*🚀 التوجيه التداولي الخوارزمي:*
{res['signal']}
-----------------------------------
"""
    return report_text

def smart_monitoring_loop():
    time.sleep(5)
    send_telegram_message(f"🚀 *تم تحديث تنسيق تنبيهات القناص وتوقيت بغداد بنجاح!* الوقت الحالي: {get_baghdad_time()}")
    
    last_signal_state = None
    last_report_time = 0

    while True:
        try:
            current_time = time.time()
            
            # تقرير دوري كل 15 دقيقة
            if current_time - last_report_time >= 900:
                report = analyze_market_and_generate_report()
                send_telegram_message(report)
                last_report_time = current_time

            # فحص فوري للتنبيهات الصارمة
            df_1h, df_5m, change_pct, error = get_multi_timeframe_data()
            if not error:
                engine = TrueInstitutionalEngine(df_1h, df_5m)
                res = engine.execute_precision_strategy()
                
                is_strong = "STRONG BUY" in res['trade_type'] or "STRONG SELL" in res['trade_type']
                if is_strong and res['trade_type'] != last_signal_state:
                    
                    # الرسالة بالتنسيق الدقيق المطلوب بالأرقام وتوقيت بغداد
                    instant_alert = f"""
🚨 *تنبيه دخول قناص فوري (Precision Alert)* 🚨
⏱ *الوقت (توقيت بغداد):* {get_baghdad_time()}

📌 *نوع الصفقة:* {res['trade_type']}
📍 *سعر الدخول:* `{res['price']:.2f}` USD
🛑 *وقف الخسارة:* `{res['sl']:.2f}` USD
🎯 *الهدف الأول (TP1):* `{res['tp1']:.2f}` USD
🚀 *الهدف الثاني (TP2):* `{res['tp2']:.2f}` USD
-----------------------------------
"""
                    send_telegram_message(instant_alert)
                    last_signal_state = res['trade_type']
                    
        except Exception as e:
            print(f"❌ خطأ في الحلقة: {e}")
            
        time.sleep(60)

@app.route("/")
def home():
    return "Baghdad Time Precision SMC Bot is Active!"

if __name__ == "__main__":
    monitor_thread = threading.Thread(target=smart_monitoring_loop, daemon=True)
    monitor_thread.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
