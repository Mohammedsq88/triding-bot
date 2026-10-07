import os
import time
import threading
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from flask import Flask

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = "GC=F"

# معامل التصحيح الدقيق للسعر والقمة والقاع
PRICE_OFFSET = -27.5  

class PrecisionInstitutionalEngine:
    def __init__(self, df_1h, df_5m):
        self.df_1h = df_1h
        self.df_5m = df_5m

    def get_market_bias(self):
        """الطلب الأول: تحديد اتجاه السعر على فريم الساعة وفريم الـ 5 دقائق"""
        # اتجاه فريم الساعة (1H) بناءً على آخر إغلاقات و متوسط الحركة
        close_1h = self.df_1h['Close']
        bias_1h = "صاعد (Bullish 📈)" if close_1h.iloc[-1] > close_1h.iloc[-3] else "هابط (Bearish 📉)"
        
        # اتجاه فريم الـ 5 دقائق (5m) للزخم اللحظي
        close_5m = self.df_5m['Close']
        bias_5m = "صاعد (Bullish ⚡)" if close_5m.iloc[-1] > close_5m.iloc[-3] else "هابط (Bearish ⚡)"
        
        return bias_1h, bias_5m

    def analyze_structure_with_exact_levels(self):
        """الطلب الثاني والثالث: تحديد مستويات الكسر الدقيقة (BOS/LG) وأرقام السيولة (IRL/ERL)"""
        df = self.df_5m
        if len(df) < 5:
            return "بيانات غير كافية", "غير محدد", 0, 0, 0, 0

        last_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]
        
        body_close = last_candle['Close']
        high_prev = prev_candle['High']
        low_prev = prev_candle['Low']
        
        # حساب النطاق والسيولة الداخلية (IRL) والخارجية (ERL) بالأرقام الدقيقة
        erl_high = self.df_1h['High'].max()
        erl_low = self.df_1h['Low'].min()
        
        # سيولة النطاق الداخلي (IRL) ممثلة بآخر قمة وقاع على فريم الـ 5 دقائق
        irl_high = df['High'].iloc[-5:].max()
        irl_low = df['Low'].iloc[-5:].min()
        
        # تقييم الهيكل مع ذكر المستوى السعري الدقيق الذي تم كسره
        structure_desc = "🔄 حركة داخلية مستقرة (No Break)"
        broken_level = 0.0
        
        if body_close > high_prev:
            broken_level = float(high_prev)
            if last_candle['High'] > body_close + 1.2:
                structure_desc = f"⚡ اكتساح سيولة علوي (Liquidity Grab / Inducement) فوق المستوى: `{broken_level:.2f}`"
            else:
                structure_desc = f"✅ كسر هيكل صاعد حقيقي (True BOS Bullish) فوق القمة: `{broken_level:.2f}`"
                
        elif body_close < low_prev:
            broken_level = float(low_prev)
            if last_candle['Low'] < body_close - 1.2:
                structure_desc = f"⚡ اكتساح سيولة سفلي (Liquidity Grab / Inducement) تحت المستوى: `{broken_level:.2f}`"
            else:
                structure_desc = f"✅ كسر هيكل هابط حقيقي (True BOS Bearish) تحت القاع: `{broken_level:.2f}`"

        return structure_desc, broken_level, erl_high, erl_low, irl_high, irl_low

    def execute_precision_strategy(self):
        bias_1h, bias_5m = self.get_market_bias()
        structure_desc, broken_level, erl_high, erl_low, irl_high, irl_low = self.analyze_structure_with_exact_levels()
        
        current_price = float(self.df_5m['Close'].iloc[-1])
        equilibrium = (erl_high + erl_low) / 2
        
        zone = "Discount (منطقة خصم - مسموح الشراء 🟢)" if current_price < equilibrium else "Premium (منطقة تضخم - مسموح البيع 🔴)"
        
        signal = "⏳ مراقبة دقيقة لمستويات السيولة والترابط بين الإطارين..."
        tp1, tp2, suggested_sl = 0, 0, 0
        
        # شروط الإشارة المؤكدة مع أرقام محددة
        if "Discount" in zone and "True BOS Bullish" in structure_desc and bias_1h.startswith("صاعد"):
            signal = "🎯 **إشارة شراء قناصة مؤكدة (STRONG BUY)** - توافق اتجاه الساعة + كسر هيكل صاعد حقيقي!"
            tp1 = irl_high  # الهدف الأول: سيولة النطاق الداخلي بالأرقام
            tp2 = erl_high  # الهدف الثاني: سيولة النطاق الخارجي الكبرى
            suggested_sl = broken_level - 1.5  # وقف خسارة مقترح تحت مستوى الكسر بدقة
            
        elif "Premium" in zone and "True BOS Bearish" in structure_desc and bias_1h.startswith("هابط"):
            signal = "🎯 **إشارة بيع قناصة مؤكدة (STRONG SELL)** - توافق اتجاه الساعة + كسر هيكل هابط حقيقي!"
            tp1 = irl_low   # الهدف الأول: سيولة النطاق الداخلي بالأرقام
            tp2 = erl_low   # الهدف الثاني: سيولة النطاق الخارجي الكبرى
            suggested_sl = broken_level + 1.5  # وقف خسارة مقترح فوق مستوى الكسر بدقة
            
        elif "Liquidity Grab" in structure_desc:
            signal = "⚡ **تنبيه اكتساح سيولة (Sweep Alert)** - احتمالية انعكاس من مستويات السيولة الحالية، راقب الحذر!"
        else:
            signal = f"👁️ الحالة التشغيلية: السعر يتحرك بين مستويات السيولة الداخلية (IRL: [`{irl_low:.2f}` - `{irl_high:.2f}`]) والخط الخارجي (ERL: [`{erl_low:.2f}` - `{erl_high:.2f}`])"

        return {
            "price": current_price,
            "bias_1h": bias_1h,
            "bias_5m": bias_5m,
            "zone": zone,
            "structure": structure_desc,
            "erl_high": erl_high,
            "erl_low": erl_low,
            "irl_high": irl_high,
            "irl_low": irl_low,
            "signal": signal,
            "tp1": tp1,
            "tp2": tp2,
            "sl": suggested_sl
        }

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ تنبيه: بيانات التليجرام غير مُعرفة!")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ خطأ في الاتصال بتليجرام: {e}")
        return False

