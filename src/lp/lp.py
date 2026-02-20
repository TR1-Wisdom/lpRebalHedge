"""
โมดูล LP (Liquidity Pool) สำหรับโปรเจกต์ Inventory LP Backtester

ทำหน้าที่จำลองการทำงานของ Concentrated Liquidity (เช่น Uniswap V3 / Steer Protocol)
โดยคำนวณมูลค่าของพอร์ต (Impermanent Loss แบบ Macro-level), การเบ้ของสัดส่วนเหรียญ (Inventory Skew),
การเก็บค่าธรรมเนียม, และการปรับสมดุลพอร์ตแบบอัตโนมัติ (Active Swap Rebalancing)

ประวัติการแก้ไข (Version Control):
- v1.0.1 (2026-02-20): เพิ่มระบบ fee_mode='base_apr' เพื่อให้สอดคล้องกับ UI Simulator
- v1.0.0 (2026-02-20): สร้าง LP Module รองรับสมการ Concentrated Liquidity Multiplier และ Skew
"""

__version__ = "1.0.1"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"
__date__ = "2026-02-20"

import math
from dataclasses import dataclass


@dataclass
class LPConfig:
    """
    Data Transfer Object (DTO) สำหรับเก็บพารามิเตอร์ของ Liquidity Pool

    Attributes:
        initial_capital (float): เงินทุนเริ่มต้นเป็นดอลลาร์ (USD)
        range_width (float): ความกว้างของช่วงราคาจากจุดกึ่งกลาง (เช่น 0.10 คือ ±10%)
        fee_tier (float): อัตราค่าธรรมเนียมของ Pool (เช่น 0.0005 คือ 0.05%)
        daily_volume (float): ปริมาณการซื้อขายเฉลี่ยรายวันของ Pool
        pool_tvl (float): มูลค่าสภาพคล่องรวม (TVL) ของ Pool ปัจจุบัน
        rebalance_threshold (float): เกณฑ์ความเบี่ยงเบนของ Skew ที่ยอมรับได้ก่อนทำการ Rebalance (เช่น 0.15)
        gas_fee (float): ค่าใช้จ่ายคงที่ (USD) ในการรันธุรกรรม Rebalance บน On-chain
        slippage (float): อัตรา Slippage ที่สูญเสียตอนทำ Active Swap (เช่น 0.001 คือ 0.1%)
        fee_mode (str): โหมดการคำนวณรายได้ ('volume' หรือ 'base_apr')
        base_apr (float): อัตราผลตอบแทนพื้นฐานรายปี (เช่น 0.05 คือ 5% ต่อปี)
    """
    initial_capital: float = 10000.0
    range_width: float = 0.10
    fee_tier: float = 0.0005
    daily_volume: float = 3560000.0
    pool_tvl: float = 112480000.0
    rebalance_threshold: float = 0.15
    gas_fee: float = 2.0
    slippage: float = 0.001
    fee_mode: str = 'volume'
    base_apr: float = 0.05


@dataclass
class RebalanceResult:
    """DTO แจ้งผลลัพธ์การ Rebalance"""
    is_rebalanced: bool
    swap_volume_usd: float
    slippage_cost: float
    gas_cost: float


class LPModule:
    """จำลองสถานะของ Liquidity Pool บน Concentrated Liquidity"""

    def __init__(self, config: LPConfig, start_price: float) -> None:
        self.config = config
        self.current_price: float = start_price
        self.position_value: float = config.initial_capital
        
        self.range_lower: float = start_price * (1.0 - config.range_width)
        self.range_upper: float = start_price * (1.0 + config.range_width)
        
        self.multiplier: float = self._calculate_multiplier(config.range_width)
        
        self.skew: float = 0.5 
        
        self.accumulated_fees: float = 0.0
        self.rebalance_count: int = 0

    def _calculate_multiplier(self, range_width: float) -> float:
        """คำนวณค่า Multiplier ที่สะท้อนความเข้มข้นของ Liquidity"""
        lower: float = 1.0 - range_width
        upper: float = 1.0 + range_width
        
        if lower <= 0.0 or upper <= 0.0:
            return 0.0
            
        numerator: float = 2.0
        denominator: float = 2.0 - (1.0 / math.sqrt(upper)) - math.sqrt(lower)
        
        if denominator <= 0.0:
            return 1.0
            
        return numerator / denominator

    def update_price(self, new_price: float) -> None:
        """อัปเดตราคาตลาด พร้อมคำนวณมูลค่าพอร์ตแบบ Mark-to-Market และปรับ Skew"""
        if self.current_price <= 0:
            self.current_price = new_price
            return

        price_change_pct: float = (new_price - self.current_price) / self.current_price

        base_asset_val: float = self.position_value * self.skew
        quote_asset_val: float = self.position_value * (1.0 - self.skew)

        new_base_asset_val: float = base_asset_val * (1.0 + price_change_pct)
        self.position_value = new_base_asset_val + quote_asset_val

        self.current_price = new_price

        if self.current_price <= self.range_lower:
            self.skew = 1.0
        elif self.current_price >= self.range_upper:
            self.skew = 0.0
        else:
            range_size: float = self.range_upper - self.range_lower
            self.skew = 1.0 - ((self.current_price - self.range_lower) / range_size)

    def collect_fee(self) -> float:
        """คำนวณและเก็บสะสมรายได้จากค่าธรรมเนียม (Trading Fee) ประจำชั่วโมง"""
        if self.range_lower < self.current_price < self.range_upper:
            # ใช้โหมด Base APR ตามที่ User ต้องการ (คำนวณผลตอบแทนรายชั่วโมง)
            if self.config.fee_mode == 'base_apr':
                effective_apr = self.config.base_apr * self.multiplier
                hourly_fee = self.position_value * (effective_apr / (365.0 * 24.0))
            else:
                # โหมด Volume เดิม
                base_share = self.position_value / self.config.pool_tvl
                volume_share = self.config.daily_volume / 24.0
                hourly_fee = base_share * volume_share * self.config.fee_tier * self.multiplier
            
            self.position_value += hourly_fee
            self.accumulated_fees += hourly_fee
            return hourly_fee
            
        return 0.0

    def check_and_rebalance(self) -> RebalanceResult:
        """ตรวจสอบว่า Skew เบี่ยงเบนเกิน Threshold หรือไม่ เพื่อ Rebalance"""
        drift: float = abs(self.skew - 0.5)
        
        if drift > self.config.rebalance_threshold:
            swap_volume: float = self.position_value * drift
            
            slippage_cost: float = swap_volume * self.config.slippage
            gas_cost: float = self.config.gas_fee
            total_cost: float = slippage_cost + gas_cost
            
            self.position_value -= total_cost
            
            self.range_lower = self.current_price * (1.0 - self.config.range_width)
            self.range_upper = self.current_price * (1.0 + self.config.range_width)
            self.skew = 0.5
            
            self.rebalance_count += 1
            
            return RebalanceResult(True, swap_volume, slippage_cost, gas_cost)
            
        return RebalanceResult(False, 0.0, 0.0, 0.0)

    def get_eth_inventory(self) -> float:
        if self.current_price <= 0:
            return 0.0
        base_value_usd: float = self.position_value * self.skew
        amount_tokens: float = base_value_usd / self.current_price
        return amount_tokens