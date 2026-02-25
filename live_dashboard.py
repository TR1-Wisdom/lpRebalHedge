"""
live_dashboard.py
Command Center v3.4.0: ระบบบัญชีคู่ขนาน พร้อมระบบติดตามผลงาน (Performance Tracker)
อัปเกรด: เพิ่มระบบบันทึก Session PnL, Cumulative Fees และ Real-time Delta Drift
"""

import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from dotenv import load_dotenv
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from datetime import datetime

# --- Configuration & Scaling ---
VIRTUAL_SCALING_FACTOR = 50.0 

# --- Session State Initialization ---
# ใช้สำหรับเก็บข้อมูลสะสมตั้งแต่เปิด Dashboard (Session Tracking)
if 'start_time' not in st.session_state:
    st.session_state.start_time = datetime.now()
if 'initial_scaled_value' not in st.session_state:
    st.session_state.initial_scaled_value = None
if 'history_log' not in st.session_state:
    st.session_state.history_log = []

# --- Page Configuration ---
st.set_page_config(page_title="Alpha Command Center", layout="wide", page_icon="🛡️")

st.markdown("""
    <style>
    .main { background-color: #0f172a; color: #f8fafc; }
    .stMetric { background-color: #1e293b; padding: 15px; border-radius: 10px; border: 1px solid #334155; }
    div[data-testid="stMetricValue"] { color: #38bdf8; }
    .status-card { background-color: #1e293b; padding: 20px; border-radius: 10px; border: 1px solid #334155; margin-bottom: 10px; }
    </style>
    """, unsafe_allow_html=True)

def fetch_onchain_data():
    load_dotenv(override=True)
    alchemy_url = os.getenv("ALCHEMY_RPC_URL")
    token_id = int(os.getenv("LP_TOKEN_ID", "0"))
    POOL_ADDR = "0xC6962004f452bE9203591991D15f6b388e09E8D0" 
    try:
        from src.utils.SafeWeb3 import SafeWeb3
        from src.lp.uniswap_v3_manager import UniswapPositionManager
        sw3 = SafeWeb3([alchemy_url])
        manager = UniswapPositionManager(sw3)
        return manager.get_inventory_balances(token_id, POOL_ADDR)
    except Exception as e:
        return {"error": f"DEX Connection Error: {str(e)}"}

