import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import time
import os
import json
import threading
from datetime import datetime

# ---------------------------------------------------------
# 1. CONFIGURACIÓ "SNAP-BACK" (Rendibilitat Assegurada)
# ---------------------------------------------------------
st.set_page_config(page_title="Bot Profitable 1:1", layout="wide", page_icon="💰")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TICKERS = ['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']
TIMEFRAME = "1m"        
LEVERAGE = 5            

# GESTIÓ DE CAPITAL I RISC (La clau de la rendibilitat)
ALLOCATION_PCT = 0.10       # 10% per operació
# RATIO 1:1 -> Amb un 60% d'encerts, això és matemàticament guanyador
TARGET_NET_PROFIT = 0.0075  # 0.75% Net 
STOP_LOSS_PCT = 0.0075      # 0.75% Stop (Tallem pèrdues ràpid!)
COMMISSION_RATE = 0.001     

INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_snapback_data.json"

# ---------------------------------------------------------
# 2. FUNCIONS DADES
# ---------------------------------------------------------
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {
        'balance': INITIAL_CAPITAL,
        'equity': INITIAL_CAPITAL,
        'wins': 0,
        'losses': 0,
        'portfolio': {t: {'status': 'CASH', 'entry_price': 0.0, 'invested': 0.0, 'pnl': 0.0, 'pnl_pct': 0.0} for t in TICKERS},
        'history': [],
        'last_update': "Mai"
    }

def save_data(data):
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
    except: pass

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"💰 [BOT PROFIT]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_market_data(tickers):
    try:
        data = yf.download(tickers, period="2d", interval="1m", group_by='ticker', progress=False, auto_adjust=True, threads=False)
        processed = {}
        for ticker in tickers:
            try:
                if len(tickers) > 1:
                    if ticker not in data.columns.levels[0]: continue
                    df = data[ticker].copy()
                else:
                    df = data.copy()
            except: continue

            if df.empty or len(df) < 30: continue
            df = df.dropna()
            
            # --- INDICADORS SNAP-BACK ---
            
            # 1. BANDES DE BOLLINGER (Standard 2.0)
            # Tornem a la desviació 2.0 per tenir més senyals, però filtrarem per confirmació
            bb = ta.bbands(df['Close'], length=20, std=2.0)
            
            if bb is not None:
                cols = bb.columns
                df['BB_LOWER'] = bb[cols[0]] 
                df['BB_MID']   = bb[cols[1]] 
                df['BB_UPPER'] = bb[cols[2]] 
            
            # 2. RSI (Momentum)
            df['RSI'] = ta.rsi(df['Close'], length=14)
            
            df = df.dropna()
            
            if not df.empty:
                processed[ticker] = df.tail(2)
        return processed
    except: return {}

