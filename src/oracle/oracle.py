"""
oracle/oracle.py
โมดูล Oracle สำหรับโปรเจกต์ Inventory LP Backtester

โมดูลนี้ใช้สำหรับสร้างข้อมูลราคาจำลอง OHLCV (Open, High, Low, Close, Volume) 
ด้วยสมการคณิตศาสตร์ Geometric Brownian Motion (GBM) 
โดยใช้กลไกการจำลองความถี่สูง (ระดับนาที) เพื่อสร้างกรอบราคาที่สมจริงก่อนจะจัดกลุ่ม (Resample) 
เป็น Timeframe ที่ต้องการใช้งาน

ประวัติการแก้ไข (Version Control):
- v1.0.1 (2026-02-20): เพิ่มระบบ Version Control, Changelog และปรับคอมเมนต์/Docstring เป็นภาษาไทย
- v1.0.0 (2026-02-20): โครงสร้างพื้นฐานของ Oracle ด้วย GBM พร้อมรองรับ Seed
"""

__version__ = "1.0.1"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"
__date__ = "2026-02-20"

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class OracleConfig:
    """
    Data Transfer Object (DTO) สำหรับเก็บค่าคอนฟิกูเรชันของการสร้างข้อมูลราคา (Oracle)

    Attributes:
        start_price (float): ราคาเริ่มต้นของสินทรัพย์ ณ t=0
        days (int): จำนวนวันทั้งหมดที่ต้องการจำลองข้อมูล
        annual_volatility (float): ความผันผวนรายปี (Sigma) ของสินทรัพย์ (เช่น 0.80 คือ 80%)
        annual_drift (float): ผลตอบแทนคาดหวังรายปี (Mu) ของสินทรัพย์
        seed (Optional[int]): รหัสสุ่ม (Seed) เพื่อให้สามารถสร้างข้อมูลชุดเดิมซ้ำได้ (ใส่ None หากต้องการสุ่มอิสระ)
        timeframe (str): ขนาด Timeframe ของ Pandas ที่ต้องการ (เช่น '1h', '1d', '15min')
        base_volume (float): ปริมาณการซื้อขาย (Volume) พื้นฐานต่อ 1 Timeframe
        start_date (datetime): วันที่และเวลาเริ่มต้นของชุดข้อมูล
    """
    start_price: float = 2000.0
    days: int = 30
    annual_volatility: float = 0.80
    annual_drift: float = 0.0
    seed: Optional[int] = None
    timeframe: str = '1h'
    base_volume: float = 3560000.0
    start_date: datetime = datetime(2024, 1, 1)


