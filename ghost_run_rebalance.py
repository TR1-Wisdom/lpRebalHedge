"""
ghost_run_rebalance.py
สคริปต์ทดสอบการทำงานของ DirectLPController บนเครือข่ายจริง (Arbitrum)
สถานะ: DRY RUN (จำลองการทำงาน 100% ไม่มีการใช้ Gas หรือเงินจริง)
"""

import os
import time
from dotenv import load_dotenv
from src.lp.direct_controller import DirectLPController, RebalanceParams

def run_ghost_test():
    load_dotenv()
    
    rpc_url = os.getenv("ALCHEMY_RPC_URL")
    token_id = int(os.getenv("LP_TOKEN_ID", "0"))
    
    if not rpc_url or token_id == 0:
        print("🚨 [ERROR] กรุณาตรวจสอบ ALCHEMY_RPC_URL และ LP_TOKEN_ID ในไฟล์ .env")
        return

    print("="*65)
    print("👻 STARTING GHOST RUN (DRY RUN MODE) 👻")
    print("="*65)
    print(f"[*] Target Network : Arbitrum One")
    print(f"[*] Target LP NFT  : {token_id}")
    print("-" * 65)

    # 1. สร้าง Controller โดยบังคับโหมด dry_run=True เสมอเพื่อความปลอดภัย
    controller = DirectLPController(rpc_url=rpc_url, dry_run=True)

    try:
        # 2. อ่านข้อมูล Inventory จริงจาก Blockchain
        print(f"[\u23f3] Fetching On-chain Inventory...")
        inventory = controller.get_current_inventory(token_id)
        
        print(f"    ✅ Token 0 (WETH) : {inventory['token0']}")
        print(f"    ✅ Token 1 (USDC) : {inventory['token1']}")
        print(f"    ✅ Fee Tier       : {inventory['fee']}")
        print(f"    ✅ Current Range  : [Tick {inventory['tickLower']} to {inventory['tickUpper']}]")
        print(f"    ✅ Liquidity      : {inventory['liquidity']}")
        
        print("-" * 65)
        # 3. สร้างพารามิเตอร์จำลองสำหรับการ Rebalance (สมมติขยับกรอบราคาขึ้น 10 Ticks)
        print("[\u23f3] Simulating Rebalance Parameters (Shift Up +10 Ticks)...")
        mock_params = RebalanceParams(
            token_id=token_id,
            new_tick_lower=inventory['tickLower'] + 10, 
            new_tick_upper=inventory['tickUpper'] + 10,
            token0_address=inventory['token0'],
            token1_address=inventory['token1'],
            fee_tier=inventory['fee'],
            amount0_desired=1000000, # ตัวเลขจำลอง
            amount1_desired=1000000, # ตัวเลขจำลอง
            deadline=int(time.time()) + 600, # +10 นาที
            slippage_tolerance=0.005 # เผื่อ Slippage 0.5%
        )

        # 4. รัน Flow การส่งคำสั่งแบบ Dry Run
        print("[\u23f3] Executing Dry Run Flow (Decrease -> Collect -> Mint)...")
        print("-" * 65)
        
        result = controller.execute_rebalance(mock_params)
        
        print("-" * 65)
        if result:
            print("✅ [SUCCESS] GHOST RUN COMPLETED!")
            print("บอทสามารถจำลองการอ่านค่า ถอนทุน เก็บค่าธรรมเนียม และเปิดพอร์ตใหม่ได้ครบถ้วนโดยไม่มี Error")
        else:
            print("❌ [FAILED] GHOST RUN FAILED: พบข้อผิดพลาดในระบบจำลอง")

    except Exception as e:
        print(f"🚨 [CRITICAL ERROR] เกิดข้อผิดพลาดระหว่างรัน: {e}")

    print("="*65)
    print("💡 Note: การรันทดสอบนี้ปลอดภัย 100% ไม่มีการใช้ Private Key หรือหักค่า Gas ใดๆ")

if __name__ == "__main__":
    run_ghost_test()