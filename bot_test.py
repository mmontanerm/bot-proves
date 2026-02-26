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
# 1. CONFIGURACIÓ "QUANT REVERSION" (Win Rate > 80%)
# ---------------------------------------------------------
st.set_page_config(page_title="Bot Quant RSI-2", layout="wide", page_icon="🤖")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Ticker List (Valors líquids)
TICKERS = ['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']

TIMEFRAME = "5m"        
LEVERAGE = 5            

# GESTIÓ DE CAPITAL CONSERVADORA (Per recuperar el 20% perdut)
ALLOCATION_PCT = 0.05       # Baixem al 5% per operació. Volem sumar moltes victòries petites.

# PARAMETRES ESTRATÈGIA QUANT (RSI-2)
RSI_ENTRY_THRESHOLD = 10    # Comprar quan RSI(2) < 10 (Molt sobrevenut)
RSI_EXIT_THRESHOLD = 70     # Vendre quan RSI(2) > 70 (Rebot complert)

# STOP LOSS D'EMERGÈNCIA
# En aquesta estratègia, normalment sortim per indicador (RSI > 70).
# L'Stop Loss és només per si el mercat s'enfonsa (Crash).
STOP_LOSS_PCT = 0.03        # 3% Stop Loss (x5 = 15% del marge invertit)
COMMISSION_RATE = 0.0015    

INITIAL_CAPITAL = 10000.0   # Si el fitxer existeix, farà servir el saldo real actual
DATA_FILE = "bot_quant_data.json"

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
        'portfolio': {t: {'status': 'CASH', 'entry_price': 0.0, 'invested': 0.0, 'highest_price': 0.0} for t in TICKERS},
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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🤖 [BOT QUANT]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_market_data(tickers):
    try:
        # Necessitem 200 espelmes per la mitjana mòbil de fons
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
            
            # --- INDICADORS "QUANT SNIPER" ---
            
            # 1. SMA 200 (Simple Moving Average)
            # Filtre de règim de mercat. Només operem si estem per sobre.
            df['SMA_200'] = ta.sma(df['Close'], length=200)
            
            # 2. RSI DE 2 PERIODES (L'arma secreta)
            # Mesura la sobrecompra/sobrevenda immediata.
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
    print("🤖 CERVELL QUANT ARRENCAT (RSI-2 Mean Reversion)...")
    
    while True:
        try:
            data = load_data()
            portfolio = data['portfolio']
            balance = data['balance']
            
            market_data = get_market_data(TICKERS)
            changes = False
            
            # Càlcul Equity
            temp_equity = balance
            
            for ticker in TICKERS:
                item = portfolio[ticker]
                current_price = 0.0
                curr_rsi2 = 50.0 # Valor neutre per defecte
                
                if market_data and ticker in market_data:
                    row = market_data[ticker].iloc[-1]
                    current_price = float(row['Close'])
                    curr_rsi2 = float(row['RSI_2'])
                
                if current_price == 0 and item['status'] == 'INVESTED':
                    current_price = item['entry_price']
                
                # --- A) GESTIÓ POSICIONS (SORTIDA DINÀMICA) ---
                if item['status'] == 'INVESTED' and current_price > 0:
                    
                    gross_val = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                    lev_invested = item['invested'] * LEVERAGE
                    # Comissions
                    fees = lev_invested * COMMISSION_RATE
                    net_pnl = (gross_val - lev_invested) - fees
                    net_pnl_pct = net_pnl / item['invested']
                    
                    temp_equity += (item['invested'] + net_pnl)
                    
                    # 1. SORTIDA PER INDICADOR (Take Profit Tècnic)
                    # Si l'RSI(2) puja per sobre de 70, el "rebot" s'ha acabat. Venem.
                    # Assegurem que tenim un mínim de benefici per cobrir comissions (pnl > 0)
                    technical_exit = (curr_rsi2 > RSI_EXIT_THRESHOLD) and (net_pnl > 0)
                    
                    if technical_exit:
                        balance += (item['invested'] + net_pnl)
                        data['wins'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN: {ticker} (+{net_pnl:.2f}$)\nSortida per RSI(2) > {RSI_EXIT_THRESHOLD}")
                        changes = True
                    
                    # 2. STOP LOSS D'EMERGÈNCIA (3%)
                    elif net_pnl_pct <= -STOP_LOSS_PCT:
                        balance += (item['invested'] + net_pnl)
                        data['losses'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'LOSS', 'PL': f"{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"❌ LOSS: {ticker} ({net_pnl:.2f}$)")
                        changes = True
                    
                    # (No hi ha Take Profit fix, deixem que l'RSI ens digui quan sortir)
                        
                # --- B) ENTRADA (ESTRATÈGIA QUANT) ---
                elif item['status'] == 'CASH' and market_data and ticker in market_data:
                    df = market_data[ticker]
                    curr = df.iloc[-1]
                    price = float(curr['Close'])
                    
                    # Usem l'Equity per calcular el %
                    trade_size = temp_equity * ALLOCATION_PCT
                    
                    if balance >= trade_size:
                        
                        # 1. FILTRE DE TENDÈNCIA: Preu > SMA 200
                        # Importantíssim. Mai comprem caigudes si la tendència general és baixista.
                        trend_ok = price > curr['SMA_200']
                        
                        # 2. TRIGGER: RSI(2) < 10
                        # Això indica una caiguda extrema i ràpida (pànic momentani).
                        # L'estadística diu que el rebot és imminent.
                        oversold_extreme = curr['RSI_2'] < RSI_ENTRY_THRESHOLD
                        
                        if trend_ok and oversold_extreme:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = price
                            item['invested'] = trade_size
                            
                            balance -= trade_size
                            send_telegram(f"🤖 ENTRADA QUANT: {ticker}\nPreu > SMA200 (Tendència OK)\nRSI(2): {curr['RSI_2']:.1f} (Extremadament baix)\nInv: {trade_size:.2f}$")
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
        
        # En 5 minuts, l'RSI(2) canvia ràpid. Comprovem cada 30s.
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

st.title("🤖 Bot Quant RSI-2 (Recuperació)")
st.caption("Estratègia: Larry Connors RSI-2 Mean Reversion. Alta probabilitat en correccions curtes.")

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
                        # Càlcul PnL visual
                        curr_price = item.get('entry_price') # Si no tenim el real aquí, usem entrada
                        # (La lògica real està al background, això és només UI)
                        
                        st.markdown(f"🟢 Inv: {item['invested']:.0f}$")
                        st.caption(f"Ent: {item['entry_price']:.2f}")
                    else:
                        st.caption("CASH")

        hist = data.get('history', [])
        if hist:
            st.write("---")
            st.dataframe(pd.DataFrame(hist).iloc[::-1].head(10))

    time.sleep(10)