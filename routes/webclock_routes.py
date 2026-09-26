import io
import pandas as pd
import bcrypt # 記得引入 bcrypt 來驗證密碼
from flask import Blueprint, render_template, request, jsonify, session, send_file, redirect, url_for
from datetime import datetime
from database import get_db_connection
from utils import login_required, role_required

webclock_bp = Blueprint('webclock', __name__)

# ==========================================
# 🛡️ 打卡系統專屬登入與登出
# ==========================================

@webclock_bp.route('/login', methods=['GET', 'POST'])
def login():
    """處理打卡系統登入"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            return render_template('login.html', error="請輸入帳號和密碼")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, password_hash, role FROM users WHERE username = %s", (username,))
            user = cur.fetchone()
            
            if user:
                user_id, hashed_pw, role = user
                if bcrypt.checkpw(password.encode('utf-8'), hashed_pw.encode('utf-8')):
                    session['user_id'] = user_id
                    session['username'] = username
                    session['role'] = role
                    # 登入成功後，導向打卡首頁
                    return redirect(url_for('webclock.index'))
                else:
                    return render_template('login.html', error="密碼錯誤")
            else:
                return render_template('login.html', error="找不到此帳號")
                
        except Exception as e:
            print(f"Login Error: {e}")
            return render_template('login.html', error="系統發生錯誤，請稍後再試")
        finally:
            cur.close()
            conn.close()
            
    return render_template('login.html')

@webclock_bp.route('/logout')
def logout():
    """處理打卡系統登出"""
    session.clear() 
    return redirect(url_for('webclock.login'))

# --- 新增：打卡系統首頁 (負責渲染 HTML) ---
@webclock_bp.route('/', methods=['GET'])
@login_required
def index():
    # 取得當前月份，傳遞給前端供預設顯示用
    current_month = datetime.now().strftime('%Y-%m')
    return render_template('webclock.html', current_month=current_month)

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
        result = cur.fetchone()
        
        if result and result[0]:
            clock_in = result[0]
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
    
    if req and req[1] == 'missed_punch':
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
