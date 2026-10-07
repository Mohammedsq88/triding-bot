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

class MultiTimeframeSMCEngine:
    def __init__(self, df_1h, df_5m):
        self.df_1h = df_1h
        self.df_5m = df_5m
        
    def analyze_htf_macro(self):
        """تحليل فريم الساعة (HTF) لتحديد النطاق والـ POI الكبرى"""
        df = self.df_1h
        if len(df) < 5:
            return None, "بيانات 1H غير كافية"
            
        recent_high = df['High'].max()
        recent_low = df['Low'].min()
        equilibrium = (recent_high + recent_low) / 2
        current_price = df['Close'].iloc[-1]
        
        zone = "Discount (منطقة خصم 🟢)" if current_price < equilibrium else "Premium (منطقة تضخم 🔴)"
        
        # استخراج أوردر بلوك فريم الساعة (1H POI)
        h_ob = None
        for i in range(len(df)-2, 2, -1):
            if zone.startswith("Discount") and df['Close'].iloc[i] > df['Open'].iloc[i]:
                for j in range(i-1, max(0, i-4), -1):
                    if df['Close'].iloc[j] < df['Open'].iloc[j]:
                        h_ob = (df['Low'].iloc[j], df['High'].iloc[j])
                        break
                if h_ob: break
            elif zone.startswith("Premium") and df['Close'].iloc[i] < df['Open'].iloc[i]:
                for j in range(i-1, max(0, i-4), -1):
                    if df['Close'].iloc[j] > df['Open'].iloc[j]:
                        h_ob = (df['Low'].iloc[j], df['High'].iloc[j])
                        break
                if h_ob: break
                
        return {
            "zone": zone,
            "high": recent_high,
            "low": recent_low,
            "equilibrium": equilibrium,
            "poi_1h": h_ob
        }, None

    def analyze_ltf_execution(self, htf_data):
        """تحليل فريم الخمس دقائق (LTF) للبحث عن إشارة الدخول الدقيقة (CHoCH / Micro BOS)"""
        df = self.df_5m
        if len(df) < 5 or not htf_data:
            return "⏳ بانتظار تشكل الهيكل على فريم 5 دقائق", 0, 0
            
        current_price = df['Close'].iloc[-1]
        poi = htf_data["poi_1h"]
        zone = htf_data["zone"]
        
        # فحص تفاعل السعر مع أوردر بلوك الساعة على فريم الـ 5 دقائق
        near_poi = False
        if poi and poi[0] - 2.0 <= current_price <= poi[1] + 2.0:
            near_poi = True
            
        # فحص الشموع الأخيرة على الـ 5 دقائق لاكتشاف تغيير الطابع (CHoCH)
        last_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]
        
        body_5m = last_candle['Close']
        high_5m_prev = prev_candle['High']
        low_5m_prev = prev_candle['Low']
        
        signal = "👁️ مراقبة تفاعل فريم 5 دقائق مع منطقة الساعة (1H POI)"
        tp1 = 0
        tp2 = 0
        
        # إذا كان السعر في منطقة الخصم وقريب من POI وحدث كسر مصغر صاعد على الـ 5 دقائق
        if "Discount" in zone and body_5m > high_5m_prev:
            if near_poi or current_price < htf_data["equilibrium"]:
                signal = "🎯 **إشارة شراء قناصة (STRONG BUY - 5m LTF Entry)** - تفاعل ناجح مع 1H POI + كسر هيكل مصغر على فريم 5 دقائق!"
                tp1 = htf_data["equilibrium"]
                tp2 = htf_data["high"]
                
        # إذا كان السعر في منطقة التضخم وقريب من POI وحدث كسر مصغر هابط على الـ 5 دقائق
        elif "Premium" in zone and body_5m < low_5m_prev:
            if near_poi or current_price > htf_data["equilibrium"]:
                signal = "🎯 **إشارة بيع قناصة (STRONG SELL - 5m LTF Entry)** - تفاعل ناجح مع 1H POI + كسر هيكل مصغر على فريم 5 دقائق!"
                tp1 = htf_data["equilibrium"]
                tp2 = htf_data["low"]
                
        return signal, tp1, tp2

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
    """سحب بيانات فريم الساعة وفريم الـ 5 دقائق مع تطبيق التصحيح الدقيق"""
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        
        # سحب بيانات الساعة (1H) للاتجاه العام
        df_1h = yf.download(SYMBOL, period="5d", interval="1h", progress=False, session=session)
        # سحب بيانات الـ 5 دقائق (5m) للتنفيذ السريع
        df_5m = yf.download(SYMBOL, period="1d", interval="5m", progress=False, session=session)
        
        if df_1h.empty or df_5m.empty:
            return None, None, "⚠️ فشل في سحب البيانات لأحد الإطارات الزمنية."

        for df in [df_1h, df_5m]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

        # تطبيق التصحيح على الإطارين
        for df in [df_1h, df_5m]:
            df['Close'] = df['Close'] + PRICE_OFFSET
            df['Low'] = df['Low'] + PRICE_OFFSET
            df['High'] = (df['High'] + PRICE_OFFSET) - 3.0

        current_price = float(df_5m['Close'].iloc[-1])
        prev_price = float(df_5m['Close'].iloc[-2])
        change_pct = ((current_price - prev_price) / prev_price) * 100
        
        return df_1h, df_5m, current_price, change_pct, None
    except Exception as e:
        return None, None, None, 0, str(e)

