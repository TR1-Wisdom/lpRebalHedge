"""
src/perp/perp.py
โมดูล Perp (Perpetual Futures)

ประวัติการแก้ไข:
- v1.0.3 (Audit Fix): เพิ่มการตรวจสอบ Available Margin ป้องกันการเปิดโพสิชันเกินตัว (Reject Order)
"""

__version__ = "1.0.3"

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class Position:
    side: PositionSide
    size: float
    entry_price: float
    unrealized_pnl: float = 0.0
    margin_used: float = 0.0


@dataclass
class PerpConfig:
    leverage: float = 1.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005


class PerpModule:
    def __init__(self, config: PerpConfig) -> None:
        self.config = config
        self.positions: Dict[PositionSide, Position] = {}
        self.current_market_price: float = 0.0
        self.total_trading_fees: float = 0.0
        self.total_funding_pnl: float = 0.0

    def update_market_price(self, new_price: float) -> None:
        self.current_market_price = new_price
        for side, pos in self.positions.items():
            if side == PositionSide.LONG:
                pos.unrealized_pnl = pos.size * (new_price - pos.entry_price)
            else:
                pos.unrealized_pnl = pos.size * (pos.entry_price - new_price)

    def open_position(self, side: PositionSide, size_in_token: float, idle_cash: float) -> float:
        """
        [Audit Fix 4] รับค่า idle_cash เข้ามาเพื่อเช็คว่ามี Margin เหลือพอให้เปิดไหม
        หากไม่พอ จะ Raise ValueError เพื่อ Reject Order จำลองการโดนล้างพอร์ตหรือเงินหมด
        """
        notional_value: float = size_in_token * self.current_market_price
        trading_fee: float = notional_value * self.config.taker_fee
        added_margin: float = notional_value / self.config.leverage

        # คำนวณ Available Margin จริงใน CEX (Cash + PnL - Locked Margin)
        total_unrealized_pnl = self.get_total_unrealized_pnl()
        total_margin_used = sum(p.margin_used for p in self.positions.values())
        available_margin = idle_cash + total_unrealized_pnl - total_margin_used

        if available_margin < (added_margin + trading_fee):
            raise ValueError("MARGIN_CALL")

        if side in self.positions:
            pos = self.positions[side]
            total_size = pos.size + size_in_token
            new_entry = ((pos.size * pos.entry_price) + (size_in_token * self.current_market_price)) / total_size
            
            pos.size = total_size
            pos.entry_price = new_entry
            pos.margin_used += added_margin
        else:
            self.positions[side] = Position(
                side=side,
                size=size_in_token,
                entry_price=self.current_market_price,
                margin_used=added_margin
            )
        
        self.total_trading_fees += trading_fee
        self.update_market_price(self.current_market_price)
        return trading_fee

    def close_partial_position(self, side: PositionSide, size_to_close: float) -> tuple[float, float]:
        if side not in self.positions:
            return 0.0, 0.0
            
        pos = self.positions[side]
        
        if size_to_close >= pos.size:
            realized_pnl = pos.unrealized_pnl
            fee = self.close_position(side)
            return realized_pnl, fee
            
        ratio = size_to_close / pos.size
        realized_pnl = pos.unrealized_pnl * ratio
        
        notional_closed = size_to_close * self.current_market_price
        fee = notional_closed * self.config.taker_fee
        
        pos.size -= size_to_close
        pos.margin_used -= (pos.margin_used * ratio)
        
        self.total_trading_fees += fee
        self.update_market_price(self.current_market_price) 
        
        return realized_pnl, fee

    def close_position(self, side: PositionSide) -> float:
        if side not in self.positions:
            return 0.0
            
        pos = self.positions[side]
        trading_fee: float = (pos.size * self.current_market_price) * self.config.taker_fee
        
        del self.positions[side]
        self.total_trading_fees += trading_fee
        return trading_fee

    def apply_funding(self, funding_rate_pct: float) -> float:
        net_funding_pnl: float = 0.0
        for side, pos in self.positions.items():
            notional: float = pos.size * self.current_market_price
            payment: float = notional * funding_rate_pct
            net_funding_pnl += payment if side == PositionSide.SHORT else -payment
                
        self.total_funding_pnl += net_funding_pnl
        return net_funding_pnl

    def get_short_position_size(self) -> float:
        return self.positions[PositionSide.SHORT].size if PositionSide.SHORT in self.positions else 0.0

    def get_total_unrealized_pnl(self) -> float:
        return sum(pos.unrealized_pnl for pos in self.positions.values())