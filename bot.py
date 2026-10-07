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

# معامل تصحيح السعر بين الفيوتشرز والسبوت
PRICE_OFFSET = -27.5  

def get_baghdad_time():
    baghdad_tz = timezone(timedelta(hours=3))
    return datetime.now(baghdad_tz)

class AdvancedSMCBlueprintEngine:
    def __init__(self, df_1h, df_5m):
        self.df_1h = df_1h
        self.df_5m = df_5m

    def get_swings(self, df, window=3):
        """ رصد السوينغات (القمم والقيعان) بدقة وفقاً لنافذة الفحص """
        highs = []
        lows = []
        if len(df) < (window * 2 + 1):
            return highs, lows

        for i in range(window, len(df) - window):
            is_sh = True
            is_sl = True
            for j in range(1, window + 1):
                if df['High'].iloc[i] < df['High'].iloc[i-j] or df['High'].iloc[i] < df['High'].iloc[i+j]:
                    is_sh = False
                if df['Low'].iloc[i] > df['Low'].iloc[i-j] or df['Low'].iloc[i] > df['Low'].iloc[i+j]:
                    is_sl = False
            
            if is_sh:
                highs.append({"index": i, "price": float(df['High'].iloc[i]), "time": df.index[i]})
            if is_sl:
                lows.append({"index": i, "price": float(df['Low'].iloc[i]), "time": df.index[i]})
                
        return highs, lows

    def validate_bos(self, df, level, direction):
        """ التحقق من كسر الهيكل (BOS) الحقيقي عبر الإغلاق بجسم الشمعة وليس الفتيل فقط """
        if len(df) == 0:
            return False
        
        current_close = float(df['Close'].iloc[-1])
        prev_close = float(df['Close'].iloc[-2]) if len(df) > 1 else current_close
        
        if direction == "bullish":
            if current_close > level or prev_close > level:
                return True
        elif direction == "bearish":
            if current_close < level or prev_close < level:
                return True
        return False

    def get_asia_session_range(self):
        """ استخراج نطاق جلسة آسيا (ARL) لتحديد سيولة النطاق الداخلي الأساسية """
        df = self.df_5m
        if df.empty:
            return 0.0, 0.0
        
        # تحويل مؤشر الساعاتي إلى توقيت بغداد وفلترة ساعات آسيا (مثلاً من 03:00 إلى 09:00 صباحاً)
        baghdad_tz = timezone(timedelta(hours=3))
        try:
            df_local = df.copy()
            df_local.index = pd.to_datetime(df_local.index).tz_convert(baghdad_tz)
            today_asia = df_local[df_local.index.hour.isin([3, 4, 5, 6, 7, 8])]
            if not today_asia.empty:
                return float(today_asia['High'].max()), float(today_asia['Low'].min())
        except:
            pass
        
        # كاحتياط في حال اختلاف صيغة التوقيت
        return float(df['High'].iloc[-24:].max()), float(df['Low'].iloc[-24:].min())

    def check_killzones(self):
        """ التحقق من نوافذ الـ Killzones الزمنية (لندن ونيويورك بتوقيت بغداد) """
        now_baghdad = get_baghdad_time()
        hour = now_baghdad.hour
        
        # جلسة لندن: 10:00 إلى 13:00 بتوقيت بغداد
        # جلسة نيويورك: 15:00 إلى 18:00 بتوقيت بغداد
        is_london_kz = 10 <= hour < 13
        is_ny_kz = 15 <= hour < 18
        
        if is_london_kz:
            return True, "London Killzone نشطة 🟢"
        elif is_ny_kz:
            return True, "New York Killzone نشطة ⚡"
        else:
            return False, "خارج أوقات الـ Killzones الأساسية 💤"

    def detect_cdc_and_sweep(self, arl_high, arl_low):
        """ اكتشاف اكتساح سيولة آسيا (Sweep) وتغير الطابع (CDC) في الإطار المنخفض """
        df = self.df_5m
        if len(df) < 5:
            return "لا توجد بيانات كافية للـ CDC", 0.0

        current_high = float(df['High'].iloc[-1])
        current_low = float(df['Low'].iloc[-1])
        current_close = float(df['Close'].iloc[-1])
        
        sweep_status = "لا يوجد اكتساح حالي"
        cdc_signal = 0.0

        # فحص ما إذا تم اكتساح قمة آسيا (Sweep High) ثم العودة بإغلاق سلبي
        if current_high > arl_high and current_close < arl_high:
            sweep_status = "⚠️ تم اكتساح قمة آسيا (Asia High Sweep / Liquidity Grab)"
            cdc_signal = -1.0 # إشارة انعكاس هابطة محتملة بعد الاكتساح

        # فحص ما إذا تم اكتساح قاع آسيا (Sweep Low) ثم العودة بإغلاق إيجابي
        elif current_low < arl_low and current_close > arl_low:
            sweep_status = "⚠️ تم اكتساح قاع آسيا (Asia Low Sweep / Liquidity Grab)"
            cdc_signal = 1.0 # إشارة انعكاس صاعدة محتملة بعد الاكتساح

        return sweep_status, cdc_signal

    def analyze_market_structure(self):
        df_1h = self.df_1h
        df_5m = self.df_5m

        if len(df_1h) < 10 or len(df_5m) < 10:
            return "بيانات غير كافية", 0, 0, 0, 0, "محايد", "محايد", 0, 0, "", 0

        # 1. تحليل الإطار العالي (1H) للسيولة الخارجية والاتجاه
        h_highs_1h, h_lows_1h = self.get_swings(df_1h, window=3)
        erl_high = h_highs_1h[-1]['price'] if h_highs_1h else float(df_1h['High'].max())
        erl_low = h_lows_1h[-1]['price'] if h_lows_1h else float(df_1h['Low'].min())
        htf_bias = "صاعد (Bullish 📈)" if df_1h['Close'].iloc[-1] > df_1h['Close'].iloc[-5] else "هابط (Bearish 📉)"

        # 2. نطاق جلسة آسيا والسيولة الداخلية (IRL)
        arl_high, arl_low = self.get_asia_session_range()
        h_highs_5m, h_lows_5m = self.get_swings(df_5m, window=2)
        irl_high = max(arl_high, h_highs_5m[-1]['price'] if h_highs_5m else float(df_5m['High'].iloc[-5:].max()))
        irl_low = min(arl_low, h_lows_5m[-1]['price'] if h_lows_5m else float(df_5m['Low'].iloc[-5:].min()))
        ltf_bias = "صاعد (Bullish ⚡)" if df_5m['Close'].iloc[-1] > df_5m['Close'].iloc[-6] else "هابط (Bearish ⚡)"

        # 3. فحص الـ Killzone واكتساح السيولة والـ CDC
        kz_active, kz_desc = self.check_killzones()
        sweep_desc, cdc_val = self.detect_cdc_and_sweep(arl_high, arl_low)

        # 4. كسر الهيكل (BOS) الحقيقي
        structure_status = f"🔄 {sweep_desc}"
        broken_level = 0.0

        if h_highs_5m:
            last_swing_high = h_highs_5m[-1]['price']
            if self.validate_bos(df_5m, last_swing_high, "bullish"):
                structure_status = f"✅ True BOS Bullish فوق: `{last_swing_high:.2f}`"
                broken_level = last_swing_high

        if h_lows_5m and broken_level == 0.0:
            last_swing_low = h_lows_5m[-1]['price']
            if self.validate_bos(df_5m, last_swing_low, "bearish"):
                structure_status = f"✅ True BOS Bearish تحت: `{last_swing_low:.2f}`"
                broken_level = last_swing_low

        return structure_status, broken_level, erl_high, erl_low, irl_high, irl_low, htf_bias, ltf_bias, cdc_val, kz_desc, arl_high, arl_low

    def execute_strategy(self):
        res_struct = self.analyze_market_structure()
        structure_status, broken_level, erl_high, erl_low, irl_high, irl_low, htf_bias, ltf_bias, cdc_val, kz_desc, arl_high, arl_low = res_struct
        
        current_price = float(self.df_5m['Close'].iloc[-1])
        equilibrium = (erl_high + erl_low) / 2
        
        # مناطق التسعير المؤسسية (Premium / Discount)
        zone = "Discount (منطقة خصم - مسموح الشراء 🟢)" if current_price < equilibrium else "Premium (منطقة تضخم - مسموح البيع 🔴)"
        
        trade_type = "غير محدد"
        signal = f"⏳ بانتظار توافق الـ Killzone والـ CDC... ({kz_desc})"
        tp1, tp2, sl = 0, 0, 0

        # شروط صفقة شراء متكاملة (منطقة خصم + اتجاه صاعد + سيولة آسيا أو CDC إيجابي)
        if "صاعد" in htf_bias and current_price < equilibrium and (cdc_val > 0 or "BOS Bullish" in structure_status):
            trade_type = "🟢 صفقة شراء مؤسسية (STRONG BUY)"
            signal = f"إشارة شراء قناصة مؤكدة وفق خريطة الطريق! ({kz_desc})"
            tp1 = irl_high if irl_high > current_price else current_price + 3.0
            tp2 = erl_high if erl_high > tp1 else tp1 + 5.0
            sl = (broken_level - 1.5) if (broken_level > 0 and broken_level < current_price) else current_price - 4.0

        # شروط صفقة بيع متكاملة (منطقة تضخم + اتجاه هابط + سيولة آسيا أو CDC سلبي)
        elif "هابط" in htf_bias and current_price >= equilibrium and (cdc_val < 0 or "BOS Bearish" in structure_status):
            trade_type = "🔴 صفقة بيع مؤسسية (STRONG SELL)"
            signal = f"إشارة بيع قناصة مؤكدة وفق خريطة الطريق! ({kz_desc})"
            tp1 = irl_low if irl_low < current_price else current_price - 3.0
            tp2 = erl_low if erl_low < tp1 else tp1 - 5.0
            sl = (broken_level + 1.5) if (broken_level > 0 and broken_level > current_price) else current_price + 4.0

        else:
            signal = f"👁️ وضع المراقبة الدقيقة: السعر في نطاق {zone.split('-')[0].strip()} | {kz_desc}"

        return {
            "price": current_price,
            "htf_bias": htf_bias,
            "ltf_bias": ltf_bias,
            "zone": zone,
            "structure": structure_status,
            "trade_type": trade_type,
            "signal": signal,
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl,
            "irl_high": irl_high,
            "irl_low": irl_low,
            "erl_high": erl_high,
            "erl_low": erl_low,
            "arl_high": arl_high,
            "arl_low": arl_low,
            "kz_desc": kz_desc
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
        df_5m = yf.download(SYMBOL, period="1d", interval="5m", progress=False, session=session)
        
        if df_1h.empty or df_5m.empty:
            return None, None, 0, "فشل جلب البيانات"

        for df in [df_1h, df_5m]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

        for df in [df_1h, df_5m]:
            df['Close'] = df['Close'] + PRICE_OFFSET
            df['Low'] = df['Low'] + PRICE_OFFSET
            df['High'] = df['High'] + PRICE_OFFSET

        current_price = float(df_5m['Close'].iloc[-1])
        prev_price = float(df_5m['Close'].iloc[-2])
        change_pct = ((current_price - prev_price) / prev_price) * 100
        
        return df_1h, df_5m, change_pct, None
    except Exception as e:
        return None, None, 0, str(e)

def generate_report():
    df_1h, df_5m, change_pct, error = fetch_data()
    if error: return f"⚠️ خطأ جلب البيانات: {error}"

    engine = AdvancedSMCBlueprintEngine(df_1h, df_5m)
    res = engine.execute_strategy()
    
    targets_block = ""
    if res['tp1'] > 0:
        targets_block = f"""
🎯 *مستويات إدارة المخاطر والتنفيذ:*
• 🛑 وقف الخسارة (SL): `{res['sl']:.2f}` USD
• 🎯 الهدف الأول (TP1 - IRL/Asia): `{res['tp1']:.2f}` USD
• 🚀 الهدف الثاني (TP2 - ERL): `{res['tp2']:.2f}` USD"""

    report = f"""
🧠 *تقرير الهيكل المؤسسي الشامل (SMC Blueprint)* 🧠
⏱ *الوقت (توقيت بغداد):* {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}
🕒 *حالة الجلسة:* {res['kz_desc']}

*📍 السعر اللحظي:* `{res['price']:.2f}` USD ({change_pct:+.2f}%)
*📈 اتجاه الإطار العالي (1H):* {res['htf_bias']}
*🗺 منطقة التسعير:* {res['zone']}
*📊 مستويات السيولة ونطاق آسيا (ARL):*
  - نطاق آسيا (ARL): [`{res['arl_low']:.2f}` - `{res['arl_high']:.2f}`]
  - سيولة خارجية (ERL): [`{res['erl_low']:.2f}` - `{res['erl_high']:.2f}`]
*🔍 حالة الهيكل والاكتساح (BOS/CDC):* {res['structure']}
{targets_block}

*🚀 التوجيه الاستراتيجي:*
{res['signal']}
-----------------------------------
"""
    return report

def monitoring_loop():
    time.sleep(5)
    send_telegram_message(f"🚀 *تم تفعيل جميع خوارزميات الـ Killzones، نطاق آسيا (ARL)، ورصد الـ CDC بنجاح!* ⏱ {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}")
    
    last_signal = None
    last_report_time = 0

    while True:
        try:
            current_time = time.time()
            # تقرير دوري كل 15 دقيقة
            if current_time - last_report_time >= 900:
                report = generate_report()
                send_telegram_message(report)
                last_report_time = current_time

            # فحص فوري للتنبيهات الصارمة
            df_1h, df_5m, change_pct, error = fetch_data()
            if not error:
                engine = AdvancedSMCBlueprintEngine(df_1h, df_5m)
                res = engine.execute_strategy()
                
                is_strong = "STRONG BUY" in res['trade_type'] or "STRONG SELL" in res['trade_type']
                if is_strong and res['trade_type'] != last_signal:
                    instant_alert = f"""
🚨 *تنبيه دخول قناص فوري (Blueprint Execution)* 🚨
⏱ *الوقت (بغداد):* {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

📌 *نوع الصفقة:* {res['trade_type']}
📍 *سعر الدخول:* `{res['price']:.2f}` USD
🛑 *وقف الخسارة:* `{res['sl']:.2f}` USD
🎯 *الهدف الأول (TP1):* `{res['tp1']:.2f}` USD
🚀 *الهدف الثاني (TP2):* `{res['tp2']:.2f}` USD
-----------------------------------
"""
                    send_telegram_message(instant_alert)
                    last_signal = res['trade_type']
                    
        except Exception as e:
            print(f"Error: {e}")
            
        time.sleep(60)

@app.route("/")
def home():
    return "Full SMC Blueprint Bot with Killzones & ARL is Running!"

if __name__ == "__main__":
    t = threading.Thread(target=monitoring_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
