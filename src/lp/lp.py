"""
โมดูล LP (Liquidity Pool) สำหรับโปรเจกต์ Inventory LP Backtester

ทำหน้าที่จำลองการทำงานของ Concentrated Liquidity (เช่น Uniswap V3 / Steer Protocol)
โดยคำนวณมูลค่าของพอร์ต (Impermanent Loss แบบ Macro-level), การเบ้ของสัดส่วนเหรียญ (Inventory Skew),
การเก็บค่าธรรมเนียม, และการปรับสมดุลพอร์ตแบบอัตโนมัติ (Active Swap Rebalancing)

ประวัติการแก้ไข (Version Control):
- v1.0.0 (2026-02-20): สร้าง LP Module รองรับสมการ Concentrated Liquidity Multiplier และ Skew
"""

__version__ = "1.0.0"
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
    """
    initial_capital: float = 10000.0
    range_width: float = 0.10
    fee_tier: float = 0.0005
    daily_volume: float = 3560000.0
    pool_tvl: float = 112480000.0
    rebalance_threshold: float = 0.15
    gas_fee: float = 2.0
    slippage: float = 0.001


@dataclass
class RebalanceResult:
    """
    Data Transfer Object (DTO) แจ้งผลลัพธ์การ Rebalance

    Attributes:
        is_rebalanced (bool): มีการทำงาน Rebalance เกิดขึ้นหรือไม่
        swap_volume_usd (float): มูลค่าที่ต้องถูก Swap เป็น USD
        slippage_cost (float): ต้นทุนแฝงจาก Slippage
        gas_cost (float): ค่าธรรมเนียม On-chain
    """
    is_rebalanced: bool
    swap_volume_usd: float
    slippage_cost: float
    gas_cost: float


class LPModule:
    """
    คลาส LPModule สำหรับจำลองสถานะของ Liquidity Pool

    ใช้หลักการ Macro-level Math เพื่อรักษาความเร็วในระดับ Backtester โดยอ้างอิงตรรกะ
    ของ Uniswap V3 ที่สัดส่วน (Skew) เหรียญจะปรับตัวตามราคาที่เคลื่อนที่ใน Range 
    """

    def __init__(self, config: LPConfig, start_price: float) -> None:
        """
        ตั้งค่าเริ่มต้นให้ LP Module

        Args:
            config (LPConfig): ตั้งค่าระบบ LP
            start_price (float): ราคาเริ่มต้นของสินทรัพย์อ้างอิง (Base Asset เช่น ETH)
        """
        self.config = config
        self.current_price: float = start_price
        self.position_value: float = config.initial_capital
        
        # คำนวณขอบเขตราคา (Price Range)
        self.range_lower: float = start_price * (1.0 - config.range_width)
        self.range_upper: float = start_price * (1.0 + config.range_width)
        
        # คำนวณอัตราทดประสิทธิภาพเงินทุน (Concentrated Liquidity Multiplier)
        self.multiplier: float = self._calculate_multiplier(config.range_width)
        
        # สัดส่วนเหรียญเริ่มต้นที่กึ่งกลาง (0.5 = 50% Base Asset / 50% Quote Asset)
        self.skew: float = 0.5 
        
        # เก็บสถิติ
        self.accumulated_fees: float = 0.0
        self.rebalance_count: int = 0

    def _calculate_multiplier(self, range_width: float) -> float:
        """
        คำนวณค่า Multiplier ที่สะท้อนความเข้มข้นของ Liquidity (เทียบกับ V2 แบบ Infinity)
        
        Args:
            range_width (float): ขนาดของช่วงราคาแบบทศนิยม
            
        Returns:
            float: อัตราคูณประสิทธิภาพเงินทุน (Capital Efficiency)
        """
        lower: float = 1.0 - range_width
        upper: float = 1.0 + range_width
        
        if lower <= 0.0 or upper <= 0.0:
            return 0.0
            
        numerator: float = 2.0
        denominator: float = 2.0 - (1.0 / math.sqrt(upper)) - math.sqrt(lower)
        
        # ป้องกันกรณีส่วนหารเป็น 0 หรือติดลบจากค่าที่ผิดพลาด
        if denominator <= 0.0:
            return 1.0
            
        return numerator / denominator

    def update_price(self, new_price: float) -> None:
        """
        อัปเดตราคาตลาดปัจจุบัน พร้อมคำนวณมูลค่าพอร์ตแบบ Mark-to-Market และปรับ Skew ของสินค้า

        Args:
            new_price (float): ราคาล่าสุดของสินทรัพย์อ้างอิง
        """
        if self.current_price <= 0:
            self.current_price = new_price
            return

        price_change_pct: float = (new_price - self.current_price) / self.current_price

        # 1. คำนวณ PnL ของมูลค่าพอร์ตจากสถานะ Inventory ปัจจุบัน
        base_asset_val: float = self.position_value * self.skew
        quote_asset_val: float = self.position_value * (1.0 - self.skew)

        # Base Asset ได้รับผลกระทบจากราคา ส่วน Quote Asset (Stablecoin) มูลค่าคงที่
        new_base_asset_val: float = base_asset_val * (1.0 + price_change_pct)
        self.position_value = new_base_asset_val + quote_asset_val

        self.current_price = new_price

        # 2. ปรับสัดส่วน Inventory Skew ใหม่ตามตำแหน่งของราคา
        if self.current_price <= self.range_lower:
            self.skew = 1.0  # ติดดอย เป็น Base Asset 100%
        elif self.current_price >= self.range_upper:
            self.skew = 0.0  # ขายหมู เป็น Quote Asset 100%
        else:
            range_size: float = self.range_upper - self.range_lower
            # สมการ Linear Skew ตาม Blueprint: ถ้าราคาลงของจะยิ่งเพิ่ม, ถ้าราคาขึ้นของจะลดลง
            self.skew = 1.0 - ((self.current_price - self.range_lower) / range_size)

    def collect_fee(self) -> float:
        """
        คำนวณและเก็บสะสมรายได้จากค่าธรรมเนียม (Trading Fee) ประจำชั่วโมง
        
        Returns:
            float: มูลค่าค่าธรรมเนียมที่ได้รับเป็นดอลลาร์
        """
        # จะได้รับค่าธรรมเนียมก็ต่อเมื่อราคาอยู่ในช่วงที่วางไว้ (In-Range)
        if self.range_lower < self.current_price < self.range_upper:
            base_share: float = self.position_value / self.config.pool_tvl
            volume_share: float = self.config.daily_volume / 24.0
            
            hourly_fee: float = base_share * volume_share * self.config.fee_tier * self.multiplier
            
            self.position_value += hourly_fee
            self.accumulated_fees += hourly_fee
            return hourly_fee
            
        return 0.0

    def check_and_rebalance(self) -> RebalanceResult:
        """
        ตรวจสอบว่าสัดส่วนเหรียญ (Skew) เบี่ยงเบนเกิน Threshold หรือไม่ 
        หากเกินจะทำการ Active Swap คืนค่าให้เป็น 50:50 และหักต้นทุน
        
        Returns:
            RebalanceResult: DTO แสดงผลลัพธ์และต้นทุนของกิจกรรมการ Rebalance
        """
        drift: float = abs(self.skew - 0.5)
        
        if drift > self.config.rebalance_threshold:
            # คำนวณปริมาณมูลค่าที่จะต้อง Swap คืนให้กลับมาเป็นสมดุล
            swap_volume: float = self.position_value * drift
            
            slippage_cost: float = swap_volume * self.config.slippage
            gas_cost: float = self.config.gas_fee
            total_cost: float = slippage_cost + gas_cost
            
            # หักต้นทุนออกจากมูลค่าพอร์ต
            self.position_value -= total_cost
            
            # รีเซ็ตช่วงราคาใหม่ให้อยู่ตรงกลาง (Re-center)
            self.range_lower = self.current_price * (1.0 - self.config.range_width)
            self.range_upper = self.current_price * (1.0 + self.config.range_width)
            self.skew = 0.5
            
            self.rebalance_count += 1
            
            return RebalanceResult(
                is_rebalanced=True,
                swap_volume_usd=swap_volume,
                slippage_cost=slippage_cost,
                gas_cost=gas_cost
            )
            
        return RebalanceResult(
            is_rebalanced=False,
            swap_volume_usd=0.0,
            slippage_cost=0.0,
            gas_cost=0.0
        )

    def get_eth_inventory(self) -> float:
        """
        ดึงค่าปริมาณของสินทรัพย์อ้างอิง (เช่น ETH) ที่ถือครองอยู่จริงในตระกร้า
        เพื่อให้สอดคล้องกับ Protocol `ILPModule` ของ Strategy

        Returns:
            float: จำนวนเหรียญอ้างอิงในพอร์ต
        """
        if self.current_price <= 0:
            return 0.0
            
        base_value_usd: float = self.position_value * self.skew
        amount_tokens: float = base_value_usd / self.current_price
        
        return amount_tokens