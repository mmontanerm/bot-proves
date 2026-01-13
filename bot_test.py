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
# 1. CONFIGURACIÓ STRICT (Background 24/7)
# ---------------------------------------------------------
st.set_page_config(page_title="Bot 24/7 Strict", layout="wide", page_icon="🛡️")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# CARTERA DIVERSIFICADA
TICKERS = ['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']

TIMEFRAME = "1m"        
LEVERAGE = 5            
ALLOCATION_PCT = 0.10       # 10% per operació
TARGET_NET_PROFIT = 0.0085  # 0.85% Benefici Net
STOP_LOSS_PCT = 0.0085      # 0.85% Stop Loss
COMMISSION_RATE = 0.001     # 0.1% Comissió estimada per volum

INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_strict_data.json"

# ---------------------------------------------------------
# 2. FUNCIONS DE DADES (JSON & TELEGRAM)
# ---------------------------------------------------------
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except: pass
    
    # Estructura per defecte si no existeix fitxer
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
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🛡️ [BOT STRICT]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_market_data(tickers):
    try:
        # Baixem 5 dies per tenir prou històric per l'EMA 50 i ADX
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

            if df.empty or len(df) < 50: continue # Mínim 50 espelmes
            df = df.dropna()
            
            # --- INDICADORS STRICTES ---
            
            # 1. EMA 50 (Filtre de Tendència Sòlida)
            df['EMA_50'] = ta.ema(df['Close'], length=50)
            
            # 2. RSI (Momentum)
            df['RSI'] = ta.rsi(df['Close'], length=14)
            
            # 3. ADX (Filtre Anti-Lateral)
            try:
                adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
                # pandas_ta retorna 3 columnes, agafem la primera (ADX_14)
                df['ADX'] = adx_df[adx_df.columns[0]] if adx_df is not None else 0
            except: df['ADX'] = 0
            
            df = df.dropna()
            
            if not df.empty:
                processed[ticker] = df.tail(2)
        return processed
    except: return {}

# ---------------------------------------------------------
# 3. EL CERVELL (BACKGROUND THREAD)
# ---------------------------------------------------------
# Aquest procés corre en paral·lel i mai s'atura mentre el servidor estigui encès

def run_trading_logic():
    print("🛡️ CERVELL STRICTE ARRENCAT: Vigilant ADX > 25 i EMA 50...")
    
    while True:
        try:
            data = load_data()
            portfolio = data['portfolio']
            balance = data['balance']
            equity = data['equity']
            
            market_data = get_market_data(TICKERS)
            changes = False
            
            # Recalculem l'equity actualitzada
            temp_equity = balance
            
            for ticker in TICKERS:
                item = portfolio[ticker]
                current_price = 0.0
                
                if market_data and ticker in market_data:
                    current_price = float(market_data[ticker].iloc[-1]['Close'])
                
                # Si no tenim preu actual però estem dins, usem referència antiga
                if current_price == 0 and item['status'] == 'INVESTED':
                    current_price = item['entry_price']
                
                # --- A) GESTIÓ DE POSICIONS OBERTES ---
                if item['status'] == 'INVESTED' and current_price > 0:
                    # 1. Valor Brut
                    gross_val = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                    lev_invested = item['invested'] * LEVERAGE
                    
                    # 2. Càlcul de PnL Net (amb comissions d'entrada i sortida)
                    net_pnl = (gross_val - lev_invested) - (lev_invested * COMMISSION_RATE)
                    net_pnl_pct = net_pnl / item['invested']
                    
                    temp_equity += (item['invested'] + net_pnl)
                    
                    # SORTIDA: TAKE PROFIT
                    if net_pnl_pct >= TARGET_NET_PROFIT:
                        balance += (item['invested'] + net_pnl)
                        data['wins'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN: {ticker} (+{net_pnl:.2f}$ | +{net_pnl_pct*100:.2f}%)")
                        changes = True
                    
                    # SORTIDA: STOP LOSS
                    elif net_pnl_pct <= -STOP_LOSS_PCT:
                        balance += (item['invested'] + net_pnl)
                        data['losses'] += 1
                        data['history'].append({'Ticker': ticker, 'Res': 'LOSS', 'PL': f"{net_pnl:.2f}$"})
                        item['status'] = 'CASH'
                        send_telegram(f"❌ LOSS: {ticker} ({net_pnl:.2f}$ | {net_pnl_pct*100:.2f}%)")
                        changes = True
                        
                # --- B) ENTRADA (LÒGICA STRICTA) ---
                elif item['status'] == 'CASH' and market_data and ticker in market_data:
                    df = market_data[ticker]
                    curr = df.iloc[-1]
                    prev = df.iloc[-2]
                    price = float(curr['Close'])
                    
                    trade_size = equity * ALLOCATION_PCT
                    
                    if balance >= trade_size:
                        # 1. TENDÈNCIA: EMA 50
                        trend_ok = price > curr['EMA_50']
                        
                        # 2. FORÇA: ADX > 25 (Filtre Clau Anti-Lateral)
                        adx_ok = curr['ADX'] > 25
                        
                        # 3. MOMENTUM: RSI > 50 i Pujant, però no sobrecomprat
                        rsi_ok = (curr['RSI'] > 50) and (curr['RSI'] < 70) and (curr['RSI'] > prev['RSI'])
                        
                        if trend_ok and adx_ok and rsi_ok:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = price
                            item['invested'] = trade_size
                            balance -= trade_size
                            send_telegram(f"🛡️ ENTRADA STRICTA: {ticker}\nPreu > EMA50\nADX: {curr['ADX']:.1f} (Fort)\nRSI: {curr['RSI']:.1f} (Pujant)\nInv: {trade_size:.2f}$")
                            changes = True

            # Actualitzem dades globals
            data['balance'] = balance
            data['equity'] = temp_equity
            data['portfolio'] = portfolio
            data['last_update'] = datetime.now().strftime("%H:%M:%S")
            
            if changes:
                save_data(data)
            
            # Guardem periòdicament per mantenir el timestamp viu
            if datetime.now().second < 5: 
                save_data(data)

        except Exception as e:
            print(f"Error al fil de fons: {e}")
        
        # Espera de 60 segons abans del següent escaneig
        time.sleep(60)

# Aquesta funció arrenca el fil NOMÉS UN COP
@st.cache_resource
def start_background_bot():
    if not os.path.exists(DATA_FILE):
        save_data(load_data()) 
    
    thread = threading.Thread(target=run_trading_logic, daemon=True)
    thread.start()
    return thread

# ---------------------------------------------------------
# 4. LA INTERFÍCIE WEB (VISOR)
# ---------------------------------------------------------
# Arrenquem el procés de fons
start_background_bot()

st.title("🛡️ Bot Strict 24/7 (Background)")
st.caption("Filtres Actius: EMA 50 + ADX > 25 + RSI. Pots tancar la pestanya.")

placeholder = st.empty()

while True:
    data = load_data()
    
    with placeholder.container():
        st.write(f"🔄 Últim escaneig del cervell: **{data.get('last_update')}**")
        
        # Mètriques
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Equity Total", f"{data.get('equity', 0):.2f}$")
        m2.metric("Cash Disponible", f"{data.get('balance', 0):.2f}$")
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
                        st.caption("CASH (Vigilant...)")

        # Historial
        hist = data.get('history', [])
        if hist:
            st.write("---")
            st.write("Historial d'Operacions:")
            st.dataframe(pd.DataFrame(hist).iloc[::-1].head(10))

    # Refresquem la pantalla cada 10 segons (només visual)
    time.sleep(10)