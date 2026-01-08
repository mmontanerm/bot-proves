import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import time
import os
import json
from datetime import datetime

# ---------------------------------------------------------
# 1. CONFIGURACIÓ "SNIPER" (Més Seguretat, Menys Risc)
# ---------------------------------------------------------
st.set_page_config(page_title="Sniper Bot 0.6%", layout="wide", page_icon="🎯")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# CARTERA (Mantenim la diversificació)
TICKERS = ['NVDA', 'TSLA', 'AMZN', 'META', 'LLY', 'JPM', 'USO', 'GLD', 'BTC-USD', 'COST']

TIMEFRAME = "1m"        
LEVERAGE = 5            

# GESTIÓ DE CAPITAL
ALLOCATION_PCT = 0.10   # 10% per operació
MAX_POSITIONS = 10      

# NOUS OBJECTIUS (SCALPING RÀPID)
TARGET_NET_PROFIT = 0.006  # 0.6% Net (Sortida Ràpida)
STOP_LOSS_PCT = 0.006      # 0.6% Stop (Ratio 1:1)

# Comissions estimades (Spread/Swap)
COMMISSION_RATE = 0.001 

INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_sniper_data.json"

# ---------------------------------------------------------
# 2. PERSISTÈNCIA
# ---------------------------------------------------------
def save_state():
    data = {
        'balance': st.session_state.balance,
        'equity': st.session_state.equity,
        'wins': st.session_state.wins,
        'losses': st.session_state.losses,
        'portfolio': st.session_state.portfolio,
        'history': st.session_state.history,
        'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
    except: pass

def load_state():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except: return None
    return None

saved_data = load_state()
if saved_data:
    if 'balance' not in st.session_state:
        st.session_state.balance = saved_data.get('balance', INITIAL_CAPITAL)
        st.session_state.equity = saved_data.get('equity', INITIAL_CAPITAL)
        st.session_state.wins = saved_data.get('wins', 0)
        st.session_state.losses = saved_data.get('losses', 0)
        st.session_state.portfolio = saved_data.get('portfolio', {})
        st.session_state.history = saved_data.get('history', [])
        st.toast("🎯 Bot Sniper carregat.")
else:
    if 'balance' not in st.session_state:
        st.session_state.balance = INITIAL_CAPITAL 
        st.session_state.equity = INITIAL_CAPITAL  
        st.session_state.wins = 0
        st.session_state.losses = 0
        st.session_state.history = []
    if 'portfolio' not in st.session_state:
        st.session_state.portfolio = {
            t: {'status': 'CASH', 'entry_price': 0.0, 'invested': 0.0, 'shares': 0.0, 'stop': 0.0, 'target': 0.0} 
            for t in TICKERS
        }

if len(st.session_state.history) > 50:
    st.session_state.history = st.session_state.history[-50:]

# ---------------------------------------------------------
# 3. MOTOR D'ANÀLISI (NOVA ESTRATÈGIA)
# ---------------------------------------------------------
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🎯 [SNIPER 0.6%]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_data_sniper(tickers):
    try:
        # Necessitem més històric (10d) per calcular bé l'EMA 200
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

            if df.empty or len(df) < 200: continue # Necessitem 200 espelmes mínim
            df = df.dropna()

            # --- INDICADORS DE SEGURETAT ---
            
            # 1. EMA 200 (Filtre de Tendència Major)
            df['EMA_200'] = ta.ema(df['Close'], length=200)
            
            # 2. ADX (Força de tendència - volem > 25)
            try:
                adx = ta.adx(df['High'], df['Low'], df['Close'], length=14)
                df['ADX'] = adx[adx.columns[0]] if adx is not None else 0
            except: df['ADX'] = 0

            # 3. STOCH RSI (Per comprar al "Dip")
            # Això ens diu si està sobrevenut a curt termini
            stoch = ta.stochrsi(df['Close'], length=14, rsi_length=14, k=3, d=3)
            if stoch is not None:
                df['STOCH_K'] = stoch[stoch.columns[0]] # Línia ràpida
                df['STOCH_D'] = stoch[stoch.columns[1]] # Línia lenta
            else:
                df['STOCH_K'] = 50
                df['STOCH_D'] = 50

            df = df.dropna()
            if not df.empty:
                processed[ticker] = df.tail(2)
        return processed
    except: return {}

# ---------------------------------------------------------
# 4. BUCLE PRINCIPAL
# ---------------------------------------------------------
st.title("🎯 Bot Sniper: Objectiu 0.6% Segur")
st.caption("Estratègia: 'Pullback' (Comprar caigudes en tendència alcista). Filtre EMA 200 + StochRSI.")

current_equity = st.session_state.balance
positions_count = 0

placeholder = st.empty()

while True:
    with placeholder.container():
        market_data = get_data_sniper(TICKERS)
        changes_made = False
        
        temp_equity = st.session_state.balance
        
        cols = st.columns(5)
        
        for i, ticker in enumerate(TICKERS):
            item = st.session_state.portfolio[ticker]
            current_price = 0.0
            net_pnl = 0.0
            net_pnl_pct = 0.0

            if market_data and ticker in market_data:
                df = market_data[ticker]
                if len(df) >= 1:
                    current_price = float(df.iloc[-1]['Close'])
            
            if current_price == 0.0 and item['status'] == 'INVESTED':
                current_price = item['entry_price']

            # --- GESTIÓ POSICIONS ---
            if item['status'] == 'INVESTED' and current_price > 0:
                positions_count += 1
                
                # Càlcul P&L
                gross_value = (item['invested'] * LEVERAGE / item['entry_price']) * current_price
                lev_invested = item['invested'] * LEVERAGE
                gross_pnl = gross_value - lev_invested
                commission_cost = lev_invested * COMMISSION_RATE
                
                net_pnl = gross_pnl - commission_cost
                net_pnl_pct = (net_pnl / item['invested']) 
                
                temp_equity += (item['invested'] + net_pnl)

                # SORTIDA: Més ràpida (0.6%)
                if net_pnl_pct >= TARGET_NET_PROFIT:
                    st.session_state.balance += (item['invested'] + net_pnl)
                    st.session_state.wins += 1
                    st.session_state.history.append({
                        'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{net_pnl:.2f}$ ({net_pnl_pct*100:.2f}%)"
                    })
                    item['status'] = 'CASH'
                    send_telegram(f"✅ WIN: {ticker}\nBenefici: +{net_pnl:.2f}$ (+0.6%)")
                    changes_made = True
                
                elif net_pnl_pct <= -STOP_LOSS_PCT:
                    remaining = item['invested'] + net_pnl
                    st.session_state.balance += remaining
                    st.session_state.losses += 1
                    st.session_state.history.append({
                        'Ticker': ticker, 'Res': 'LOSS', 'PL': f"{net_pnl:.2f}$ ({net_pnl_pct*100:.2f}%)"
                    })
                    item['status'] = 'CASH'
                    send_telegram(f"❌ LOSS: {ticker}\nPèrdua: {net_pnl:.2f}$")
                    changes_made = True

            # --- ENTRADA: ESTRATÈGIA MÉS SEGURA ---
            elif item['status'] == 'CASH' and market_data and ticker in market_data:
                df = market_data[ticker]
                if len(df) >= 2:
                    curr = df.iloc[-1]
                    prev = df.iloc[-2]
                    current_price = float(curr['Close'])
                    
                    trade_size = st.session_state.equity * ALLOCATION_PCT
                    
                    if st.session_state.balance >= trade_size:
                        
                        # 1. SEGURETAT: Estem per sobre de la mitjana de 200 sessions?
                        # Si el preu està per sota de l'EMA 200, la tendència és baixista i NO comprem.
                        trend_safe = current_price > curr['EMA_200']
                        
                        # 2. FORÇA: La tendència té força real?
                        adx_strong = curr['ADX'] > 25
                        
                        # 3. OPORTUNITAT (DIP): Estem comprant barat?
                        # StochRSI per sota de 20 (sobrevenut) i creuant cap amunt (K > D)
                        stoch_oversold = prev['STOCH_K'] < 20
                        stoch_crossing_up = (prev['STOCH_K'] < prev['STOCH_D']) and (curr['STOCH_K'] > curr['STOCH_D'])
                        dip_entry = stoch_oversold and stoch_crossing_up
                        
                        # NOMÉS SI ES COMPLEIX TOT
                        if trend_safe and adx_strong and dip_entry:
                            item['status'] = 'INVESTED'
                            item['entry_price'] = current_price
                            item['invested'] = trade_size
                            
                            st.session_state.balance -= trade_size
                            send_telegram(f"🎯 SNIPER ENTRY: {ticker}\nPreu > EMA200 (Tendència OK)\nStochRSI < 20 (Preu Barat)\nInversió: {trade_size:.2f}$")
                            changes_made = True
            
            # --- VISUALITZACIÓ ---
            col_idx = i % 5 
            with cols[col_idx]:
                border = "green" if item['status'] == 'INVESTED' else "grey"
                with st.container(border=True):
                    st.markdown(f"**{ticker}**")
                    if item['status'] == 'INVESTED':
                        color = "green" if net_pnl > 0 else "red"
                        st.markdown(f"<span style='color:{color}'>{net_pnl:.2f}$</span>", unsafe_allow_html=True)
                    else:
                        st.caption(f"{current_price:.2f}$")

        st.session_state.equity = temp_equity
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Valor Compte", f"{st.session_state.equity:.2f} $")
        m2.metric("Cash", f"{st.session_state.balance:.2f} $")
        
        open_pos = sum(1 for t in TICKERS if st.session_state.portfolio[t]['status'] == 'INVESTED')
        m3.metric("Posicions", f"{open_pos} / 10")
        
        total = st.session_state.wins + st.session_state.losses
        wr = (st.session_state.wins/total*100) if total > 0 else 0
        m4.metric("Win Rate", f"{wr:.1f}%")

        if changes_made:
            save_state()

        if st.session_state.history:
            st.write("---")
            st.dataframe(pd.DataFrame(st.session_state.history).iloc[::-1].head(5))

    time.sleep(60)