"""
tests/test_direct_controller.py
Unit Tests สำหรับคลาส DirectLPController (รองรับ v3.3.0)
แก้ไข: เพิ่ม sys.path เพื่อแก้ปัญหา Collection Error และปรับ Mock ให้ตรงกับ Audit Patch
"""

import sys
import os
import pytest
import logging
from unittest.mock import MagicMock, patch

# [FIX: Collection Error] ดึง Root Directory เข้ามาใน Path เพื่อให้ pytest เจอโฟลเดอร์ src/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from web3.exceptions import TimeExhausted
from src.lp.direct_controller import DirectLPController, RebalanceParams

# ปิดระบบ Logging ระหว่างการ Test เพื่อไม่ให้ Console รก
logging.disable(logging.CRITICAL)

@pytest.fixture
def mock_env_vars():
    """Mock ตัวแปร Environment ให้มี Private Key เสมอ สำหรับการ Test"""
    # [FIX] เปลี่ยนจาก PRIVATE_KEY เป็น BOT_PRIVATE_KEY ให้ตรงกับโค้ด v3.3.0
    with patch.dict(os.environ, {"BOT_PRIVATE_KEY": "0x" + "1" * 64}):
        yield

@pytest.fixture
def mock_web3():
    """สร้าง Mock สำหรับ Web3 Component ป้องกันการยิง Network จริง"""
    with patch("src.lp.direct_controller.Web3") as mock_w3_class:
        mock_w3_instance = MagicMock()
        mock_w3_instance.is_connected.return_value = True
        
        # Mock ค่าพื้นฐานสำหรับ Dynamic Gas
        mock_w3_instance.eth.get_transaction_count.return_value = 1
        mock_w3_instance.eth.get_block.return_value = {'baseFeePerGas': 100000000} # 0.1 Gwei
        mock_w3_instance.eth.chain_id = 42161 # Arbitrum One
        
        mock_w3_class.return_value = mock_w3_instance
        mock_w3_class.HTTPProvider = MagicMock()
        mock_w3_class.to_checksum_address = lambda x: x # Mock เช็ค Address
        
        yield mock_w3_instance

@pytest.fixture
def controller(mock_env_vars, mock_web3):
    return DirectLPController(rpc_url="http://mock-rpc", dry_run=False)

def test_get_current_inventory(controller):
    """ทดสอบการดึงข้อมูล Inventory ว่าสามารถ Map ค่าจาก Tuple ของ Smart Contract ได้ถูกต้อง"""
    # จำลองค่าที่ Smart Contract จะตอบกลับมา (12 ตัวแปรตาม ABI)
    mock_pos = (0, "0xOperator", "0xToken0", "0xToken1", 500, -100, 100, 1000000, 0, 0, 50, 60)
    controller.contract.functions.positions.return_value.call.return_value = mock_pos
    
    inv = controller.get_current_inventory(1)
    
    assert inv["liquidity"] == 1000000
    assert inv["tokensOwed0"] == 50
    assert inv["tokensOwed1"] == 60

def test_execute_rebalance_flow_success(controller):
    """ทดสอบ Flow การทำงานครบวงจร: Decrease -> Collect -> Mint"""
    # 1. Mock การดึง Inventory ว่ามีสภาพคล่องอยู่ 1,000,000
    controller.get_current_inventory = MagicMock(return_value={"liquidity": 1000000})
    
    # 2. Mock ฟังก์ชันส่งธุรกรรมให้ผ่านฉลุย
    controller._send_transaction = MagicMock(return_value=True)
    
    # 3. Mock การเช็ค Approval (ไม่ต้องจำลองการ Approve จริง)
    controller._ensure_approval = MagicMock(return_value=True)
    
    params = RebalanceParams(
        token_id=1, new_tick_lower=-1000, new_tick_upper=1000,
        token0_address="0xT0", token1_address="0xT1",
        fee_tier=500, amount0_desired=100, amount1_desired=100, 
        deadline=9999999999, slippage_tolerance=0.01
    )
    
    result = controller.execute_rebalance(params)
    
    # ต้องทำงานสำเร็จ
    assert result is True
    
    # ต้องมีการส่ง 3 ธุรกรรมตามลำดับ (Decrease, Collect, Mint)
    assert controller._send_transaction.call_count == 3
    
    # ต้องมีการตรวจ Approval 2 ครั้ง (Token0 และ Token1)
    assert controller._ensure_approval.call_count == 2

def test_execute_rebalance_circuit_breaker(controller):
    """ทดสอบระบบหยุดอัตโนมัติ (Circuit Breaker) หากขั้นตอนแรก (Decrease) พัง"""
    controller.get_current_inventory = MagicMock(return_value={"liquidity": 1000000})
    
    # Mock ให้ _send_transaction ล้มเหลวทันที
    controller._send_transaction = MagicMock(return_value=False)
    
    params = RebalanceParams(
        token_id=1, new_tick_lower=-1000, new_tick_upper=1000,
        token0_address="0xT0", token1_address="0xT1",
        fee_tier=500, amount0_desired=100, amount1_desired=100, deadline=9999999999
    )
    
    result = controller.execute_rebalance(params)
    
    # ต้องล้มเหลว
    assert result is False
    
    # ต้องเรียก _send_transaction แค่ 1 ครั้ง แล้วหยุดเลย (ไม่ทำ Collect/Mint ต่อ)
    assert controller._send_transaction.call_count == 1