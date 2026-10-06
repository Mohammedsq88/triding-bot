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
SYMBOL = "GC=F"  # رمز عقد الذهب

def send_telegram_message(message):
    """دالة مسؤولة عن إرسال الرسائل إلى بوت التليجرام"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ تنبيه: بيانات التليجرام (Token أو Chat ID) غير مُعرفة في المتغيرات البيئية!")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"❌ فشل إرسال رسالة التليجرام: {response.text}")
    except Exception as e:
        print(f"❌ خطأ في الاتصال بتليجرام: {e}")

def analyze_market_and_generate_report():
    """تحليل شارت الذهب وجلب البيانات الحقيقية لتكوين التقرير الساعي"""
    try:
        # جلب بيانات آخر 5 أيام بإطار زمني ساعة واحدة
        data = yf.download(SYMBOL, period="5d", interval="1h", progress=False)
        if data.empty:
            return "⚠️ عذراً، لم يتم استرجاع بيانات الذهب من السوق حالياً."

        # معالجة تنسيق أعمدة yfinance الحديثة
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        current_price = float(data['Close'].iloc[-1])
        prev_price = float(data['Close'].iloc[-2])
        change_pct = ((current_price - prev_price) / prev_price) * 100

        # تحليل هيكل السوق المبسط (SMC)
        rolling_mean = data['Close'].rolling(10).mean().iloc[-1]
        trend = "صاعد 🟢" if current_price > rolling_mean else "هابط 🔴"
        
        # حساب مناطق الخصم والتضخم (Premium & Discount)
        highest = data['High'].tail(15).max()
        lowest = data['Low'].tail(15).min()
        equilibrium = (highest + lowest) / 2
        
        zone = "منطقة خصم (Discount Zone - فرصة للشراء)" if current_price < equilibrium else "منطقة تضخم (Premium Zone - فرصة للبيع)"
        
        # صياغة التقرير الساعي المتفق عليه
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
    except Exception as e:
        return f"❌ حدث خطأ أثناء تحليل بيانات السوق: {str(e)}"

def hourly_scheduler():
    """حلقة تكرارية تعمل في الخلفية لإرسال التقرير كل ساعة تماماً"""
    # انتظار 15 ثانية بعد تشغيل السيرفر لأول مرة ثم إرسال التقرير الأول
    time.sleep(15)
    while True:
        report = analyze_market_and_generate_report()
        send_telegram_message(report)
        # الانتظار لمدة ساعة كاملة (3600 ثانية) قبل الإرسال القادم
        time.sleep(3600)

@app.route("/")
def home():
    """مسار الويب الخاص بـ Flask لكي يستجيب لطلبات UptimeRobot ويظل البوت مستيقظاً"""
    return "Gold SMC + AI Trading Bot is active and running with Hourly Reports!"

if __name__ == "__main__":
    # تشغيل نظام التقارير الساعية في خلفية السيرفر (Background Thread)
    reporter_thread = threading.Thread(target=hourly_scheduler, daemon=True)
    reporter_thread.start()
    print("🚀 تم تشغيل نظام التقارير التلقائية في الخلفية بنجاح.")

    # تشغيل سيرفر الـ Flask على البورت المخصص من Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
