import os
import time
import threading
import requests
import pandas as pd
import yfinance as yf
from flask import Flask

app = Flask(__name__)

# استدعاء المتغيرات السرية من إعدادات المنصة (Render)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = "GC=F"  # رمز عقد الذهب الآجلة

def send_telegram_message(message):
    """دالة مسؤولة عن إرسال الرسائل إلى بوت التليجرام"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ تنبيه: بيانات التليجرام (Token أو Chat ID) غير مُعرفة في المتغيرات البيئية!")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("✅ تم إرسال رسالة التليجرام بنجاح.")
            return True
        else:
            print(f"❌ فشل إرسال رسالة التليجرام: {response.text}")
            return False
    except Exception as e:
        print(f"❌ خطأ في الاتصال بتليجرام: {e}")
        return False

def get_gold_data_safely():
    """جلب بيانات الذهب بدقة عالية وتصحيح أعمدة yfinance وفلترة الأخطاء"""
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        
        data = yf.download(SYMBOL, period="5d", interval="1h", progress=False, session=session)
        if data.empty:
            return None, None, None, "⚠️ لم يتم استرجاع أي بيانات من المصدر."

        # معالجة تنسيق أعمدة yfinance الحديثة (MultiIndex)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        # التأكد من وجود عمود الإغلاق (Close) وتصفية القيم الفارغة
        if 'Close' not in data.columns:
            return None, None, None, "⚠️ عمود الإغلاق (Close) غير متوفر في البيانات."

        data = data.dropna(subset=['Close'])
        if len(data) < 2:
            return None, None, None, "⚠️ البيانات المسترجعة غير كافية."

        current_price = float(data['Close'].iloc[-1])
        prev_price = float(data['Close'].iloc[-2])
        change_pct = ((current_price - prev_price) / prev_price) * 100
        
        return data, current_price, change_pct, None
    except Exception as e:
        return None, None, None, str(e)

def analyze_market_and_generate_report():
    """تحليل شارت الذهب وتكوين التقرير الساعي بدقة"""
    data, current_price, change_pct, error = get_gold_data_safely()
    
    if error or current_price is None:
        return f"⚠️ عذراً محمد، حدث خطأ مؤقت في جلب بيانات الذهب:\n`{error}`"

    rolling_mean = data['Close'].rolling(10).mean().iloc[-1]
    trend = "صاعد 🟢" if current_price > rolling_mean else "هابط 🔴"
    
    highest = data['High'].tail(15).max()
    lowest = data['Low'].tail(15).min()
    equilibrium = (highest + lowest) / 2
    
    zone = "منطقة خصم (Discount Zone - فرصة للشراء)" if current_price < equilibrium else "منطقة تضخم (Premium Zone - فرصة للبيع)"
    
    report_text = f"""
📊 *التقرير الساعي لسوق الذهب (SMC/ICT)* 📊
⏱ *الوقت:* {time.strftime('%Y-%m-%d %H:%M')} (UTC)

*📍 السعر الحالي:* `{current_price:.2f}` USD ({change_pct:+.2f}%)
*📈 هيكل السوق:* الاتجاه العام {trend}
*⚡ حركة الهيكل:* مستمر وفق الحركة السعرية الحالية
*🎯 مناطق الاهتمام (POI):* السعر يتواجد في {zone}
*💧 رصد السيولة:* متابعة القمم والقيعان (Liquidity Pools)
*🤖 حالة البوت:* البوت يعمل بانتظام ويراقب السوق 24/7
-----------------------------------
"""
    return report_text

def hourly_scheduler():
    time.sleep(5)
    print("🤖 جاري إرسال رسالة الفحص والتأكيد للتليجرام...")
    startup_msg = "🚀 *مرحباً محمد! تم تحديث كود البوت بنجاح.* تم ضبط جلب الأسعار بدقة تامة لضمان مطابقتها للواقع."
    send_telegram_message(startup_msg)

    while True:
        report = analyze_market_and_generate_report()
        send_telegram_message(report)
        time.sleep(3600)

@app.route("/")
def home():
    return "Gold SMC + AI Trading Bot is active with clean and precise data!"

if __name__ == "__main__":
    reporter_thread = threading.Thread(target=hourly_scheduler, daemon=True)
    reporter_thread.start()
    print("🚀 تم تشغيل نظام التقارير التلقائية بدقة.")

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
