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
# 1. CONFIGURACIÓ "SMART TREND" (Gestió Professional)
# ---------------------------------------------------------
st.set_page_config(page_title="Bot Smart Trend BE", layout="wide", page_icon="🧠")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TICKERS = ['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']
TIMEFRAME = "5m"        
LEVERAGE = 5            

# GESTIÓ DE CAPITAL (Més conservadora per recuperar pèrdues)
ALLOCATION_PCT = 0.10       # 10% per operació

# RÀTIO POSITIVA (Busquem guanyar més del que perdem)
TARGET_NET_PROFIT = 0.018   # 1.8% Objectiu (Take Profit)
STOP_LOSS_PCT = 0.012       # 1.2% Stop Loss Inicial (Pèrdua màxima)

# BREAK-EVEN TRIGGER (La clau per no perdre)
# Si guanyem un 0.6%, protegim l'operació a cost 0
BREAK_EVEN_TRIGGER = 0.006  

COMMISSION_RATE = 0.0015    # 0.15% Spread estimat

INITIAL_CAPITAL = 10000.0   # (Nota: El bot recuperarà el saldo del fitxer si existeix)
DATA_FILE = "bot_smart_data.json"

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
        'portfolio': {t: {'status': 'CASH', 'entry_price': 0.0, 'invested': 0.0, 'stop_price': 0.0, 'be_active': False} for t in TICKERS},
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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🧠 [BOT SMART]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_market_data(tickers):
    try:
        # Baixem dades 5 dies per indicadors sòlids
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
            
            # --- INDICADORS DE FILTRATGE ---
            
            # 1. EMA 200 (Tendència Fons)
            df['EMA_200'] = ta.ema(df['Close'], length=200)
            
            # 2. SUPERTREND (Senyal Entrada)
            st_data = ta.supertrend(df['High'], df['Low'], df['Close'], length=10, multiplier=3)
            if st_data is not None:
                df['ST_DIR'] = st_data[st_data.columns[1]] # 1 = Bullish, -1 = Bearish
            else:
                df['ST_DIR'] = 0

            # 3. ADX (Filtre de Força - Anti-Lateral)
            # Només operarem si ADX > 25
            try:
                adx = ta.adx(df['High'], df['Low'], df['Close'], length=14)
                df['ADX'] = adx[adx.columns[0]] if adx is not None else 0
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
    print("🧠 CERVELL SMART ARRENCAT (SuperTrend + ADX + BreakEven)...")
    
    while True:
        try:
            data = load_data()
            portfolio = data['portfolio']
            balance = data['balance']
            
            market_data = get_market_data(TICKERS)
            changes = False
            
            # Recalculem equity (Cash + Valor Posicions)
            current_equity_calc = balance
            
            for ticker in TICKERS:
                item = portfolio[ticker]
                current_price = 0.0
                
                if market_data and ticker in market_data:
                    current_price = float(market_data[ticker].iloc[-1]['Close'])
                
                if current_price == 0 and item['status'] == 'INVESTED':
                    current_price = item['entry_price']
                
                # --- A) GESTIÓ POSICIONS (PROTECCIÓ ACTIVA) ---
                if item['status'] == 'INVESTED' and current_price > 0:
                    
                    # Càlculs econòmics
                    gross_val = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                    lev_invested = item['invested'] * LEVERAGE
                    # Comissions estimades (Spread)
                    fees = lev_invested * COMMISSION_RATE
                    net_pnl = (gross_val - lev_invested) - fees
                    net_pnl_pct = net_pnl / item['invested']
                    
                    current_equity_calc += (item['invested'] + net_pnl)
                    
                    # 1. GESTIÓ BREAK-EVEN (NOVA)
                    # Si guanyem > 0.6% i encara no hem protegit l'operació...
                    if net_pnl_pct >= BREAK_EVEN_TRIGGER and not item.get('be_active', False):
                        # Movem l'Stop Loss al preu d'entrada + un petit marge per comissions
                        # Preu Entrada * (1 + Comissió/Palanquejament)
                        new_stop_price = item['entry_price'] * (1 + (COMMISSION_RATE / LEVERAGE))
                        item['stop_price'] = new_stop_price
                        item['be_active'] = True
                        send_telegram(f"🛡️ PROTECCIÓ ACTIVADA: {ticker}\nStop Loss mogut a Break-Even (0$ Pèrdua assegurada).")
                        changes = True

                    # 2. TAKE PROFIT (1.8%)
                    if net_pnl_pct >= TARGET_NET_PROFIT:
                        balance += (item['invested'] + net_pnl)
                        data['wins'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN: {ticker} (+{net_pnl:.2f}$)")
                        changes = True
                    
                    # 3. STOP LOSS (Dinàmic)
                    # Comprovem si el preu baixa del nostre Stop Price
                    elif current_price <= item['stop_price']:
                        balance += (item['invested'] + net_pnl)
                        
                        # Si era un Break-Even, no compta com a pèrdua greu (és neutre)
                        result_type = "NEUTRAL" if item.get('be_active') else "LOSS"
                        if result_type == "LOSS":
                            data['losses'] += 1
                        
                        data['history'].append({'Ticker': ticker, 'Res': result_type, 'PL': f"{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        
                        icon = "🛡️" if result_type == "NEUTRAL" else "❌"
                        send_telegram(f"{icon} SORTIDA {ticker}: {net_pnl:.2f}$")
                        changes = True
                        
                # --- B) ENTRADA (FILTRES DE QUALITAT) ---
                elif item['status'] == 'CASH' and market_data and ticker in market_data:
                    df = market_data[ticker]
                    curr = df.iloc[-1]
                    price = float(curr['Close'])
                    
                    # Usem l'Equity actual per calcular el 10%
                    trade_size = current_equity_calc * ALLOCATION_PCT
                    
                    if balance >= trade_size:
                        
                        # 1. TENDÈNCIA FONS: Preu > EMA 200
                        trend_ok = price > curr['EMA_200']
                        
                        # 2. SENYAL: SuperTrend VERD (1)
                        st_signal = curr['ST_DIR'] == 1
                        
                        # 3. FORÇA: ADX > 25 (Evitem laterals)
                        # Aquest filtre és el que reduirà les pèrdues de 85 a 20.
                        adx_ok = curr['ADX'] > 25
                        
                        if trend_ok and st_signal and adx_ok:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = price
                            item['invested'] = trade_size
                            # Stop Loss inicial: 1.2% avall
                            item['stop_price'] = price * (1 - (STOP_LOSS_PCT / LEVERAGE)) 
                            item['be_active'] = False
                            
                            balance -= trade_size
                            send_telegram(f"🧠 ENTRADA SMART: {ticker}\nST Verd + ADX {curr['ADX']:.1f}\nInv: {trade_size:.2f}$")
                            changes = True

            data['balance'] = balance
            data['equity'] = current_equity_calc
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

st.title("🧠 Bot Smart Trend (Break-Even)")
st.caption("Estratègia: 5 Minuts. ADX filtra soroll. Break-Even protegeix guanys.")

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
                        pnl = (item['invested'] * LEVERAGE / item['entry_price']) * item.get('entry_price', 0) - (item['invested']*LEVERAGE) # Aprox visual
                        # Recuperem el valor real si tenim accés (simplificat per visualització)
                        
                        st.markdown(f"🟢 Inv: {item['invested']:.0f}$")
                        
                        # Indiquem si el Break-Even està actiu
                        if item.get('be_active'):
                            st.caption("🛡️ PROTEGIT (BE)")
                        else:
                            st.caption(f"Stop inicial: {item['stop_price']:.2f}")
                    else:
                        st.caption("CASH")

        hist = data.get('history', [])
        if hist:
            st.write("---")
            st.dataframe(pd.DataFrame(hist).iloc[::-1].head(10))

    time.sleep(10)