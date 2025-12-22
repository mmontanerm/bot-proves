import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import time
import os  
from datetime import datetime

# ---------------------------------------------------------
# 1. CONFIGURACIÓ DE L'ESTRATÈGIA (SCALPING)
# ---------------------------------------------------------
st.set_page_config(page_title="Test Lab: Scalping 1%", layout="wide", page_icon="🧪")

# --- CREDENCIALS TELEGRAM (Substitueix o usa st.secrets al núvol) ---
# Per proves locals, pots posar-los aquí directament si no ho puges a GitHub encara
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Si per algun motiu no les troba, avisarà
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    print("⚠️ ERROR: No s'han trobat les credencials de Telegram a les Variables d'Entorn.")

# --- PARAMETRES ---
TICKERS = ['NVDA', 'TSLA', 'META', 'MSFT', 'BTC-USD', 'ETH-USD']
TIMEFRAME = "1m"        
LEVERAGE = 5            
TARGET_PROFIT = 0.01    # 1% de Benefici Objectiu
STOP_LOSS_PCT = 0.005   # 0.5% de moviment real en contra (2.5% pèrdua amb x5) com a seguretat

# Simulem un capital inicial
INITIAL_CAPITAL = 10000.0

# ---------------------------------------------------------
# 2. GESTIÓ D'ESTAT (MEMÒRIA)
# ---------------------------------------------------------
if 'balance' not in st.session_state:
    st.session_state.balance = INITIAL_CAPITAL
    st.session_state.wins = 0
    st.session_state.losses = 0
    st.session_state.history = [] # Llista per guardar operacions passades

if 'portfolio' not in st.session_state:
    st.session_state.portfolio = {
        ticker: {
            'status': 'CASH',
            'entry_price': 0.0,
            'amount_invested': 0.0, # Invertim 1000$ per operació virtual
            'stop_price': 0.0,
            'target_price': 0.0
        } for ticker in TICKERS
    }

# ---------------------------------------------------------
# 3. FUNCIONS
# ---------------------------------------------------------

def send_telegram(msg):
    """Envia alerta indicant que és un TEST."""
    if "ENGANXA" in TELEGRAM_TOKEN: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🧪 [MODE TEST]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_data(tickers):
    try:
        # Baixem només 1 dia, interval 1m és suficient per scalping
        data = yf.download(tickers, period="1d", interval="1m", group_by='ticker', progress=False)
        processed = {}
        for ticker in tickers:
            df = data[ticker].copy() if len(tickers) > 1 else data.copy()
            if df.empty: continue
            df = df.dropna()
            
            # ESTRATÈGIA RÀPIDA: EMA 20 + RSI
            # Per scalping volem coses més ràpides que l'EMA 50
            df['EMA'] = ta.ema(df['Close'], length=20)
            df['RSI'] = ta.rsi(df['Close'], length=14)
            
            processed[ticker] = df.tail(2)
        return processed
    except Exception as e:
        return {}

# ---------------------------------------------------------
# 4. INTERFÍCIE I BUCLE
# ---------------------------------------------------------

st.title("🧪 Laboratori de Proves: Objectiu 1%")
st.info(f"Estratègia: Buscar un moviment del {TARGET_PROFIT/LEVERAGE*100:.2f}% al mercat (x{LEVERAGE} = 1%).")

# --- METRIQUES DEL COMPTE VIRTUAL ---
col1, col2, col3 = st.columns(3)
col1.metric("Capital Virtual", f"{st.session_state.balance:.2f} $", delta=st.session_state.balance - INITIAL_CAPITAL)
total_trades = st.session_state.wins + st.session_state.losses
win_rate = (st.session_state.wins / total_trades * 100) if total_trades > 0 else 0
col2.metric("Win Rate", f"{win_rate:.1f} %", f"{st.session_state.wins}W / {st.session_state.losses}L")
col3.metric("Operacions Tancades", total_trades)

# Botó d'execució
run = st.toggle("🚀 ACTIVAR SIMULADOR", value=False)
placeholder = st.empty()

