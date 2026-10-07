import os
import time
import threading
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

app = Flask(__name__)

# استدعاء المتغيرات السرية من إعدادات المنصة (Render)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = "GC=F"  # رمز العقود الآجلة المعتمد للاستقرار

# معامل تصحيح السعر المحدث بدقة تامية لمطابقة السعر الفوري (Spot XAUUSD)
PRICE_OFFSET = -27.5  

class SMCTradingEngine:
    def __init__(self, df):
        self.df = df
        
    def detect_swing_points(self, window=3):
        """تحديد القمم (Swing Highs) والقيعان (Swing Lows)"""
        df = self.df
        df['Swing_High'] = df['High'][(df['High'] == df['High'].rolling(window*2+1, center=True).max())]
        df['Swing_Low'] = df['Low'][(df['Low'] == df['Low'].rolling(window*2+1, center=True).min())]
        return df

    def calculate_premium_discount(self):
        """حساب مناطق البريميوم والخصم (Premium & Discount) واستخراج القمة والقاع للنطاق"""
        recent_high = self.df['High'].max()
        recent_low = self.df['Low'].min()
        equilibrium = (recent_high + recent_low) / 2
        
        current_price = self.df['Close'].iloc[-1]
        
        if current_price < equilibrium:
            zone = "Discount (منطقة خصم - مسموح الشراء فقط 🟢)"
        else:
            zone = "Premium (منطقة تضخم - مسموح البيع فقط 🔴)"
            
        return recent_high, recent_low, equilibrium, zone

    def validate_bos_vs_liquidity(self):
        """التحقق من كسر الهيكل (BOS) مقابل سحب السيولة (Liquidity Grab)"""
        df = self.df
        if len(df) < 3:
            return "بيانات غير كافية"
            
        last_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]
        
        body_close = last_candle['Close']
        high_prev = prev_candle['High']
        low_prev = prev_candle['Low']
        
        if body_close > high_prev:
            if last_candle['High'] > body_close:
                return "سحب سيولة علوي (Liquidity Grab - احتمالية انعكاس)"
            return "كسر هيكل صاعد صحيح (BOS Bullish)"
            
        elif body_close < low_prev:
            if last_candle['Low'] < body_close:
                return "سحب سيولة سفلي (Liquidity Grab - احتمالية انعكاس)"
            return "كسر هيكل هابط صحيح (BOS Bearish)"
            
        return "حركة داخلية (No Break)"

    def detect_order_blocks(self):
        """دالة كشف مناطق الأوردر بلوك (Order Blocks - OB) المؤسسية"""
        df = self.df
        if len(df) < 5:
            return "بيانات غير كافية للاكتشاف", None
        
        bullish_ob_zone = None
        bearish_ob_zone = None
        
        # البحث عن آخر أوردر بلوك صاعد (آخر شمعة هابطة قبل الانفجار الصاعد)
        for i in range(len(df)-2, 2, -1):
            if df['Close'].iloc[i] > df['Open'].iloc[i]: # شمعة صاعدة
                for j in range(i-1, max(0, i-4), -1):
                    if df['Close'].iloc[j] < df['Open'].iloc[j]: # شمعة هابطة سابقة
                        bullish_ob_zone = (df['Low'].iloc[j], df['High'].iloc[j])
                        break
                if bullish_ob_zone:
                    break
                    
        # البحث عن آخر أوردر بلوك هابط (آخر شمعة صاعدة قبل الانفجار الهابط)
        for i in range(len(df)-2, 2, -1):
            if df['Close'].iloc[i] < df['Open'].iloc[i]: # شمعة هابطة
                for j in range(i-1, max(0, i-4), -1):
                    if df['Close'].iloc[j] > df['Open'].iloc[j]: # شمعة صاعدة سابقة
                        bearish_ob_zone = (df['Low'].iloc[j], df['High'].iloc[j])
                        break
                if bearish_ob_zone:
                    break
                    
        current_price = df['Close'].iloc[-1]
        ob_status = "السعر ينتقل بين مناطق السيولة (خارج نطاق OB الحالي)"
        
        if bullish_ob_zone and bullish_ob_zone[0] <= current_price <= bullish_ob_zone[1]:
            ob_status = f"🟢 السعر يختبر أوردر بلوك شرائي (Bullish OB: [{bullish_ob_zone[0]:.2f} - {bullish_ob_zone[1]:.2f}])"
        elif bearish_ob_zone and bearish_ob_zone[0] <= current_price <= bearish_ob_zone[1]:
            ob_status = f"🔴 السعر يختبر أوردر بلوك بيعي (Bearish OB: [{bearish_ob_zone[0]:.2f} - {bearish_ob_zone[1]:.2f}])"
            
        return ob_status, {"bullish": bullish_ob_zone, "bearish": bearish_ob_zone}

    def generate_smart_signal(self):
        """توليد التوجيه الاستراتيجي الشامل للبوت"""
        high, low, eq, zone = self.calculate_premium_discount()
        structure_status = self.validate_bos_vs_liquidity()
        ob_status, _ = self.detect_order_blocks()
        current_price = self.df['Close'].iloc[-1]
        
        signal_type = "⏳ مراقبة السوق (Waiting for Setup)"
        
        # تقاطع الشروط المؤسسية الذكية لإعطاء إشارة دقيقة
        if "Discount" in zone and "صاعد" in structure_status and "يختبر أوردر بلوك شرائي" in ob_status:
            signal_type = "🎯 **إشارة شراء مؤكدة (STRONG BUY)** - توافق منطقة الخصم + كسر صاعد + اختبار أوردر بلوك شرائي!"
        elif "Premium" in zone and "هابط" in structure_status and "يختبر أوردر بلوك بيعي" in ob_status:
            signal_type = "🎯 **إشارة بيع مؤكدة (STRONG SELL)** - توافق منطقة التضخم + كسر هابط + اختبار أوردر بلوك بيعي!"
        elif "Liquidity Grab" in structure_status:
            signal_type = "⚡ **تنبيه سيولة (Sweep/Manipulation)** - احتمالية انعكاس قوية، راقب الـ CDC!"
        else:
            signal_type = f"👁️ وضع المراقبة النشطة: {ob_status}"

        return {
            "price": current_price,
            "high": high,
            "low": low,
            "zone": zone,
            "structure": structure_status,
            "ob_info": ob_status,
            "signal": signal_type
        }

