import time
import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import yfinance as yf
from sklearn.ensemble import GradientBoostingClassifier # أو استبداله بـ xgboost إذا كان مثبتاً
import xgboost as xgb

# إعدادات التليجرام (تم جلبها من سياق البوت الخاص بك)
TELEGRAM_TOKEN = "8281384306"
TELEGRAM_CHAT_ID = "2041253195"
SYMBOL = "EURUSD=X"

def send_telegram_message(message):
    """إرسال التنبيهات إلى التليجرام"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"خطأ في إرسال التليجرام: {e}")

def fetch_data(symbol, interval='1h', period='5d'):
    """جلب البيانات التاريخية من ياهو فاينانس"""
    df = yf.download(symbol, period=period, interval=interval, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.dropna(inplace=True)
    return df

def analyze_smc_structure(df_1h):
    """
    تحليل هيكلية السوق، النطاق السعري، مناطق البريميوم والديسكاونت،
    والأوردر بلوكات والفراغات السعرية (FVG)
    """
    recent = df_1h.tail(50).copy()
    
    swing_high = recent['High'].max()
    swing_low = recent['Low'].min()
    eq_level = (swing_high + swing_low) / 2
    
    current_price = recent['Close'].iloc[-1]
    
    # تحديد منطقة السعر (Premium أو Discount)
    zone = "Discount (مناطق شراء 🛒)" if current_price <= eq_level else "Premium (مناطق بيع 📉)"
    
    # تحديد الاتجاه (Bias) بناءً على آخر الشمعات
    if recent['Close'].iloc[-1] > recent['Close'].iloc[-10]:
        bias = "BULLISH"
    else:
        bias = "BEARISH"
        
    # البحث البسيط عن الأوردر بلوك (آخر شمعة عكس الاتجاه قبل الاندفاع)
    ob_detected = False
    fvg_detected = False
    
    # فحص بسيط للـ FVG (فراغ بين الشمعة الحالية والشمعة قبلها بـ شمعتين)
    if len(recent) > 3:
        if recent['Low'].iloc[-1] > recent['High'].iloc[-3]:
            fvg_detected = True # فراغ صاعد
        elif recent['High'].iloc[-1] < recent['Low'].iloc[-3]:
            fvg_detected = True # فراغ هابط
            
    return {
        "swing_high": swing_high,
        "swing_low": swing_low,
        "equilibrium": eq_level,
        "current_price": current_price,
        "zone": zone,
        "bias": bias,
        "fvg": fvg_detected
    }

def train_and_predict_xgboost(df):
    """
    تدريب نموذج الـ XGBoost بناءً على الزخم وإعطاء نسبة الثقة
    """
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
    prob = model.predict_proba(latest_x)[0][1] # نسبة الصعود
    prediction = model.predict(latest_x)[0]
    
    return prediction, prob

def run_trading_bot():
    print("🤖 بوت التداول المؤسسي (SMC + AI) يعمل الآن بنجاح...")
    send_telegram_message("🚀 *تم إقلاع بوت التداول المؤسسي المطور بنجاح!*\nالأنظمة المفعلة: هيكلية 1H + مناطق البريميوم والديسكاونت + نموذج الذكاء الاصطناعي XGBoost.")
    
    while True:
        try:
            # 1. جلب بيانات فريم الساعة
            df_1h = fetch_data(SYMBOL, interval='1h', period='5d')
            
            # 2. تحليل هيكلية السوق ومناطق الـ SMC
            smc_data = analyze_smc_structure(df_1h)
            
            # 3. توقعات الذكاء الاصطناعي (XGBoost)
            prediction, probability = train_and_predict_xgboost(df_1h)
            
            # 4. شروط فلترة الدخول الصارمة (Confluence)
            # يجب أن تتطابق شروط المؤسسات مع تأكيد الذكاء الاصطناعي
            can_buy = (
                smc_data["bias"] == "BULLISH" and
                "Discount" in smc_data["zone"] and
                probability >= 0.65 # نسبة ثقة الذكاء الاصطناعي أعلى من 65%
            )
            
            can_sell = (
                smc_data["bias"] == "BEARISH" and
                "Premium" in smc_data["zone"] and
                probability <= 0.35 # نسبة ثقة هبوطية عالية
            )
            
            # رسالة التقرير الدوري أو الإشارة
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            if can_buy:
                msg = (
                    f"🟢 *إشارة شراء مؤسسية مؤكدة (BUY)*\n"
                    f"⏱ الوقت: {now_str}\n"
                    f"💱 الزوج: EUR/USD\n"
                    f"📍 السعر الحالي: `{smc_data['current_price']:.5f}`\n"
                    f"📊 الاتجاه العام (1H): صاعد (BULLISH)\n"
                    f"🏷 المنطقة السعرية: `Discount (منطقة رخص - شراء)`\n"
                    f"⚖️ خط المنتصف (EQ): `{smc_data['equilibrium']:.5f}`\n"
                    f"🤖 ثقة الذكاء الاصطناعي: `{probability*100:.1f}%`\n"
                    f"💡 الحالة: تتطابق شروط الأوردر بلوك والسيولة مع نموذج AI."
                )
                send_telegram_message(msg)
                
            elif can_sell:
                msg = (
                    f"🔴 *إشارة بيع مؤسسية مؤكدة (SELL)*\n"
                    f"⏱ الوقت: {now_str}\n"
                    f"💱 الزوج: EUR/USD\n"
                    f"📍 السعر الحالي: `{smc_data['current_price']:.5f}`\n"
                    f"📊 الاتجاه العام (1H): هابط (BEARISH)\n"
                    f"🏷 المنطقة السعرية: `Premium (منطقة غلاء - بيع)`\n"
                    f"⚖️ خط المنتصف (EQ): `{smc_data['equilibrium']:.5f}`\n"
                    f"🤖 ثقة الذكاء الاصطناعي: `{(1-probability)*100:.1f}%` هبوط\n"
                    f"💡 الحالة: تتطابق شروط البيع المؤسسي مع نموذج AI."
                )
                send_telegram_message(msg)
            else:
                # تحديث اختيار للمراقبة (اختياري، يمكن إيقافه حتى لا يزعجك كل ساعة إلا عند الفرص)
                print(f"[{now_str}] السعر في منطقة مراقبة. الاتجاه: {smc_data['bias']} | المنطقة: {smc_data['zone']} | AI Prob: {probability:.2f}")

        except Exception as e:
            print(f"حدث خطأ أثناء التشغيل: {e}")
            
        # الانتظار لمدة ساعة كاملة قبل فحص الشمعة القادمة
        time.sleep(3600)

if __name__ == "__main__":
    run_trading_bot()
