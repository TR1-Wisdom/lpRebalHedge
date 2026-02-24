Release v2.0.0 - Ultimate Delta Hedge Simulation Suite

🎯 เป้าหมายของเวอร์ชันนี้

เป็น Baseline ที่เสถียรที่สุดสำหรับการทดสอบกลยุทธ์ Inventory-based LP + Smart Hedge ก่อนเริ่มพัฒนาโมดูล Live Trading (Direct Control)

🚀 ฟีเจอร์หลัก (Key Features)

Stable Engine v1.4.3: - แก้ไขสูตร Margin Snapshot ให้รวม Unrealized PnL แม่นยำ 100%

ระบบ Emergency Rescue ดึงกำไร LP ช่วย CEX แบบจำกัดวงเงิน (Circuit Breaker)

ป้องกัน Infinite Rescue Loop ที่ทำให้พอร์ตพัง

Inventory Tracking (v1.5.1 UI):

ติดตามค่า Residual Delta (ส่วนต่าง Long-Short) แบบเรียลไทม์

หน้าจอ Dashboard แบบ Dual-Charts ชำแหละไส้ในพอร์ต (LP vs Perp Component)

Advanced Logging:

ระบบบันทึกเหตุการณ์ (Event Logging) ลงใน CSV ครบถ้วน (Rebalance, Hedge, Rescue, Funding)

ระบบ Cross-Margin Sweep รักษาสมดุลทุนอัตโนมัติ

🛡️ สถิติความสำเร็จ (Simulation Baseline)

CAGR: ~64% (ที่ Vol 70%, Range ±10%)

Max Drawdown: < 1.0%

System Health: No Margin Calls (with Emergency Rescue enabled)
