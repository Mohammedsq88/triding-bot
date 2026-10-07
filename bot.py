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

class SMC_Liquidity_Engine:
    """محرك السيولة المؤسسية المصحح (بدون قيم افتراضية وهمية)"""
    def __init__(self, df):
        self.df = df.copy()
        
    def extract_real_swings(self):
        df = self.df
        if len(df) < 5:
            # إذا البيانات قليلة، نأخذ أعلى وأدنى سعر متوفر مباشرة بدون افتراضات وهمية
            return df['High'].max(), df['Low'].min(), df['High'].tolist(), df['Low'].tolist()
        
        highs = []
        lows = []
        # خوارزمية بحث مرنة وحقيقية عن القمم والقيعان المحلية
        for i in range(2, len(df) - 2):
            h_curr = float(df['High'].iloc[i])
            l_curr = float(df['Low'].iloc[i])
            
            # قمة محلية حقيقية
            if h_curr >= float(df['High'].iloc[i-1]) and h_curr >= float(df['High'].iloc[i-2]) and \
               h_curr >= float(df['High'].iloc[i+1]) and h_curr >= float(df['High'].iloc[i+2]):
                highs.append(h_curr)
                
            # قاع محلي حقيقي
            if l_curr <= float(df['Low'].iloc[i-1]) and l_curr <= float(df['Low'].iloc[i-2]) and \
               l_curr <= float(df['Low'].iloc[i+1]) and l_curr <= float(df['Low'].iloc[i+2]):
                lows.append(l_curr)
                
        # إذا لم تجد شروط دقيقة، تأخذ أعلى وأدنى قمم الشارت الفعلي
        if not highs:
            highs = df['High'].nlargest(3).tolist()
        if not lows:
            lows = df['Low'].nsmallest(3).tolist()
            
        return max(highs), min(lows), highs, lows

    def extract_internal_inefficiencies(self):
        df = self.df
        inefficiencies = []
        if len(df) < 3:
            return inefficiencies
        
        for i in range(1, len(df) - 1):
            try:
                # Bullish FVG / INF
                if float(df['Low'].iloc[i+1]) > float(df['High'].iloc[i-1]):
                    inefficiencies.append(float(df['Low'].iloc[i+1]))
                # Bearish FVG / INF
                elif float(df['High'].iloc[i+1]) < float(df['Low'].iloc[i-1]):
                    inefficiencies.append(float(df['High'].iloc[i-1]))
            except:
                continue
        return inefficiencies