def send_telegram_message(message):
    """إرسال الرسائل إلى بوت التليجرام"""
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
    """جلب بيانات الذهب وتطبيق معامل التصحيح الفوري بدقة -27.5"""
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

        # تصحيح أعمدة الداتا فريم بالكامل بالمعامل الدقيق (-27.5)
        data['Close'] = data['Close'] + PRICE_OFFSET
        data['High'] = data['High'] + PRICE_OFFSET
        data['Low'] = data['Low'] + PRICE_OFFSET

        current_price = float(data['Close'].iloc[-1])
        prev_price = float(data['Close'].iloc[-2])
        change_pct = ((current_price - prev_price) / prev_price) * 100
        
        return data, current_price, change_pct, None
    except Exception as e:
        return None, None, None, str(e)

def analyze_market_and_generate_report():
    """توليد التقرير الاحترافي مع القمة والقاع ومحرك الذكاء المؤسسي"""
    data, current_price, change_pct, error = get_gold_data_safely()
    
    if error or current_price is None:
        return f"⚠️ عذراً محمد، حدث خطأ مؤقت في جلب بيانات الذهب:\n`{error}`"

    engine = SMCTradingEngine(data)
    analysis = engine.generate_smart_signal()
    
    report_text = f"""
🤖 *التقرير الذكي لمنظومة الـ SMC / ICT* 🤖
⏱ *الوقت:* {time.strftime('%Y-%m-%d %H:%M')} (UTC)

*📍 السعر الحالي (المصحح):* `{analysis['price']:.2f}` USD ({change_pct:+.2f}%)
*📈 أعلى قمة بالنطاق:* `{analysis['high']:.2f}` USD
*📉 أدنى قاع بالنطاق:* `{analysis['low']:.2f}` USD
*🗺 النطاق الاستراتيجي:* {analysis['zone']}
*⚡ حالة الهيكل والسيولة:* {analysis['structure']}
*🧱 مناطق الأوردر بلوك (OB):* {analysis['ob_info']}

*🚀 التوجيه التداولي للبوت:*
{analysis['signal']}
-----------------------------------
"""
    return report_text

def hourly_scheduler():
    time.sleep(5)
    print("🤖 جاري إرسال رسالة التحديث للتليجرام...")
    startup_msg = "🚀 *مرحباً محمد! تم تحديث البوت بنجاح.* تم ضبط معامل التصحيح (-27.5) وإضافة عرض القمة والقاع في التقارير الساعية."
    send_telegram_message(startup_msg)

    while True:
        report = analyze_market_and_generate_report()
        send_telegram_message(report)
        time.sleep(3600)

@app.route("/")
def home():
    return "Institutional SMC Trading Bot with High/Low and Price Offset is Active!"

if __name__ == "__main__":
    reporter_thread = threading.Thread(target=hourly_scheduler, daemon=True)
    reporter_thread.start()
    print("🚀 تم تشغيل نظام التداول الذكي بنجاح مع التعديلات الجديدة.")

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
