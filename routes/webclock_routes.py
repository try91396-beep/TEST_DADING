import io
import pandas as pd
import bcrypt
from flask import Blueprint, render_template, request, jsonify, session, send_file, redirect, url_for, flash
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

# ==========================================
# ⏱️ 員工功能：打卡首頁與打卡動作
# ==========================================

@webclock_bp.route('/', methods=['GET'])
@login_required
def index():
    current_month = datetime.now().strftime('%Y-%m')
    return render_template('webclock.html', current_month=current_month)

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

# ==========================================
# 💰 員工薪資與時薪設定 (限 Admin)
# ==========================================

@webclock_bp.route('/salaries', methods=['GET', 'POST'])
@role_required('admin')  # 限制只有 admin 權限可進入
def manage_salaries():
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        try:
            user_id = request.form.get('user_id')
            hourly_wage = request.form.get('hourly_wage', 0)
            
            cur.execute("UPDATE users SET hourly_wage = %s WHERE id = %s", (hourly_wage, user_id))
            conn.commit()
            flash('✅ 薪資設定已成功更新！', 'success')
        except Exception as e:
            flash(f'❌ 更新失敗：{str(e)}', 'danger')
        return redirect(url_for('webclock.manage_salaries'))

    # 取得所有員工名單 (包含管理員自己，因為管理員也需要打卡與設定)
    cur.execute("SELECT id, username, role, hourly_wage FROM users ORDER BY role ASC, id ASC")
    users = [{'id': row[0], 'username': row[1], 'role': row[2], 'hourly_wage': row[3]} for row in cur.fetchall()]
    
    cur.close()
    conn.close()
    
    return render_template('salaries.html', users=users)

# ==========================================
# 📋 管理員功能：審核申請與薪資匯出
# ==========================================

@webclock_bp.route('/admin/requests/<int:req_id>/approve', methods=['POST'])
@role_required('admin')
def approve_request(req_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT user_id, request_type, target_date, start_time, end_time FROM attendance_requests WHERE id = %s", (req_id,))
    req = cur.fetchone()
    
    if req and req[1] == 'missed_punch':
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

@webclock_bp.route('/admin/export_salary', methods=['GET'])
@role_required('admin')
def export_salary():
    year_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    
    conn = get_db_connection()
    query = """
        SELECT u.username, u.salary_type, u.hourly_wage, u.monthly_wage, 
               COALESCE(SUM(c.work_hours), 0) as total_hours
        FROM users u
        LEFT JOIN clock_records c ON u.id = c.user_id AND TO_CHAR(c.work_date, 'YYYY-MM') = %s
        GROUP BY u.id
    """
    df = pd.read_sql_query(query, conn, params=(year_month,))
    conn.close()
    
    def calculate_pay(row):
        if row.get('salary_type') == 'hourly':
            return row['total_hours'] * row.get('hourly_wage', 0)
        else:
            monthly_wage = row.get('monthly_wage') or 0
            leave_hours = max(0, 160 - row['total_hours'])
            hourly_rate = monthly_wage / 240 
            return monthly_wage - (leave_hours * hourly_rate)
            
    df['calculated_salary'] = df.apply(calculate_pay, axis=1)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Salary Report')
    output.seek(0)
    
    return send_file(output, as_attachment=True, download_name=f'Salary_{year_month}.xlsx')