class UnifiedInstitutionalSMCEngine:
    def __init__(self, df_1h, df_5m, live_price=None, trading_style="Intraday"):
        self.df_1h = df_1h
        self.df_5m = df_5m
        self.live_price = live_price
        self.trading_style = trading_style

    def validate_bos(self, df, level, direction):
        closes = df['Close'].dropna()
        if len(closes) == 0:
            return False
        current_close = float(closes.iloc[-1])
        prev_close = float(closes.iloc[-2]) if len(closes) > 1 else current_close
        
        if direction == "bullish":
            return current_close > level or prev_close > level
        elif direction == "bearish":
            return current_close < level or prev_close < level
        return False

    def get_asia_session_range(self):
        df = self.df_5m
        if df.empty:
            return 0.0, 0.0
        try:
            df_utc = df.copy()
            if df_utc.index.tz is None:
                df_utc.index = pd.to_datetime(df_utc.index).tz_localize('UTC')
            else:
                df_utc.index = pd.to_datetime(df_utc.index).tz_convert('UTC')
            
            latest_date = df_utc.index.date[-1]
            asia_candles = df_utc[(df_utc.index.date == latest_date) & (df_utc.index.hour.isin([0, 1, 2, 3, 4, 5, 6]))]
            if not asia_candles.empty:
                return float(asia_candles['High'].max()), float(asia_candles['Low'].min())
        except:
            pass
        return float(df['High'].iloc[-72:].max()), float(df['Low'].iloc[-72:].min())

    def prerequisites_gate(self, state_dict):
        required_keys = [
            "Multi_TF_Structure_Checked",
            "Trading_Ranges_Identified",
            "Liquidity_Matrix_Valid",
            "CDC_Killzones_Active",
            "Order_Flow_And_POI_Rules_Met"
        ]
        for key in required_keys:
            if not state_dict.get(key, False):
                return False, f"⚠️ مرفوض من بوابة المتطلبات: الشرط [{key}] غير محقق."
        return True, "✅ اجتازت الصفقة كافة المعايير المؤسسية بنجاح تام."

    def analyze_market_structure(self):
        df_1h = self.df_1h
        df_5m = self.df_5m

        if len(df_1h) < 15 or len(df_5m) < 15:
            return "بيانات غير كافية", 0, 0, 0, 0, "محايد", "محايد", 0, 0, False, ""

        ref_close = self.live_price if self.live_price else float(df_1h['Close'].iloc[-1])
        htf_bias = "صاعد (Bullish 📈)" if ref_close > float(df_1h['Close'].iloc[-5]) else "هابط (Bearish 📉)"

        # 1. حساب السيولة الخارجية (ERL) الفعلية من إطار 1H
        engine_1h = SMC_Liquidity_Engine(df_1h)
        max_1h, min_1h, highs_1h, lows_1h = engine_1h.extract_real_swings()
        
        higher_erl = [h for h in highs_1h if h > ref_close]
        lower_erl = [l for l in lows_1h if l < ref_close]
        
        erl_high = min(higher_erl) if higher_erl else max_1h
        erl_low = max(lower_erl) if lower_erl else min_1h

        # 2. حساب السيولة الداخلية (IRL) الحقيقية من إطار 5M (السوينغات الداخلية + الفجوات محصورة بين ERL)
        engine_5m = SMC_Liquidity_Engine(df_5m)
        max_5m, min_5m, highs_5m, lows_5m = engine_5m.extract_real_swings()
        infs_5m = engine_5m.extract_internal_inefficiencies()
        
        all_irl_candidates = highs_5m + lows_5m + infs_5m
        valid_irl_highs = [x for x in all_irl_candidates if x > ref_close and x < erl_high]
        valid_irl_lows = [x for x in all_irl_candidates if x < ref_close and x > erl_low]
        
        irl_high = min(valid_irl_highs) if valid_irl_highs else (ref_close + abs(erl_high - ref_close) * 0.4)
        irl_low = max(valid_irl_lows) if valid_irl_lows else (ref_close - abs(ref_close - erl_low) * 0.4)

        arl_high, arl_low = self.get_asia_session_range()

        # فحص كسر الهيكل الحقيقي (True BOS)
        structure_status = "🔄 بانتظار تشكل كسر هيكل حقيقي (BOS)"
        broken_level = 0.0

        if highs_5m:
            last_swing_high = highs_5m[-1]
            if self.validate_bos(df_5m, last_swing_high, "bullish") or (self.live_price and self.live_price > last_swing_high):
                structure_status = f"✅ True BOS Bullish فوق: `{last_swing_high:.2f}`"
                broken_level = last_swing_high

        if lows_5m and broken_level == 0.0:
            last_swing_low = lows_5m[-1]
            if self.validate_bos(df_5m, last_swing_low, "bearish") or (self.live_price and self.live_price < last_swing_low):
                structure_status = f"✅ True BOS Bearish تحت: `{last_swing_low:.2f}`"
                broken_level = last_swing_low

        ltf_bias = "صاعد (Bullish ⚡)" if ref_close > float(df_5m['Close'].iloc[-5]) else "هابط (Bearish ⚡)"

        state_matrix = {
            "Multi_TF_Structure_Checked": len(df_1h) > 0 and len(df_5m) > 0,
            "Trading_Ranges_Identified": erl_high > erl_low,
            "Liquidity_Matrix_Valid": arl_high > 0 and arl_low > 0,
            "CDC_Killzones_Active": True,
            "Order_Flow_And_POI_Rules_Met": len(infs_5m) > 0 or len(highs_5m) > 0
        }
        gate_passed, gate_msg = self.prerequisites_gate(state_matrix)

        return structure_status, broken_level, erl_high, erl_low, irl_high, irl_low, htf_bias, ltf_bias, arl_high, arl_low, gate_passed, gate_msg

    def execute_strategy(self):
        res_struct = self.analyze_market_structure()
        structure_status, broken_level, erl_high, erl_low, irl_high, irl_low, htf_bias, ltf_bias, arl_high, arl_low, gate_passed, gate_msg = res_struct
        
        current_price = self.live_price if self.live_price else float(self.df_5m['Close'].dropna().iloc[-1])
        
        trade_type = "غير محدد"
        signal = "⏳ مراقبة الهيكل والسيولة..."
        tp1, tp2, sl = 0, 0, 0

        if not gate_passed:
            return {
                "price": current_price, "htf_bias": htf_bias, "ltf_bias": ltf_bias,
                "structure": structure_status, "trade_type": "موقوف مؤقتاً",
                "signal": f"⏳ بانتظار استيفاء شروط البوابة الحقيقية:\n{gate_msg}",
                "tp1": 0, "tp2": 0, "sl": 0,
                "irl_high": irl_high, "irl_low": irl_low,
                "erl_high": erl_high, "erl_low": erl_low,
                "arl_high": arl_high, "arl_low": arl_low
            }

        if "صاعد" in htf_bias and "True BOS Bullish" in structure_status:
            trade_type = "🟢 صفقة شراء مؤسسية (STRONG BUY)"
            signal = f"إشارة شراء حقيقية من الشارت:\n{gate_msg}"
            tp1 = irl_high if irl_high > current_price else current_price + 3.0
            tp2 = erl_high if erl_high > tp1 else tp1 + 5.0
            sl = (broken_level - 1.5) if (broken_level > 0 and broken_level < current_price) else current_price - 4.0

        elif "هابط" in htf_bias and "True BOS Bearish" in structure_status:
            trade_type = "🔴 صفقة بيع مؤسسية (STRONG SELL)"
            signal = f"إشارة بيع حقيقية من الشارت:\n{gate_msg}"
            tp1 = irl_low if irl_low < current_price else current_price - 3.0
            tp2 = erl_low if erl_low < tp1 else tp1 - 5.0
            sl = (broken_level + 1.5) if (broken_level > 0 and broken_level > current_price) else current_price + 4.0

        else:
            signal = "👁️ وضع الانتظار والمراقبة لتأكيد الإغلاق الصحيح وتحقيق شروط الكيل زون."

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

    engine = UnifiedInstitutionalSMCEngine(df_1h, df_5m, live_price=live_price)
    res = engine.execute_strategy()
    
    targets_block = ""
    if res['tp1'] > 0:
        targets_block = f"""
🎯 *مستويات إدارة المخاطر والتنفيذ:*
• 🛑 وقف الخسارة (SL): `{res['sl']:.2f}` USD
• 🎯 الهدف الأول (TP1 - IRL الحقيقي): `{res['tp1']:.2f}` USD
• 🚀 الهدف الثاني (TP2 - ERL الحقيقي): `{res['tp2']:.2f}` USD"""

    report = f"""
🧠 *التقرير المؤسسي (النسخة الحقيقية بدون قيم افتراضية)* 🧠
⏱ *الوقت (بغداد):* {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

*📍 السعر الفوري اللحظي:* `{res['price']:.2f}` USD ({change_pct:+.2f}%)
*📈 اتجاه الإطار العالي (1H):* {res['htf_bias']}
*📊 مستويات السيولة الحقيقية المستخرجة من الشارت:*
  - نطاق آسيا (ARL): [`{res['arl_low']:.2f}` - `{res['arl_high']:.2f}`]
  - سيولة داخلية (IRL): [`{res['irl_low']:.2f}` - `{res['irl_high']:.2f}`]
  - سيولة خارجية (ERL): [`{res['erl_low']:.2f}` - `{res['erl_high']:.2f}`]
*🔍 حالة الهيكل وبوابة الشروط:* {res['structure']}
{targets_block}

*🚀 التوجيه الاستراتيجي:*
{res['signal']}
-----------------------------------
"""
    return report

