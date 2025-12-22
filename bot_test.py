import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import time
import os
from datetime import datetime

# ---------------------------------------------------------
# 1. CONFIGURACIÓ
# ---------------------------------------------------------
st.set_page_config(page_title="Bot 24/7 Render", layout="wide", page_icon="🤖")

# Recuperem credencials de l'entorn (Render)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Paràmetres
TICKERS = ['NVDA', 'TSLA', 'META', 'MSFT', 'BTC-USD', 'ETH-USD']
TIMEFRAME = "1m"        
LEVERAGE = 5            
TARGET_PROFIT = 0.01    # 1%
STOP_LOSS_PCT = 0.005   # 0.5%
INITIAL_CAPITAL = 10000.0

# ---------------------------------------------------------
# 2. GESTIÓ D'ESTAT (MEMÒRIA)
# ---------------------------------------------------------
if 'balance' not in st.session_state:
    st.session_state.balance = INITIAL_CAPITAL
    st.session_state.wins = 0
    st.session_state.losses = 0
    st.session_state.history = []

if 'portfolio' not in st.session_state:
    st.session_state.portfolio = {
        ticker: {
            'status': 'CASH',
            'entry_price': 0.0,
            'amount_invested': 0.0,
            'stop_price': 0.0,
            'target_price': 0.0
        } for ticker in TICKERS
    }

# --- OPTIMITZACIÓ DE MEMÒRIA (NOU) ---
# Si l'historial té més de 50 files, esborrem les velles per no saturar la RAM de Render
if len(st.session_state.history) > 50:
    st.session_state.history = st.session_state.history[-50:]

# ---------------------------------------------------------
# 3. FUNCIONS
# ---------------------------------------------------------
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🤖 [BOT RENDER]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_data(tickers):
    try:
        data = yf.download(tickers, period="1d", interval="1m", group_by='ticker', progress=False)
        processed = {}
        for ticker in tickers:
            df = data[ticker].copy() if len(tickers) > 1 else data.copy()
            if df.empty: continue
            df = df.dropna()
            df['EMA'] = ta.ema(df['Close'], length=20)
            df['RSI'] = ta.rsi(df['Close'], length=14)
            processed[ticker] = df.tail(2)
        return processed
    except: return {}

# ---------------------------------------------------------
# 4. BUCLE AUTOMÀTIC (MODIFICAT)
# ---------------------------------------------------------

st.title("🤖 Bot Autònom 24/7 (Render)")
st.caption("Aquest bot s'executa automàticament. No cal prémer res.")

# Mètriques
c1, c2, c3 = st.columns(3)
c1.metric("Capital", f"{st.session_state.balance:.2f} $")
c2.metric("Wins", st.session_state.wins)
c3.metric("Losses", st.session_state.losses)

placeholder = st.empty()

# --- CANVI CLAU: BUCLE INFINIT SENSE CONDICIÓ DE BOTÓ ---
# Això força que el bot arrenqui sempre que la pàgina es carregui
while True:
    with placeholder.container():
        now = datetime.now().strftime('%H:%M:%S')
        st.write(f"📡 Escanejant mercat... {now}")
        
        market_data = get_data(TICKERS)
        
        if market_data:
            # Creem columnes per visualitzar
            cols = st.columns(3)
            
            for i, ticker in enumerate(TICKERS):
                if ticker not in market_data: continue
                
                df = market_data[ticker]
                curr = df.iloc[-1]
                prev = df.iloc[-2]
                current_price = float(curr['Close'])
                
                item = st.session_state.portfolio[ticker]
                
                # --- LÒGICA TRADING ---
                if item['status'] == 'CASH':
                    trend_ok = current_price > curr['EMA']
                    rsi_ok = prev['RSI'] < curr['RSI'] and 40 < curr['RSI'] < 70
                    
                    if trend_ok and rsi_ok:
                        item['status'] = 'INVESTED'
                        item['entry_price'] = current_price
                        item['amount_invested'] = 1000.0
                        
                        raw_move = TARGET_PROFIT / LEVERAGE
                        item['target_price'] = current_price * (1 + raw_move)
                        item['stop_price'] = current_price * (1 - STOP_LOSS_PCT)
                        
                        send_telegram(f"🔵 COMPRA SIMULADA: {ticker}\nPreu: {current_price:.2f}$")

                elif item['status'] == 'INVESTED':
                    # Take Profit
                    if current_price >= item['target_price']:
                        profit = item['amount_invested'] * TARGET_PROFIT
                        st.session_state.balance += profit
                        st.session_state.wins += 1
                        st.session_state.history.append({'Ticker': ticker, 'Res': 'WIN'})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN: {ticker}\nNou Saldo: {st.session_state.balance:.2f}$")
                    
                    # Stop Loss
                    elif current_price <= item['stop_price']:
                        loss = item['amount_invested'] * (STOP_LOSS_PCT * LEVERAGE)
                        st.session_state.balance -= loss
                        st.session_state.losses += 1
                        st.session_state.history.append({'Ticker': ticker, 'Res': 'LOSS'})
                        item['status'] = 'CASH'
                        send_telegram(f"❌ LOSS: {ticker}\nNou Saldo: {st.session_state.balance:.2f}$")

                # Visualització mini
                idx = i % 3
                with cols[idx]:
                    color = "red" if item['status'] == 'INVESTED' else "grey"
                    st.markdown(f"**{ticker}**: {current_price:.2f}$ <span style='color:{color}'>●</span>", unsafe_allow_html=True)

        # Historial limitat
        if st.session_state.history:
            st.dataframe(pd.DataFrame(st.session_state.history).iloc[::-1].head(10), height=150)

    # Espera entre cicles per no saturar la CPU
    time.sleep(60)
