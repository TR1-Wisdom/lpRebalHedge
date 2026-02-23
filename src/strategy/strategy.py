"""
src/strategy/strategy.py
โมดูล Strategy (The Brain) สำหรับ Inventory LP Backtester

อัปเดต: v1.0.4 (Anti-Whipsaw Fix)
- เพิ่ม Hysteresis Band (0.2%) ป้องกันสัญญาณหลอกในกราฟ Timeframe ต่ำ
"""

__version__ = "1.0.4"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"

import numpy as np
import pandas as pd
from typing import Protocol, List
from dataclasses import dataclass
from datetime import datetime


class ILPModule(Protocol):
    def get_eth_inventory(self) -> float:
        ...

class IPerpModule(Protocol):
    def get_short_position_size(self) -> float:
        ...

@dataclass
class StrategyConfig:
    ema_period: int = 200
    safety_net_pct: float = 0.02
    hedge_threshold: float = 0.05
    hedge_mode: str = 'smart' 
    use_safety_net: bool = True


@dataclass
class OrderEvent:
    timestamp: datetime
    target_module: str
    action: str
    target_size: float
    reason: str


class StrategyModule:
    def __init__(self, lp_module: ILPModule, perp_module: IPerpModule) -> None:
        self.lp = lp_module
        self.perp = perp_module

    def populate_indicators(self, dataframe: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
        df = dataframe.copy()
        df['ema'] = df['close'].ewm(span=config.ema_period, adjust=False).mean()
        df['pct_change'] = df['close'].pct_change().fillna(0.0)
        return df

    def populate_signals(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        df = dataframe.copy()
        
        # [KEY FIX] เพิ่ม Hysteresis Band (โซนกันชน 0.2%) ป้องกัน AI สับขาหลอกตอน Sideway
        upper_band = df['ema'] * 1.002
        lower_band = df['ema'] * 0.998
        
        df['signal'] = np.nan
        df.loc[df['close'] > upper_band, 'signal'] = 1   # ทะลุบนชัดเจน -> ปลดโล่
        df.loc[df['close'] < lower_band, 'signal'] = -1  # ทะลุล่างชัดเจน -> กางโล่
        
        # เติมช่องว่างด้วยสถานะเดิม (ไม่สับสวิตช์ถ้ายังอยู่ในโซนกันชน)
        df['signal'] = df['signal'].ffill().fillna(-1) 
        
        return df

    def generate_orders(self, current_tick: pd.Series, config: StrategyConfig) -> List[OrderEvent]:
        orders: List[OrderEvent] = []
        pct_change: float = float(current_tick['pct_change'])
        timestamp: datetime = current_tick['date']
        
        if getattr(config, 'hedge_mode', 'smart') == 'always':
            signal: int = -1
        else:
            signal: int = int(current_tick['signal'])
            
        is_safety_trigger: bool = False
        force_adjust: bool = False
        
        if getattr(config, 'use_safety_net', True):
            if abs(pct_change) >= config.safety_net_pct:
                is_safety_trigger = True
                force_adjust = True 
                
                if signal == 1 and pct_change <= -config.safety_net_pct:
                    signal = -1
                    
        actual_eth: float = self.lp.get_eth_inventory()
        current_short: float = self.perp.get_short_position_size()
        
        target_short: float = actual_eth if signal == -1 else 0.0
        diff: float = abs(target_short - current_short)
        is_flip: bool = (target_short == 0.0 and current_short > 0.0) or (target_short > 0.0 and current_short == 0.0)
        
        needs_adjustment: bool = False
        
        if is_flip:
            needs_adjustment = True
        elif target_short > 0.0:
            drift_ratio: float = diff / target_short if target_short > 0 else 0.0
            if drift_ratio > config.hedge_threshold or force_adjust:
                if diff > 0.0001: 
                    needs_adjustment = True
                
        if needs_adjustment:
            action_type: str = 'HEDGE_ON' if target_short > 0 else 'HEDGE_OFF'
            if not is_flip and target_short > 0:
                action_type = 'ADJUST_HEDGE'
                
            reason: str = "Safety Net Triggered" if is_safety_trigger else ("Signal Flip" if is_flip else "Threshold Drift")
            
            orders.append(OrderEvent(
                timestamp=timestamp,
                target_module='PERP',
                action=action_type,
                target_size=target_short,
                reason=reason
            ))
            
        return orders