def fetch_cex_data_official():
    load_dotenv(override=True)
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_SECRET")
    is_demo = os.getenv("USE_BINANCE_DEMO", "true").lower() == "true"
    
    if not api_key or not api_secret:
        return {"is_mock": True, "mode": "Mockup"}

    base_url = "https://testnet.binancefuture.com" if is_demo else "https://fapi.binance.com"
    endpoint = "/fapi/v2/positionRisk"
    
    timestamp = int(time.time() * 1000)
    params = {"timestamp": timestamp}
    query_string = urlencode(params)
    signature = hmac.new(api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    
    url = f"{base_url}{endpoint}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if response.status_code != 200:
            return {"is_mock": True, "error": data.get('msg', 'API Error'), "mode": "Auth Error"}

        total_short = 0.0
        pnl = 0.0
        symbol = "None"
        for pos in data:
            if 'ETH' in pos['symbol']:
                amt = float(pos['positionAmt'])
                if amt != 0:
                    total_short = abs(amt)
                    pnl = float(pos['unRealizedProfit'])
                    symbol = pos['symbol']
                    break
        return {"short_size": total_short, "unrealized_pnl": pnl, "is_mock": False, "mode": "Demo" if is_demo else "Real", "symbol": symbol}
    except Exception as e:
        return {"is_mock": True, "error": str(e)}

# --- Execution ---
st.title("🛡️ Alpha Command Center v3.4.0")
st.caption(f"Session Active: {st.session_state.start_time.strftime('%Y-%m-%d %H:%M:%S')} | Virtual Scale: {VIRTUAL_SCALING_FACTOR:.0f}x")

col_btn1, col_btn2 = st.columns([1, 4])
with col_btn1:
    if st.button("🔄 Sync All", use_container_width=True):
        st.cache_data.clear()
with col_btn2:
    if st.button("🗑️ Reset Session Stats"):
        st.session_state.start_time = datetime.now()
        st.session_state.initial_scaled_value = None
        st.session_state.history_log = []
        st.cache_data.clear()

st.markdown("---")

with st.spinner("🔄 กำลังประมวลผลข้อมูล Real-time..."):
    dex = fetch_onchain_data()
    cex = fetch_cex_data_official()

if "error" in dex:
    st.error(dex["error"])
else:
    # 1. Price & Scaling Calculations
    eth_price = (1.0001 ** dex['current_tick']) * (10**(18-6))
    eth_long_scaled = dex['total_amount0'] * VIRTUAL_SCALING_FACTOR
    usdc_scaled = dex['total_amount1'] * VIRTUAL_SCALING_FACTOR
    lp_val_scaled = (eth_long_scaled * eth_price) + usdc_scaled
    
    eth_short = cex.get('short_size', 0.0)
    future_pnl = cex.get('unrealized_pnl', 0.0)
    
    # 2. Accounting logic
    current_net_wealth = lp_val_scaled + future_pnl
    
    if st.session_state.initial_scaled_value is None:
        st.session_state.initial_scaled_value = current_net_wealth
    
    session_pnl_usd = current_net_wealth - st.session_state.initial_scaled_value
    session_pnl_pct = (session_pnl_usd / st.session_state.initial_scaled_value * 100) if st.session_state.initial_scaled_value > 0 else 0
    
    net_delta = eth_long_scaled - eth_short
    delta_usd = net_delta * eth_price

    # 3. UI Row 1: Session Performance
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Current Wealth (Scaled)", f"${current_net_wealth:,.2f}")
    
    pnl_color = "normal" if session_pnl_usd >= 0 else "inverse"
    m2.metric("Session PnL", f"${session_pnl_usd:+,.4f}", f"{session_pnl_pct:+.4f}%", delta_color=pnl_color)
    
    # คำนวณ Uncollected Fees (Scaled)
    uncollected_fees_scaled = (dex['owed_amount0'] * eth_price + dex['owed_amount1']) * VIRTUAL_SCALING_FACTOR
    m3.metric("Accrued Fees (Scaled)", f"${uncollected_fees_scaled:,.4f}", "รายได้ที่รอเก็บเกี่ยว")
    
    m4.metric("Net Delta Exposure", f"{net_delta:.4f} ETH", f"${delta_usd:+.2f} USD", delta_color="normal" if abs(delta_usd) < 5 else "inverse")

    st.markdown("---")

    # 4. Charts & Real-time Drift
    c1, c2 = st.columns([1.5, 1])
    
    with c1:
        st.markdown("### 📊 Live Inventory vs Hedge")
        fig = go.Figure(data=[
            go.Bar(name='Long (DEX Scaled)', x=['ETH'], y=[eth_long_scaled], marker_color='#6366f1'),
            go.Bar(name='Short (CEX Actual)', x=['ETH'], y=[eth_short], marker_color='#f43f5e')
        ])
        fig.update_layout(barmode='group', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#f8fafc'), height=350)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.markdown("### 📋 Position Intelligence")
        st.info(f"**Current Price:** ${eth_price:,.2f}")
        
        # คำนวณความเบี่ยงเบน (Drift)
        drift_pct = (abs(net_delta) / eth_long_scaled * 100) if eth_long_scaled > 0 else 0
        st.write(f"**Inventory Drift:** {drift_pct:.2f}%")
        
        if drift_pct > 5:
            st.warning(f"⚠️ **Action Required:** กรุณาปรับ Short ให้เป็น {eth_long_scaled:.4f} ETH")
        else:
            st.success("✅ **Status:** Delta Neutral Stable")

        # Session Log (บันทึกข้อมูลย้อนหลังสั้นๆ)
        st.session_state.history_log.append({
            "Time": datetime.now().strftime("%H:%M:%S"),
            "Price": eth_price,
            "Delta": delta_usd
        })
        if len(st.session_state.history_log) > 10: st.session_state.history_log.pop(0)
        
        st.markdown("#### 🕒 Last 10 Syncs")
        st.table(pd.DataFrame(st.session_state.history_log).sort_index(ascending=False))

    # Sidebar Insights
    st.sidebar.markdown("### 🧠 Live Quant Insight")
    st.sidebar.write(f"**RPC Latency:** {dex['latency_ms']} ms")
    st.sidebar.write(f"**Residual Risk:** ในวินาทีนี้ หากราคาขยับ 1% พอร์ตสุทธิจะขยับเพียง `${abs(delta_usd * 0.01):,.4f}` (นี่คือพลังของ Delta Neutral ครับ)")