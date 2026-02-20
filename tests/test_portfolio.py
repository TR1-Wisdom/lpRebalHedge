"""
Unit Tests สำหรับโมดูล Portfolio (Audit Passed v1.2.1)

ทำหน้าที่ทดสอบระบบบัญชีกลาง (Central Ledger), การจัดสรรเงินให้ LP 
และการคำนวณ Net Equity ที่รวมทุก Module เข้าด้วยกันตามมาตรฐานบัญชี Quant

ประวัติการแก้ไข:
- v1.2.1 (2026-02-20): แก้ไข Method names และ Parameter matching ให้ตรงกับ PortfolioModule v1.0.2
"""

__version__ = "1.2.1"

import sys
import os
import pytest
from datetime import datetime

# เพิ่ม Root Directory เข้าไปใน Path เพื่อให้เรียกใช้ src ได้
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.portfolio.portfolio import PortfolioModule, TransactionType, PortfolioState

class TestPortfolioModule:
    """ชุดการทดสอบสำหรับคลาส PortfolioModule"""

    @pytest.fixture
    def portfolio(self) -> PortfolioModule:
        """เตรียมเงินต้น 10,000 USD สำหรับทุกเคสทดสอบ"""
        return PortfolioModule(initial_capital=10000.0)

    def test_initial_state(self, portfolio: PortfolioModule) -> None:
        """ตรวจสอบสถานะเงินตั้งต้นและการดึง State"""
        now = datetime.now()
        # get_state ต้องการ (timestamp, lp_val, perp_pnl)
        state = portfolio.get_state(now, 0.0, 0.0)
        
        assert state.idle_cash == 10000.0
        assert state.net_equity == 10000.0
        assert state.timestamp == now

    def test_lp_allocation_and_reconciliation(self, portfolio: PortfolioModule) -> None:
        """ทดสอบการจัดสรรเงิน (Allocate) และการคืนเงิน (Return) ระหว่าง LP และ Cash"""
        # จัดสรรเงิน 8,000 USD ไป LP
        allocated = portfolio.allocate_to_lp(8000.0)
        assert allocated == 8000.0
        assert portfolio.idle_cash == 2000.0
        assert portfolio.lp_allocated_cash == 8000.0
        
        # กรณี LP ใช้เงินไม่หมด คืนกลับมา 500.50 USD
        portfolio.return_from_lp(500.50)
        assert portfolio.idle_cash == 2500.50
        assert portfolio.lp_allocated_cash == 7499.50

    def test_transaction_ledger_revenue_expense(self, portfolio: PortfolioModule) -> None:
        """ทดสอบการบันทึกรายได้และค่าใช้จ่ายผ่าน TransactionType Enum"""
        # จ่ายค่า Gas (ต้องเป็นค่าลบ)
        portfolio.record_transaction(TransactionType.EXPENSE_GAS, -10.0)
        # รับค่าธรรมเนียมจาก LP (ต้องเป็นค่าบวก)
        portfolio.record_transaction(TransactionType.REVENUE_LP_FEE, 150.0)
        
        # ยอดเงินควรเป็น: 10000 - 10 + 150 = 10140.0
        assert portfolio.idle_cash == 10140.0
        assert portfolio.ledgers[TransactionType.REVENUE_LP_FEE] == 150.0

    def test_net_equity_calculation(self, portfolio: PortfolioModule) -> None:
        """ทดสอบสูตร Net Equity ที่รวมมูลค่า Mark-to-Market จากทุกโมดูล"""
        portfolio.allocate_to_lp(5000.0)
        
        # สถานการณ์จำลอง:
        # Cash = 5000, LP Value = 5200, Perp Unrealized PnL = -100
        # Net Equity = 5000 + 5200 - 100 = 10100
        net_equity = portfolio.get_net_equity(current_lp_value=5200.0, current_perp_pnl=-100.0)
        assert net_equity == 10100.0