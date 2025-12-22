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
# 1. CONFIGURACIÓ
# ---------------------------------------------------------
st.set_page_config(page_title="Bot MaxProfit Secure", layout="wide", page_icon="🛡️")

# Recuperem credencials de l'entorn (Render)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Paràmetres de l'estratègia
TICKERS = ['NVDA', 'TSLA', 'META', 'MSFT', 'BTC-USD', 'ETH-USD']
TIMEFRAME = "1m"        
LEVERAGE = 5            
TARGET_PROFIT = 0.015   # 1.5% Guany Objectiu
STOP_LOSS_PCT = 0.003   # 0.3% Moviment real (x5 = 1.5% Pèrdua)
INITIAL_CAPITAL = 10000.0
DATA_FILE = "bot_data.json"

# ---------------------------------------------------------
# 2. FUNCIONS DE PERSISTÈNCIA (MEMÒRIA)
# ---------------------------------------------------------
def save_state():
    """Guarda l'estat actual al fitxer JSON."""
    data = {
        'balance': st.session_state.balance,
        'wins': st.session_state.wins,
        'losses': st.session_state.losses,
        'portfolio': st.session_state.portfolio,
        'history': st.session_state.history,
        'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Error guardant estat: {e}")

def load_state():
    """Carrega l'estat des del fitxer JSON si existeix."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except:
            return None
    return None

# ---------------------------------------------------------
# 3. INICIALITZACIÓ D'ESTAT
# ---------------------------------------------------------
saved_data = load_state()

if saved_data:
    if 'balance' not in st.session_state:
        st.session_state.balance = saved_data.get('balance', INITIAL_CAPITAL)
        st.session_state.wins = saved_data.get('wins', 0)
        st.session_state.losses = saved_data.get('losses', 0)
        st.session_state.portfolio = saved_data.get('portfolio', {})
        st.session_state.history = saved_data.get('history', [])
        st.toast(f"💾 Dades recuperades! Últim guardat: {saved_data.get('last_update')}")
else:
    if 'balance' not in st.session_state:
        st.session_state.balance = INITIAL_CAPITAL
        st.session_state.wins = 0
        st.session_state.losses = 0
        st.session_state.history = []
        
    if 'portfolio' not in st.session_state:
        st.session_state.portfolio = {
            ticker: {
                'status': 'CASH',
                'entry_price': 0.0,
                'amount_invested': 0.0,
                'stop_price': 0.0,
                'target_price': 0.0
            } for ticker in TICKERS
        }

# Limitar historial per no saturar memòria
if len(st.session_state.history) > 50:
    st.session_state.history = st.session_state.history[-50:]

# ---------------------------------------------------------
# 4. FUNCIONS AUXILIARS
# ---------------------------------------------------------
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🚀 [BOT RENDER]\n{msg}", "parse_mode": "Markdown"}
        requests.post(url, json=payload)
    except: pass

def get_data_optimized(tickers):
    """
    Baixa dades de manera robusta i calcula indicadors.
    """
    try:
        # CORRECCIÓ 1: auto_adjust=True per evitar warnings
        # Afegim threads=False de vegades ajuda en entorns limitats, però ho deixem per defecte
        data = yf.download(tickers, period="5d", interval="1m", group_by='ticker', progress=False, auto_adjust=True)
        
        processed = {}
        
        for ticker in tickers:
            # CORRECCIÓ 2: Gestió d'errors si un ticker falla
            try:
                if len(tickers) > 1:
                    # Comprovem si el ticker existeix a les dades baixades
                    if ticker not in data.columns.levels[0]:
                        continue
                    df = data[ticker].copy()
                else:
                    df = data.copy()
            except KeyError:
                continue

            # CORRECCIÓ 3: Comprovació de dades buides
            if df.empty: continue
            
            # Neteja de nuls
            df = df.dropna()
            
            # Necessitem mínim 20 files per calcular EMA
            if len(df) < 20: continue

            # --- INDICADORS ---
            df['EMA'] = ta.ema(df['Close'], length=20)
            df['RSI'] = ta.rsi(df['Close'], length=14)
            
            # ADX
            try:
                adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
                df['ADX'] = adx_df[adx_df.columns[0]] if adx_df is not None else 0
            except:
                df['ADX'] = 0

            # Volum
            df['VOL_SMA'] = ta.sma(df['Volume'], length=20)
            
            # Neteja final després de calcular indicadors
            df = df.dropna()

            # Guardem només si queden dades
            if not df.empty:
                processed[ticker] = df.tail(2)
            
        return processed
    except Exception as e:
        print(f"Error general baixant dades: {e}")
        return {}

# ---------------------------------------------------------
# 5. BUCLE PRINCIPAL
# ---------------------------------------------------------

st.title("🛡️ Bot MaxProfit (Secure Mode)")
st.caption("Mode segur actiu: Protecció contra fallades de Yahoo Finance i reinicis.")

# Mètriques
c1, c2, c3 = st.columns(3)
c1.metric("Capital", f"{st.session_state.balance:.2f} $")
total = st.session_state.wins + st.session_state.losses
winrate = (st.session_state.wins/total*100) if total > 0 else 0
c2.metric("Win Rate", f"{winrate:.1f}%")
c3.metric("Trades", total)

placeholder = st.empty()

while True:
    with placeholder.container():
        st.write(f"📡 Rastrejant... {datetime.now().strftime('%H:%M:%S')}")
        
        market_data = get_data_optimized(TICKERS)
        changes_made = False
        
        if market_data:
            cols = st.columns(3)
            
            for i, ticker in enumerate(TICKERS):
                if ticker not in market_data: continue
                
                df = market_data[ticker]
                
                # CORRECCIÓ 4: CRÍTICA - Comprovació de longitud abans d'accedir
                if len(df) < 2:
                    continue

                curr = df.iloc[-1]
                prev = df.iloc[-2]
                
                # Protecció extra contra valors nuls puntuals
                if pd.isna(curr['Close']): continue
                
                current_price = float(curr['Close'])
                item = st.session_state.portfolio[ticker]
                
                # --- LÒGICA TRADING ---
                if item['status'] == 'CASH':
                    # Check segur dels indicadors
                    trend_ok = current_price > curr['EMA']
                    rsi_ok = (prev['RSI'] < curr['RSI']) and (45 < curr['RSI'] < 70)
                    adx_ok = curr['ADX'] > 20
                    vol_ok = curr['Volume'] > curr['VOL_SMA']
                    
                    if trend_ok and rsi_ok and adx_ok and vol_ok:
                        item['status'] = 'INVESTED'
                        item['entry_price'] = current_price
                        item['amount_invested'] = 1000.0
                        
                        raw_move = TARGET_PROFIT / LEVERAGE
                        item['target_price'] = current_price * (1 + raw_move)
                        item['stop_price'] = current_price * (1 - STOP_LOSS_PCT)
                        
                        send_telegram(f"🔵 COMPRA: {ticker} a {current_price:.2f}$ (ADX: {curr['ADX']:.1f})")
                        changes_made = True

                elif item['status'] == 'INVESTED':
                    # Take Profit
                    if current_price >= item['target_price']:
                        profit = item['amount_invested'] * TARGET_PROFIT
                        st.session_state.balance += profit
                        st.session_state.wins += 1
                        st.session_state.history.append({'Ticker': ticker, 'Res': 'WIN', 'Amt': f"+{profit:.1f}"})
                        item['status'] = 'CASH'
                        send_telegram(f"✅ WIN: {ticker}\nBenefici: +1.5%")
                        changes_made = True
                    
                    # Stop Loss
                    elif current_price <= item['stop_price']:
                        loss = item['amount_invested'] * (STOP_LOSS_PCT * LEVERAGE)
                        st.session_state.balance -= loss
                        st.session_state.losses += 1
                        st.session_state.history.append({'Ticker': ticker, 'Res': 'LOSS', 'Amt': f"-{loss:.1f}"})
                        item['status'] = 'CASH'
                        send_telegram(f"❌ LOSS: {ticker}\nPèrdua: -1.5%")
                        changes_made = True

                # Visualització
                idx = i % 3
                with cols[idx]:
                    color = "green" if item['status'] == 'INVESTED' else "gray"
                    st.markdown(f"**{ticker}**: {current_price:.2f}$ <span style='color:{color}'>●</span>", unsafe_allow_html=True)

        if changes_made:
            save_state()

        if st.session_state.history:
            st.dataframe(pd.DataFrame(st.session_state.history).iloc[::-1].head(5), height=150)

    # Espera de 60 segons
    time.sleep(60)