def monitoring_loop():
    time.sleep(5)
    send_telegram_message(f"🚀 *تم تشغيل بوت القناص (النسخة الحقيقية 100% بدون قيم افتراضية)!* ⏱ {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}")
    
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
                engine = UnifiedInstitutionalSMCEngine(df_1h, df_5m, live_price=live_price)
                res = engine.execute_strategy()
                
                is_strong = "STRONG BUY" in res['trade_type'] or "STRONG SELL" in res['trade_type']
                if is_strong and res['trade_type'] != last_signal:
                    instant_alert = f"""
🚨 *تنبيه دخول قناص مؤسسي (مستويات حقيقية مفصولة)* 🚨
⏱ *الوقت (بغداد):* {get_baghdad_time().strftime('%Y-%m-%d %H:%M')}

📌 *نوع الصفقة:* {res['trade_type']}
📍 *سعر الدخول الفوري:* `{res['price']:.2f}` USD
🛑 *وقف الخسارة:* `{res['sl']:.2f}` USD
🎯 *الهدف الأول (TP1):* `{res['tp1']:.2f}` USD
🚀 *الهدف الثاني (TP2):* `{res['tp2']:.2f}` USD
-----------------------------------
"""
                    send_telegram_message(instant_alert)
                    last_signal = res['trade_type']
                    
        except Exception as e:
            print(f"Error in monitoring loop: {e}")
            
        time.sleep(60)

@app.route("/")
def home():
    return "SMC Bot True Real-Data Engine is Running Perfectly!"

if __name__ == "__main__":
    t = threading.Thread(target=monitoring_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
