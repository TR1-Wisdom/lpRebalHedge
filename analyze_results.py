"""
analyze_results.py
สคริปต์สำหรับอ่านไฟล์ CSV จาก Optimizer และวาดกราฟ Dashboard วิเคราะห์ความคุ้มค่า

อัปเดต: ขยาย Dashboard เป็น 6 ช่อง (2x3 Grid) 
เพิ่มการไขว้ตัวแปร Hedge_Threshold จับคู่กับ Range และ Rebalance
"""

import os
import glob
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns 

def get_latest_csv():
    os.makedirs('results', exist_ok=True)
    list_of_files = glob.glob(os.path.join('results', 'optimization_results_*.csv'))
    
    if not list_of_files:
        return None
    latest_file = max(list_of_files, key=os.path.getctime)
    return latest_file

def visualize_results():
    file_path = get_latest_csv()
    if not file_path:
        print("🚨 ไม่พบไฟล์ optimization_results_*.csv ในโฟลเดอร์ results/")
        print("💡 กรุณารัน optimizer.py ก่อนอย่างน้อย 1 ครั้งครับ")
        return

    print(f"📊 กำลังโหลดข้อมูลและสร้าง Ultimate Dashboard จาก: {file_path}")
    df = pd.read_csv(file_path)

    df_safe = df[df['Margin_Calls'] == 0].copy()
    df_dead = df[df['Margin_Calls'] > 0].copy()

    if df_safe.empty:
        print("⚠️ ไม่มี Config ใดเลยที่รอดจาก Margin Call ไม่สามารถวาดกราฟวิเคราะห์ได้")
        return

    try:
        sns.set_theme(style="whitegrid")
    except ImportError:
        plt.style.use('ggplot')

    # ขยายขนาด Canvas ให้กว้างขึ้นเพื่อรองรับ 6 กราฟ (2 แถว x 3 คอลัมน์)
    fig = plt.figure(figsize=(20, 12))
    fig.suptitle('Quant Lab: Ultimate Optimization Dashboard', fontsize=20, fontweight='bold', y=0.98)

    # ==========================================
    # แถวที่ 1: วิเคราะห์ภาพรวมความเสี่ยงและผลตอบแทน
    # ==========================================
    
    # 1.1: Risk vs Reward (Max DD vs CAGR)
    ax1 = plt.subplot(2, 3, 1)
    if not df_dead.empty:
        ax1.scatter(df_dead['Max_DD_%'], df_dead['CAGR_%'], color='red', alpha=0.15, label='Liquidated')
    
    scatter = ax1.scatter(df_safe['Max_DD_%'], df_safe['CAGR_%'], 
                          c=df_safe['Min_CEX_Margin'], cmap='viridis', 
                          s=80, alpha=0.8, edgecolors='white', label='Safe')
    
    ax1.set_title('Risk vs Reward', fontweight='bold')
    ax1.set_xlabel('Max Drawdown (%)')
    ax1.set_ylabel('Annual CAGR (%)')
    ax1.invert_xaxis() 
    fig.colorbar(scatter, ax=ax1, label='Min CEX Margin ($)')
    ax1.legend()

    # 1.2: Min CEX Margin vs CAGR (หาจุดคุ้มทุนความเสี่ยง)
    ax2 = plt.subplot(2, 3, 2)
    ax2.scatter(df_safe['Min_CEX_Margin'], df_safe['CAGR_%'], color='dodgerblue', alpha=0.7)
    ax2.set_title('Safety Buffer vs Profitability', fontweight='bold')
    ax2.set_xlabel('Lowest CEX Margin Reached ($)')
    ax2.set_ylabel('CAGR (%)')
    ax2.axvline(x=1000, color='orange', linestyle='--', label='Warning ($1,000)')
    ax2.legend()

    # 1.3: Impact of Range Width
    ax3 = plt.subplot(2, 3, 3)
    sns.boxplot(x='Range_Width', y='CAGR_%', data=df_safe, ax=ax3, palette='Blues')
    ax3.set_title('Impact of LP Range Width', fontweight='bold')
    ax3.set_xlabel('LP Range Width (±%)')
    ax3.set_ylabel('CAGR (%)')

    # ==========================================
    # แถวที่ 2: Heatmap ไขว้ตัวแปร (หา Sweet Spot)
    # ==========================================

    # 2.1: Range Width vs Rebalance Threshold (การจัดการ LP เพียวๆ)
    ax4 = plt.subplot(2, 3, 4)
    pivot1 = df_safe.pivot_table(values='CAGR_%', index='Range_Width', columns='Rebal_Thresh', aggfunc='mean')
    sns.heatmap(pivot1, annot=True, fmt=".1f", cmap="YlGnBu", ax=ax4)
    ax4.set_title('Avg CAGR: Range vs Rebalance', fontweight='bold')
    ax4.set_xlabel('Rebalance Threshold')
    ax4.set_ylabel('Range Width')

    # 2.2: Range Width vs Hedge Threshold (สมดุลระหว่าง LP กับ CEX)
    ax5 = plt.subplot(2, 3, 5)
    pivot2 = df_safe.pivot_table(values='CAGR_%', index='Range_Width', columns='Hedge_Thresh', aggfunc='mean')
    sns.heatmap(pivot2, annot=True, fmt=".1f", cmap="Purples", ax=ax5)
    ax5.set_title('Avg CAGR: Range vs Hedge Thresh', fontweight='bold')
    ax5.set_xlabel('Hedge Threshold')
    ax5.set_ylabel('Range Width')

    # 2.3: Rebalance Threshold vs Hedge Threshold (สงครามค่าธรรมเนียม: Gas vs Taker Fee)
    ax6 = plt.subplot(2, 3, 6)
    pivot3 = df_safe.pivot_table(values='CAGR_%', index='Rebal_Thresh', columns='Hedge_Thresh', aggfunc='mean')
    sns.heatmap(pivot3, annot=True, fmt=".1f", cmap="Greens", ax=ax6)
    ax6.set_title('Avg CAGR: Rebalance vs Hedge Thresh', fontweight='bold')
    ax6.set_xlabel('Hedge Threshold (Perp)')
    ax6.set_ylabel('Rebalance Threshold (LP)')

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    
    img_name = file_path.replace('.csv', '_ultimate_dashboard.png')
    plt.savefig(img_name, dpi=300)
    print(f"✅ บันทึกรูปภาพ Ultimate Dashboard สำเร็จ: {img_name}")
    
    plt.show()

if __name__ == "__main__":
    visualize_results()