class OracleModule:
    """
    ตัวสร้างข้อมูลราคาจำลองด้วย Geometric Brownian Motion (GBM)
    
    คลาสนี้ไม่มีการเก็บ State (Stateless) และแยกการทำงานอย่างอิสระ
    ให้ผลลัพธ์เป็น Pandas DataFrame ที่รองรับการทำงานร่วมกับ Freqtrade ทันที
    """

    def __init__(self) -> None:
        """เริ่มต้นการทำงานของโมดูล Oracle"""
        pass

    def generate_data(self, config: OracleConfig) -> pd.DataFrame:
        """
        สร้างข้อมูล OHLCV สังเคราะห์ อ้างอิงจากคอนฟิกูเรชันที่กำหนด

        กระบวนการทำงาน:
        1. สร้างเส้นทางราคาระดับ 1 นาที ด้วย log-returns และ exact GBM discretization
        2. จัดกลุ่ม (Resample) ข้อมูลระดับนาทีให้กลายเป็น OHLC ใน Timeframe ที่ต้องการ
        3. จำลอง Volume การซื้อขายด้วยการเติม Noise แบบ Log-normal

        Args:
            config (OracleConfig): ตัวแปรตั้งค่าสำหรับการสร้างข้อมูล

        Returns:
            pd.DataFrame: DataFrame ที่เข้ากันได้กับ Freqtrade ประกอบด้วยคอลัมน์:
                          ['date', 'open', 'high', 'low', 'close', 'volume']
        """
        # ตั้งค่าตัวสร้างตัวเลขสุ่ม
        rng = np.random.default_rng(config.seed)

        # คำนวณจำนวนนาทีทั้งหมดที่ต้องใช้จำลอง (1 ปีใน Crypto = 365 วัน)
        total_minutes: int = config.days * 24 * 60
        
        # คำนวณส่วนเพิ่มของเวลา (dt) ในหน่วยปี
        dt: float = 1.0 / (365.0 * 24.0 * 60.0)

        # สร้างตัวแปรสุ่มแบบ Standard Normal Distribution
        z_scores: np.ndarray = rng.standard_normal(total_minutes)

        # คำนวณ Geometric Brownian Motion (ใช้ Exact Solution รูปแบบ Euler-Maruyama สำหรับ Log Prices)
        # สูตร: S_t = S_0 * exp((mu - sigma^2 / 2) * t + sigma * W_t)
        drift_component: float = (config.annual_drift - (config.annual_volatility ** 2) / 2) * dt
        shock_component: np.ndarray = config.annual_volatility * np.sqrt(dt) * z_scores

        log_returns: np.ndarray = drift_component + shock_component
        
        # เติม 0 ที่ตำแหน่งแรกสำหรับราคาเริ่มต้น จากนั้นคำนวณผลรวมสะสม (Cumulative sum) ของ log returns
        cumulative_log_returns: np.ndarray = np.insert(np.cumsum(log_returns), 0, 0.0)
        
        # คำนวณเส้นทางราคาจริง (Price path)
        price_path: np.ndarray = config.start_price * np.exp(cumulative_log_returns)

        # สร้าง High-frequency DateTime index
        # เพิ่มจำนวนบวก 1 นาที เพื่อให้ครอบคลุมจุดเริ่มต้น (Zero-th point)
        date_range: pd.DatetimeIndex = pd.date_range(
            start=config.start_date, 
            periods=total_minutes + 1, 
            freq='1min'
        )

        # สร้าง High-frequency DataFrame
        hf_df = pd.DataFrame({'price': price_path}, index=date_range)

        # จัดกลุ่มข้อมูล (Resample) ไปเป็น Timeframe เป้าหมาย (เช่น '1h')
        ohlc_df: pd.DataFrame = hf_df['price'].resample(config.timeframe).ohlc()
        
        # ลบแถวที่เป็น NaN ทิ้ง (อาจเกิดขึ้นกรณีขอบเขตเวลาไม่ลงตัวพอดี)
        ohlc_df = ohlc_df.dropna()

        # สร้าง Volume ซื้อขายจำลองโดยกระจายตัวแบบ Log-normal รอบค่า base_volume
        # ปรับสัดส่วน Volume ให้สัมพันธ์กับความยาวของ Timeframe (เช่น 60 นาที = Volume 1 ชั่วโมง)
        minutes_in_tf: int = int(pd.Timedelta(config.timeframe).total_seconds() / 60.0)
        adjusted_base_vol: float = config.base_volume * (minutes_in_tf / 60.0)
        
        vol_noise: np.ndarray = rng.lognormal(mean=0.0, sigma=0.2, size=len(ohlc_df))
        ohlc_df['volume'] = adjusted_base_vol * vol_noise

        # จัดฟอร์แมตให้อยู่ในรูปแบบมาตรฐานของ Freqtrade
        ohlc_df.reset_index(inplace=True)
        ohlc_df.rename(columns={'index': 'date'}, inplace=True)
        
        # เรียงลำดับคอลัมน์ให้ถูกต้องอย่างชัดเจน
        ohlc_df = ohlc_df[['date', 'open', 'high', 'low', 'close', 'volume']]

        return ohlc_df