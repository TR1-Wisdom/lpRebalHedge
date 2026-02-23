"""
src/portfolio/portfolio.py
โมดูล Portfolio (Central Ledger)

อัปเดต: v1.0.5 รองรับระบบ WITHDRAWAL สำหรับ Passive Income
"""

__version__ = "1.0.5"

from dataclasses import dataclass
from typing import Dict
from datetime import datetime
from enum import Enum


class TransactionType(Enum):
    REVENUE_LP_FEE = "REVENUE_LP_FEE"
    REVENUE_FUNDING = "REVENUE_FUNDING"
    EXPENSE_GAS = "EXPENSE_GAS"
    EXPENSE_PERP_FEE = "EXPENSE_PERP_FEE"
    EXPENSE_SLIPPAGE = "EXPENSE_SLIPPAGE"
    EXPENSE_FUNDING = "EXPENSE_FUNDING"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL" # [NEW] สำหรับถอน Passive Income ออกจากระบบ


@dataclass
class PortfolioState:
    timestamp: datetime
    net_equity: float
    idle_cash: float
    lp_value: float
    perp_pnl: float
    total_fees_collected: float
    total_costs: float
    total_withdrawn: float # [NEW] ติดตามยอดเงินที่ถอนออกไปแล้ว


class PortfolioModule:
    def __init__(self, initial_capital: float) -> None:
        self.initial_capital: float = initial_capital
        self.idle_cash: float = initial_capital  
        self.lp_allocated_cash: float = 0.0
        self.total_withdrawn_amount: float = 0.0 # สะสมยอดถอนจริง
        
        self.ledgers: Dict[TransactionType, float] = {
            TransactionType.REVENUE_LP_FEE: 0.0,
            TransactionType.REVENUE_FUNDING: 0.0,
            TransactionType.EXPENSE_GAS: 0.0,
            TransactionType.EXPENSE_PERP_FEE: 0.0,
            TransactionType.EXPENSE_SLIPPAGE: 0.0,
            TransactionType.EXPENSE_FUNDING: 0.0,
            TransactionType.DEPOSIT: 0.0,
            TransactionType.WITHDRAWAL: 0.0
        }

    def allocate_to_lp(self, amount: float) -> float:
        allocation: float = min(amount, self.idle_cash)
        self.idle_cash -= allocation
        self.lp_allocated_cash += allocation
        return allocation

    def record_transaction(self, category: TransactionType, amount: float) -> None:
        if category in self.ledgers:
            self.ledgers[category] += amount
            
            cex_transactions = [
                TransactionType.EXPENSE_PERP_FEE,
                TransactionType.REVENUE_FUNDING,
                TransactionType.EXPENSE_FUNDING,
                TransactionType.DEPOSIT
            ]
            if category in cex_transactions:
                self.idle_cash += amount
            
            # บันทึกยอดถอนสะสม (ยอดถอนจะถูกส่งมาเป็นค่าลบ)
            if category == TransactionType.WITHDRAWAL:
                self.total_withdrawn_amount += abs(amount)

    def get_net_equity(self, current_lp_value: float, current_perp_pnl: float) -> float:
        return self.idle_cash + current_lp_value + current_perp_pnl

    def get_state(self, timestamp: datetime, lp_val: float, perp_pnl: float) -> PortfolioState:
        total_costs = (
            abs(self.ledgers[TransactionType.EXPENSE_GAS]) + 
            abs(self.ledgers[TransactionType.EXPENSE_PERP_FEE]) +
            abs(self.ledgers[TransactionType.EXPENSE_SLIPPAGE])
        )
        
        return PortfolioState(
            timestamp=timestamp,
            net_equity=self.get_net_equity(lp_val, perp_pnl),
            idle_cash=self.idle_cash,
            lp_value=lp_val,
            perp_pnl=perp_pnl,
            total_fees_collected=self.ledgers[TransactionType.REVENUE_LP_FEE],
            total_costs=total_costs,
            total_withdrawn=self.total_withdrawn_amount
        )