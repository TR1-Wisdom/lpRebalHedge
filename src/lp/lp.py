"""
src/lp/lp.py
โมดูล LP (v1.2.2 - Exact Math & Stat Fix)
- คำนวณ IL แม่นยำ 100% ตาม Uniswap V3 Whitepaper
- แก้ปัญหา Fee Explosion โดยส่งอัตรารายปีให้ Engine เป็นผู้จัดการ (Scaled per Tick)
- รองรับระบบ Active Rebalance พร้อมการคิดต้นทุน Slippage และ Gas จริง
"""

import math
from dataclasses import dataclass
from typing import Optional

@dataclass
class LPConfig:
    """การตั้งค่าพารามิเตอร์ของโมดูล LP"""
    initial_capital: float
    range_width: float         # เช่น 0.10 สำหรับ ±10%
    rebalance_threshold: float    # เช่น 0.20 สำหรับจุดขยับพอร์ตเมื่อ Skew เอียงเกิน 70:30
    base_apr: float = 0.05       # APR พื้นฐานของ Pool V2 (ก่อนคูณ Multiplier)
    gas_fee: float = 2.0         # ค่าแก๊สต่อการ Rebalance ($)
    slippage: float = 0.001      # Slippage จากการ Swap ส่วนเกิน (0.1%)

@dataclass
class RebalanceResult:
    """ข้อมูลสรุปผลหลังจากการ Rebalance"""
    is_rebalanced: bool
    gas_cost: float = 0.0
    slippage_cost: float = 0.0

class LPModule:
    def __init__(self, config: LPConfig, start_price: float):
        self.config = config
        self.position_value = config.initial_capital
        self.current_price = start_price
        
        # ตั้งค่าช่วงราคา (Range) แบบ V3
        self.range_lower = start_price * (1 - config.range_width)
        self.range_upper = start_price * (1 + config.range_width)
        
        # หัวใจสำคัญ: คำนวณ Multiplier และ Liquidity Amount (L)
        self.multiplier = self._calculate_multiplier()
        self.L = self._calculate_initial_L(self.position_value, start_price)
        
        self.skew = 0.5           # สัดส่วนมูลค่า ETH เทียบกับ Total
        self.rebalance_count = 0
        self.accumulated_fees = 0.0 # ใช้สำรองเฉพาะเป็น Buffer สำหรับระบบ Rescue

    def _calculate_multiplier(self) -> float:
        """คำนวณประสิทธิภาพเงินทุนจาก Range Width ตามหลัก V3 Whitepaper"""
        sa = math.sqrt(max(0.0001, 1 - self.config.range_width))
        sb = math.sqrt(1 + self.config.range_width)
        # Multiplier = 2 / (2 - (1/sqrt(Pb)) - sqrt(Pa)) โดยประมาณที่จุดกึ่งกลาง
        return 2 / (2 - (1 / sb) - sa)

    def _calculate_initial_L(self, capital: float, price: float) -> float:
        """หาค่าสภาพคล่อง (L) จากเงินต้นและช่วงราคา (Square Root Invariant)"""
        sp = math.sqrt(price)
        sa = math.sqrt(self.range_lower)
        sb = math.sqrt(self.range_upper)
        # L = Capital / ((sqrt(P) - sqrt(Pa)) + P * (sqrt(Pb) - sqrt(P)) / (sqrt(P) * sqrt(Pb)))
        return capital / ((sp - sa) + (price * (sb - sp) / (sp * sb)))

    def update_price(self, new_price: float):
        """[CORE UPDATE] อัปเดตมูลค่าพอร์ต MTM โดยใช้ Exact Square Root Math"""
        if new_price <= 0: return
        self.current_price = new_price
        
        sp = math.sqrt(new_price)
        sa = math.sqrt(self.range_lower)
        sb = math.sqrt(self.range_upper)

        if new_price <= self.range_lower:
            # หลุดล่าง: ถือ ETH เต็มพอร์ต (100% Skew)
            self.position_value = self.L * (sb - sa) / (sa * sb) * new_price
            self.skew = 1.0
        elif new_price >= self.range_upper:
            # หลุดบน: ถือ USDC เต็มพอร์ต (0% Skew)
            self.position_value = self.L * (sb - sa)
            self.skew = 0.0
        else:
            # อยู่ในกรอบ: คำนวณมูลค่า ETH และ USDC แยกส่วนกัน
            eth_amount = self.L * (sb - sp) / (sp * sb)
            usdc_amount = self.L * (sp - sa)
            
            self.position_value = (eth_amount * new_price) + usdc_amount
            # คำนวณ Skew ที่แม่นยำ (สัดส่วน ETH ในพอร์ต)
            self.skew = (eth_amount * new_price) / self.position_value

    def get_annual_fee_rate(self) -> float:
        """[STAT FIX] คืนค่าอัตรากำไรรายปี เพื่อให้ Engine นำไปหารเป็นรายนาที"""
        if self.range_lower < self.current_price < self.range_upper:
            # กำไรรายปี = มูลค่าพอร์ตปัจจุบัน * (APR * Multiplier)
            return self.position_value * (self.config.base_apr * self.multiplier)
        return 0.0

    def check_and_rebalance(self) -> RebalanceResult:
        """ตรวจสอบและ Re-center พอร์ตเมื่อ Skew เอียงเกินเกณฑ์"""
        drift = abs(self.skew - 0.5)
        
        if drift > self.config.rebalance_threshold:
            # 1. คำนวณต้นทุน Slippage จากปริมาณที่ต้อง Swap จริง (Active Swap)
            swap_volume = self.position_value * drift
            slippage_cost = swap_volume * self.config.slippage
            gas_cost = self.config.gas_fee
            
            # 2. หักต้นทุนออกจากมูลค่าพอร์ต
            self.position_value -= (slippage_cost + gas_cost)
            
            # 3. วาง Range ใหม่ที่ราคาปัจจุบัน
            self.range_lower = self.current_price * (1 - self.config.range_width)
            self.range_upper = self.current_price * (1 + self.config.range_width)
            
            # 4. คำนวณค่า L ใหม่สำหรับตำแหน่งใหม่ (Re-centering)
            self.L = self._calculate_initial_L(self.position_value, self.current_price)
            self.skew = 0.5
            self.rebalance_count += 1
            
            return RebalanceResult(True, gas_cost, slippage_cost)
            
        return RebalanceResult(False)

    def get_eth_inventory(self) -> float:
        """ส่งจำนวนเหรียญ ETH ที่ถืออยู่จริง (หน่วย Token) เพื่อใช้คำนวณ Delta Hedge"""
        if self.current_price <= 0: return 0.0
        return (self.position_value * self.skew) / self.current_price