def get_multi_timeframe_data():
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        
        df_1h = yf.download(SYMBOL, period="5d", interval="1h", progress=False, session=session)
        df_5m = yf.download(SYMBOL, period="1d", interval="5m", progress=False, session=session)
        
        if df_1h.empty or df_5m.empty:
            return None, None, "⚠️ فشل في سحب البيانات."

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
        return None, None, str(e)

def analyze_market_and_generate_report():
    df_1h, df_5m, change_pct, error = get_multi_timeframe_data()
    if error: return f"⚠️ خطأ في جلب البيانات: {error}"

    engine = PrecisionInstitutionalEngine(df_1h, df_5m)
    res = engine.execute_precision_strategy()
    
    targets_block = ""
    if res['tp1'] > 0:
        targets_block = f"""
🎯 *مستويات التنفيذ والمخاطرة المقترحة:*
• 🛑 وقف الخسارة المقترح (Micro SL): `{res['sl']:.2f}` USD
• 🎯 الهدف الأول (IRL): `{res['tp1']:.2f}` USD
• 🚀 الهدف الثاني (ERL): `{res['tp2']:.2f}` USD"""

    report_text = f"""
🧠 *التقرير الخوارزمي الدقيق (Multi-Timeframe SMC)* 🧠
⏱ *الوقت:* {time.strftime('%Y-%m-%d %H:%M')} (UTC)

*📍 السعر الحالي:* `{res['price']:.2f}` USD ({change_pct:+.2f}%)
*📈 اتجاه فريم الساعة (1H Bias):* {res['bias_1h']}
*⚡ اتجاه فريم الـ 5 دقائق (5m Bias):* {res['bias_5m']}
*🗺 النطاق الاستراتيجي:* {res['zone']}
*📊 مستويات السيولة الرقمية:*
  - سيولة داخلية (IRL): [`{res['irl_low']:.2f}` - `{res['irl_high']:.2f}`]
  - سيولة خارجية (ERL): [`{res['erl_low']:.2f}` - `{res['erl_high']:.2f}`]
*⚡ تقييم الهيكل والكسر:* {res['structure']}
{targets_block}

*🚀 التوجيه التداولي الخوارزمي:*
{res['signal']}
-----------------------------------
"""
    return report_text

def smart_monitoring_loop():
    time.sleep(5)
    print("🤖 تفعيل محرك التقارير الدقيقة (Precision Engine)...")
    send_telegram_message("🚀 *مرحباً محمد! تم تحديث خوارزمية البوت بنجاح.* التقرير أصبح يذكر اتجاه الفريمين، مستويات الكسر الدقيقة بالأرقام، وأسعار مستويات السيولة (IRL/ERL) بوضوح تام.")
    
    last_signal_state = None
    last_hourly_report_time = 0

    while True:
        try:
            df_1h, df_5m, change_pct, error = get_multi_timeframe_data()
            if not error:
                engine = PrecisionInstitutionalEngine(df_1h, df_5m)
                res = engine.execute_precision_strategy()
                current_signal = res['signal']
                
                is_strong = "STRONG BUY" in current_signal or "STRONG SELL" in current_signal
                if is_strong and current_signal != last_signal_state:
                    instant_alert = f"""
🚨 *تنبيه دخول قناص فوري (Precision Alert)* 🚨
⏱ *الوقت:* {time.strftime('%Y-%m-%d %H:%M')} (UTC)

*📍 سعر التنفيذ:* `{res['price']:.2f}` USD
*📈 اتجاه 1H:* {res['bias_1h']} | *5m:* {res['bias_5m']}
*🛑 وقف الخسارة المقترح:* `{res['sl']:.2f}` USD
*🎯 الأهداف:* IRL: `{res['tp1']:.2f}` | ERL: `{res['tp2']:.2f}`
*🎯 الإشارة:* 
{current_signal}
-----------------------------------
"""
                    send_telegram_message(instant_alert)
                    last_signal_state = current_signal

                if time.time() - last_hourly_report_time >= 3600:
                    report = analyze_market_and_generate_report()
                    send_telegram_message(report)
                    last_hourly_report_time = time.time()
                    
        except Exception as e:
            print(f"❌ خطأ حلقة المراقبة: {e}")
            
        time.sleep(300)

@app.route("/")
def home():
    return "Precision MTF SMC Trading Bot with Exact Level Reporting is Active!"

if __name__ == "__main__":
    monitor_thread = threading.Thread(target=smart_monitoring_loop, daemon=True)
    monitor_thread.start()
    print("🚀 تم تشغيل البوت بنجاح بالصيغة المحدثة.")

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
