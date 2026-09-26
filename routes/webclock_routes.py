import io
import pandas as pd
from flask import Blueprint, render_template, request, jsonify, session, send_file
from datetime import datetime
from database import get_db_connection
from utils import login_required, role_required # 假設引入你定義的權限裝飾器

webclock_bp = Blueprint('/webclock', __name__)

# --- 員工功能：打卡 ---
@webclock_bp.route('/punch', methods=['POST'])
@login_required
def punch():
    user_id = session['user_id']
    action = request.json.get('action') # 'in' 或 'out'
    today = datetime.now().date()
    now = datetime.now()
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    if action == 'in':
        cur.execute("""
            INSERT INTO clock_records (user_id, work_date, clock_in) 
            VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING id
        """, (user_id, today, now))
    elif action == 'out':
        # 計算工時
        cur.execute("SELECT clock_in FROM clock_records WHERE user_id = %s AND work_date = %s", (user_id, today))
        clock_in = cur.fetchone()[0]
        if clock_in:
            hours = (now - clock_in).total_seconds() / 3600
            cur.execute("""
                UPDATE clock_records SET clock_out = %s, work_hours = %s 
                WHERE user_id = %s AND work_date = %s
            """, (now, round(hours, 2), user_id, today))
            
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})

# --- 管理員功能：審核申請 ---
@webclock_bp.route('/admin/requests/<int:req_id>/approve', methods=['POST'])
@role_required('admin')
def approve_request(req_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 取得申請資料
    cur.execute("SELECT user_id, request_type, target_date, start_time, end_time FROM attendance_requests WHERE id = %s", (req_id,))
    req = cur.fetchone()
    
    if req[1] == 'missed_punch':
        # 補打卡：寫入或更新打卡紀錄
        hours = (req[4] - req[3]).total_seconds() / 3600
        cur.execute("""
            INSERT INTO clock_records (user_id, work_date, clock_in, clock_out, work_hours, status)
            VALUES (%s, %s, %s, %s, %s, 'missed_fixed')
            ON CONFLICT (id) DO UPDATE SET clock_in = EXCLUDED.clock_in, clock_out = EXCLUDED.clock_out, work_hours = EXCLUDED.work_hours
        """, (req[0], req[2], req[3], req[4], round(hours, 2)))
        
    cur.execute("UPDATE attendance_requests SET status = 'approved' WHERE id = %s", (req_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True})

# --- 薪資計算與 Excel 匯出 ---
@webclock_bp.route('/admin/export_salary', methods=['GET'])
@role_required('admin')
def export_salary():
    year_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    
    conn = get_db_connection()
    # 抓取該月份所有員工的打卡時數與薪資設定
    query = """
        SELECT u.username, u.salary_type, u.hourly_wage, u.monthly_wage, 
               COALESCE(SUM(c.work_hours), 0) as total_hours
        FROM users u
        LEFT JOIN clock_records c ON u.id = c.user_id AND TO_CHAR(c.work_date, 'YYYY-MM') = %s
        GROUP BY u.id
    """
    df = pd.read_sql_query(query, conn, params=(year_month,))
    conn.close()
    
    # 自動計算薪資邏輯 (可依勞基法擴充)
    def calculate_pay(row):
        if row['salary_type'] == 'hourly':
            return row['total_hours'] * row['hourly_wage']
        else:
            # 月薪制：假設每月應上班 160 小時，請假扣薪算法
            leave_hours = max(0, 160 - row['total_hours'])
            hourly_rate = row['monthly_wage'] / 240 # 勞基法日薪/8
            return row['monthly_wage'] - (leave_hours * hourly_rate)
            
    df['calculated_salary'] = df.apply(calculate_pay, axis=1)
    
    # 匯出為 Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Salary Report')
    output.seek(0)
    
    return send_file(output, as_attachment=True, download_name=f'Salary_{year_month}.xlsx')
