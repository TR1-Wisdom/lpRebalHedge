"""
Unit Tests สำหรับโมดูล Backtest Engine (Audit Passed)

ทดสอบการทำงานร้อยเรียงกันของทุกโมดูล (Integration Test)
เพื่อให้มั่นใจว่าลูปเวลา (Event Loop) สามารถวิ่งจากต้นจนจบโดยไม่มี Error
และบันทึกบัญชีลง Portfolio ได้อย่างถูกต้อง

ประวัติการแก้ไข:
- v1.0.0 (2026-02-20): สร้างชุดทดสอบสำหรับ Engine v1.0.2
"""

__version__ = "1.0.0"

import sys
import os
import pytest
import pandas as pd

# เพิ่ม Root Directory เข้าไปใน Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.oracle.oracle import OracleModule, OracleConfig
from src.lp.lp import LPModule, LPConfig
from src.perp.perp import PerpModule, PerpConfig
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule
from src.engine.backtest_engine import BacktestEngine

class TestBacktestEngine:
    """ชุดการทดสอบสำหรับ Integration Test ของ Backtest Engine"""

    @pytest.fixture
    def setup_components(self) -> tuple:
        """จำลองการตั้งค่าพารามิเตอร์และสร้าง Module ทั้งหมด"""
        capital = 10000.0
        
        # ใช้ Oracle จำลองเวลาแค่ 2 วัน (48 ชั่วโมง) เพื่อให้รันเทสได้รวดเร็ว
        oracle_cfg = OracleConfig(start_price=2000.0, days=2, annual_volatility=0.5, seed=42, timeframe='1h')
        lp_cfg = LPConfig(initial_capital=capital, range_width=0.10, rebalance_threshold=0.15)
        perp_cfg = PerpConfig(leverage=1.0)
        strat_cfg = StrategyConfig(ema_period=5, safety_net_pct=0.02, hedge_threshold=0.05)
        
        oracle = OracleModule()
        lp = LPModule(lp_cfg, oracle_cfg.start_price)
        perp = PerpModule(perp_cfg)
        strategy = StrategyModule(lp, perp)
        portfolio = PortfolioModule(capital)
        
        engine = BacktestEngine(oracle, lp, perp, strategy, portfolio)
        df_feed = oracle.generate_data(oracle_cfg)
        
        return engine, df_feed, strat_cfg

    def test_engine_run_completes(self, setup_components: tuple) -> None:
        """ทดสอบว่า Engine สามารถวิ่งตั้งแต่แท่งแรกยันแท่งสุดท้ายได้โดยไม่ Crash"""
        engine, df_feed, strat_cfg = setup_components
        
        # สั่งรันลูป
        df_result = engine.run(df_feed, strat_cfg, funding_rate=0.0001)
        
        # ตรวจสอบผลลัพธ์
        assert not df_result.empty, "ผลลัพธ์ที่คืนค่ามาต้องไม่เป็น DataFrame ว่างเปล่า"
        assert len(df_result) == len(df_feed), "จำนวนแถวของผลลัพธ์ต้องเท่ากับจำนวนแถวของข้อมูลที่ป้อนเข้าไป"
        
        # ตรวจสอบคอลัมน์สำคัญที่ต้องมีใน PortfolioState
        expected_columns = ['timestamp', 'net_equity', 'idle_cash', 'lp_value', 'perp_pnl', 'total_fees_collected', 'total_costs']
        for col in expected_columns:
            assert col in df_result.columns, f"ไม่พบคอลัมน์ {col} ในผลลัพธ์"
            
    def test_no_money_leak(self, setup_components: tuple) -> None:
        """ทดสอบว่าเงินในระบบไม่มีการงอกหรือหายไปเอง (Accounting Check)"""
        engine, df_feed, strat_cfg = setup_components
        
        df_result = engine.run(df_feed, strat_cfg, funding_rate=0.0001)
        final_state = df_result.iloc[-1]
        
        # Net Equity ต้องสะท้อนมาจาก LP Value + Idle Cash + Perp PnL จริงๆ 
        calculated_net_equity = final_state['lp_value'] + final_state['idle_cash'] + final_state['perp_pnl']
        
        # ยอมให้เกิด floating point precision error ได้เล็กน้อย
        assert abs(final_state['net_equity'] - calculated_net_equity) < 1e-5, "สมการ Net Equity ขัดแย้งกัน เงินหล่นหาย!"