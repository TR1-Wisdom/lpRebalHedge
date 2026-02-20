"""
Unit Tests สำหรับโมดูล Strategy (The Brain)

ทดสอบลอจิกการคำนวณ Indicator ของ Pandas (ตามมาตรฐาน Freqtrade)
รวมถึงทดสอบหัวใจสำคัญ 3 ข้อ: Signal Flip, Safety Net และ Delta Threshold Drift

ประวัติการแก้ไข (Version Control):
- v1.0.2 (2026-02-20): แก้ไข path การ import โมดูล strategy ให้ตรงกับโครงสร้างโฟลเดอร์ย่อย (src.strategy.strategy)
- v1.0.1 (2026-02-20): เพิ่ม sys.path.insert เพื่อแก้ปัญหา ModuleNotFoundError สำหรับ 'src'
- v1.0.0 (2026-02-20): สร้าง Unit Test ควบคุมพฤติกรรมกลยุทธ์ 100%
"""

__version__ = "1.0.2"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"
__date__ = "2026-02-20"

import sys
import os

# แก้ปัญหา ModuleNotFoundError: No module named 'src' ตอนรัน pytest
# โดยการเพิ่ม Root Directory (ย้อนกลับ 1 ขั้นจากไฟล์เทส) เข้าไปใน Path ของระบบ
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

# แก้ไข Path การ Import ให้ชี้ทะลุเข้าไปถึงไฟล์ strategy.py ในโฟลเดอร์ย่อย
from src.strategy.strategy import StrategyModule, StrategyConfig, ILPModule, IPerpModule, OrderEvent


class DummyLPModule:
    """Mock ออบเจ็กต์สำหรับแทนที่ LP Module จริงในระหว่างการรันเทส"""
    def __init__(self, eth_inventory: float):
        self._eth_inventory = eth_inventory
        
    def get_eth_inventory(self) -> float:
        return self._eth_inventory


class DummyPerpModule:
    """Mock ออบเจ็กต์สำหรับแทนที่ Perp Module จริงในระหว่างการรันเทส"""
    def __init__(self, short_size: float):
        self._short_size = short_size
        
    def get_short_position_size(self) -> float:
        return self._short_size


class TestStrategyModule:
    """ชุดทดสอบการทำงานของคลาส StrategyModule"""

    @pytest.fixture
    def config(self) -> StrategyConfig:
        return StrategyConfig(ema_period=10, safety_net_pct=0.02, hedge_threshold=0.05)

    @pytest.fixture
    def sample_df(self) -> pd.DataFrame:
        """สร้างข้อมูลจำลองขนาด 20 แถว ขาขึ้น"""
        dates = pd.date_range(start="2024-01-01", periods=20, freq="15min")
        closes = np.linspace(2000, 2100, 20)  # ค่อยๆ ขึ้น
        return pd.DataFrame({'date': dates, 'close': closes})

    def test_populate_indicators(self, config: StrategyConfig, sample_df: pd.DataFrame) -> None:
        """ทดสอบการสร้างคอลัมน์ Indicators (EMA และ %Change)"""
        strategy = StrategyModule(DummyLPModule(1.0), DummyPerpModule(0.0))
        df_out = strategy.populate_indicators(sample_df, config)
        
        assert 'ema' in df_out.columns
        assert 'pct_change' in df_out.columns
        assert not df_out['ema'].isna().all()
        assert df_out['pct_change'].iloc[0] == 0.0  # ค่าแรกต้องเติมเป็น 0

    def test_populate_signals(self, config: StrategyConfig, sample_df: pd.DataFrame) -> None:
        """ทดสอบการสร้างสัญญาณ ซื้อ(1) หรือ ขาย(-1)"""
        strategy = StrategyModule(DummyLPModule(1.0), DummyPerpModule(0.0))
        df_ind = strategy.populate_indicators(sample_df, config)
        df_sig = strategy.populate_signals(df_ind)
        
        assert 'signal' in df_sig.columns
        assert df_sig['signal'].isin([1, -1]).all()

    def test_generate_orders_flip(self, config: StrategyConfig) -> None:
        """ทดสอบการออกคำสั่งแบบกลับด้าน (Signal Flip จาก 1 เป็น -1)"""
        lp = DummyLPModule(10.0)    # มี ETH จริง 10 เหรียญ
        perp = DummyPerpModule(0.0) # ตอนนี้ไม่ได้ Short
        strategy = StrategyModule(lp, perp)
        
        # สมมติสถานการณ์ AI ส่งสัญญาณมองลง (-1)
        current_tick = pd.Series({'date': datetime.now(), 'signal': -1, 'pct_change': -0.01})
        orders = strategy.generate_orders(current_tick, config)
        
        assert len(orders) == 1
        assert orders[0].action == 'HEDGE_ON'
        assert orders[0].target_size == 10.0  # ต้องสั่ง Hedge เต็ม 10.0
        assert orders[0].reason == 'Signal Flip'

    def test_generate_orders_safety_net(self, config: StrategyConfig) -> None:
        """ทดสอบการทำงานของ Safety Net เมื่อสัญญาณขึ้น(1) แต่โดนทุบหนักเกินเกณฑ์"""
        lp = DummyLPModule(10.0)
        perp = DummyPerpModule(0.0)
        strategy = StrategyModule(lp, perp)
        
        # AI มองขึ้น (1) แต่ราคาแท่งนี้รูดลง -3% ซึ่งเกิน safety_net_pct (-2%)
        current_tick = pd.Series({'date': datetime.now(), 'signal': 1, 'pct_change': -0.03})
        orders = strategy.generate_orders(current_tick, config)
        
        assert len(orders) == 1
        assert orders[0].action == 'HEDGE_ON'
        assert orders[0].target_size == 10.0
        assert orders[0].reason == 'Safety Net Triggered'

    def test_generate_orders_drift_threshold(self, config: StrategyConfig) -> None:
        """ทดสอบระบบป้องกันการปรับ Hedge พร่ำเพรื่อ (Threshold)"""
        # ปัจจุบันมีของจริง 10.0 ETH แต่ Short ไว้ 9.8 ETH
        lp = DummyLPModule(10.0)
        perp = DummyPerpModule(9.8)
        strategy = StrategyModule(lp, perp)
        
        # กรณีที่ 1: ขาดไป 0.2 ETH (2%) ไม่เกินเกณฑ์ Threshold 5% -> ไม่ต้องทำอะไร
        tick1 = pd.Series({'date': datetime.now(), 'signal': -1, 'pct_change': 0.00})
        orders1 = strategy.generate_orders(tick1, config)
        assert len(orders1) == 0
        
        # กรณีที่ 2: สมมติว่าของจริงพองขึ้นเป็น 11.0 ETH แต่ Short ค้างไว้แค่ 9.8 (ห่าง > 5%) -> ต้องออกออเดอร์ปรับสมดุล
        lp_drift = DummyLPModule(11.0)
        strategy_drift = StrategyModule(lp_drift, perp)
        orders2 = strategy_drift.generate_orders(tick1, config)
        
        assert len(orders2) == 1
        assert orders2[0].action == 'ADJUST_HEDGE'
        assert orders2[0].target_size == 11.0
        assert orders2[0].reason == 'Threshold Drift'