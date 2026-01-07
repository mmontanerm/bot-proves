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
# 1. CONFIGURACIÓ PROFESSIONAL
# ---------------------------------------------------------
st.set_page_config(page_title="Hedge Fund Bot 10x", layout="wide", page_icon="🏦")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# CARTERA DIVERSIFICADA
TICKERS = ['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']

TIMEFRAME = "1m"        
LEVERAGE = 5            

# GESTIÓ DE CAPITAL
ALLOCATION_PCT = 0.10   # 10% del capital per operació
MAX_POSITIONS = 10      

# OBJECTIUS
TARGET_NET_PROFIT = 0.01  # 1% Net
STOP_LOSS_PCT = 0.01      # 1% Stop Loss
COMMISSION_RATE = 0.001 

INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_hedge_data.json"

# ---------------------------------------------------------
# 2. SISTEMA DE PERSISTÈNCIA
# ---------------------------------------------------------
def save_state():
    data = {
        'balance': st.session_state.balance,
        'equity': st.session_state.equity,
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
        st.session_state.equity = saved_data.get('equity', INITIAL_CAPITAL)
        st.session_state.wins = saved_data.get('wins', 0)
        st.session_state.losses = saved_data.get('losses', 0)
        st.session_state.portfolio = saved_data.get('portfolio', {})
        st.session_state.history = saved_data.get('history', [])
        st.toast("🏦 Cartera carregada.")
else:
    if 'balance' not in st.session_state:
        st.session_state.balance = INITIAL_CAPITAL 
        st.session_state.equity = INITIAL_CAPITAL  
        st.session_state.wins = 0
        st.session_state.losses = 0
        st.session_state.history = []
    if 'portfolio' not in st.session_state:
        st.session_state.portfolio = {
            t: {'status': 'CASH', 'entry_price': 0.0, 'invested': 0.0, 'shares': 0.0, 'stop': 0.0, 'target': 0.0} 
            for t in TICKERS
        }

if len(st.session_state.history) > 50:
    st.session_state.history = st.session_state.history[-50:]

# ---------------------------------------------------------
# 3. MOTOR D'ANÀLISI
# ---------------------------------------------------------
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🏦 [HEDGE FUND]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_data_pro(tickers):
    try:
        # Timeout més alt i auto_adjust per evitar errors de Yahoo
        data = yf.download(tickers, period="5d", interval="1m", group_by='ticker', progress=False, auto_adjust=True, threads=False)
        processed = {}
        for ticker in tickers:
            try:
                if len(tickers) > 1:
                    if ticker not in data.columns.levels[0]: continue
                    df = data[ticker].copy()
                else:
                    df = data.copy()
            except: continue

            if df.empty or len(df) < 20: continue
            df = df.dropna()

            df['EMA_SHORT'] = ta.ema(df['Close'], length=9)
            df['EMA_LONG'] = ta.ema(df['Close'], length=21)
            df['RSI'] = ta.rsi(df['Close'], length=14)
            try:
                adx = ta.adx(df['High'], df['Low'], df['Close'], length=14)
                df['ADX'] = adx[adx.columns[0]] if adx is not None else 0
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
st.title("🏦 Hedge Fund Bot: Diversificació 10x")
st.caption("Estratègia: 10% per operació | 10 Valors | Comissions incloses")

current_equity = st.session_state.balance
positions_count = 0

placeholder = st.empty()

while True:
    with placeholder.container():
        market_data = get_data_pro(TICKERS)
        changes_made = False
        
        # Reiniciem càlcul temporal de l'equity
        temp_equity = st.session_state.balance
        
        cols = st.columns(5)
        
        for i, ticker in enumerate(TICKERS):
            item = st.session_state.portfolio[ticker]
            current_price = 0.0
            
            # --- CORRECCIÓ DE L'ERROR: INICIALITZEM VARIABLES ---
            net_pnl = 0.0
            net_pnl_pct = 0.0
            # ----------------------------------------------------

            if market_data and ticker in market_data:
                df = market_data[ticker]
                if len(df) >= 1:
                    current_price = float(df.iloc[-1]['Close'])
            
            if current_price == 0.0 and item['status'] == 'INVESTED':
                current_price = item['entry_price']

            # --- GESTIÓ POSICIONS ---
            if item['status'] == 'INVESTED' and current_price > 0:
                positions_count += 1
                
                # Càlculs
                gross_value = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                lev_invested = item['invested'] * LEVERAGE
                gross_pnl = gross_value - lev_invested
                commission_cost = lev_invested * COMMISSION_RATE
                
                # Assignem valors a les variables que abans donaven error
                net_pnl = gross_pnl - commission_cost
                net_pnl_pct = (net_pnl / item['invested']) 
                
                temp_equity += (item['invested'] + net_pnl)

                # Sortides
                if net_pnl_pct >= TARGET_NET_PROFIT:
                    st.session_state.balance += (item['invested'] + net_pnl)
                    st.session_state.wins += 1
                    st.session_state.history.append({
                        'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$ ({net_pnl_pct*100:.2f}%)"
                    })
                    item['status'] = 'CASH'
                    send_telegram(f"✅ WIN: {ticker}\nBenefici Net: +{net_pnl:.2f}$ (+1%)\nComissió: {commission_cost:.2f}$")
                    changes_made = True
                
                elif net_pnl_pct <= -STOP_LOSS_PCT:
                    remaining = item['invested'] + net_pnl
                    st.session_state.balance += remaining
                    st.session_state.losses += 1
                    st.session_state.history.append({
                        'Ticker': ticker, 'Res': 'LOSS', 'PL': f"{net_pnl:.2f}$ ({net_pnl_pct*100:.2f}%)"
                    })
                    item['status'] = 'CASH'
                    send_telegram(f"❌ LOSS: {ticker}\nPèrdua Neta: {net_pnl:.2f}$")
                    changes_made = True

            elif item['status'] == 'CASH' and market_data and ticker in market_data:
                df = market_data[ticker]
                if len(df) >= 2:
                    curr = df.iloc[-1]
                    prev = df.iloc[-2]
                    current_price = float(curr['Close'])
                    
                    trade_size = st.session_state.equity * ALLOCATION_PCT
                    
                    if st.session_state.balance >= trade_size:
                        trend = curr['EMA_SHORT'] > curr['EMA_LONG']
                        momentum = (prev['RSI'] < curr['RSI']) and (curr['RSI'] > 50) and (curr['RSI'] < 70)
                        strength = curr['ADX'] > 20
                        
                        if trend and momentum and strength:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = current_price
                            item['invested'] = trade_size
                            
                            st.session_state.balance -= trade_size
                            send_telegram(f"🔵 OBRINT: {ticker}\nInversió: {trade_size:.2f}$ (10%)\nPreu: {current_price:.2f}$")
                            changes_made = True
            
            # --- VISUALITZACIÓ TARGETA ---
            col_idx = i % 5 
            with cols[col_idx]:
                border = "green" if item['status'] == 'INVESTED' else "grey"
                with st.container(border=True):
                    st.markdown(f"**{ticker}**")
                    if item['status'] == 'INVESTED':
                        # ARA JA NO FALLARÀ: net_pnl sempre existeix (encara que sigui 0)
                        color = "green" if net_pnl > 0 else "red"
                        st.markdown(f"<span style='color:{color}'>{net_pnl:.2f}$</span>", unsafe_allow_html=True)
                        st.caption(f"Inv: {item['invested']:.0f}$")
                    else:
                        st.caption(f"{current_price:.2f}$")

        st.session_state.equity = temp_equity
        
        # Mètriques
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Valor Compte (Equity)", f"{st.session_state.equity:.2f} $")
        m2.metric("Cash Disponible", f"{st.session_state.balance:.2f} $")
        
        # Comptador real de posicions obertes
        open_positions = sum(1 for t in TICKERS if st.session_state.portfolio[t]['status'] == 'INVESTED')
        m3.metric("Posicions Obertes", f"{open_positions} / 10")
        
        total_trades = st.session_state.wins + st.session_state.losses
        wr = (st.session_state.wins/total_trades*100) if total_trades > 0 else 0
        m4.metric("Win Rate", f"{wr:.1f}%")

        if changes_made:
            save_state()

        if st.session_state.history:
            st.write("---")
            st.write("Historial Recent:")
            st.dataframe(pd.DataFrame(st.session_state.history).iloc[::-1].head(5))

    time.sleep(60)
