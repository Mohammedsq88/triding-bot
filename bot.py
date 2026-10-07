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

class InstitutionalSMCEngine:
    def __init__(self, df):
        self.df = df
        
    def analyze_timing_and_killzones(self):
        """المرحلة الأولى: فلترة الوقت وجلسات التداول (Timing & Killzones & Asian Range)"""
        if len(self.df) < 24:
            return {"session": "Normal", "arl_high": 0, "arl_low": 0, "killzone_active": True}
        
        # استخراج آخر يوم تداول لرصد نطاق آسيا (افتراض الساعات الأولى من UTC)
        recent_day = self.df.index[-1].date()
        day_data = self.df[self.df.index.date == recent_day]
        
        if not day_data.empty:
            # نطاق آسيا التقريبي (أول ساعات الجلسة الآسيوية)
            asian_session = day_data[(day_data.index.hour >= 0) & (day_data.index.hour <= 6)]
            arl_high = asian_session['High'].max() if not asian_session.empty else day_data['High'].max()
            arl_low = asian_session['Min'] if 'Min' in asian_session else (asian_session['Low'].min() if not asian_session.empty else day_data['Low'].min())
        else:
            arl_high = self.df['High'].max()
            arl_low = self.df['Low'].min()
            
        current_hour = datetime.utcnow().hour
        # فترات الـ Killzones (لندن ونيويورك)
        is_london_kz = 7 <= current_hour <= 10
        is_ny_kz = 12 <= current_hour <= 15
        killzone_active = is_london_kz or is_ny_kz
        
        session_name = "London/NY Killzone (عالية التقلب ⚡)" if killzone_active else "Asian/Off-Killzone (تجميع وترقب ⏳)"
        
        return {
            "session": session_name,
            "arl_high": arl_high,
            "arl_low": arl_low,
            "killzone_active": killzone_active
        }

    def calculate_structure_and_liquidity(self):
        """المرحلة الثانية: هيكل السوق وتصنيف السيولة (ERL & IRL)"""
        df = self.df
        recent_high = df['High'].max()
        recent_low = df['Low'].min()
        equilibrium = (recent_high + recent_low) / 2
        
        current_price = df['Close'].iloc[-1]
        
        if current_price < equilibrium:
            zone = "Discount (منطقة خصم - مسموح الشراء فقط 🟢)"
        else:
            zone = "Premium (منطقة تضخم - مسموح البيع فقط 🔴)"
            
        return {
            "erl_high": recent_high,
            "erl_low": recent_low,
            "equilibrium": equilibrium,
            "zone": zone
        }

    def validate_bos_vs_liquidity_grab(self):
        """المرحلة الثالثة: كشف الفخاخ والهياكل المزيفة (BOS vs LG & Inducement)"""
        df = self.df
        if len(df) < 3:
            return "بيانات غير كافية للتحقق الهيكلي"
            
        last_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]
        
        body_close = last_candle['Close']
        high_prev = prev_candle['High']
        low_prev = prev_candle['Low']
        
        # فحص إغلاق الجسد مقارنة بالقمم السابقة
        if body_close > high_prev:
            if last_candle['High'] > body_close + 1.5:  # ذيل طويل يعبر عن تلاعب
                return "⚡ اكتساح سيولة علوي (Liquidity Grab / Inducement Sweep)"
            return "✅ كسر هيكل صاعد حقيقي (True BOS Bullish)"
            
        elif body_close < low_prev:
            if last_candle['Low'] < body_close - 1.5:
                return "⚡ اكتساح سيولة سفلي (Liquidity Grab / Inducement Sweep)"
            return "✅ كسر هيكل هابط حقيقي (True BOS Bearish)"
            
        return "🔄 حركة داخلية (Inducement / No Break)"

    def filter_poi_and_order_blocks(self):
        """المرحلة الرابعة: تصفية واختيار مناطق الاهتمام (POI & Order Blocks)"""
        df = self.df
        if len(df) < 5:
            return "بيانات غير كافية", None
        
        bullish_ob = None
        bearish_ob = None
        
        # البحث عن آخر شمعة هابطة قبل الانفجار الصاعد (Bullish OB)
        for i in range(len(df)-2, 2, -1):
            if df['Close'].iloc[i] > df['Open'].iloc[i]:
                for j in range(i-1, max(0, i-4), -1):
                    if df['Close'].iloc[j] < df['Open'].iloc[j]:
                        bullish_ob = (df['Low'].iloc[j], df['High'].iloc[j])
                        break
                if bullish_ob:
                    break
                    
        # البحث عن آخر شمعة صاعدة قبل الانفجار الهابط (Bearish OB)
        for i in range(len(df)-2, 2, -1):
            if df['Close'].iloc[i] < df['Open'].iloc[i]:
                for j in range(i-1, max(0, i-4), -1):
                    if df['Close'].iloc[j] > df['Open'].iloc[j]:
                        bearish_ob = (df['Low'].iloc[j], df['High'].iloc[j])
                        break
                if bearish_ob:
                    break
                    
        current_price = df['Close'].iloc[-1]
        poi_status = "السعر يتحرك بين مستويات السيولة الداخلية (IRL)"
        
        if bullish_ob and bullish_ob[0] <= current_price <= bullish_ob[1]:
            poi_status = f"🟢 اختبار ناجح لمنطقة الأوردر بلوك الشرائي (Bullish POI: [{bullish_ob[0]:.2f} - {bullish_ob[1]:.2f}])"
        elif bearish_ob and bearish_ob[0] <= current_price <= bearish_ob[1]:
            poi_status = f"🔴 اختبار ناجح لمنطقة الأوردر بلوك البيعي (Bearish POI: [{bearish_ob[0]:.2f} - {bearish_ob[1]:.2f}])"
            
        return poi_status, {"bullish": bullish_ob, "bearish": bearish_ob}

    def execute_strategy_and_risk_management(self):
        """المرحلة الخامسة: شروط التنفيذ، إدارة المخاطر، وتحديد الأهداف (TP1 & TP2)"""
        timing = self.analyze_timing_and_killzones()
        structure = self.calculate_structure_and_liquidity()
        structure_status = self.validate_bos_vs_liquidity_grab()
        poi_status, _ = self.filter_poi_and_order_blocks()
        current_price = self.df['Close'].iloc[-1]
        
        signal = "⏳ مراقبة البنية المؤسسية (Waiting for Institutional Confluence)"
        tp1 = 0
        tp2 = 0
        
        # شروط إعطاء الإشارة الذكية المستندة لكل المراحل
        if "Discount" in structure['zone'] and "صاعد" in structure_status and "اختبار ناجح لمنطقة الأوردر بلوك الشرائي" in poi_status:
            signal = "🎯 **إشارة شراء مؤكدة (STRONG BUY)** - توافق منطقة الخصم + كسر صاعد حقيقي + اختبار POI مؤسسي!"
            tp1 = timing['arl_high']  # الهدف الأول: قمة آسيا / سيولة داخلية
            tp2 = structure['erl_high'] # الهدف الثاني: سيولة النطاق الخارجي الكبرى
            
        elif "Premium" in structure['zone'] and "هابط" in structure_status and "اختبار ناجح لمنطقة الأوردر بلوك البيعي" in poi_status:
            signal = "🎯 **إشارة بيع مؤكدة (STRONG SELL)** - توافق منطقة التضخم + كسر هابط حقيقي + اختبار POI مؤسسي!"
            tp1 = timing['arl_low']   # الهدف الأول: قاع آسيا / سيولة داخلية
            tp2 = structure['erl_low']  # الهدف الثاني: سيولة النطاق الخارجي الكبرى
            
        elif "اكتساح سيولة" in structure_status:
            signal = "⚡ **تنبيه اكتساح سيولة عالي الخطورة (Liquidity Sweep)** - احتمالية انعكاس من نطاق آسيا، راقب الحذر!"
        else:
            signal = f"👁️ الحالة التشغيلية: {poi_status} | الجلسة: {timing['session']}"

        return {
            "price": current_price,
            "timing": timing,
            "structure": structure,
            "structure_status": structure_status,
            "poi_status": poi_status,
            "signal": signal,
            "tp1": tp1,
            "tp2": tp2
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

def get_gold_data_safely():
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        
        data = yf.download(SYMBOL, period="5d", interval="1h", progress=False, session=session)
        if data.empty:
            return None, None, None, "⚠️ لم يتم استرجاع أي بيانات."

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        if 'Close' not in data.columns:
            return None, None, None, "⚠️ عمود الإغلاق غير متوفر."

        data = data.dropna(subset=['Close'])
        if len(data) < 5:
            return None, None, None, "⚠️ البيانات غير كافية."

        # تطبيق معامل التصحيح والضبط الدقيق
        data['Close'] = data['Close'] + PRICE_OFFSET
        data['Low'] = data['Low'] + PRICE_OFFSET
        data['High'] = (data['High'] + PRICE_OFFSET) - 3.0  

        current_price = float(data['Close'].iloc[-1])
        prev_price = float(data['Close'].iloc[-2])
        change_pct = ((current_price - prev_price) / prev_price) * 100
        
        return data, current_price, change_pct, None
    except Exception as e:
        return None, None, None, str(e)

def analyze_market_and_generate_report():
    data, current_price, change_pct, error = get_gold_data_safely()
    
    if error or current_price is None:
        return f"⚠️ عذراً محمد، حدث خطأ مؤقت في جلب بيانات الذهب:\n`{error}`"

    engine = InstitutionalSMCEngine(data)
    result = engine.execute_strategy_and_risk_management()
    
    tp_text = f"\n🎯 *الأهداف الاستراتيجية المقترحة:*\n• الهدف الأول (TP1 - سيولة آسيا): `{result['tp1']:.2f}` USD\n• الهدف الثاني (TP2 - النطاق الخارجي): `{result['tp2']:.2f}` USD" if result['tp1'] > 0 else ""
    
    report_text = f"""
🧠 *التقرير الخوارزمي المتقدم (SMC / ICT 5-Phases)* 🧠
⏱ *الوقت:* {time.strftime('%Y-%m-%d %H:%M')} (UTC)

*📍 السعر الحالي:* `{result['price']:.2f}` USD ({change_pct:+.2f}%)
*⏰ حالة التوقيت والجلسات:* {result['timing']['session']}
*🗺 النطاق الاستراتيجي:* {result['structure']['zone']}
*📈 سيولة نطاق آسيا (ARL):* قمة `{result['timing']['arl_high']:.2f}` | قاع `{result['timing']['arl_low']:.2f}`
*⚡ تقييم الهيكل (BOS / LG):* {result['structure_status']}
*🧱 مناطق الاهتمام (POI):* {result['poi_status']}
{tp_text}

*🚀 التوجيه التداولي الخوارزمي:*
{result['signal']}
-----------------------------------
"""
    return report_text

def smart_monitoring_loop():
    time.sleep(5)
    print("🤖 جاري تشغيل المنظومة الذكية الكاملة للـ SMC / ICT...")
    startup_msg = "🚀 *مرحباً محمد! تم تفعيل خوارزمية المراحل الخمسة (5-Phases SMC Engine)* بنجاح واستقرار تام."
    send_telegram_message(startup_msg)
    
    last_signal_state = None
    last_hourly_report_time = 0

    while True:
        try:
            data, current_price, change_pct, error = get_gold_data_safely()
            if not error and current_price is not None:
                engine = InstitutionalSMCEngine(data)
                result = engine.execute_strategy_and_risk_management()
                current_signal = result['signal']
                
                is_strong_signal = "STRONG BUY" in current_signal or "STRONG SELL" in current_signal or "اكتساح سيولة" in current_signal
                
                if is_strong_signal and current_signal != last_signal_state:
                    instant_msg = f"""
🚨 *تنبيه فوري خوارزمي (Institutional Alert)* 🚨
⏱ *الوقت:* {time.strftime('%Y-%m-%d %H:%M')} (UTC)

*📍 السعر الحالي:* `{result['price']:.2f}` USD
*🗺 حالة السوق:* {result['structure']['zone']}
*🎯 التوجيه التنفيذي:* 
{current_signal}
-----------------------------------
"""
                    send_telegram_message(instant_msg)
                    last_signal_state = current_signal

                current_time = time.time()
                if current_time - last_hourly_report_time >= 3600:
                    report = analyze_market_and_generate_report()
                    send_telegram_message(report)
                    last_hourly_report_time = current_time
                    
        except Exception as e:
            print(f"❌ خطأ في حلقة المراقبة الخوارزمية: {e}")
            
        time.sleep(300) # فحص كل 5 دقائق

@app.route("/")
def home():
    return "Full 5-Phases Institutional SMC/ICT Trading Bot is Active and Optimized!"

if __name__ == "__main__":
    monitor_thread = threading.Thread(target=smart_monitoring_loop, daemon=True)
    monitor_thread.start()
    print("🚀 تم تشغيل البوت الخوارزمي المتقدم بنجاح.")

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