def analyze_and_generate_mtf_report():
    df_1h, df_5m, current_price, change_pct, error = get_multi_timeframe_data()
    if error or current_price is None:
        return f"⚠️ عذراً محمد، خطأ في جلب بيانات التحليل المزدوج:\n`{error}`"

    engine = MultiTimeframeSMCEngine(df_1h, df_5m)
    htf_data, err = engine.analyze_htf_macro()
    if err: return err
    
    signal, tp1, tp2 = engine.analyze_ltf_execution(htf_data)
    
    tp_text = f"\n🎯 *الأهداف المقترحة (5m Execution):*\n• الهدف الأول: `{tp1:.2f}` USD\n• الهدف الثاني: `{tp2:.2f}` USD" if tp1 > 0 else ""
    
    report_text = f"""
⚡ *التقرير المؤسسي المزدوج (1H Macro + 5m LTF)* ⚡
⏱ *الوقت:* {time.strftime('%Y-%m-%d %H:%M')} (UTC)

*📍 السعر الحالي:* `{current_price:.2f}` USD ({change_pct:+.2f}%)
*🗺 الاتجاه العام (1H Zone):* {htf_data['zone']}
*📈 نطاق الساعة:* قمة `{htf_data['high']:.2f}` | قاع `{htf_data['low']:.2f}`
{tp_text}

*🚀 التوجيه التنفيذي (على فريم 5 دقائق):*
{signal}
-----------------------------------
"""
    return report_text

def smart_monitoring_loop():
    time.sleep(5)
    print("🤖 جاري تفعيل محرك التحليل المزدوج (1H + 5m)...")
    startup_msg = "🚀 *مرحباً محمد! تم تفعيل نظام التحليل المزدوج بنجاح.* البوت الآن يراقب اتجاه الساعة (1H) وينفذ الصفقات على فريم الـ 5 دقائق (5m) بدقة القناص."
    send_telegram_message(startup_msg)
    
    last_signal_state = None
    last_hourly_report_time = 0

    while True:
        try:
            df_1h, df_5m, current_price, change_pct, error = get_multi_timeframe_data()
            if not error and current_price is not None:
                engine = MultiTimeframeSMCEngine(df_1h, df_5m)
                htf_data, _ = engine.analyze_htf_macro()
                current_signal, _, _ = engine.analyze_ltf_execution(htf_data)
                
                is_strong_signal = "STRONG BUY" in current_signal or "STRONG SELL" in current_signal
                
                if is_strong_signal and current_signal != last_signal_state:
                    instant_msg = f"""
🚨 *تنبيه دخول فوري (5m Sniper Entry)* 🚨
⏱ *الوقت:* {time.strftime('%Y-%m-%d %H:%M')} (UTC)

*📍 سعر التنفيذ:* `{current_price:.2f}` USD
*🗺 الاتجاه العام (1H):* {htf_data['zone']}
*🎯 الإشارة المنفذة:* 
{current_signal}
-----------------------------------
"""
                    send_telegram_message(instant_msg)
                    last_signal_state = current_signal

                current_time = time.time()
                if current_time - last_hourly_report_time >= 3600:
                    report = analyze_and_generate_mtf_report()
                    send_telegram_message(report)
                    last_hourly_report_time = current_time
                    
        except Exception as e:
            print(f"❌ خطأ في حلقة المراقبة المزدوجة: {e}")
            
        time.sleep(300) # فحص مستمر كل 5 دقائق

@app.route("/")
def home():
    return "Multi-Timeframe 1H + 5m SMC Trading Bot is Active!"

if __name__ == "__main__":
    monitor_thread = threading.Thread(target=smart_monitoring_loop, daemon=True)
    monitor_thread.start()
    print("🚀 تم تشغيل البوت بنظام القنص (1H + 5m) بنجاح.")

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
