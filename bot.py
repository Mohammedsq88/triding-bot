import os
import threading
import time
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from flask import Flask

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = "GC=F"

def get_baghdad_time():
    baghdad_tz = timezone(timedelta(hours=3))
    return datetime.now(baghdad_tz)

def get_live_spot_price():
    url = "https://api.goldprice.dev/v1/prices?symbol=XAU-USD-SPOT"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            symbols = data.get("symbols", [])
            if symbols:
                price = symbols[0].get("price")
                if price:
                    return float(price)
    except Exception as e:
        print(f"Free Spot API Error: {e}")
    return None

class PureInstitutionalEngine:
    """محرك السيولة الخام (بدون أي نسب أو معادلات تقديرية)"""
    def __init__(self, df_1h, df_5m, live_price=None):
        self.df_1h = df_1h
        self.df_5m = df_5m
        self.live_price = live_price

    def get_pure_levels(self):
        df_1h = self.df_1h
        df_5m = self.df_5m
        
        ref_price = self.live_price if self.live_price else float(df_5m['Close'].iloc[-1])

        # استخراج قوائم القمم والقيعان الخام مباشرة من الـ DataFrame بدون شروط معقدة تعود بقوائم فارغة
        h_1h = df_1h['High'].dropna().tolist()
        l_1h = df_1h['Low'].dropna().tolist()
        
        h_5m = df_5m['High'].dropna().tolist()
        l_5m = df_5m['Low'].dropna().tolist()

        # 1. السيولة الخارجية (ERL): من الإطار العالي 1H (أقرب قمة فوق السعر وأقرب قاع تحته)
        higher_erl = [h for h in h_1h if h > ref_price]
        lower_erl = [l for l in l_1h if l < ref_price]
        
        erl_high = min(higher_erl) if higher_erl else (max(h_1h) if h_1h else ref_price + 15.0)
        erl_low = max(lower_erl) if lower_erl else (min(l_1h) if l_1h else ref_price - 15.0)

        # 2. السيولة الداخلية (IRL): من الإطار الصغير 5M (محصورة تماماً بين السعر الحالي و ERL)
        higher_irl = [h for h in h_5m if h > ref_price and h < erl_high]
        lower_irl = [l for l in l_5m if l < ref_price and l > erl_low]
        
        # إذا وُجدت قمم/قيعان داخلية نأخذ الأقرب للسعر، وإذا لم توجد نأخذ الشمعة السابقة مباشرة كسيولة داخلية خام
        irl_high = min(higher_irl) if higher_irl else (float(df_5m['High'].iloc[-2]) if len(df_5m) > 1 else ref_price + 3.0)
        irl_low = max(lower_irl) if lower_irl else (float(df_5m['Low'].iloc[-2]) if len(df_5m) > 1 else ref_price - 3.0)

        # اتجاه الأسواق الخام
        htf_bias = "صاعد (Bullish 📈)" if ref_price > float(df_1h['Close'].iloc[-5]) else "هابط (Bearish 📉)"
        ltf_bias = "صاعد (Bullish ⚡)" if ref_price > float(df_5m['Close'].iloc[-5]) else "هابط (Bearish ⚡)"

        # نطاق جلسة آسيا (ARL)
        arl_high, arl_low = float(df_5m['High'].max()), float(df_5m['Low'].min())
        try:
            df_utc = df_5m.copy()
            if df_utc.index.tz is None:
                df_utc.index = pd.to_datetime(df_utc.index).tz_localize('UTC')
            else:
                df_utc.index = pd.to_datetime(df_utc.index).tz_convert('UTC')
            latest_date = df_utc.index.date[-1]
            asia_candles = df_utc[(df_utc.index.date == latest_date) & (df_utc.index.hour.isin([0, 1, 2, 3, 4, 5, 6]))]
            if not asia_candles.empty:
                arl_high = float(asia_candles['High'].max())
                arl_low = float(asia_candles['Low'].min())
        except:
            pass

        # كسر الهيكل البسيط المباشر (BOS)
        structure_status = "🔄 بانتظار حركة هيكلية واضحة"
        broken_level = 0.0
        last_h_5m = float(df_5m['High'].iloc[-3]) if len(df_5m) > 2 else ref_price
        last_l_5m = float(df_5m['Low'].iloc[-3]) if len(df_5m) > 2 else ref_price

        if ref_price > last_h_5m:
            structure_status = f"✅ True BOS Bullish فوق: `{last_h_5m:.2f}`"
            broken_level = last_h_5m
        elif ref_price < last_l_5m:
            structure_status = f"✅ True BOS Bearish تحت: `{last_l_5m:.2f}`"
            broken_level = last_l_5m

        return structure_status, broken_level, erl_high, erl_low, irl_high, irl_low, htf_bias, ltf_bias, arl_high, arl_low, ref_price

    def execute_strategy(self):
        res = self.get_pure_levels()
        structure_status, broken_level, erl_high, erl_low, irl_high, irl_low, htf_bias, ltf_bias, arl_high, arl_low, current_price = res
        
        trade_type = "غير محدد"
        signal = "⏳ مراقبة حركة الشارت الخام..."
        tp1, tp2, sl = 0, 0, 0

        if "صاعد" in htf_bias and "True BOS Bullish" in structure_status:
            trade_type = "🟢 صفقة شراء مؤسسية (STRONG BUY)"
            signal = "إشارة شراء مؤكدة من البيانات الخام للشارت."
            tp1 = irl_high if irl_high > current_price else current_price + 3.0
            tp2 = erl_high if erl_high > tp1 else tp1 + 5.0
            sl = (broken_level - 1.5) if (broken_level > 0 and broken_level < current_price) else current_price - 4.0

        elif "هابط" in htf_bias and "True BOS Bearish" in structure_status:
            trade_type = "🔴 صفقة بيع مؤسسية (STRONG SELL)"
            signal = "إشارة بيع مؤكدة من البيانات الخام للشارت."
            tp1 = irl_low if irl_low < current_price else current_price - 3.0
            tp2 = erl_low if erl_low < tp1 else tp1 - 5.0
            sl = (broken_level + 1.5) if (broken_level > 0 and broken_level > current_price) else current_price + 4.0

        else:
            signal = "👁️ بانتظار إغلاق شمعة مؤكد لتفعيل الصفقة."

        return {
            "price": current_price, "htf_bias": htf_bias, "ltf_bias": ltf_bias,
            "structure": structure_status, "trade_type": trade_type, "signal": signal,
            "tp1": tp1, "tp2": tp2, "sl": sl,
            "irl_high": irl_high, "irl_low": irl_low,
            "erl_high": erl_high, "erl_low": erl_low,
            "arl_high": arl_high, "arl_low": arl_low
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

def fetch_data():
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        
        df_1h = yf.download(SYMBOL, period="5d", interval="1h", progress=False, session=session)
        df_5m = yf.download(SYMBOL, period="5d", interval="5m", progress=False, session=session)
        
        if df_1h.empty or df_5m.empty:
            return None, None, 0, 0, "فشل جلب البيانات"

        for df in [df_1h, df_5m]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

        live_price = get_live_spot_price()
        if not live_price:
            closes = df_5m['Close'].dropna()
            live_price = float(closes.iloc[-1]) if not closes.empty else 0.0

        prev_closes = df_5m['Close'].dropna()
        prev_price = float(prev_closes.iloc[-2]) if len(prev_closes) > 1 else live_price
        change_pct = ((live_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0.0
        
        return df_1h, df_5m, live_price, change_pct, None
    except Exception as e:
        return None, None, 0, 0, str(e)

def generate_report():
    df_1h, df_5m, live_price, change_pct, error = fetch_data()
    if error: return f"⚠️ خطأ جلب البيانات: {error}"

    engine = PureInstitutionalEngine(df_1h, df_5m, live_price=live_price)
    res = engine.execute_strategy()
    
    targets_block = ""
    if res['tp1'] > 0:
        targets_block = f"""
🎯 *مستويات المخاطر التنفيذية:*
• 🛑 وقف الخسارة (SL): `{res['sl']:.2f}` USD
• 🎯 الهدف الأول (IRL): `{res['tp1']:.2f}` USD
• 🚀 الهدف الثاني (ERL): `{res['tp2']:.2f}` USD"""

    report = f"""
🧠 *التقرير المؤسسي الخام (بدون أي قيم افتراضية)* 🧠
⏱ *الوقت (بغداد):* {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

*📍 السعر الفوري اللحظي:* `{res['price']:.2f}` USD ({change_pct:+.2f}%)
*📈 اتجاه الإطار العالي (1H):* {res['htf_bias']}
*📊 مستويات السيولة من الشارت مباشرة:*
  - نطاق آسيا (ARL): [`{res['arl_low']:.2f}` - `{res['arl_high']:.2f}`]
  - سيولة داخلية (IRL الخام): [`{res['irl_low']:.2f}` - `{res['irl_high']:.2f}`]
  - سيولة خارجية (ERL الخام): [`{res['erl_low']:.2f}` - `{res['erl_high']:.2f}`]
*🔍 حالة الهيكل:* {res['structure']}
{targets_block}

*🚀 التوجيه الاستراتيجي:*
{res['signal']}
-----------------------------------
"""
    return report

def monitoring_loop():
    time.sleep(5)
    send_telegram_message(f"🚀 *تم تشغيل النسخة الجذرية الخام (تحديث مباشر من حركة الشارت)!* ⏱ {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}")
    
    last_signal = None
    last_report_time = 0

    while True:
        try:
            current_time = time.time()
            if current_time - last_report_time >= 900:
                report = generate_report()
                send_telegram_message(report)
                last_report_time = current_time

            df_1h, df_5m, live_price, change_pct, error = fetch_data()
            if not error:
                engine = PureInstitutionalEngine(df_1h, df_5m, live_price=live_price)
                res = engine.execute_strategy()
                
                is_strong = "STRONG BUY" in res['trade_type'] or "STRONG SELL" in res['trade_type']
                if is_strong and res['trade_type'] != last_signal:
                    instant_alert = f"""
🚨 *تنبيه دخول قناص (بيانات حقيقية خام)* 🚨
⏱ *الوقت (بغداد):* {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

📌 *نوع الصفقة:* {res['trade_type']}
📍 *سعر الدخول الفوري:* `{res['price']:.2f}` USD
🛑 *وقف الخسارة:* `{res['sl']:.2f}` USD
🎯 *الهدف الأول:* `{res['tp1']:.2f}` USD
🚀 *الهدف الثاني:* `{res['tp2']:.2f}` USD
-----------------------------------
"""
                    send_telegram_message(instant_alert)
                    last_signal = res['trade_type']
                    
        except Exception as e:
            print(f"Error in monitoring loop: {e}")
            
        time.sleep(60)

@app.route("/")
def home():
    return "Pure Institutional SMC Engine is Running!"

if __name__ == "__main__":
    t = threading.Thread(target=monitoring_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
