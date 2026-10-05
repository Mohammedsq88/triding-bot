import os
import time
import threading
import numpy as np
import pandas as pd
import yfinance as yf
import requests
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from flask import Flask

# إعداد خادم ويب بسيط لضمان استمرار البوت على المنصات السحابية
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Trading Bot is running 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- إعدادات التليجرام الخاصة بك ---
TELEGRAM_TOKEN = "8281384306:AAE9Z3XwobfvcdSpKthGvGL0jH7Wze3opDo"
CHAT_ID = "2041253195"

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"Telegram Error: {response.text}")
    except Exception as e:
        print(f"Failed to send telegram message: {e}")

# حساب مؤشر الـ ADX
def calculate_adx(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    pos_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    neg_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    
    tr_smooth = tr.ewm(alpha=1/period, adjust=False).mean()
    pos_dm_smooth = pos_dm.ewm(alpha=1/period, adjust=False).mean()
    neg_dm_smooth = neg_dm.ewm(alpha=1/period, adjust=False).mean()
    
    pos_di = 100 * (pos_dm_smooth / (tr_smooth + 1e-9))
    neg_di = 100 * (neg_dm_smooth / (tr_smooth + 1e-9))
    
    sum_di = pos_di + neg_di
    sum_di = np.where(sum_di == 0, 1e-9, sum_di)
    
    dx = 100 * (pos_di - neg_di).abs() / sum_di
    adx = pd.Series(dx, index=df.index).ewm(alpha=1/period, adjust=False).mean()
    
    return adx.bfill().ffill()

# حلقة التداول المستمرة (تعمل كل ساعة تلقائياً)
def trading_bot_loop():
    send_telegram_message("🚀 *تم تفعيل بوت التداول 24/7 بنجاح!* \nالبوت بدأ بمراقبة السوق الآن.")
    
    while True:
        try:
            print("🔄 جاري جلب بيانات السوق وتحليلها...")
            data_1h = yf.download("EURUSD=X", period="60d", interval="1h", progress=False)
            data_4h = yf.download("EURUSD=X", period="60d", interval="4h", progress=False)

            for df in [data_1h, data_4h]:
                if isinstance(df.columns, pd.MultiIndex):
                    try:
                        df.columns = df.columns.droplevel(1)
                    except:
                        df.columns = df.columns.get_level_values(0)
                df.columns = [str(col).strip() for col in df.columns]
                df.dropna(subset=['Open', 'High', 'Low', 'Close'], inplace=True)

            data_4h["HTF_SMA"] = data_4h["Close"].rolling(window=20).mean()
            data_4h["HTF_Bullish"] = (data_4h["Close"] > data_4h["HTF_SMA"]).astype(int)
            data_1h["HTF_Trend"] = data_4h["HTF_Bullish"].reindex(data_1h.index).ffill().fillna(1).astype(int)

            data_1h["SMA_50"] = data_1h["Close"].rolling(window=50).mean().bfill().ffill()
            data_1h["HL_Range"] = data_1h["High"] - data_1h["Low"]
            data_1h["Volatility_14"] = data_1h["HL_Range"].rolling(window=14).mean().bfill().ffill()

            data_1h["FVG_Bullish"] = (data_1h["Low"] > data_1h["High"].shift(2)).fillna(0).astype(int)
            data_1h["FVG_Bearish"] = (data_1h["High"] < data_1h["Low"].shift(2)).fillna(0).astype(int)

            data_1h["Body"] = abs(data_1h["Close"] - data_1h["Open"])
            data_1h["Displacement"] = (data_1h["Body"] > (data_1h["Volatility_14"] * 1.5)).fillna(0).astype(int)
            data_1h["Bullish_OB"] = ((data_1h["Displacement"] == 1) & (data_1h["Close"].shift(1) < data_1h["Open"].shift(1))).fillna(0).astype(int)

            data_1h["ADX"] = calculate_adx(data_1h, period=14)

            features = [
                "Open", "High", "Low", "Close", 
                "SMA_50", "Volatility_14", 
                "FVG_Bullish", "FVG_Bearish", 
                "Displacement", "Bullish_OB", "HTF_Trend", "ADX"
            ]

            data_1h["Target"] = (data_1h["Close"].shift(-1) > data_1h["Close"]).astype(int)
            X = data_1h[features].iloc[:-1]
            y = data_1h["Target"].iloc[:-1]

            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

            history_file = "trade_history.csv"
            sample_weights = pd.Series(1.0, index=X_train.index)

            if os.path.exists(history_file) and os.path.getsize(history_file) > 0:
                try:
                    history_df = pd.read_csv(history_file)
                    if "Result" in history_df.columns and "Time" in history_df.columns:
                        history_df["Time"] = pd.to_datetime(history_df["Time"])
                        losing_trades = history_df[history_df["Result"] == "LOSS"]
                        for t in losing_trades["Time"]:
                            if t in sample_weights.index:
                                sample_weights.loc[t] = 3.0 
                except Exception as e:
                    print(f"Note: Error parsing history file: {e}")

            model = XGBClassifier(n_estimators=100, random_state=42, eval_metric='logloss')
            model.fit(X_train, y_train, sample_weight=sample_weights)

            latest_idx = data_1h.tail(1).index[0]
            latest_data = data_1h[features].tail(1)
            current_signal = model.predict(latest_data)[0]
            current_price = data_1h.loc[latest_idx, "Close"]
            current_vol = data_1h.loc[latest_idx, "Volatility_14"]
            current_adx = data_1h.loc[latest_idx, "ADX"]

            signal_text = ""
            if pd.isna(current_adx) or current_adx < 18:
                print("⚪ السوق عرضي والـ ADX ضعيف (لا توجد صفقة).")
            else:
                if current_signal == 1:
                    sl_price = current_price - (current_vol * 1.0)
                    tp_price = current_price + (current_vol * 2.0)
                    signal_text = (
                        "🟢 *إشارة شراء جديدة (HIGH-CONFIDENCE BUY)* 🚀\n\n"
                        f"💱 *الزوج:* EURUSD\n"
                        f"📍 *سعر الدخول:* `{current_price:.5f}`\n"
                        f"🛑 *وقف الخسارة (SL):* `{sl_price:.5f}`\n"
                        f"🎯 *هدف الربح (TP):* `{tp_price:.5f}`\n"
                        f"📊 *قوة الترند (ADX):* `{current_adx:.2f}`"
                    )
                else:
                    sl_price = current_price + (current_vol * 1.0)
                    tp_price = current_price - (current_vol * 2.0)
                    signal_text = (
                        "🔴 *إشارة بيع جديدة (HIGH-CONFIDENCE SELL)* 🔻\n\n"
                        f"💱 *الزوج:* EURUSD\n"
                        f"📍 *سعر الدخول:* `{current_price:.5f}`\n"
                        f"🛑 *وقف الخسارة (SL):* `{sl_price:.5f}`\n"
                        f"🎯 *هدف الربح (TP):* `{tp_price:.5f}`\n"
                        f"📊 *قوة الترند (ADX):* `{current_adx:.2f}`"
                    )
                print(signal_text)
                send_telegram_message(signal_text)

            print("⏳ انتهاء دورة الفحص. الانتظار للشمعة القادمة (ساعة كاملة)...")
            time.sleep(3600)  # ينتظر ساعة كاملة قبل الفحص التالي

        except Exception as e:
            print(f"حدث خطأ: {e}")
            send_telegram_message(f"⚠️ تنبيه: حدث خطأ مؤقت في البوت: {e}")
            time.sleep(60)

if __name__ == "__main__":
    # تشغيل حلقة التداول في الخلفية
    t = threading.Thread(target=trading_bot_loop)
    t.daemon = True
    t.start()
    
    # تشغيل سيرفر الـ Flask
    run_flask()
