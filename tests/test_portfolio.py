"""
Unit Tests สำหรับโมดูล Portfolio (Audit Passed v1.3.0)

ประวัติการแก้ไข:
- v1.3.0 (Audit Fix): อัปเดตทดสอบการแยกบัญชี (Isolate LP) 
  และการใช้งานตัวแปร cex_wallet_balance แทน idle_cash
"""

__version__ = "1.3.0"

import sys
import os
import pytest
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.portfolio.portfolio import PortfolioModule, TransactionType, PortfolioState

class TestPortfolioModule:
    @pytest.fixture
    def portfolio(self) -> PortfolioModule:
        return PortfolioModule(initial_capital=10000.0)

    def test_initial_state(self, portfolio: PortfolioModule) -> None:
        now = datetime.now()
        state = portfolio.get_state(now, 0.0, 0.0, 0.0)
        
        assert state.cex_wallet_balance == 10000.0
        assert state.net_equity == 10000.0
        assert state.cex_available_margin == 10000.0
        assert state.timestamp == now

    def test_lp_allocation_and_reconciliation(self, portfolio: PortfolioModule) -> None:
        allocated = portfolio.allocate_to_lp(8000.0)
        assert allocated == 8000.0
        assert portfolio.cex_wallet_balance == 2000.0
        assert portfolio.lp_allocated_cash == 8000.0
        
        portfolio.return_from_lp(500.50)
        assert portfolio.cex_wallet_balance == 2500.50
        assert portfolio.lp_allocated_cash == 7499.50

    def test_transaction_ledger_revenue_expense(self, portfolio: PortfolioModule) -> None:
        """ทดสอบว่าธุรกรรม LP จะไม่กวนเงิน CEX"""
        portfolio.record_transaction(TransactionType.EXPENSE_GAS, -10.0)
        portfolio.record_transaction(TransactionType.REVENUE_LP_FEE, 150.0)
        
        # เงิน CEX ต้องเหลือ 10,000 เท่าเดิม เพราะข้างบนคือเรื่องของ LP
        assert portfolio.cex_wallet_balance == 10000.0
        assert portfolio.ledgers[TransactionType.REVENUE_LP_FEE] == 150.0

        # ถ้าเป็นธุรกรรม CEX (เช่น DEPOSIT PnL) ถึงจะบวกเงินในกระเป๋า
        portfolio.record_transaction(TransactionType.DEPOSIT, 50.0)
        assert portfolio.cex_wallet_balance == 10050.0

    def test_net_equity_calculation(self, portfolio: PortfolioModule) -> None:
        portfolio.allocate_to_lp(5000.0)
        # CEX = 5000, LP Value = 5200, Perp PnL = -100
        # Net Equity = 5000 + 5200 - 100 = 10100
        net_equity = portfolio.get_net_equity(current_lp_value=5200.0, current_perp_pnl=-100.0)
        assert net_equity == 10100.0