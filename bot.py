import time
import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import yfinance as yf
import xgboost as xgb
from flask import Flask
import threading

# إعداد خادم فلاسك لفتح الـ Port وإرضاء منصة Render
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Gold SMC + AI Trading Bot is active and running!"

# إعدادات التليجرام وتغيير الرمز إلى الذهب (GC=X)
TELEGRAM_TOKEN = "8669845166:AAffLfdsgcuE14wFZwBv1tXEQFYPeNcIsFQ"
TELEGRAM_CHAT_ID = "2041253195"
SYMBOL = "GC=X"  # رمز الذهب (Gold Futures) على ياهو فاينانس

def send_telegram_message(message):
    """إرسال التنبيهات إلى التليجرام مع طباعة السبب في حال وجود خطأ"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"⚠️ خطأ من تليجرام: {response.text}", flush=True)
    except Exception as e:
        print(f"خطأ في الاتصال بتليجرام: {e}", flush=True)

def fetch_data(symbol, interval='1h', period='5d'):
    """جلب البيانات التاريخية من ياهو فاينانس"""
    df = yf.download(symbol, period=period, interval=interval, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.dropna(inplace=True)
    return df

def analyze_smc_structure(df_1h):
    """تحليل هيكلية السوق، النطاق السعري، ومناطق البريميوم والديسكاونت"""
    recent = df_1h.tail(50).copy()
    
    swing_high = recent['High'].max()
    swing_low = recent['Low'].min()
    eq_level = (swing_high + swing_low) / 2
    
    current_price = recent['Close'].iloc[-1]
    
    zone = "Discount (مناطق شراء 🛒)" if current_price <= eq_level else "Premium (مناطق بيع 📉)"
    
    if recent['Close'].iloc[-1] > recent['Close'].iloc[-10]:
        bias = "BULLISH"
    else:
        bias = "BEARISH"
        
    return {
        "swing_high": swing_high,
        "swing_low": swing_low,
        "equilibrium": eq_level,
        "current_price": current_price,
        "zone": zone,
        "bias": bias
    }

def train_and_predict_xgboost(df):
    """تدريب نموذج الـ XGBoost وإعطاء نسبة الثقة"""
    df['Returns'] = df['Close'].pct_change()
    df['Target'] = np.where(df['Close'].shift(-1) > df['Close'], 1, 0)
    df.dropna(inplace=True)
    
    features = ['Returns']
    X = df[features]
    y = df['Target']
    
    if len(X) < 20:
        return 0, 0.5
        
    model = xgb.XGBClassifier(n_estimators=50, max_depth=3, learning_rate=0.05, random_state=42, verbosity=0)
    model.fit(X, y)
    
    latest_x = X.tail(1)
    prob = model.predict_proba(latest_x)[0][1]
    prediction = model.predict(latest_x)[0]
    
    return prediction, prob

def run_trading_bot():
    """حلقة عمل البوت المستمرة للذهب"""
    print("🤖 بوت تداول الذهب المؤسسي (SMC + AI) بدأ بالعمل...", flush=True)
    try:
        send_telegram_message("🚀 *تم إقلاع بوت تداول الذهب (Gold) المؤسسي بنجاح!*\nالأنظمة المفعلة: هيكلية 1H + مناطق البريميوم والديسكاونت + نموذج الذكاء الاصطناعي XGBoost.")
    except Exception as e:
        print(f"خطأ في إرسال رسالة الإقلاع: {e}", flush=True)
    
    while True:
        try:
            df_1h = fetch_data(SYMBOL, interval='1h', period='5d')
            smc_data = analyze_smc_structure(df_1h)
            prediction, probability = train_and_predict_xgboost(df_1h)
            
            can_buy = (
                smc_data["bias"] == "BULLISH" and
                "Discount" in smc_data["zone"] and
                probability >= 0.65
            )
            
            can_sell = (
                smc_data["bias"] == "BEARISH" and
                "Premium" in smc_data["zone"] and
                probability <= 0.35
            )
            
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            if can_buy:
                msg = (
                    f"🟢 *إشارة شراء ذهب مؤسسية مؤكدة (BUY)*\n"
                    f"⏱ الوقت: {now_str}\n"
                    f"🟡 الأداة: الذهب (Gold - GC=X)\n"
                    f"📍 السعر الحالي: `{smc_data['current_price']:.2f}`\n"
                    f"📊 الاتجاه العام (1H): صاعد (BULLISH)\n"
                    f"🏷 المنطقة السعرية: `Discount (منطقة رخص - شراء)`\n"
                    f"⚖ خط المنتصف (EQ): `{smc_data['equilibrium']:.2f}`\n"
                    f"🤖 ثقة الذكاء الاصطناعي: `{probability*100:.1f}%`"
                )
                send_telegram_message(msg)
                
            elif can_sell:
                msg = (
                    f"🔴 *إشارة بيع ذهب مؤسسية مؤكدة (SELL)*\n"
                    f"⏱ الوقت: {now_str}\n"
                    f"🟡 الأداة: الذهب (Gold - GC=X)\n"
                    f"📍 السعر الحالي: `{smc_data['current_price']:.2f}`\n"
                    f"📊 الاتجاه العام (1H): هابط (BEARISH)\n"
                    f"🏷 المنطقة السعرية: `Premium (منطقة غلاء - بيع)`\n"
                    f"⚖️ خط المنتصف (EQ): `{smc_data['equilibrium']:.2f}`\n"
                    f"🤖 ثقة الذكاء الاصطناعي: `{(1-probability)*100:.1f}%` هبوط"
                )
                send_telegram_message(msg)

        except Exception as e:
            print(f"❌ خطأ أثناء التشغيل داخل الحلقة: {e}", flush=True)
            
        time.sleep(3600)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_trading_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
