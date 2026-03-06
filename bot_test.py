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
# 1. CONFIGURACIÓ "QUANT PRO" (Maximització de Guanys)
# ---------------------------------------------------------
st.set_page_config(page_title="Bot Quant PRO", layout="wide", page_icon="🚀")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TICKERS = ['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']
TIMEFRAME = "5m"        
LEVERAGE = 5            

# GESTIÓ DE CAPITAL
ALLOCATION_PCT = 0.10       # Pugem al 10% (Ja tenim un WinRate del 70%, podem arriscar una mica més)

# NOUS PARÀMETRES DE SORTIDA (The Squeeze)
RSI_ENTRY_THRESHOLD = 10    # Entrem a l'infern (sobrevenda extrema)
RSI_EXIT_THRESHOLD = 79     # Sortim al cel (sobrecompra extrema) - Abans era 70

# FILTRE DE BENEFICI MÍNIM (Nou)
# No tancarem per RSI si no guanyem almenys un 0.40% NET
MIN_PROFIT_TO_CLOSE = 0.0040 

# STOP LOSS AJUSTAT
STOP_LOSS_PCT = 0.020       # 2.0% (Reduït de 3% per tallar pèrdues abans)
COMMISSION_RATE = 0.0015    

INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_quant_pro_data.json"

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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🚀 [BOT PRO]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_market_data(tickers):
    try:
        # 5 dies de dades
        data = yf.download(tickers, period="5d", interval="5m", group_by='ticker', progress=False, auto_adjust=True, threads=False)
        processed = {}
        for ticker in tickers:
            try:
                if len(tickers) > 1:
                    if ticker not in data.columns.levels[0]: continue
                    df = data[ticker].copy()
                else:
                    df = data.copy()
            except: continue

            if df.empty or len(df) < 200: continue
            df = df.dropna()
            
            # --- INDICADORS ---
            # 1. SMA 200 (Filtre de règim)
            df['SMA_200'] = ta.sma(df['Close'], length=200)
            
            # 2. RSI-2 (L'arma del 70% WinRate)
            df['RSI_2'] = ta.rsi(df['Close'], length=2)
            
            df = df.dropna()
            if not df.empty:
                processed[ticker] = df.tail(2)
        return processed
    except: return {}

# ---------------------------------------------------------
# 3. CERVELL (BACKGROUND)
# ---------------------------------------------------------
def run_trading_logic():
    print("🚀 CERVELL QUANT PRO ARRENCAT (Optimització de Guanys)...")
    
    while True:
        try:
            data = load_data()
            portfolio = data['portfolio']
            balance = data['balance']
            
            market_data = get_market_data(TICKERS)
            changes = False
            
            # Recalculem equity
            temp_equity = balance
            
            for ticker in TICKERS:
                item = portfolio[ticker]
                current_price = 0.0
                curr_rsi2 = 50.0 
                
                if market_data and ticker in market_data:
                    row = market_data[ticker].iloc[-1]
                    current_price = float(row['Close'])
                    curr_rsi2 = float(row['RSI_2'])
                
                if current_price == 0 and item['status'] == 'INVESTED':
                    current_price = item['entry_price']
                
                # --- A) GESTIÓ POSICIONS (MAXIMITZAR GUANYS) ---
                if item['status'] == 'INVESTED' and current_price > 0:
                    
                    gross_val = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                    lev_invested = item['invested'] * LEVERAGE
                    # Comissions Spread x5
                    fees = lev_invested * COMMISSION_RATE
                    
                    net_pnl = (gross_val - lev_invested) - fees
                    net_pnl_pct = net_pnl / item['invested']
                    
                    # Actualitzem dades visuals
                    item['pnl'] = net_pnl
                    item['pnl_pct'] = net_pnl_pct
                    
                    temp_equity += (item['invested'] + net_pnl)
                    
                    # 1. SORTIDA INTEL·LIGENT
                    # Condició A: L'indicador està extremadament alt (RSI > 79)
                    rsi_exit = curr_rsi2 > RSI_EXIT_THRESHOLD
                    
                    # Condició B: Ja guanyem diners decents (> 0.40% Net)
                    profit_ok = net_pnl_pct > MIN_PROFIT_TO_CLOSE
                    
                    # Només venem si l'indicador ho diu I tenim benefici real
                    if rsi_exit and profit_ok:
                        balance += (item['invested'] + net_pnl)
                        data['wins'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN: {ticker} (+{net_pnl:.2f}$ | {net_pnl_pct*100:.2f}%)\nRSI(2) extrem ({curr_rsi2:.0f})")
                        changes = True
                    
                    # 2. STOP LOSS AJUSTAT (2.0%)
                    elif net_pnl_pct <= -STOP_LOSS_PCT:
                        balance += (item['invested'] + net_pnl)
                        data['losses'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'LOSS', 'PL': f"{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"❌ LOSS: {ticker} ({net_pnl:.2f}$)")
                        changes = True
                        
                # --- B) ENTRADA (MANTENIM EL QUE FUNCIONA) ---
                elif item['status'] == 'CASH' and market_data and ticker in market_data:
                    df = market_data[ticker]
                    curr = df.iloc[-1]
                    price = float(curr['Close'])
                    
                    trade_size = temp_equity * ALLOCATION_PCT
                    
                    if balance >= trade_size:
                        
                        # 1. FILTRE TENDÈNCIA (SMA 200)
                        trend_ok = price > curr['SMA_200']
                        
                        # 2. TRIGGER (RSI-2 < 10)
                        # Comprem el pànic extrem.
                        oversold_extreme = curr['RSI_2'] < RSI_ENTRY_THRESHOLD
                        
                        if trend_ok and oversold_extreme:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = price
                            item['invested'] = trade_size
                            
                            balance -= trade_size
                            send_telegram(f"🚀 ENTRADA PRO: {ticker}\nPreu > SMA200\nRSI(2): {curr['RSI_2']:.1f} (Pànic)\nInv: {trade_size:.2f}$")
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
        
        time.sleep(30)

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

st.title("🚀 Bot Quant PRO (Max Profit)")
st.caption("Estratègia: RSI-2 (70% WinRate) + Filtre de Benefici Mínim.")

placeholder = st.empty()

while True:
    data = load_data()
    
    with placeholder.container():
        st.write(f"🔄 Últim escaneig: **{data.get('last_update')}**")
        
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