while run:
    with placeholder.container():
        st.write(f"⏳ Analitzant preus... {datetime.now().strftime('%H:%M:%S')}")
        market_data = get_data(TICKERS)
        
        if not market_data:
            time.sleep(2)
            continue

        cols = st.columns(3)
        for i, ticker in enumerate(TICKERS):
            if ticker not in market_data: continue
            
            df = market_data[ticker]
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            current_price = float(curr['Close'])
            
            item = st.session_state.portfolio[ticker]
            
            # --- LÒGICA DE TRADING ---
            
            # 1. BUSCAR ENTRADA (Si tenim CASH)
            if item['status'] == 'CASH':
                # Estratègia Scalping:
                # Preu > EMA 20 (Tendència alcista curta)
                # RSI < 70 (No sobrecomprat)
                # RSI creuant cap amunt (Momentum)
                trend_ok = current_price > curr['EMA']
                rsi_ok = prev['RSI'] < curr['RSI'] and 40 < curr['RSI'] < 70
                
                if trend_ok and rsi_ok:
                    # SIMULACIÓ DE COMPRA
                    item['status'] = 'INVESTED'
                    item['entry_price'] = current_price
                    item['amount_invested'] = 1000.0 # Fixem 1000$ per trade
                    
                    # Calculem el preu objectiu exacte per guanyar 1% palanquejat
                    # Moviment necessari = 1% / 5 = 0.2% = 0.002
                    raw_move_needed = TARGET_PROFIT / LEVERAGE
                    item['target_price'] = current_price * (1 + raw_move_needed)
                    item['stop_price'] = current_price * (1 - STOP_LOSS_PCT)
                    
                    msg = f"🔵 *SIMULACIÓ COMPRA: {ticker}*\nPreu: {current_price:.2f}$\nTarget (+1%): {item['target_price']:.2f}$"
                    send_telegram(msg)
                    st.toast(f"Compra simulada {ticker}")

            # 2. GESTIÓ DE POSICIÓ (Si estem INVESTED)
            elif item['status'] == 'INVESTED':
                
                # A) TAKE PROFIT (GUANYEM 1%)
                if current_price >= item['target_price']:
                    # Càlcul benefici
                    profit_amt = item['amount_invested'] * TARGET_PROFIT
                    st.session_state.balance += profit_amt
                    st.session_state.wins += 1
                    
                    # Registre
                    st.session_state.history.append({'Ticker': ticker, 'Res': 'WIN', 'PL': f"+{TARGET_PROFIT*100}%"})
                    item['status'] = 'CASH'
                    
                    msg = f"✅ *OBJECTIU ASSOLIT: {ticker}*\nVenda a: {current_price:.2f}$\nBenefici: +1%"
                    send_telegram(msg)
                    st.toast(f"Take Profit {ticker}!", icon="💰")
                
                # B) STOP LOSS (PROTECCIÓ)
                elif current_price <= item['stop_price']:
                    # Càlcul pèrdua
                    raw_loss = (item['entry_price'] - current_price) / item['entry_price']
                    leveraged_loss = raw_loss * LEVERAGE
                    loss_amt = item['amount_invested'] * leveraged_loss
                    
                    st.session_state.balance -= loss_amt
                    st.session_state.losses += 1
                    
                    # Registre
                    st.session_state.history.append({'Ticker': ticker, 'Res': 'LOSS', 'PL': f"-{leveraged_loss*100:.2f}%"})
                    item['status'] = 'CASH'
                    
                    msg = f"❌ *STOP LOSS: {ticker}*\nVenda a: {current_price:.2f}$\nPèrdua: -{leveraged_loss*100:.2f}%"
                    send_telegram(msg)
                    st.toast(f"Stop Loss {ticker}", icon="💀")

            # --- VISUALITZACIÓ ---
            col_idx = i % 3
            with cols[col_idx]:
                with st.container(border=True):
                    st.subheader(f"{ticker}")
                    st.write(f"Preu: {current_price:.2f}$")
                    
                    if item['status'] == 'INVESTED':
                        # Barra de progrés cap a l'objectiu
                        dist_total = item['target_price'] - item['entry_price']
                        dist_curr = current_price - item['entry_price']
                        if dist_total > 0:
                            progress = max(0.0, min(1.0, dist_curr / dist_total))
                            st.progress(progress, text="Camí cap a l'1%")
                        
                        st.caption(f"Target: {item['target_price']:.2f}$ | Stop: {item['stop_price']:.2f}$")
                    else:
                        st.caption("Esperant entrada...")

        # Taula historial
        if st.session_state.history:
            st.write("### 📜 Historial de Proves")
            st.dataframe(pd.DataFrame(st.session_state.history).iloc[::-1]) # Invertir ordre

    time.sleep(30) # Refresc més ràpid (30s) per scalping
    st.rerun()
