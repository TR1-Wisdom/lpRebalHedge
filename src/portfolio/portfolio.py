"""
src/portfolio/portfolio.py
โมดูล Portfolio (Central Ledger) สำหรับโปรเจกต์ Inventory LP Backtester

บริหารจัดการกระแสเงินสดและ Net Equity ของระบบทั้งหมด โดยใช้ Enum เพื่อความแม่นยำของบัญชี
อัปเดต: v1.0.3 เพิ่ม EXPENSE_SLIPPAGE แยกออกจาก PERP_FEE เพื่อรองรับ PnL Statement แบบเจาะลึก
"""

__version__ = "1.0.3"

from dataclasses import dataclass
from typing import Dict
from datetime import datetime
from enum import Enum


class TransactionType(Enum):
    """หมวดหมู่ธุรกรรมทางบัญชี เพื่อป้องกันความผิดพลาดจากการใช้ String"""
    REVENUE_LP_FEE = "REVENUE_LP_FEE"
    REVENUE_FUNDING = "REVENUE_FUNDING"
    EXPENSE_GAS = "EXPENSE_GAS"
    EXPENSE_PERP_FEE = "EXPENSE_PERP_FEE"  # [PM FIXED] เปลี่ยนชื่อให้ชัดเจน
    EXPENSE_SLIPPAGE = "EXPENSE_SLIPPAGE"  # [PM FIXED] เพิ่มหมวด Slippage แยกต่างหาก
    EXPENSE_FUNDING = "EXPENSE_FUNDING"
    DEPOSIT = "DEPOSIT"


@dataclass
class PortfolioState:
    """สถานะพอร์ตรายช่วงเวลา สำหรับส่งออกไปแสดงผล"""
    timestamp: datetime
    net_equity: float
    idle_cash: float
    lp_value: float
    perp_pnl: float
    total_fees_collected: float
    total_costs: float


class PortfolioModule:
    """ระบบบัญชีกลาง (Central Bank) - Audit Version v1.0.3"""

    def __init__(self, initial_capital: float) -> None:
        """เริ่มต้นพอร์ตด้วยเงินทุนตั้งต้น"""
        self.initial_capital: float = initial_capital
        self.idle_cash: float = initial_capital
        self.lp_allocated_cash: float = 0.0
        
        # สมุดบัญชีแยกประเภทโดยใช้ Enum เป็น Key
        self.ledgers: Dict[TransactionType, float] = {
            TransactionType.REVENUE_LP_FEE: 0.0,
            TransactionType.REVENUE_FUNDING: 0.0,
            TransactionType.EXPENSE_GAS: 0.0,
            TransactionType.EXPENSE_PERP_FEE: 0.0,
            TransactionType.EXPENSE_SLIPPAGE: 0.0,
            TransactionType.EXPENSE_FUNDING: 0.0
        }

    def allocate_to_lp(self, amount: float) -> float:
        """จัดสรรเงินสดไปใช้ใน LP (คืนค่าจำนวนที่จัดสรรจริง)"""
        allocation: float = min(amount, self.idle_cash)
        self.idle_cash -= allocation
        self.lp_allocated_cash += allocation
        return allocation

    def return_from_lp(self, amount: float) -> None:
        """รับเงินคืนจาก LP กลับเข้าบัญชีเงินสด (เช่น กรณีเปิด LP ไม่หมด)"""
        self.lp_allocated_cash -= amount
        self.idle_cash += amount

    def record_transaction(self, category: TransactionType, amount: float) -> None:
        """บันทึกธุรกรรมลงในสมุดบัญชี (เงินสดจะขยับตาม Amount ที่ส่งมา)"""
        if category in self.ledgers:
            self.ledgers[category] += amount
            self.idle_cash += amount

    def get_net_equity(self, current_lp_value: float, current_perp_pnl: float) -> float:
        """คำนวณมูลค่าพอร์ตสุทธิ: Cash + LP Mark-to-Market + Perp Unrealized PnL"""
        return self.idle_cash + current_lp_value + current_perp_pnl

    def get_state(self, timestamp: datetime, lp_val: float, perp_pnl: float) -> PortfolioState:
        """สรุปสถานะปัจจุบันของพอร์ตเป็น PortfolioState DTO"""
        return PortfolioState(
            timestamp=timestamp,
            net_equity=self.get_net_equity(lp_val, perp_pnl),
            idle_cash=self.idle_cash,
            lp_value=lp_val,
            perp_pnl=perp_pnl,
            total_fees_collected=self.ledgers[TransactionType.REVENUE_LP_FEE],
            total_costs=abs(self.ledgers[TransactionType.EXPENSE_GAS]) + 
                        abs(self.ledgers[TransactionType.EXPENSE_PERP_FEE]) +
                        abs(self.ledgers[TransactionType.EXPENSE_SLIPPAGE])
        )