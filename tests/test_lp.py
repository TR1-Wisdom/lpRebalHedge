"""
Unit Tests สำหรับโมดูล LP (Liquidity Pool)

ทดสอบลอจิกการเคลื่อนที่ของ Skew (Imbalance), การคำนวณ Impermanent Loss แบบกลายๆ ผ่าน Value PnL,
รวมถึงการทดสอบกลไกเก็บค่าธรรมเนียมและการทำ Active Swap Rebalance

ประวัติการแก้ไข (Version Control):
- v1.0.1 (2026-02-20): เพิ่ม sys.path.insert เพื่อแก้ปัญหา ModuleNotFoundError สำหรับ 'src'
- v1.0.0 (2026-02-20): สร้าง Unit Test ควบคุมพฤติกรรมของสมการคณิตศาสตร์ LP
"""

__version__ = "1.0.1"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"
__date__ = "2026-02-20"

import sys
import os

# แก้ปัญหา ModuleNotFoundError: No module named 'src' ตอนรัน pytest
# โดยการเพิ่ม Root Directory (ย้อนกลับ 1 ขั้นจากไฟล์เทส) เข้าไปใน Path ของระบบ
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from src.lp.lp import LPConfig, LPModule, RebalanceResult


class TestLPModule:
    """ชุดการทดสอบการทำงานของคลาส LPModule"""

    @pytest.fixture
    def default_config(self) -> LPConfig:
        """เตรียม Config มาตรฐานสำหรับการทดสอบ"""
        return LPConfig(
            initial_capital=10000.0,
            range_width=0.10,
            rebalance_threshold=0.15,
            fee_tier=0.0005,
            daily_volume=1_000_000.0,
            pool_tvl=10_000_000.0,
            gas_fee=2.0,
            slippage=0.001
        )

    @pytest.fixture
    def lp_module(self, default_config: LPConfig) -> LPModule:
        """เตรียม LPModule เริ่มต้นที่ราคา $2000"""
        return LPModule(config=default_config, start_price=2000.0)

    def test_initialization(self, lp_module: LPModule) -> None:
        """ทดสอบการตั้งค่าเริ่มต้น และการคำนวณ Range/Multiplier"""
        assert lp_module.position_value == 10000.0
        assert lp_module.skew == 0.5
        assert lp_module.range_lower == 1800.0  # 2000 - 10%
        assert lp_module.range_upper == 2200.0  # 2000 + 10%
        assert lp_module.multiplier > 1.0  # Multiplier ต้องสูงกว่า 1.0 เสมอใน V3

    def test_update_price_in_range(self, lp_module: LPModule) -> None:
        """ทดสอบราคาวิ่งขึ้นใน Range (มูลค่าพอร์ตต้องเพิ่ม แต่ Skew ต้องลดลงเพราะทยอยขาย ETH)"""
        # ราคาวิ่งจาก 2000 เป็น 2100 (+5%)
        lp_module.update_price(2100.0)
        
        # สินทรัพย์เดิม 5k (ETH) + 5k (USDC)
        # ETH ขึ้น 5% มูลค่าควรเป็น 5250 + 5000 = 10250
        assert lp_module.position_value == 10250.0
        
        # เช็ค Skew : Range กว้าง 400 (1800 -> 2200)
        # ราคา 2100 ห่างจากขอบล่าง 300 -> Skew = 1 - (300/400) = 0.25 (เหลือ ETH แค่ 25%)
        assert lp_module.skew == 0.25

    def test_update_price_out_of_range_down(self, lp_module: LPModule) -> None:
        """ทดสอบราคาทุบหลุด Range (ต้องกลายเป็น ETH 100% ทันที)"""
        # ราคาวิ่งไป 1500 (หลุดขอบล่างที่ 1800)
        lp_module.update_price(1500.0)
        
        assert lp_module.skew == 1.0  # ติดดอย ETH เต็มแม็กซ์
        assert lp_module.position_value < 10000.0  # มูลค่ารวมต้องลดลง

    def test_update_price_out_of_range_up(self, lp_module: LPModule) -> None:
        """ทดสอบราคาพุ่งทะลุ Range (ต้องกลายเป็น USDC 100% ทันที)"""
        # ราคาวิ่งไป 2500 (ทะลุขอบบนที่ 2200)
        lp_module.update_price(2500.0)
        
        assert lp_module.skew == 0.0  # ขายหมูเรียบร้อย เหลือแต่ Stablecoin
        assert lp_module.position_value > 10000.0  # มูลค่ารวมพอร์ตเพิ่มขึ้น

    def test_collect_fee_in_range(self, lp_module: LPModule) -> None:
        """ทดสอบการเก็บ Fee เมื่อราคาอยู่ในช่วง"""
        fee = lp_module.collect_fee()
        assert fee > 0.0
        assert lp_module.accumulated_fees == fee
        assert lp_module.position_value == 10000.0 + fee

    def test_collect_fee_out_of_range(self, lp_module: LPModule) -> None:
        """ทดสอบว่าหลุด Range จะต้องไม่ได้รับ Fee"""
        lp_module.update_price(2500.0)  # ทะลุบน
        fee = lp_module.collect_fee()
        assert fee == 0.0

    def test_check_and_rebalance_trigger(self, lp_module: LPModule) -> None:
        """ทดสอบกลไก Rebalance เมื่อ Skew เสียสมดุลเกินเป้า"""
        # ดันราคาไปที่ 2100 ให้ Skew ไปที่ 0.25 (Drift = 0.25 ซึ่งเกิน 0.15 threshold)
        lp_module.update_price(2100.0)
        assert lp_module.skew == 0.25
        
        old_value = lp_module.position_value # 10250.0
        
        result: RebalanceResult = lp_module.check_and_rebalance()
        
        assert result.is_rebalanced is True
        assert result.gas_cost == 2.0
        # Drift คือ 0.25 (0.5 - 0.25) -> swap volume = 10250 * 0.25 = 2562.5
        assert result.swap_volume_usd == 2562.5
        assert result.slippage_cost == 2562.5 * 0.001
        
        # ตรวจสอบการหักเงิน
        expected_new_value = old_value - result.gas_cost - result.slippage_cost
        assert lp_module.position_value == expected_new_value
        
        # ตรวจสอบว่า Range และ Skew ถูกจัดใหม่
        assert lp_module.skew == 0.5
        assert lp_module.range_lower == 2100.0 * 0.90
        assert lp_module.range_upper == 2100.0 * 1.10
        assert lp_module.rebalance_count == 1

    def test_check_and_rebalance_no_trigger(self, lp_module: LPModule) -> None:
        """ทดสอบกรณีที่ราคาวิ่งแต่ยังไม่ถึง Threshold ที่ต้อง Rebalance"""
        # ราคาวิ่งนิดหน่อยไปที่ 2050 (Skew น่าจะอยู่แถวๆ 0.375 -> Drift 0.125 ไม่เกิน 0.15)
        lp_module.update_price(2050.0)
        
        result: RebalanceResult = lp_module.check_and_rebalance()
        assert result.is_rebalanced is False
        assert lp_module.rebalance_count == 0

    def test_get_eth_inventory(self, lp_module: LPModule) -> None:
        """ทดสอบฟังก์ชัน Interface ที่ดึงจำนวน ETH ออกมา (เพื่อประสานกับ Strategy)"""
        # ทุน 10,000 / 50% = $5,000 ETH / ราคา 2000 = 2.5 ETH
        eth_amt = lp_module.get_eth_inventory()
        assert eth_amt == 2.5
        
        # ถ้าราคาพุ่งจนขาย ETH ทิ้งหมด
        lp_module.update_price(2500.0)
        assert lp_module.get_eth_inventory() == 0.0