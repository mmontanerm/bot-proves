import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import time
import os
import json
from datetime import datetime

# ---------------------------------------------------------
# 1. CONFIGURACIÓ
# ---------------------------------------------------------
st.set_page_config(page_title="Bot Mono-Task Realista", layout="wide", page_icon="🎯")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TICKERS = ['NVDA', 'TSLA', 'META', 'MSFT', 'BTC-USD', 'ETH-USD']
TIMEFRAME = "1m"        
LEVERAGE = 5            
TARGET_PROFIT = 0.015   # 1.5% Guany
STOP_LOSS_PCT = 0.003   # 0.3% Pèrdua real (x5 = 1.5%)
INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_data.json"

# ---------------------------------------------------------
# 2. GESTIÓ DE MEMÒRIA (PERSISTÈNCIA)
# ---------------------------------------------------------
def save_state():
    data = {
        'balance': st.session_state.balance,
        'wins': st.session_state.wins,
        'losses': st.session_state.losses,
        'portfolio': st.session_state.portfolio,
        'history': st.session_state.history,
        'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
    except: pass

def load_state():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except: return None
    return None

# Inicialització
saved_data = load_state()
if saved_data:
    if 'balance' not in st.session_state:
        st.session_state.balance = saved_data.get('balance', INITIAL_CAPITAL)
        st.session_state.wins = saved_data.get('wins', 0)
        st.session_state.losses = saved_data.get('losses', 0)
        st.session_state.portfolio = saved_data.get('portfolio', {})
        st.session_state.history = saved_data.get('history', [])
        st.toast("💾 Estat recuperat.")
else:
    if 'balance' not in st.session_state:
        st.session_state.balance = INITIAL_CAPITAL
        st.session_state.wins = 0
        st.session_state.losses = 0
        st.session_state.history = []
    if 'portfolio' not in st.session_state:
        st.session_state.portfolio = {
            t: {'status': 'CASH', 'entry_price': 0.0, 'amount_invested': 0.0, 'stop_price': 0.0, 'target_price': 0.0} 
            for t in TICKERS
        }

if len(st.session_state.history) > 50:
    st.session_state.history = st.session_state.history[-50:]

# ---------------------------------------------------------
# 3. FUNCIONS AUXILIARS
# ---------------------------------------------------------
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🎯 [BOT REALISTA]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_data_optimized(tickers):
    try:
        data = yf.download(tickers, period="5d", interval="1m", group_by='ticker', progress=False, auto_adjust=True)
        processed = {}
        for ticker in tickers:
            try:
                if len(tickers) > 1:
                    if ticker not in data.columns.levels[0]: continue
                    df = data[ticker].copy()
                else:
                    df = data.copy()
            except: continue

            if df.empty: continue
            df = df.dropna()
            if len(df) < 20: continue

            df['EMA'] = ta.ema(df['Close'], length=20)
            df['RSI'] = ta.rsi(df['Close'], length=14)
            try:
                adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
                df['ADX'] = adx_df[adx_df.columns[0]] if adx_df is not None else 0
            except: df['ADX'] = 0
            df['VOL_SMA'] = ta.sma(df['Volume'], length=20)
            df = df.dropna()

            if not df.empty:
                processed[ticker] = df.tail(2)
        return processed
    except: return {}

# ---------------------------------------------------------
# 4. BUCLE PRINCIPAL
# ---------------------------------------------------------
st.title("🎯 Bot Mono-Tasca (1 Operació Simultània)")
st.caption("Només s'obre una posició a la vegada utilitzant TOT el capital disponible.")

c1, c2, c3 = st.columns(3)
c1.metric("Capital Total", f"{st.session_state.balance:.2f} $")
total = st.session_state.wins + st.session_state.losses
winrate = (st.session_state.wins/total*100) if total > 0 else 0
c2.metric("Win Rate", f"{winrate:.1f}%")
c3.metric("Trades", total)

placeholder = st.empty()

while True:
    with placeholder.container():
        # 1. Comprovem si JA tenim alguna posició oberta
        # Busquem si algun ticker té l'estat 'INVESTED'
        active_ticker = None
        for t in TICKERS:
            if st.session_state.portfolio[t]['status'] == 'INVESTED':
                active_ticker = t
                break
        
        status_msg = f"🔒 GESTIONANT {active_ticker}" if active_ticker else "🔍 ESCANEJANT MERCAT (CASH)"
        st.write(f"📡 {status_msg} - {datetime.now().strftime('%H:%M:%S')}")

        market_data = get_data_optimized(TICKERS)
        changes_made = False
        
        if market_data:
            cols = st.columns(3)
            
            for i, ticker in enumerate(TICKERS):
                if ticker not in market_data: continue
                df = market_data[ticker]
                if len(df) < 2: continue

                curr = df.iloc[-1]
                prev = df.iloc[-2]
                if pd.isna(curr['Close']): continue
                current_price = float(curr['Close'])
                
                item = st.session_state.portfolio[ticker]

                # --- LÒGICA MONO-TASCA ---

                # CAS A: ESTEM DINS D'AQUEST TICKER (Gestionar Venda)
                if item['status'] == 'INVESTED':
                    # Take Profit
                    if current_price >= item['target_price']:
                        profit = item['amount_invested'] * TARGET_PROFIT
                        st.session_state.balance += profit
                        st.session_state.wins += 1
                        st.session_state.history.append({'Ticker': ticker, 'Res': 'WIN', 'Amt': f"+{profit:.2f}"})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN: {ticker}\nBenefici: +{profit:.2f}$\nNou Capital: {st.session_state.balance:.2f}$")
                        changes_made = True
                    
                    # Stop Loss
                    elif current_price <= item['stop_price']:
                        loss = item['amount_invested'] * (STOP_LOSS_PCT * LEVERAGE)
                        st.session_state.balance -= loss
                        st.session_state.losses += 1
                        st.session_state.history.append({'Ticker': ticker, 'Res': 'LOSS', 'Amt': f"-{loss:.2f}"})
                        item['status'] = 'CASH'
                        send_telegram(f"❌ LOSS: {ticker}\nPèrdua: -{loss:.2f}$\nNou Capital: {st.session_state.balance:.2f}$")
                        changes_made = True

                # CAS B: ESTEM FORA (CASH) I VOLEM ENTRAR
                elif item['status'] == 'CASH':
                    # CONDICIÓ CRUCIAL: Només entrem si NO hi ha cap altra posició activa (active_ticker és None)
                    if active_ticker is None:
                        # Lògica d'entrada (Estratègia)
                        trend_ok = current_price > curr['EMA']
                        rsi_ok = (prev['RSI'] < curr['RSI']) and (45 < curr['RSI'] < 70)
                        adx_ok = curr['ADX'] > 20
                        vol_ok = curr['Volume'] > curr['VOL_SMA']
                        
                        if trend_ok and rsi_ok and adx_ok and vol_ok:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = current_price
                            
                            # ARA INVERTIM TOT EL CAPITAL (ALL-IN)
                            item['amount_invested'] = st.session_state.balance 
                            
                            raw_move = TARGET_PROFIT / LEVERAGE
                            item['target_price'] = current_price * (1 + raw_move)
                            item['stop_price'] = current_price * (1 - STOP_LOSS_PCT)
                            
                            send_telegram(f"🔵 OBRINT POSICIÓ: {ticker}\nInversió: {item['amount_invested']:.2f}$ (Tot el capital)\nPreu: {current_price:.2f}$")
                            changes_made = True
                            # Forcem que active_ticker sigui aquest per no obrir-ne més en aquest bucle
                            active_ticker = ticker 

                # Visualització
                idx = i % 3
                with cols[idx]:
                    # Si hi ha un actiu, els altres es veuen grisos i apagats
                    if active_ticker and active_ticker != ticker:
                        opacity = "0.3" # Apagat
                        border_color = "grey"
                        status_txt = "Bloquejat"
                    elif active_ticker == ticker:
                        opacity = "1.0" # Actiu
                        border_color = "green"
                        status_txt = "ACTIU 🟢"
                    else:
                        opacity = "0.8" # Disponible
                        border_color = "grey"
                        status_txt = "Escanejant..."

                    st.markdown(f"""
                    <div style="opacity: {opacity}; border: 1px solid {border_color}; padding: 10px; border-radius: 5px;">
                        <strong>{ticker}</strong>: {current_price:.2f}$<br>
                        <small>{status_txt}</small>
                    </div>
                    """, unsafe_allow_html=True)

        if changes_made:
            save_state()

        if st.session_state.history:
            st.dataframe(pd.DataFrame(st.session_state.history).iloc[::-1].head(5), height=150)

    time.sleep(60)
