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
# 1. CONFIGURACIÓ "SNIPER SCALP" (Alta Precisió & Risc Mínim)
# ---------------------------------------------------------
st.set_page_config(page_title="Bot Sniper Scalp", layout="wide", page_icon="🎯")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TICKERS =['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']
TIMEFRAME = "5m"        
LEVERAGE = 5            

ALLOCATION_PCT = 0.10       # 10% per operació

# ---------------------------------------------------------
# NOUS PARÀMETRES DE RISC (LA CLAU DE LA RENDIBILITAT)
# ---------------------------------------------------------
TARGET_NET_PROFIT = 0.010   # 1.0% Take Profit Llarg (Si hi arriba de cop, perfecte)
STOP_LOSS_PCT = 0.010       # 1.0% STOP LOSS QUIRÚRGIC (Tallem pèrdues a la meitat que abans!)

# SORTIDA TÈCNICA (Recuperem la que funcionava bé)
RSI_ENTRY_THRESHOLD = 12    # Sobrevenut
RSI_EXIT_THRESHOLD = 70     # Sobrecomprat (Venem immediatament, sense demanar mínims)

COMMISSION_RATE = 0.0015    # 0.15% Comissió/Spread

INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_sniper_scalp_data.json"

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
        'history':[],
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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🎯 [SNIPER SCALP]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_market_data(tickers):
    try:
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
            
            # 1. SMA 200 (Tendència)
            df['SMA_200'] = ta.sma(df['Close'], length=200)
            
            # 2. RSI(2) (Reversió a curt termini)
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
    print("🎯 CERVELL SNIPER SCALP ARRENCAT (Stop 1%, Sortida RSI Ràpida)...")
    
    while True:
        try:
            data = load_data()
            portfolio = data['portfolio']
            balance = data['balance']
            
            market_data = get_market_data(TICKERS)
            changes = False
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
                
                # --- A) GESTIÓ POSICIONS (PROTECCIÓ TOTAL) ---
                if item['status'] == 'INVESTED' and current_price > 0:
                    
                    gross_val = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                    lev_invested = item['invested'] * LEVERAGE
                    fees = lev_invested * COMMISSION_RATE
                    net_pnl = (gross_val - lev_invested) - fees
                    net_pnl_pct = net_pnl / item['invested']
                    
                    item['pnl'] = net_pnl
                    item['pnl_pct'] = net_pnl_pct
                    temp_equity += (item['invested'] + net_pnl)
                    
                    # 1. SORTIDA TÈCNICA PER INDICADOR (LA CLAU DE L'ÈXIT D'ABANS)
                    # Si l'indicador toca el sostre (>70), sortim amb el que tinguem (sempre que sigui profit)
                    # Eliminem la regla absurda de "exigir un % mínim".
                    if (curr_rsi2 > RSI_EXIT_THRESHOLD) and (net_pnl > 0):
                        balance += (item['invested'] + net_pnl)
                        data['wins'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN (RSI Rebot): {ticker} (+{net_pnl:.2f}$)")
                        changes = True

                    # 2. TAKE PROFIT DIRECTE (1.0%)
                    # Si hi ha una pujada violenta i de cop guanyem un 1%, assegurem i tanquem.
                    elif net_pnl_pct >= TARGET_NET_PROFIT:
                        balance += (item['invested'] + net_pnl)
                        data['wins'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"🎯 WIN (Take Profit): {ticker} (+{net_pnl:.2f}$)")
                        changes = True
                    
                    # 3. STOP LOSS QUIRÚRGIC (1.0%)
                    # Hem passat del 2.5/2.0% a només l'1.0%. Tallem l'hemorràgia de soca-rel.
                    elif net_pnl_pct <= -STOP_LOSS_PCT:
                        balance += (item['invested'] + net_pnl)
                        data['losses'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'LOSS', 'PL': f"{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"❌ LOSS (Stop Tallat): {ticker} ({net_pnl:.2f}$)")
                        changes = True
                        
                # --- B) ENTRADA (AMB CONFIRMACIÓ) ---
                elif item['status'] == 'CASH' and market_data and ticker in market_data:
                    df = market_data[ticker]
                    curr = df.iloc[-1]
                    prev = df.iloc[-2]
                    price = float(curr['Close'])
                    
                    trade_size = temp_equity * ALLOCATION_PCT
                    
                    if balance >= trade_size:
                        
                        # 1. TENDÈNCIA
                        trend_ok = price > curr['SMA_200']
                        
                        # 2. PÀNIC (RSI-2 BAIX)
                        # Demanem que l'espelma *anterior* o l'*actual* estiguin al límit (<12)
                        rsi_extreme = (curr['RSI_2'] < RSI_ENTRY_THRESHOLD) or (prev['RSI_2'] < RSI_ENTRY_THRESHOLD)
                        
                        # 3. CONFIRMACIÓ VISUAL (ESPELMA VERDA) -> *NOVA ASSEGURANÇA*
                        # L'espelma actual ha de ser verda (Tancament > Obertura).
                        # Això significa que, malgrat estar molt abaix, els compradors ja estan guanyant aquesta batalla de 5 minuts.
                        green_candle = curr['Close'] > curr['Open']
                        
                        if trend_ok and rsi_extreme and green_candle:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = price
                            item['invested'] = trade_size
                            
                            balance -= trade_size
                            send_telegram(f"🎯 ENTRADA SCALP: {ticker}\nPreu > SMA200\nRSI(2) Sobrevenut + ESPELMA VERDA\nInv: {trade_size:.2f}$")
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

st.title("🎯 Bot Sniper Scalp (Alta Eficiència)")
st.caption("Estratègia: RSI-2 amb Sortida Ràpida i Stop Loss ultra curt (1%).")

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

        hist = data.get('history',[])
        if hist:
            st.write("---")
            st.dataframe(pd.DataFrame(hist).iloc[::-1].head(10))

    time.sleep(10)