"""
Unit Tests สำหรับโมดูล Perp (Audit Passed v1.1.1)

ทำหน้าที่ทดสอบการจัดการสถานะ Futures และการคำนวณกำไรขาดทุน Mark-to-Market
ให้สอดคล้องกับโครงสร้าง PerpModule ล่าสุด

ประวัติการแก้ไข:
- v1.1.1 (2026-02-20): ปรับปรุง Interface การเรียกใช้ให้ตรงกับ PerpModule v1.0.1
"""

__version__ = "1.1.1"

import sys
import os
import pytest

# เพิ่ม Root Directory เข้าไปใน Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.perp.perp import PerpModule, PerpConfig, PositionSide

class TestPerpModule:
    """ชุดการทดสอบสำหรับคลาส PerpModule"""

    @pytest.fixture
    def perp(self) -> PerpModule:
        """เตรียม PerpModule พร้อม Config ที่รองรับ leverage"""
        config = PerpConfig(leverage=1.0, taker_fee=0.0005)
        return PerpModule(config)

    def test_initialization(self, perp: PerpModule) -> None:
        """ตรวจสอบสถานะเริ่มต้น"""
        assert perp.total_trading_fees == 0.0
        assert len(perp.positions) == 0

    def test_open_short_and_fee(self, perp: PerpModule) -> None:
        """ทดสอบการเปิด Short และการคำนวณค่าธรรมเนียม Taker Fee"""
        # ตั้งราคาตลาดก่อนเปิด
        perp.update_market_price(2000.0)
        
        # เปิด Short 2 ETH (Notional = 4000 USD)
        # Fee = 4000 * 0.0005 = 2.0 USD
        fee = perp.open_position(PositionSide.SHORT, size_in_token=2.0)
        
        assert fee == 2.0
        assert perp.total_trading_fees == 2.0
        assert perp.get_short_position_size() == 2.0
        assert perp.positions[PositionSide.SHORT].entry_price == 2000.0

    def test_unrealized_pnl_mtm(self, perp: PerpModule) -> None:
        """ทดสอบการคำนวณ Unrealized PnL เมื่อราคาวิ่งตามและสวนทาง"""
        perp.update_market_price(2000.0)
        perp.open_position(PositionSide.LONG, size_in_token=1.0)
        
        # ราคาวิ่งไป 2100 -> Long ต้องกำไร 100
        perp.update_market_price(2100.0)
        assert perp.get_total_unrealized_pnl() == 100.0
        
        # ราคาทุบไป 1950 -> Long ต้องขาดทุน -50
        perp.update_market_price(1950.0)
        assert perp.get_total_unrealized_pnl() == -50.0

    def test_apply_funding_payment(self, perp: PerpModule) -> None:
        """ทดสอบระบบ Funding Rate (Short ได้เงินเมื่อ Rate บวก)"""
        perp.update_market_price(2000.0)
        perp.open_position(PositionSide.SHORT, size_in_token=1.0)
        
        # Funding Rate 0.01% (0.0001) ของ 2000 USD = 0.2 USD
        # ในระบบ PerpModule v1.0.1: payment = notional * rate
        # net_funding_pnl += payment if side == SHORT
        funding_received = perp.apply_funding(0.0001)
        
        assert funding_received == 0.2
        assert perp.total_funding_pnl == 0.2

    def test_close_and_accrue_fee(self, perp: PerpModule) -> None:
        """ทดสอบการปิดสถานะและสะสมค่าธรรมเนียมตอนปิด"""
        perp.update_market_price(2000.0)
        perp.open_position(PositionSide.SHORT, size_in_token=1.0)
        
        # ปิดที่ราคาเดิม 2000 -> เสีย Fee อีก 1.0 USD
        close_fee = perp.close_position(PositionSide.SHORT)
        assert close_fee == 1.0
        assert perp.total_trading_fees == 2.0 # 1.0 ตอนเปิด + 1.0 ตอนปิด
        assert len(perp.positions) == 0