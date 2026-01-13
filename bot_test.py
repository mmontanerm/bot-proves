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
# 1. CONFIGURACIÓ "ELITE STRICT"
# ---------------------------------------------------------
st.set_page_config(page_title="Bot Elite Strict", layout="wide", page_icon="👮‍♂️")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# CARTERA
TICKERS = ['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']

TIMEFRAME = "1m"        
LEVERAGE = 5            
ALLOCATION_PCT = 0.10   
TARGET_NET_PROFIT = 0.0085  # 0.85% Net
STOP_LOSS_PCT = 0.0085      # 0.85% Stop
COMMISSION_RATE = 0.001 

INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_elite_data.json"

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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"👮‍♂️ [BOT ELITE]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_market_data(tickers):
    try:
        # Necessitem històric suficient per l'EMA 200
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

            if df.empty or len(df) < 200: continue # Mínim 200 espelmes OBLIGATORI
            df = df.dropna()
            
            # --- INDICADORS ELITE ---
            
            # 1. EMA 200 (Tendència Major - El filtre suprem)
            df['EMA_200'] = ta.ema(df['Close'], length=200)
            
            # 2. EMA 50 (Tendència Curta)
            df['EMA_50'] = ta.ema(df['Close'], length=50)
            
            # 3. RSI (Momentum)
            df['RSI'] = ta.rsi(df['Close'], length=14)
            
            # 4. ADX (Força)
            try:
                adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
                df['ADX'] = adx_df[adx_df.columns[0]] if adx_df is not None else 0
            except: df['ADX'] = 0
            
            df = df.dropna()
            
            if not df.empty:
                processed[ticker] = df.tail(2)
        return processed
    except: return {}

# ---------------------------------------------------------
# 3. CERVELL (BACKGROUND)
# ---------------------------------------------------------
def run_trading_logic():
    print("👮‍♂️ CERVELL ELITE ARRENCAT (EMA200 + ADX>30)...")
    
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
                
                # --- GESTIÓ POSICIONS ---
                if item['status'] == 'INVESTED' and current_price > 0:
                    gross_val = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                    lev_invested = item['invested'] * LEVERAGE
                    net_pnl = (gross_val - lev_invested) - (lev_invested * COMMISSION_RATE)
                    net_pnl_pct = net_pnl / item['invested']
                    
                    temp_equity += (item['invested'] + net_pnl)
                    
                    # Sortida (Mantenim els objectius)
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
                        
                # --- ENTRADA (LÒGICA MOLT RESTRICTIVA) ---
                elif item['status'] == 'CASH' and market_data and ticker in market_data:
                    df = market_data[ticker]
                    curr = df.iloc[-1]
                    prev = df.iloc[-2]
                    price = float(curr['Close'])
                    
                    trade_size = equity * ALLOCATION_PCT
                    
                    if balance >= trade_size:
                        
                        # 1. FILTRE SUPREM: Preu per sobre de EMA 200
                        # Això garanteix que només comprem en tendència alcista clara de fons
                        trend_major = price > curr['EMA_200']
                        
                        # 2. FILTRE CURT: Preu per sobre de EMA 50
                        trend_minor = price > curr['EMA_50']
                        
                        # 3. FORÇA EXTREMA: ADX > 30 (Més exigent que abans)
                        # Només entrem si el mercat té molta potència.
                        adx_ok = curr['ADX'] > 30
                        
                        # 4. MOMENTUM CONFIRMAT: RSI > 55 però < 70
                        # No entrem al 50 (dubte), entrem al 55 (confirmació).
                        # I vigilem que no estigui ja sobrecomprat (>70).
                        rsi_ok = (curr['RSI'] > 55) and (curr['RSI'] < 70) and (curr['RSI'] > prev['RSI'])
                        
                        # TOT S'HA DE COMPLIR
                        if trend_major and trend_minor and adx_ok and rsi_ok:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = price
                            item['invested'] = trade_size
                            balance -= trade_size
                            send_telegram(f"👮‍♂️ ENTRADA ELITE: {ticker}\nPreu > EMA200\nADX: {curr['ADX']:.1f} (>30)\nRSI: {curr['RSI']:.1f}\nInv: {trade_size:.2f}$")
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

st.title("👮‍♂️ Bot Elite Strict (24/7)")
st.caption("Filtres Actius: EMA 200 + EMA 50 + ADX > 30. Màxima seguretat.")

placeholder = st.empty()

while True:
    data = load_data()
    
    with placeholder.container():
        st.write(f"🔄 Últim escaneig: **{data.get('last_update')}**")
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Equity", f"{data.get('equity', 0):.2f}$")
        m2.metric("Cash", f"{data.get('balance', 0):.2f}$")
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
                        st.markdown(f"🟢 {item['invested']:.0f}$")
                        st.caption(f"Ent: {item['entry_price']:.2f}")
                    else:
                        st.caption("CASH")

        hist = data.get('history', [])
        if hist:
            st.write("---")
            st.dataframe(pd.DataFrame(hist).iloc[::-1].head(10))

    time.sleep(10)