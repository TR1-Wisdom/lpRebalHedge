"""
live_dashboard.py
Command Center สำหรับดูพอร์ตจริงบน On-chain (Phase 1: Monitoring)
รันด้วยคำสั่ง: streamlit run live_dashboard.py
"""

import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from dotenv import load_dotenv

# นำเข้า Modules ของเรา
from src.utils.SafeWeb3 import SafeWeb3
from src.lp.uniswap_v3_manager import UniswapPositionManager

# --- Page Configuration ---
st.set_page_config(page_title="Quant Lab: Live Monitor", layout="wide", page_icon="📡")

# Custom CSS
st.markdown("""
    <style>
    .main { background-color: #0f172a; color: #f8fafc; }
    .stMetric { background-color: #1e293b; padding: 15px; border-radius: 10px; border: 1px solid #334155; }
    div[data-testid="stMetricValue"] { color: #38bdf8; }
    </style>
    """, unsafe_allow_html=True)

def fetch_onchain_data():
    """ฟังก์ชันดึงข้อมูลจาก Web3"""
    load_dotenv()
    alchemy_url = os.getenv("ALCHEMY_RPC_URL")
    token_id = int(os.getenv("LP_TOKEN_ID", "0"))
    
    if not alchemy_url or token_id == 0:
        return {"error": "กรุณาตั้งค่า ALCHEMY_RPC_URL และ LP_TOKEN_ID ในไฟล์ .env"}

    # Pool USDC/USDT 0.01% บน Arbitrum (ของพาร์ทเนอร์)
    STABLE_POOL_ADDR = "0xbE3aD6a5669Dc0B8b12FeBC03608860C31E2eef6"
    
    try:
        sw3 = SafeWeb3([alchemy_url])
        manager = UniswapPositionManager(sw3)
        res = manager.get_inventory_balances(token_id, STABLE_POOL_ADDR)
        return res
    except Exception as e:
        return {"error": str(e)}

# --- UI Layout ---
st.title("📡 Live On-chain Monitor (V3)")
st.caption("ระบบดึงข้อมูล Inventory สดจาก Arbitrum Network")

# ปุ่ม Refresh
col1, col2 = st.columns([1, 5])
with col1:
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear() # เคลียร์แคชเพื่อให้ดึงใหม่
with col2:
    st.markdown("<p style='color: #94a3b8; padding-top: 10px;'>อัปเดตล่าสุด: กดปุ่ม Refresh เพื่อดึงข้อมูล On-chain</p>", unsafe_allow_html=True)

st.markdown("---")

# ดึงข้อมูล
with st.spinner("กำลังเชื่อมต่อ RPC และดึงข้อมูลจาก Smart Contract..."):
    data = fetch_onchain_data()

if "error" in data:
    st.error(f"🚨 ตรวจพบข้อผิดพลาด: {data['error']}")
else:
    # 1. คำนวณมูลค่ารวม
    total_usdc = data['total_amount0']
    total_usdt = data['total_amount1']
    total_value = total_usdc + total_usdt
    
    # 2. แถบ Metrics ด้านบน
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Portfolio Value", f"${total_value:,.4f}")
    m2.metric("USDC Balance", f"{total_usdc:,.4f}")
    m3.metric("USDT Balance", f"{total_usdt:,.4f}")
    
    # [Quant Risk Management] วิเคราะห์ Residual Risk จาก Latency
    latency = data['latency_ms']
    latency_color = "normal"
    if latency > 1000:
        latency_color = "inverse" # สีแดง
        st.sidebar.warning("⚠️ **Residual Risk Alert:** RPC Latency สูงกว่า 1 วินาที! หากตลาดสวิงแรง บอทอาจเปิด Hedge ช้ากว่าความเป็นจริง")
    elif latency < 300:
        st.sidebar.success(f"⚡ **Excellent Latency:** {latency} ms (พร้อมลุย Direct Control)")
        
    m4.metric("RPC Latency (Risk Radar)", f"{latency} ms", delta="ความเร็วการเชื่อมต่อ", delta_color=latency_color)

    st.markdown("<br>", unsafe_allow_html=True)

    # 3. ส่วนแสดงกราฟและตาราง
    c1, c2 = st.columns([1.5, 1])
    
    with c1:
        st.markdown("### 🍩 Inventory Ratio (สัดส่วนเหรียญ)")
        # สร้าง Donut Chart ด้วย Plotly
        fig = go.Figure(data=[go.Pie(
            labels=['USDC (Token0)', 'USDT (Token1)'],
            values=[total_usdc, total_usdt],
            hole=.5,
            marker_colors=['#2563eb', '#16a34a'],
            textinfo='label+percent',
            hoverinfo='label+value'
        )])
        fig.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#f8fafc'),
            margin=dict(t=0, b=0, l=0, r=0),
            height=300
        )
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.markdown("### 🗃️ Position Details")
        
        # สถานะ Range
        status_text = "🟢 In Range (Working)" if data.get('is_in_range') else "🔴 Out of Range (Idle)"
        st.info(f"**Status:** {status_text}")
        st.write(f"**Token ID:** `{data['token_id']}`")
        
        # ตารางแยกถังเงิน (Active vs Owed)
        st.markdown("#### 💰 Accounting Breakdown")
        df_breakdown = pd.DataFrame({
            "ประเภทเงิน (Type)": ["Active LP (กำลังทำงาน)", "Uncollected (รอเก็บเกี่ยว)"],
            "USDC": [f"{data['active_amount0']:,.4f}", f"{data['owed_amount0']:,.4f}"],
            "USDT": [f"{data['active_amount1']:,.4f}", f"{data['owed_amount1']:,.4f}"]
        })
        st.dataframe(df_breakdown, hide_index=True, use_container_width=True)