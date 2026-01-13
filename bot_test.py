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
# 1. CONFIGURACIÓ
# ---------------------------------------------------------
st.set_page_config(page_title="Bot 24/7 Background", layout="wide", page_icon="👻")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TICKERS = ['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']
TIMEFRAME = "1m"        
LEVERAGE = 5            
ALLOCATION_PCT = 0.10   
TARGET_NET_PROFIT = 0.0085  
STOP_LOSS_PCT = 0.0085      
COMMISSION_RATE = 0.001 
INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_background_data.json"

# ---------------------------------------------------------
# 2. FUNCIONS DE GESTIÓ DE DADES (JSON)
# ---------------------------------------------------------
# Aquestes funcions han de ser independents de Streamlit per funcionar en segon pla

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except: pass
    
    # Si no existeix, retornem estructura per defecte
    return {
        'balance': INITIAL_CAPITAL,
        'equity': INITIAL_CAPITAL,
        'wins': 0,
        'losses': 0,
        'portfolio': {t: {'status': 'CASH', 'entry_price': 0.0, 'invested': 0.0} for t in TICKERS},
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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"👻 [BOT BACKGROUND]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_market_data(tickers):
    try:
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

            if df.empty or len(df) < 30: continue
            df = df.dropna()
            
            # Indicadors
            df['EMA_20'] = ta.ema(df['Close'], length=20)
            df['RSI'] = ta.rsi(df['Close'], length=14)
            df = df.dropna()
            
            if not df.empty:
                processed[ticker] = df.tail(2)
        return processed
    except: return {}

# ---------------------------------------------------------
# 3. EL CERVELL (BACKGROUND THREAD)
# ---------------------------------------------------------
# Aquesta funció s'executa en un fil paral·lel i NO depèn del navegador

def run_trading_logic():
    print("🚀 CERVELL ARRENCAT: El bot està treballant en segon pla...")
    
    while True:
        try:
            # 1. Carreguem l'estat actual del disc
            data = load_data()
            portfolio = data['portfolio']
            balance = data['balance']
            equity = data['equity']
            
            # 2. Baixem dades de mercat
            market_data = get_market_data(TICKERS)
            changes = False
            
            temp_equity = balance # Recalculem equity
            
            # 3. Bucle d'anàlisi
            for ticker in TICKERS:
                item = portfolio[ticker]
                current_price = 0.0
                
                if market_data and ticker in market_data:
                    current_price = float(market_data[ticker].iloc[-1]['Close'])
                
                # Si no tenim preu actual però estem invertits, usem el d'entrada com a referència
                if current_price == 0 and item['status'] == 'INVESTED':
                    current_price = item['entry_price']
                
                # --- LÒGICA TRADING ---
                if item['status'] == 'INVESTED' and current_price > 0:
                    gross_val = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                    lev_invested = item['invested'] * LEVERAGE
                    net_pnl = (gross_val - lev_invested) - (lev_invested * COMMISSION_RATE)
                    net_pnl_pct = net_pnl / item['invested']
                    
                    temp_equity += (item['invested'] + net_pnl)
                    
                    # SORTIDA
                    if net_pnl_pct >= TARGET_NET_PROFIT:
                        balance += (item['invested'] + net_pnl)
                        data['wins'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN: {ticker} (+{net_pnl:.2f}$)")
                        changes = True
                    
                    elif net_pnl_pct <= -STOP_LOSS_PCT:
                        balance += (item['invested'] + net_pnl)
                        data['losses'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'LOSS', 'PL': f"{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"❌ LOSS: {ticker} ({net_pnl:.2f}$)")
                        changes = True
                        
                elif item['status'] == 'CASH' and market_data and ticker in market_data:
                    df = market_data[ticker]
                    curr = df.iloc[-1]
                    prev = df.iloc[-2]
                    price = float(curr['Close'])
                    
                    # ENTRADA (Lògica Activa)
                    trade_size = equity * ALLOCATION_PCT
                    if balance >= trade_size:
                        trend_ok = price > curr['EMA_20']
                        rsi_ok = (curr['RSI'] > 40) and (curr['RSI'] < 70) and (curr['RSI'] > prev['RSI'])
                        
                        if trend_ok and rsi_ok:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = price
                            item['invested'] = trade_size
                            balance -= trade_size
                            send_telegram(f"🚀 ENTRADA: {ticker} a {price:.2f}$")
                            changes = True

            # Actualitzem dades globals
            data['balance'] = balance
            data['equity'] = temp_equity
            data['portfolio'] = portfolio
            data['last_update'] = datetime.now().strftime("%H:%M:%S")
            
            if changes:
                save_data(data)
            
            # Guardem sempre l'última hora encara que no hi hagi canvis per saber que està viu
            # (Però guardem cada minut per no cremar el disc)
            if datetime.now().second < 5: 
                save_data(data)

        except Exception as e:
            print(f"Error al fil de fons: {e}")
        
        # Esperem 60 segons abans de la següent volta
        time.sleep(60)

# Aquesta funció màgica arrenca el fil NOMÉS UN COP i el manté viu
@st.cache_resource
def start_background_bot():
    if not os.path.exists(DATA_FILE):
        save_data(load_data()) # Inicialitzar fitxer si no existeix
    
    # Creem i arrenquem el fil dimoni (Daemon Thread)
    thread = threading.Thread(target=run_trading_logic, daemon=True)
    thread.start()
    return thread

# ---------------------------------------------------------
# 4. LA INTERFÍCIE WEB (VISOR)
# ---------------------------------------------------------
# Arrenquem el bot en segon pla (si no està ja en marxa)
start_background_bot()

st.title("👻 Bot 100% Autònom (Background)")
st.caption("Aquest bot treballa en un fil paral·lel. Pots tancar la pestanya.")

# Bucle visualització (només per refrescar la pantalla)
# Això NO executa operacions, només llegeix el JSON
placeholder = st.empty()

while True:
    data = load_data()
    
    with placeholder.container():
        st.write(f"🔄 Última activitat del cervell: **{data.get('last_update')}**")
        
        # Mètriques
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Equity", f"{data.get('equity', 0):.2f}$")
        m2.metric("Cash", f"{data.get('balance', 0):.2f}$")
        m3.metric("Wins", data.get('wins', 0))
        m4.metric("Losses", data.get('losses', 0))
        
        # Mostrar cartera
        cols = st.columns(5)
        portfolio = data.get('portfolio', {})
        
        for i, ticker in enumerate(TICKERS):
            if ticker not in portfolio: continue
            item = portfolio[ticker]
            
            col_idx = i % 5
            with cols[col_idx]:
                status = item['status']
                border = "green" if status == 'INVESTED' else "grey"
                with st.container(border=True):
                    st.markdown(f"**{ticker}**")
                    if status == 'INVESTED':
                        st.markdown(f"🟢 INV: {item['invested']:.0f}$")
                        st.caption(f"Entrada: {item['entry_price']:.2f}")
                    else:
                        st.caption("CASH")

        # Historial
        hist = data.get('history', [])
        if hist:
            st.write("---")
            st.dataframe(pd.DataFrame(hist).iloc[::-1].head(10))

    # Refresquem la pantalla cada 10 segons
    time.sleep(10)