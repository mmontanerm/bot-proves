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
# 1. CONFIGURACIÓ "TREND SNIPER" (Marge de Respiració)
# ---------------------------------------------------------
st.set_page_config(page_title="Bot Trend Sniper", layout="wide", page_icon="🎯")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TICKERS =['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']
TIMEFRAME = "5m"        
LEVERAGE = 5            

ALLOCATION_PCT = 0.15       # 15% per operació (Menys operacions, més segures)

# ---------------------------------------------------------
# MATEMÀTICA ANTI-ASFIXIA (La clau del nou Win Rate)
# ---------------------------------------------------------
TARGET_NET_PROFIT = 0.035   # 3.5% Net de guany (El preu real ha de pujar un 0.7%)
STOP_LOSS_PCT = 0.050       # 5.0% Stop Loss (El preu real pot caure un 1% sense fer-nos fora)
COMMISSION_RATE = 0.0015    # Cost estimat del Spread d'entrada i sortida

INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_trend_sniper_data.json"

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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🎯 [TREND SNIPER]\n{msg}", "parse_mode": "Markdown"}
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
            
            # --- INDICADORS INFAL·LIBLES ---
            # 1. EMA 200 (L'escut protector)
            df['EMA_200'] = ta.ema(df['Close'], length=200)
            
            # 2. MACD (El gallet d'entrada)
            macd = ta.macd(df['Close'])
            if macd is not None:
                df['MACD'] = macd.iloc[:, 0]        # Línia MACD (Ràpida)
                df['MACD_SIG'] = macd.iloc[:, 2]    # Línia Senyal (Lenta)
            else:
                df['MACD'] = 0
                df['MACD_SIG'] = 0
                
            df = df.dropna()
            if not df.empty:
                processed[ticker] = df.tail(2)
        return processed
    except: return {}

# ---------------------------------------------------------
# 3. CERVELL (BACKGROUND)
# ---------------------------------------------------------
def run_trading_logic():
    print("🎯 CERVELL TREND SNIPER ARRENCAT (MACD Cross + Anti-Asfixia)...")
    
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
                
                if market_data and ticker in market_data:
                    row = market_data[ticker].iloc[-1]
                    current_price = float(row['Close'])
                
                if current_price == 0 and item['status'] == 'INVESTED':
                    current_price = item['entry_price']
                
                # --- A) GESTIÓ POSICIONS (AMB MARGE) ---
                if item['status'] == 'INVESTED' and current_price > 0:
                    
                    gross_val = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                    lev_invested = item['invested'] * LEVERAGE
                    fees = lev_invested * COMMISSION_RATE
                    net_pnl = (gross_val - lev_invested) - fees
                    net_pnl_pct = net_pnl / item['invested']
                    
                    item['pnl'] = net_pnl
                    item['pnl_pct'] = net_pnl_pct
                    temp_equity += (item['invested'] + net_pnl)
                    
                    # 1. TAKE PROFIT (3.5%)
                    if net_pnl_pct >= TARGET_NET_PROFIT:
                        balance += (item['invested'] + net_pnl)
                        data['wins'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN 🎯: {ticker} (+{net_pnl:.2f}$ | +{net_pnl_pct*100:.2f}%)")
                        changes = True
                    
                    # 2. STOP LOSS AMPLI (5.0%)
                    elif net_pnl_pct <= -STOP_LOSS_PCT:
                        balance += (item['invested'] + net_pnl)
                        data['losses'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'LOSS', 'PL': f"{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"❌ LOSS: {ticker} ({net_pnl:.2f}$)")
                        changes = True
                        
                # --- B) ENTRADA (MACD GOLDEN CROSS) ---
                elif item['status'] == 'CASH' and market_data and ticker in market_data:
                    df = market_data[ticker]
                    curr = df.iloc[-1]
                    prev = df.iloc[-2]
                    price = float(curr['Close'])
                    
                    trade_size = temp_equity * ALLOCATION_PCT
                    
                    if balance >= trade_size:
                        
                        # 1. TENDÈNCIA ALCISTA: Preu > EMA 200
                        trend_ok = price > curr['EMA_200']
                        
                        # 2. MACD CROSSOVER (El Gir Confirmat)
                        # Al minut anterior, la línia MACD estava per sota del Senyal.
                        # En aquest minut, ha creuat per sobre.
                        # Això ens indica que la força de caiguda s'ha acabat i comença la pujada.
                        macd_crossing_up = (prev['MACD'] < prev['MACD_SIG']) and (curr['MACD'] > curr['MACD_SIG'])
                        
                        # 3. ZONA NEGATIVA (Comprem barato)
                        # Volem que aquest creuament es produeixi per sota de la línia zero (o molt a prop), 
                        # indicant que l'acció havia corregit i ara s'aixeca.
                        macd_below_zero = curr['MACD'] < 0
                        
                        if trend_ok and macd_crossing_up and macd_below_zero:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = price
                            item['invested'] = trade_size
                            
                            balance -= trade_size
                            send_telegram(f"🚀 ENTRADA CONFIRMADA: {ticker}\nPreu > EMA200\nMACD Creuant a l'Alça\nInv: {trade_size:.2f}$")
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

st.title("🎯 Bot Trend Sniper (Win Rate Fix)")
st.caption("Estratègia: MACD Crossover + Stop Loss Ampli per evitar asfíxia de comissions.")

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