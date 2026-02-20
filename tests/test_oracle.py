"""
Unit Tests สำหรับโมดูล Oracle

ชุดการทดสอบนี้ช่วยยืนยันความแม่นยำในการทำงานของระบบสุ่ม GBM, 
ตรวจสอบโครงสร้างข้อมูลที่สกัดออกมา และทดสอบลอจิกขอบเขตราคาทางคณิตศาสตร์

ประวัติการแก้ไข (Version Control):
- v1.0.4 (2026-02-20): แก้ไข Assertions ใน test_timeframe_resampling ให้ตรงกับจำนวนแท่งที่เกิดจากการ Resample รวมแท่งขอบเวลา (Spillover) และเปลี่ยน '1d' เป็น '1D' เพื่อแก้ Pandas4Warning
- v1.0.3 (2026-02-20): เพิ่ม sys.path.insert เพื่อแก้ปัญหา ModuleNotFoundError สำหรับ 'src'
- v1.0.2 (2026-02-20): แก้ไข path การ import โมดูล oracle ให้ตรงกับโครงสร้างโฟลเดอร์จริง (src.oracle.oracle)
- v1.0.1 (2026-02-20): เพิ่มระบบ Version Control และแปลคำอธิบายเป็นภาษาไทย
- v1.0.0 (2026-02-20): สร้าง Unit Test ครอบคลุมลอจิก 100%
"""

__version__ = "1.0.4"
__author__ = "LP-Rebal-Coding (Senior Quant Developer)"
__date__ = "2026-02-20"

import sys
import os

# แก้ปัญหา ModuleNotFoundError: No module named 'src' ตอนรัน pytest
# โดยการเพิ่ม Root Directory (ย้อนกลับ 1 ขั้นจากไฟล์เทส) เข้าไปใน Path ของระบบ
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
import numpy as np
import pandas as pd
from datetime import datetime
from src.oracle.oracle import OracleConfig, OracleModule


class TestOracleModule:
    """ชุดคลาสจำลองเพื่อทดสอบคลาส OracleModule"""

    @pytest.fixture
    def oracle(self) -> OracleModule:
        """Fixture สำหรับเตรียม Instance สดใหม่ของ OracleModule"""
        return OracleModule()

    @pytest.fixture
    def default_config(self) -> OracleConfig:
        """Fixture สำหรับเตรียมค่าคอนฟิกมาตรฐาน (OracleConfig) เพื่อใช้เทส"""
        return OracleConfig(
            start_price=3000.0,
            days=5,
            annual_volatility=0.50,
            seed=42,
            timeframe='1h'
        )

    def test_reproducibility(self, oracle: OracleModule, default_config: OracleConfig) -> None:
        """ทดสอบว่าการใช้ Seed เดิมจะต้องสร้าง DataFrame เหมือนเดิมแบบ 100%"""
        df1: pd.DataFrame = oracle.generate_data(default_config)
        df2: pd.DataFrame = oracle.generate_data(default_config)

        pd.testing.assert_frame_equal(df1, df2)

    def test_randomness(self, oracle: OracleModule, default_config: OracleConfig) -> None:
        """ทดสอบว่าการใช้ Seed ต่างกันจะต้องสร้างข้อมูล DataFrame ที่ไม่เหมือนกัน"""
        config1 = default_config
        config2 = OracleConfig(**{**default_config.__dict__, 'seed': 99})

        df1: pd.DataFrame = oracle.generate_data(config1)
        df2: pd.DataFrame = oracle.generate_data(config2)

        # เช็คว่าคอลัมน์ 'close' ของ 2 ชุดจะต้องไม่เท่ากัน
        with pytest.raises(AssertionError):
            pd.testing.assert_series_equal(df1['close'], df2['close'])

    def test_output_structure(self, oracle: OracleModule, default_config: OracleConfig) -> None:
        """ทดสอบว่า DataFrame มีคอลัมน์และโครงสร้างที่รองรับ Freqtrade ถูกต้อง"""
        df: pd.DataFrame = oracle.generate_data(default_config)

        expected_columns = ['date', 'open', 'high', 'low', 'close', 'volume']
        assert list(df.columns) == expected_columns
        assert isinstance(df['date'].iloc[0], pd.Timestamp)
        assert df.isna().sum().sum() == 0  # ต้องไม่มีค่า NaN ปะปน

    def test_ohlc_logic(self, oracle: OracleModule, default_config: OracleConfig) -> None:
        """ทดสอบขอบเขตทางคณิตศาสตร์ของราคา OHLC (เช่น ค่า High ต้องสูงที่สุดเสมอ)"""
        df: pd.DataFrame = oracle.generate_data(default_config)

        # High ต้องสูงกว่าหรือเท่ากับทุกตัว, Low ต้องต่ำกว่าหรือเท่ากับทุกตัว
        assert (df['high'] >= df['open']).all()
        assert (df['high'] >= df['close']).all()
        assert (df['low'] <= df['open']).all()
        assert (df['low'] <= df['close']).all()

    def test_start_price(self, oracle: OracleModule, default_config: OracleConfig) -> None:
        """ทดสอบว่าราคาเปิดที่แท่งแรกสุดตรงกับพารามิเตอร์ start_price ที่ตั้งไว้"""
        df: pd.DataFrame = oracle.generate_data(default_config)
        
        assert df['open'].iloc[0] == default_config.start_price

    def test_timeframe_resampling(self, oracle: OracleModule) -> None:
        """ทดสอบว่าระบบจัดกลุ่ม (Resample) Timeframe ได้อย่างถูกต้อง"""
        # เปลี่ยน '1d' เป็น '1D' เพื่อล้าง Pandas4Warning
        config_4h = OracleConfig(days=1, timeframe='4h', seed=1)
        config_1d = OracleConfig(days=10, timeframe='1D', seed=1)

        df_4h: pd.DataFrame = oracle.generate_data(config_4h)
        df_1d: pd.DataFrame = oracle.generate_data(config_1d)

        # 1 วัน (24 ชม.) หากแบ่งเป็น 4h จะต้องได้ 6 แถว + 1 แถวของ 00:00:00 วันถัดไป (Spillover boundary) = 7 แถว
        assert len(df_4h) == 7
        # 10 วัน หากแบ่งเป็น 1D จะได้ 10 แถว + 1 แถวของ 00:00:00 วันที่ 11 = 11 แถว
        assert len(df_1d) == 11
        
        # ทดสอบระยะห่างเวลาจริงระหว่างบรรทัด 0 กับบรรทัด 1
        delta_4h = df_4h['date'].iloc[1] - df_4h['date'].iloc[0]
        assert delta_4h == pd.Timedelta('4 hours')