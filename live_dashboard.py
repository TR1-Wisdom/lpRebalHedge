"""
live_dashboard.py
Command Center สำหรับดูพอร์ต ETH/USDC จริงบน On-chain (v3.0.4)
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
st.set_page_config(page_title="Quant Lab: ETH/USDC Monitor", layout="wide", page_icon="📡")

# Custom CSS สำหรับ Dark Mode ของ Quant
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

    # Pool ETH/USDC 0.05% บน Arbitrum (ของพาร์ทเนอร์)
    POOL_ADDR = "0xC6962004f452bE9203591991D15f6b388e09E8D0"
    
    try:
        sw3 = SafeWeb3([alchemy_url])
        manager = UniswapPositionManager(sw3)
        res = manager.get_inventory_balances(token_id, POOL_ADDR)
        return res
    except Exception as e:
        return {"error": str(e)}

# --- UI Layout ---
st.title("📡 Live Inventory Monitor: ETH/USDC")
st.caption("ระบบตรวจสอบสภาพคล่องและสัดส่วนเหรียญ On-chain แบบ Real-time")

# ปุ่ม Refresh
col1, col2 = st.columns([1, 5])
with col1:
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear() 
with col2:
    st.markdown("<p style='color: #94a3b8; padding-top: 10px;'>สถานะ: เชื่อมต่อ Arbitrum One | ดึงข้อมูลทุกครั้งที่กด Refresh</p>", unsafe_allow_html=True)

st.markdown("---")

# ดึงข้อมูล
with st.spinner("กำลังติดต่อ Smart Contract เพื่อดึงข้อมูล Inventory..."):
    data = fetch_onchain_data()

if "error" in data:
    st.error(f"🚨 Error: {data['error']}")
else:
    # 1. จัดเตรียมข้อมูลเหรียญ
    eth_val = data['total_amount0']
    usdc_val = data['total_amount1']
    
    # [FIX] แก้ไขสมการคำนวณราคาให้ถูกต้อง (WETH=18, USDC=6)
    # ราคาจริง = 1.0001^tick * 10^(Decimal_Token0 - Decimal_Token1)
    eth_price_approx = (1.0001 ** data['current_tick']) * (10**(18-6))
    
    total_value_usd = (eth_val * eth_price_approx) + usdc_val
    
    # 2. แถบ Metrics ด้านบน
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Est. Total Value", f"${total_value_usd:,.2f}")
    m2.metric("WETH Inventory", f"{eth_val:.6f} ETH")
    m3.metric("USDC Inventory", f"{usdc_val:.2f} USDC")
    
    # Residual Risk Radar
    latency = data['latency_ms']
    latency_status = "🟢 Healthy" if latency < 500 else "🔴 High Lag"
    m4.metric("RPC Risk Radar", f"{latency} ms", f"Status: {latency_status}")

    st.markdown("<br>", unsafe_allow_html=True)

    # 3. ส่วนแสดงกราฟและรายละเอียด
    c1, c2 = st.columns([1.5, 1])
    
    with c1:
        st.markdown("### 🍩 Portfolio Composition (Delta Base)")
        # สร้าง Donut Chart
        # คำนวณ Value สัดส่วนเป็น USD เพื่อให้เห็นภาพ Delta
        eth_usd = eth_val * eth_price_approx
        fig = go.Figure(data=[go.Pie(
            labels=['WETH (Long Exposure)', 'USDC (Cash Layer)'],
            values=[eth_usd, usdc_val],
            hole=.5,
            marker_colors=['#6366f1', '#94a3b8'],
            textinfo='label+percent',
        )])
        fig.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color='#f8fafc'),
            margin=dict(t=30, b=0, l=0, r=0),
            height=350,
            legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5)
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"ราคา ETH ที่ใช้คำนวณ (จาก Pool): ${eth_price_approx:,.2f}")

    with c2:
        st.markdown("### 📋 Position Audit")
        
        # สถานะ Range
        if data.get('is_in_range'):
            st.success("**Status:** 🟢 In Range (Generating Fees)")
        else:
            st.error("**Status:** 🔴 Out of Range (Position Idle)")
            
        st.write(f"**NFT Token ID:** `{data['token_id']}`")
        st.write(f"**Current Tick:** `{data['current_tick']}`")
        
        # ตารางแยกถังเงิน
        st.markdown("#### 💰 Balances Breakdown")
        df_breakdown = pd.DataFrame({
            "Asset": ["WETH (Token0)", "USDC (Token1)"],
            "Active LP": [f"{data['active_amount0']:.6f}", f"{data['active_amount1']:.2f}"],
            "Uncollected": [f"{data['owed_amount0']:.6f}", f"{data['owed_amount1']:.2f}"]
        })
        st.dataframe(df_breakdown, hide_index=True, use_container_width=True)

    # Sidebar Insights
    st.sidebar.markdown("### 🧠 Quant Insights")
    st.sidebar.info(f"""
    **Residual Risk Analysis:**
    ในสภาวะราคาปัจจุบัน บอทควรเปิด Short ใน CEX ขนาดประมาณ **{eth_val:.4f} ETH** เพื่อรักษาค่า Delta ให้เป็น 0 (Neutral)
    """)
    
    if latency > 500:
        st.sidebar.warning(f"⚠️ **Warning:** Latency {latency}ms อาจทำให้เกิดข้อมูลขาค้างได้")