class ExactSMCBlueprintEngine:
    def __init__(self, df_1h, df_5m, live_price=None):
        self.df_1h = df_1h
        self.df_5m = df_5m
        self.live_price = live_price

    def get_institutional_swings(self, df, window=5):
        """تحديد القمم والقيعان الرئيسية بدقة مؤسسية (Dealing Range Swings)"""
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

    def get_fair_value_gaps(self, df):
        """استخراج الفجوات السعرية الداخلية (FVG) لتمثيل السيولة الداخلية IRL بدقة"""
        fvgs = []
        if len(df) < 3:
            return fvgs
        
        for i in range(1, len(df) - 1):
            prev_high = float(df['High'].iloc[i-1])
            next_low = float(df['Low'].iloc[i+1])
            curr_low = float(df['Low'].iloc[i])
            curr_high = float(df['High'].iloc[i])
            
            # Bullish FVG
            if next_low > prev_high:
                fvgs.append({"type": "bullish", "level": (next_low + prev_high) / 2})
            # Bearish FVG
            elif curr_high < float(df['Low'].iloc[i-1]):
                pass
                
        return fvgs

    def analyze_market_structure(self):
        df_1h = self.df_1h
        df_5m = self.df_5m

        if len(df_1h) < 15 or len(df_1h) < 15:
            return "بيانات غير كافية", 0, 0, 0, 0, "محايد", "محايد", 0, 0

        # ERL: السيولة الخارجية من الإطار العالي (1H Swing Highs/Lows)
        h_highs_1h, h_lows_1h = self.get_institutional_swings(df_1h, window=4)
        erl_high = h_highs_1h[-1]['price'] if h_highs_1h else float(df_1h['High'].max())
        erl_low = h_lows_1h[-1]['price'] if h_lows_1h else float(df_1h['Low'].min())
        
        ref_close = self.live_price if self.live_price else float(df_1h['Close'].iloc[-1])
        htf_bias = "صاعد (Bullish 📈)" if ref_close > float(df_1h['Close'].iloc[-5]) else "هابط (Bearish 📉)"

        # IRL: السيولة الداخلية مستمدة من الفجوات (FVG) أو قيعان الـ 5 دقائق الداخلية
        arl_high, arl_low = self.get_asia_session_range()
        h_highs_5m, h_lows_5m = self.get_institutional_swings(df_5m, window=3)
        
        # تحديد أقرب مستوى سيولة داخلية (IRL) بناءً على الهيكل الداخلي أو الفجوات
        fvgs = self.get_fair_value_gaps(df_5m)
        nearest_fvg = fvgs[-1]['level'] if fvgs else (ref_close + 2.0 if htf_bias.startswith("صاعد") else ref_close - 2.0)
        
        irl_high = float(h_highs_5m[-1]['price']) if h_highs_5m else nearest_fvg
        irl_low = float(h_lows_5m[-1]['price']) if h_lows_5m else nearest_fvg

        structure_status = "🔄 بانتظار تشكل كسر هيكل حقيقي (BOS)"
        broken_level = 0.0

        if h_highs_5m:
            last_swing_high = h_highs_5m[-1]['price']
            if ref_close > last_swing_high:
                structure_status = f"✅ True BOS Bullish فوق: `{last_swing_high:.2f}`"
                broken_level = last_swing_high

        if h_lows_5m and broken_level == 0.0:
            last_swing_low = h_lows_5m[-1]['price']
            if ref_close < last_swing_low:
                structure_status = f"✅ True BOS Bearish تحت: `{last_swing_low:.2f}`"
                broken_level = last_swing_low

        return structure_status, broken_level, erl_high, erl_low, irl_high, irl_low, htf_bias, "صاعد (Bullish ⚡)" if ref_close > float(df_5m['Close'].iloc[-5]) else "هابط (Bearish ⚡)", arl_high, arl_low

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
