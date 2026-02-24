"""
src/lp/uniswap_v3_manager.py
โมดูลควบคุมและตรวจสอบ Uniswap V3 Position (v3.0.3 - Stablecoin & Tokens Owed)

อัปเดตล่าสุด:
- รองรับการดึง Decimal ของเหรียญอัตโนมัติ (เช่น USDC=6, USDT=6)
- คำนวณยอด Active LP (เงินที่ทำงาน) + Tokens Owed (เงินที่รอเก็บเกี่ยว)
- ใช้ sqrtPriceX96 จาก slot0 เพื่อความแม่นยำสูงสุด
"""

import time
from typing import Dict, Any
from web3 import Web3
from src.utils.SafeWeb3 import SafeWeb3

class UniswapPositionManager:
    # ที่อยู่สัญญามาตรฐานของ Uniswap V3: Nonfungible Position Manager บน Arbitrum One
    POSITION_MANAGER_ADDRESS = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
    
    def __init__(self, safe_w3: SafeWeb3):
        self.sw3 = safe_w3
        self.w3 = safe_w3.w3
        
        # ABI สำหรับ Position Manager (อ่านข้อมูล NFT Position)
        self.manager_abi = [
            {"inputs":[{"internalType":"uint256","name":"tokenId","type":"uint256"}],"name":"positions","outputs":[{"internalType":"uint96","name":"nonce","type":"uint96"},{"internalType":"address","name":"operator","type":"address"},{"internalType":"address","name":"token0","type":"address"},{"internalType":"address","name":"token1","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"},{"internalType":"int24","name":"tickLower","type":"int24"},{"internalType":"int24","name":"tickUpper","type":"int24"},{"internalType":"uint128","name":"liquidity","type":"uint128"},{"internalType":"uint256","name":"feeGrowthInside0LastX128","type":"uint256"},{"internalType":"uint256","name":"feeGrowthInside1LastX128","type":"uint256"},{"internalType":"uint128","name":"tokensOwed0","type":"uint128"},{"internalType":"uint128","name":"tokensOwed1","type":"uint128"}],"stateMutability":"view","type":"function"}
        ]
        
        # ABI สำหรับดึง Decimal ของเหรียญ ERC20
        self.erc20_abi = [
            {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
        ]
        
        # ABI สำหรับ Pool (อ่านราคากลาง slot0)
        self.pool_abi = [
            {"inputs":[],"name":"slot0","outputs":[{"internalType":"uint160","name":"sqrtPriceX96","type":"uint160"},{"internalType":"int24","name":"tick","type":"int24"},{"internalType":"uint16","name":"observationIndex","type":"uint16"},{"internalType":"uint16","name":"observationCardinality","type":"uint16"},{"internalType":"uint16","name":"observationCardinalityNext","type":"uint16"},{"internalType":"uint8","name":"feeProtocol","type":"uint8"},{"internalType":"bool","name":"unlocked","type":"bool"}],"stateMutability":"view","type":"function"}
        ]
        
        self.manager_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.POSITION_MANAGER_ADDRESS),
            abi=self.manager_abi
        )

    def get_token_decimals(self, token_address: str) -> int:
        """ดึงค่า Decimal ของเหรียญอัตโนมัติ"""
        token_contract = self.w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=self.erc20_abi)
        return self.sw3.call_contract_safe(token_contract.functions.decimals())

    def get_inventory_balances(self, token_id: int, pool_address: str) -> Dict[str, Any]:
        """คำนวณยอดเงินจริง โดยตรวจจับ Decimal และรวมเหรียญที่ค้างในระบบ (Tokens Owed)"""
        start_time = time.time()
        
        try:
            # 1. ดึงข้อมูล NFT Position
            pos = self.sw3.call_contract_safe(self.manager_contract.functions.positions(token_id))
            
            # ดึง Decimal ของ Token0 และ Token1
            dec0 = self.get_token_decimals(pos[2])
            dec1 = self.get_token_decimals(pos[3])
            
            # 2. ดึงราคากลาง (Current Price) จาก Pool
            pool_contract = self.w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=self.pool_abi)
            slot0 = self.sw3.call_contract_safe(pool_contract.functions.slot0())
            
            current_tick = slot0[1]
            sqrt_p = slot0[0] / (2**96) # แปลง sqrtPriceX96 เป็นทศนิยมปกติ

            # 3. แกะตัวแปรจาก Position
            tick_lower = pos[5]
            tick_upper = pos[6]
            liquidity = pos[7]
            tokens_owed0 = pos[10]
            tokens_owed1 = pos[11]

            # คำนวณขอบเขตราคา (Range)
            sqrt_p_lower = 1.0001 ** (tick_lower / 2)
            sqrt_p_upper = 1.0001 ** (tick_upper / 2)

            # 4. สมการ Uniswap V3 (คำนวณเหรียญที่กำลังทำงานอยู่)
            amt0, amt1 = 0.0, 0.0
            if current_tick < tick_lower:
                amt0 = liquidity * (sqrt_p_upper - sqrt_p_lower) / (sqrt_p_lower * sqrt_p_upper)
            elif current_tick < tick_upper:
                amt0 = liquidity * (sqrt_p_upper - sqrt_p) / (sqrt_p * sqrt_p_upper)
                amt1 = liquidity * (sqrt_p - sqrt_p_lower)
            else:
                amt1 = liquidity * (sqrt_p_upper - sqrt_p_lower)

            # 5. แปลงหน่วย Active LP (หารด้วย Decimal)
            active_amt0 = amt0 / (10 ** dec0)
            active_amt1 = amt1 / (10 ** dec1)
            
            # 6. แปลงหน่วย Tokens Owed (เงินที่ถอนแล้วรอเก็บเกี่ยว / ค่าธรรมเนียม)
            owed_amt0 = tokens_owed0 / (10 ** dec0)
            owed_amt1 = tokens_owed1 / (10 ** dec1)
            
            latency_ms = (time.time() - start_time) * 1000

            return {
                "token_id": token_id,
                "active_amount0": active_amt0,
                "active_amount1": active_amt1,
                "owed_amount0": owed_amt0,
                "owed_amount1": owed_amt1,
                "total_amount0": active_amt0 + owed_amt0,
                "total_amount1": active_amt1 + owed_amt1,
                "is_in_range": tick_lower <= current_tick <= tick_upper, # <-- ใช้ชื่อนี้เป็นมาตรฐาน
                "latency_ms": round(latency_ms, 2)
            }
        except Exception as e:
            return {"error": str(e)}