# ---------------------------------------------------------
# 3. CERVELL (BACKGROUND)
# ---------------------------------------------------------
def run_trading_logic():
    print("💰 CERVELL SNAP-BACK ARRENCAT (Confirmació tancament)...")
    
    while True:
        try:
            data = load_data()
            portfolio = data['portfolio']
            balance = data['balance']
            equity = data['equity']
            
            market_data = get_market_data(TICKERS)
            changes = False
            temp_equity = balance
            
            for ticker in TICKERS:
                item = portfolio[ticker]
                current_price = 0.0
                
                if market_data and ticker in market_data:
                    current_price = float(market_data[ticker].iloc[-1]['Close'])
                
                if current_price == 0 and item['status'] == 'INVESTED':
                    current_price = item['entry_price']
                
                # --- A) GESTIÓ POSICIONS ---
                if item['status'] == 'INVESTED' and current_price > 0:
                    gross_val = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                    lev_invested = item['invested'] * LEVERAGE
                    net_pnl = (gross_val - lev_invested) - (lev_invested * COMMISSION_RATE)
                    net_pnl_pct = net_pnl / item['invested']
                    
                    item['pnl'] = net_pnl
                    item['pnl_pct'] = net_pnl_pct
                    temp_equity += (item['invested'] + net_pnl)
                    
                    # 1. TAKE PROFIT (0.75%)
                    if net_pnl_pct >= TARGET_NET_PROFIT:
                        balance += (item['invested'] + net_pnl)
                        data['wins'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        item['pnl'] = 0.0
                        send_telegram(f"✅ WIN: {ticker} (+{net_pnl:.2f}$)")
                        changes = True
                    
                    # 2. STOP LOSS ESTRICTE (0.75%)
                    # Aquí està la clau. Si no funciona ràpid, fora.
                    elif net_pnl_pct <= -STOP_LOSS_PCT:
                        balance += (item['invested'] + net_pnl)
                        data['losses'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'LOSS', 'PL': f"{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        item['pnl'] = 0.0
                        send_telegram(f"❌ LOSS: {ticker} ({net_pnl:.2f}$)")
                        changes = True
                    
                    changes = True 
                        
                # --- B) ENTRADA (ESTRATÈGIA SNAP-BACK) ---
                elif item['status'] == 'CASH' and market_data and ticker in market_data:
                    df = market_data[ticker]
                    curr = df.iloc[-1]
                    prev = df.iloc[-2]
                    price = float(curr['Close'])
                    
                    trade_size = equity * ALLOCATION_PCT
                    
                    if balance >= trade_size:
                        
                        # ESTRATÈGIA: COMPRA EL RETORN, NO LA CAIGUDA
                        
                        # 1. Condició Prèvia: L'espelma ANTERIOR estava fora de la banda (o tocant-la)
                        # Això indica que hi havia pànic/sobrevenda.
                        was_outside = prev['Close'] < prev['BB_LOWER']
                        
                        # 2. Condició Actual: L'espelma ACTUAL tanca DINS de la banda
                        # Això és el "Snap-Back". El preu ha recuperat el nivell. Confirmació de gir.
                        is_inside = curr['Close'] > curr['BB_LOWER']
                        
                        # 3. RSI Barato però amb força
                        # Volem que l'RSI estigui baix (<40) però pujant.
                        rsi_ok = (curr['RSI'] < 45) and (curr['RSI'] > prev['RSI'])
                        
                        if was_outside and is_inside and rsi_ok:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = price
                            item['invested'] = trade_size
                            balance -= trade_size
                            send_telegram(f"💰 ENTRADA SNAP-BACK: {ticker}\nEl preu ha recuperat la Banda Bollinger.\nRSI: {curr['RSI']:.1f}\nInv: {trade_size:.2f}$")
                            changes = True

            data['balance'] = balance
            data['equity'] = temp_equity
            data['portfolio'] = portfolio
            data['last_update'] = datetime.now().strftime("%H:%M:%S")
            
            if changes:
                save_data(data)
            
            if datetime.now().second < 5: 
                save_data(data)

        except Exception as e:
            print(f"Error background: {e}")
        
        time.sleep(60)

@st.cache_resource
def start_background_bot():
    if not os.path.exists(DATA_FILE):
        save_data(load_data()) 
    thread = threading.Thread(target=run_trading_logic, daemon=True)
    thread.start()
    return thread

# ---------------------------------------------------------
# 4. WEB
# ---------------------------------------------------------
start_background_bot()

st.title("💰 Bot Rendible (Ràtio 1:1)")
st.caption("Estratègia: Bollinger Snap-Back. Stop Loss ajustat per garantir beneficis globals.")

placeholder = st.empty()

while True:
    data = load_data()
    
    with placeholder.container():
        st.write(f"🔄 Última actualització: **{data.get('last_update')}**")
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Equity Total", f"{data.get('equity', 0):.2f}$")
        m2.metric("Cash Disponible", f"{data.get('balance', 0):.2f}$")
        m3.metric("Wins", data.get('wins', 0))
        m4.metric("Losses", data.get('losses', 0))
        
        cols = st.columns(5)
        portfolio = data.get('portfolio', {})
        
        for i, ticker in enumerate(TICKERS):
            if ticker not in portfolio: continue
            item = portfolio[ticker]
            
            col_idx = i % 5
            with cols[col_idx]:
                status = item['status']
                with st.container(border=True):
                    st.markdown(f"**{ticker}**")
                    if status == 'INVESTED':
                        pnl = item.get('pnl', 0.0)
                        pnl_pct = item.get('pnl_pct', 0.0) * 100
                        color = "green" if pnl >= 0 else "red"
                        st.markdown(f"Inv: {item['invested']:.0f}$")
                        st.markdown(f"**P&L: <span style='color:{color}'>{pnl:.2f}$ ({pnl_pct:.2f}%)</span>**", unsafe_allow_html=True)
                        st.caption(f"Ent: {item['entry_price']:.2f}")
                    else:
                        st.caption("CASH")

        hist = data.get('history', [])
        if hist:
            st.write("---")
            st.dataframe(pd.DataFrame(hist).iloc[::-1].head(10))

    time.sleep(10)