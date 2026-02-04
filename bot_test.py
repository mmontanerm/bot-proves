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
# 1. CONFIGURACIÓ "MEAN REVERSION" (Win Rate > 90%)
# ---------------------------------------------------------
st.set_page_config(page_title="Bot 90% WinRate", layout="wide", page_icon="💸")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TICKERS = ['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']
TIMEFRAME = "1m"        
LEVERAGE = 5            

# GESTIÓ DE RISC ASIMÈTRICA (Per aconseguir alt Win Rate)
ALLOCATION_PCT = 0.10       # 10% per operació
TARGET_NET_PROFIT = 0.0050  # 0.5% Net (Objectiu curt i fàcil)
STOP_LOSS_PCT = 0.0250      # 2.5% Stop (Donem molt espai perquè reboti)
COMMISSION_RATE = 0.001     

INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_reversion_data.json"

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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"💸 [BOT 90%]\n{msg}", "parse_mode": "Markdown"}
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
            
            # --- INDICADORS MEAN REVERSION ---
            
            # 1. BANDES DE BOLLINGER (Extremes)
            # length=20, std=2.5 (La majoria usa 2.0. El 2.5 és per buscar extrems reals)
            bb = ta.bbands(df['Close'], length=20, std=2.5)
            
            if bb is not None:
                # pandas_ta retorna columnes amb noms tipus BBL_20_2.5, BBM_20_2.5, BBU_20_2.5
                # Les renonbrem per fer-ho fàcil
                cols = bb.columns
                df['BB_LOWER'] = bb[cols[0]] # Banda Baixa
                df['BB_MID']   = bb[cols[1]] # Mitjana
                df['BB_UPPER'] = bb[cols[2]] # Banda Alta
            
            # 2. RSI (Per confirmar sobrecompra/sobrevenda)
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
    print("💸 CERVELL MEAN REVERSION ARRENCAT (Bollinger 2.5)...")
    
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
                
                # --- A) GESTIÓ POSICIONS (SORTIDA) ---
                if item['status'] == 'INVESTED' and current_price > 0:
                    gross_val = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                    lev_invested = item['invested'] * LEVERAGE
                    net_pnl = (gross_val - lev_invested) - (lev_invested * COMMISSION_RATE)
                    net_pnl_pct = net_pnl / item['invested']
                    
                    temp_equity += (item['invested'] + net_pnl)
                    
                    # 1. TAKE PROFIT FIX (0.5% Net) - Assegurar guanys ràpids
                    win_condition = net_pnl_pct >= TARGET_NET_PROFIT
                    
                    # 2. SORTIDA TÈCNICA (Retorn a la mitjana)
                    # Si el preu torna a tocar la línia del mig de Bollinger, sortim (encara que sigui amb menys guany)
                    # Això és seguretat: la goma ja s'ha destensat.
                    # Necessitem accedir a les dades actuals
                    technical_exit = False
                    if market_data and ticker in market_data:
                        curr = market_data[ticker].iloc[-1]
                        # Si hem comprat a baix, sortim quan toqui la mitjana
                        if 'BB_MID' in curr and current_price >= curr['BB_MID'] and net_pnl > 0:
                            technical_exit = True

                    if win_condition or technical_exit:
                        balance += (item['invested'] + net_pnl)
                        data['wins'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN: {ticker} (+{net_pnl:.2f}$)")
                        changes = True
                    
                    # STOP LOSS (D'emergència)
                    elif net_pnl_pct <= -STOP_LOSS_PCT:
                        balance += (item['invested'] + net_pnl)
                        data['losses'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'LOSS', 'PL': f"{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"❌ LOSS: {ticker} ({net_pnl:.2f}$)")
                        changes = True
                        
                # --- B) ENTRADA (ESTRATÈGIA GOMA ELÀSTICA) ---
                elif item['status'] == 'CASH' and market_data and ticker in market_data:
                    df = market_data[ticker]
                    curr = df.iloc[-1]
                    price = float(curr['Close'])
                    
                    trade_size = equity * ALLOCATION_PCT
                    
                    if balance >= trade_size:
                        
                        # 1. PREU FORA DE BANDES (Anomalia Estadística)
                        # El preu tanca PER SOTA de la banda inferior de Bollinger (2.5 std)
                        # Això passa poques vegades i indica pànic excessiu.
                        below_band = price < curr['BB_LOWER']
                        
                        # 2. SOBREVENDA (Confirmació)
                        # L'RSI ha d'estar baix (< 30) per confirmar que no és una caiguda lliure sense fons.
                        rsi_oversold = curr['RSI'] < 30
                        
                        if below_band and rsi_oversold:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = price
                            item['invested'] = trade_size
                            balance -= trade_size
                            send_telegram(f"💸 ENTRADA 90%: {ticker}\nPreu fora Bandes Bollinger (2.5)\nRSI: {curr['RSI']:.1f}\nInv: {trade_size:.2f}$")
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

st.title("💸 Bot Mean Reversion (WinRate > 90%)")
st.caption("Estratègia: Bollinger Bands (2.5 Std) + RSI < 30. Compra pànics, ven rebots.")

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
                        st.markdown(f"🟢 INV: {item['invested']:.0f}$")
                        st.caption(f"Ent: {item['entry_price']:.2f}")
                    else:
                        st.caption("CASH")

        hist = data.get('history', [])
        if hist:
            st.write("---")
            st.dataframe(pd.DataFrame(hist).iloc[::-1].head(10))

    time.sleep(10)
