"""
Unit Tests สำหรับโมดูล Backtest Engine (Audit Passed v1.1.0)

ประวัติการแก้ไข:
- v1.1.0 (Audit Fix): อัปเดตคอลัมน์และสมการใหม่ตามระบบบัญชี Isolate LP
"""

__version__ = "1.1.0"

import sys
import os
import pytest
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.oracle.oracle import OracleModule, OracleConfig
from src.lp.lp import LPModule, LPConfig
from src.perp.perp import PerpModule, PerpConfig
from src.strategy.strategy import StrategyModule, StrategyConfig
from src.portfolio.portfolio import PortfolioModule
from src.engine.backtest_engine import BacktestEngine

class TestBacktestEngine:
    @pytest.fixture
    def setup_components(self) -> tuple:
        capital = 10000.0
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
        engine, df_feed, strat_cfg = setup_components
        df_result = engine.run(df_feed, strat_cfg, funding_rate=0.0001)
        
        assert not df_result.empty
        assert len(df_result) == len(df_feed)
        
        # [KEY FIX] ตรวจสอบคอลัมน์ให้ตรงกับ PortfolioState ฉบับใหม่
        expected_columns = ['timestamp', 'net_equity', 'cex_wallet_balance', 'cex_available_margin', 
                            'lp_value', 'perp_pnl', 'total_fees_collected', 'total_costs', 'total_withdrawn']
        for col in expected_columns:
            assert col in df_result.columns, f"ไม่พบคอลัมน์ {col}"
            
    def test_no_money_leak(self, setup_components: tuple) -> None:
        engine, df_feed, strat_cfg = setup_components
        df_result = engine.run(df_feed, strat_cfg, funding_rate=0.0001)
        final_state = df_result.iloc[-1]
        
        # [KEY FIX] ใช้ตัวแปร cex_wallet_balance แทน idle_cash
        calculated_net_equity = final_state['lp_value'] + final_state['cex_wallet_balance'] + final_state['perp_pnl']
        
        assert abs(final_state['net_equity'] - calculated_net_equity) < 1e-5, "สมการ Net Equity ขัดแย้งกัน เงินหล่นหาย!"