"""
app.py (Streamlit UI for Quant Lab v1.5.1)
- หน้าจอควบคุมสำหรับกลยุทธ์ Single LP + Single Hedge
- อัปเกรด: เพิ่มคอลัมน์ Inventory (LP ETH vs Perp ETH) ในตาราง Event Log และ CSV
- ปรับปรุงการแสดงผลให้แสดง Delta ของ Residual Risk ได้ชัดเจน
- ระบบ Cross-Margin Frequency แบบ Dynamic
- Fix: แก้ไข KeyError 'lp_eth' ด้วยระบบ Fallback ป้องกัน Engine เวอร์ชันเก่า
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

# Import stable modules จากโฟลเดอร์ src/
from src.oracle.oracle import OracleModule, OracleConfig
from src.lp.lp import LPModule, LPConfig
from src.perp.perp import PerpModule, PerpConfig
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule
from src.engine.backtest_engine import BacktestEngine

# --- Page Configuration ---
st.set_page_config(page_title="Quant Lab v1.5.1", layout="wide", page_icon="🚀")

# Custom CSS เพื่อความสวยงาม
st.markdown("""
    <style>
    .main { background-color: #f8fafc; }
    .stMetric { background-color: white; padding: 15px; border-radius: 15px; border: 1px solid #e2e8f0; }
    [data-testid="stSidebar"] { background-color: #f1f5f9; }
    </style>
    """, unsafe_allow_html=True)

# --- Sidebar Control Panel ---
with st.sidebar:
    st.title("⚙️ Strategy Control")
    
    with st.expander("📊 Market Simulator", expanded=True):
        start_price = st.number_input("Start Price ($)", value=2000.0)
        days = st.slider("Duration (Days)", 7, 360, 360)
        volatility = st.slider("Annual Volatility (%)", 10, 150, 70) / 100
        seed = st.number_input("Random Seed", value=42)

    with st.expander("🚜 LP Configuration", expanded=True):
        lp_cap = st.number_input("LP Capital ($)", value=10000.0)
        base_apr = st.slider("Base APR (%)", 1.0, 20.0, 4.0) / 100
        range_width = st.slider("Range Width (±%)", 1, 50, 10) / 100
        rebal_thresh = st.slider("Rebalance Threshold (%)", 5, 80, 30) / 100

    with st.expander("🛡️ Hedge & Capital Management", expanded=True):
        perp_cap = st.number_input("CEX Capital ($)", value=5000.0)
        leverage = st.number_input("Leverage (x)", value=5.0)
        hedge_mode = st.selectbox("Hedge Mode", options=['always', 'smart'], index=0)
        hedge_thresh = st.slider("Hedge Threshold (%)", 1, 30, 20) / 100
        interval = st.select_slider("Execution Lag (Min)", options=[1, 5, 15, 30, 60], value=5)
        
        # Dynamic Cross-Margin Frequency
        cross_rebal_freq = st.slider("Cross-Margin Freq (Days)", 1, 60, 15)

    st.info(f"💡 ระบบจะทำการโอนเงิน Cross-Margin อัตโนมัติทุก {cross_rebal_freq} วัน")

# --- Simulation Execution Logic ---
def run_quant_sim():
    # 1. Setup Configuration
    oracle_cfg = OracleConfig(start_price=start_price, days=days, annual_volatility=volatility, seed=seed, timeframe='5m')
    lp_cfg = LPConfig(
        initial_capital=lp_cap, 
        range_width=range_width, 
        rebalance_threshold=rebal_thresh, 
        base_apr=base_apr, 
        fee_mode='base_apr', 
        gas_fee=2.0, 
        slippage=0.001
    )
    strat_cfg = StrategyConfig(
        hedge_mode=hedge_mode, 
        use_safety_net=True, 
        safety_net_pct=0.1, 
        hedge_threshold=hedge_thresh, 
        ema_period=200
    )
    perp_cfg = PerpConfig(leverage=leverage, taker_fee=0.0005)
    
    # 2. Initialize Engine
    oracle = OracleModule()
    lp = LPModule(lp_cfg, start_price)
    perp = PerpModule(perp_cfg)
    strategy = StrategyModule(lp, perp)
    portfolio = PortfolioModule(lp_cap + perp_cap)
    portfolio.allocate_to_lp(lp_cap)
    
    engine = BacktestEngine(oracle, lp, perp, strategy, portfolio)
    data = oracle.generate_data(oracle_cfg)
    
    # 3. Run
    results = engine.run(
        data, strat_cfg, 
        funding_rate=0.0001, 
        cross_rebalance_config={'enabled': True, 'freq_days': cross_rebal_freq}, 
        execution_interval_min=interval
    )
    results['price'] = data['close'].values
    
    # 4. คำนวณ Residual Delta (Inventory Difference)
    # [FIX] ป้องกัน KeyError กรณี BacktestEngine ยังไม่ถูกอัปเดตเป็น v1.4.2
    if 'lp_eth' in results.columns and 'perp_size' in results.columns:
        results['residual_delta'] = results['lp_eth'] - results['perp_size']
    else:
        results['lp_eth'] = 0.0
        results['perp_size'] = 0.0
        results['residual_delta'] = 0.0
        
    if 'event' not in results.columns:
        results['event'] = ""
    
    return results, engine, lp

# --- Main UI ---
st.title("🚀 Quant Lab: Delta Hedge Dashboard")
st.caption(f"Status: Stable v1.5.1 | Inventory Tracking Active | Analysis: {datetime.now().strftime('%H:%M:%S')}")

if st.button("▶️ RUN BACKTEST SIMULATION", use_container_width=True):
    with st.spinner("ประมวลผลกลยุทธ์และจำลองสถานการณ์ตลาด..."):
        results, engine, lp_obj = run_quant_sim()
        
        # แจ้งเตือนถ้าระบบไม่พบข้อมูล Inventory (เนื่องจาก Engine เก่า)
        if 'lp_eth' not in results.columns or (results['lp_eth'] == 0).all():
            st.warning("⚠️ **คำแนะนำ:** ไม่พบข้อมูล Inventory กรุณาอัปเดตไฟล์ `src/engine/backtest_engine.py` เป็นเวอร์ชันล่าสุดเพื่อดูตาราง Event Log และ Residual Delta")
        
        # --- Metrics Section ---
        initial_cap = lp_cap + perp_cap
        final_net_equity = results['net_equity'].iloc[-1]
        total_withdrawn = results['total_withdrawn'].iloc[-1]
        total_wealth = final_net_equity + total_withdrawn
        
        roi_pct = ((total_wealth / initial_cap) - 1) * 100
        cagr = (pow(1 + (roi_pct/100), 365/days) - 1) * 100
        
        # Drawdown Calculation (from Total Wealth)
        wealth_series = results['net_equity'] + results['total_withdrawn']
        roll_max = wealth_series.cummax()
        dd = (wealth_series - roll_max) / roll_max
        max_dd = dd.min() * 100

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Wealth Created", f"${total_wealth:,.2f}", f"{roi_pct:+.2f}% ROI")
        m2.metric("Annualized CAGR", f"{cagr:.2f}%")
        m3.metric("Max Drawdown", f"{max_dd:.2f}%", delta_color="inverse")
        m4.metric("Effective APR", f"{lp_obj.config.base_apr * lp_obj.multiplier * 100:.1f}%")

        # --- Charts Section ---
        st.subheader("📈 Portfolio Analysis")
        
        # Chart 1: Equity Curve
        fig1 = make_subplots(specs=[[{"secondary_y": True}]])
        fig1.add_trace(go.Scatter(x=results.index, y=results['net_equity'], name="Live Equity", line=dict(color='#6366f1', width=2)), secondary_y=False)
        fig1.add_trace(go.Scatter(x=results.index, y=wealth_series, name="Total Wealth", line=dict(color='#10b981', width=2, dash='dash')), secondary_y=False)
        fig1.add_trace(go.Scatter(x=results.index, y=results['price'], name="ETH Price", line=dict(color='#94a3b8', width=1), opacity=0.4), secondary_y=True)
        fig1.update_layout(title="Portfolio Equity Progression", hovermode="x unified", height=450)
        st.plotly_chart(fig1, use_container_width=True)

        # Chart 2: Inventory Components
        st.subheader("🛡️ Hedge Engine Dynamics (LP vs CEX)")
        perp_equity_series = results['cex_wallet_balance'] + results['perp_pnl']
        
        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        fig2.add_trace(go.Scatter(x=results.index, y=results['lp_value'], name="LP Value (On-chain)", line=dict(color='#10b981', width=2)), secondary_y=False)
        fig2.add_trace(go.Scatter(x=results.index, y=perp_equity_series, name="Perp Margin (CEX)", line=dict(color='#3b82f6', width=2)), secondary_y=False)
        fig2.add_trace(go.Scatter(x=results.index, y=results['price'], name="ETH Price", line=dict(color='#94a3b8', width=1), opacity=0.25), secondary_y=True)
        fig2.update_layout(title="Component Equity Analysis (Mirroring)", hovermode="x unified", height=450)
        st.plotly_chart(fig2, use_container_width=True)

        # --- Activity Log Preview with Inventory ---
        st.subheader("📋 Simulation Detailed Logs (Inventory & Events)")
        events_only = results[results['event'] != ""].copy()
        
        if not events_only.empty:
            # เลือกคอลัมน์สำคัญมาแสดงผล
            display_df = events_only[[
                'timestamp', 'price', 'lp_eth', 'perp_size', 'residual_delta', 
                'lp_value', 'perp_pnl', 'net_equity', 'event'
            ]].copy()
            
            # ปรับชื่อคอลัมน์ให้อ่านง่าย
            display_df.columns = [
                'Time', 'ETH Price', 'LP ETH (Long)', 'Perp Size (Short)', 'Delta (Residual)', 
                'LP Value', 'Perp PnL', 'Net Equity', 'Event'
            ]
            
            st.write("🎯 **Key Events Highlight (Hedge Adjustments & Rebalances):**")
            st.dataframe(display_df.head(100), use_container_width=True)
        else:
            st.info("ไม่พบเหตุการณ์พิเศษ (Hedge/Rebalance) หรือยังไม่ได้อัปเดตไฟล์ Backtest Engine")

        # --- Stats Breakdown ---
        c_a, c_b = st.columns(2)
        with c_a:
            st.markdown("### 📊 Activity Stats")
            st.write(f"- LP Rebalances: {engine.lp.rebalance_count} times")
            st.write(f"- Hedge Trades: {engine.hedge_count} times")
            st.write(f"- Cross-Margin Sweeps: {engine.cross_rebalance_count} times")
            st.write(f"- Margin Call Rejects: `{len(engine.margin_call_events)}` 🚨")
        with c_b:
            st.markdown("### 💰 PnL Breakdown")
            st.write(f"- Gross LP Fees: `${engine.lp.accumulated_fees:,.2f}`")
            st.write(f"- Net Funding Rate: `${engine.perp.total_funding_pnl:,.2f}`")
            st.write(f"- Min Margin Buffer: `${results['cex_available_margin'].min():,.2f}`")

        # --- Download Button ---
        csv = results.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Full CSV (Including Inventory & Events)",
            data=csv,
            file_name=f"quant_full_result_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True
        )
else:
    st.info("👈 ปรับพารามิเตอร์ที่ Sidebar แล้วกดปุ่ม RUN เพื่อเริ่มการจำลองผล")