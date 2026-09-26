import io
import pandas as pd
import bcrypt
from flask import Blueprint, render_template, request, jsonify, session, send_file, redirect, url_for, flash
from datetime import datetime, timedelta, timezone
from database import get_db_connection
from utils import login_required, role_required

# 定義台灣時區 (UTC+8)
TAIWAN_TZ = timezone(timedelta(hours=8))

def get_taiwan_now():
    """取得台灣當前時間 (無時區標籤，但時間數值已調整為 UTC+8)"""
    return datetime.now(TAIWAN_TZ).replace(tzinfo=None)

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
    current_month = get_taiwan_now().strftime('%Y-%m')
    return render_template('webclock.html', current_month=current_month)

@webclock_bp.route('/punch', methods=['POST'])
@login_required
def punch():
    user_id = session['user_id']
    action = request.json.get('action') # 'in' 或 'out'
    today = get_taiwan_now().date()
    now = get_taiwan_now()
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
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
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ==========================================
# 📊 員工個人明細 API
# ==========================================

@webclock_bp.route('/my_records', methods=['GET'])
@login_required
def my_records():
    """取得當前使用者的當月打卡與薪資紀錄"""
    user_id = session['user_id']
    month = request.args.get('month', get_taiwan_now().strftime('%Y-%m'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1. 取得員工時薪與薪資類型
        cur.execute("SELECT hourly_wage, salary_type, monthly_wage FROM users WHERE id = %s", (user_id,))
        user_row = cur.fetchone()
        hourly_wage = float(user_row[0]) if user_row and user_row[0] else 183.0
        
        # 2. 取得當月打卡與假勤紀錄
        cur.execute("""
            SELECT work_date, clock_in, clock_out, work_hours, status 
            FROM clock_records 
            WHERE user_id = %s AND TO_CHAR(work_date, 'YYYY-MM') = %s
            ORDER BY work_date DESC
        """, (user_id, month))
        
        records = []
        total_hours = 0.0
        for r in cur.fetchall():
            hrs = float(r[3] or 0)
            total_hours += hrs
            records.append({
                "work_date": str(r[0]),
                "clock_in": r[1].strftime('%H:%M:%S') if r[1] else None,
                "clock_out": r[2].strftime('%H:%M:%S') if r[2] else None,
                "work_hours": hrs,
                "status": r[4] or 'normal'
            })
            
        estimated_salary = int(total_hours * hourly_wage)
        
        return jsonify({
            "success": True,
            "total_hours": round(total_hours, 2),
            "estimated_salary": estimated_salary,
            "records": records
        })
    except Exception as e:
        print(f"Fetch Records Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@webclock_bp.route('/requests', methods=['POST'])
@login_required
def submit_request():
    """處理員工提交補打卡或請假申請"""
    user_id = session['user_id']
    data = request.json or {}
    
    request_type = data.get('request_type') # 'missed_punch' 或 'leave'
    target_date = data.get('target_date')
    reason = data.get('reason', '')
    leave_type = data.get('leave_type')
    start_time_str = data.get('start_time')
    end_time_str = data.get('end_time')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO attendance_requests 
            (user_id, request_type, target_date, start_time, end_time, reason, leave_type, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
        """, (user_id, request_type, target_date, start_time_str, end_time_str, reason, leave_type))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ==========================================
# 💰 員工薪資與時薪設定 (限 Admin)
# ==========================================

@webclock_bp.route('/salaries', methods=['GET', 'POST'])
@role_required('admin')
def manage_salaries():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if request.method == 'POST':
            user_id = request.form.get('user_id')
            hourly_wage = request.form.get('hourly_wage', 0)
            
            cur.execute("UPDATE users SET hourly_wage = %s WHERE id = %s", (hourly_wage, user_id))
            conn.commit()
            flash('✅ 薪資設定已成功更新！', 'success')
            return redirect(url_for('webclock.manage_salaries'))

        cur.execute("SELECT id, username, role, hourly_wage FROM users ORDER BY role ASC, id ASC")
        users = [{'id': row[0], 'username': row[1], 'role': row[2], 'hourly_wage': row[3]} for row in cur.fetchall()]
        return render_template('salaries.html', users=users)
    finally:
        cur.close()
        conn.close()

# ==========================================
# 📋 管理員功能：審核申請與薪資匯出
# ==========================================

@webclock_bp.route('/admin/requests/<int:req_id>/approve', methods=['POST'])
@role_required('admin')
def approve_request(req_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id, request_type, target_date, start_time, end_time, leave_type FROM attendance_requests WHERE id = %s", (req_id,))
        req = cur.fetchone()
        
        if req:
            user_id, req_type, target_date, start_time, end_time, leave_type = req
            
            if req_type == 'missed_punch' and start_time and end_time:
                hours = (end_time - start_time).total_seconds() / 3600
                cur.execute("""
                    INSERT INTO clock_records (user_id, work_date, clock_in, clock_out, work_hours, status)
                    VALUES (%s, %s, %s, %s, %s, 'missed_fixed')
                    ON CONFLICT (user_id, work_date) DO UPDATE 
                    SET clock_in = EXCLUDED.clock_in, clock_out = EXCLUDED.clock_out, work_hours = EXCLUDED.work_hours, status = 'missed_fixed'
                """, (user_id, target_date, start_time, end_time, round(hours, 2)))
                
            elif req_type == 'leave':
                # 處理請假登記
                hours = (end_time - start_time).total_seconds() / 3600 if start_time and end_time else 8.0
                cur.execute("""
                    INSERT INTO clock_records (user_id, work_date, work_hours, status)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, work_date) DO UPDATE 
                    SET status = EXCLUDED.status
                """, (user_id, target_date, 0, f'leave_{leave_type}'))
                
            cur.execute("UPDATE attendance_requests SET status = 'approved' WHERE id = %s", (req_id,))
            conn.commit()
            return jsonify({"success": True})
            
        return jsonify({"success": False, "error": "找不到該申請單"}), 404
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@webclock_bp.route('/admin/requests/<int:req_id>/reject', methods=['POST'])
@role_required('admin')
def reject_request(req_id):
    """駁回補打卡或請假申請"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE attendance_requests SET status = 'rejected' WHERE id = %s", (req_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@webclock_bp.route('/admin/export_salary', methods=['GET'])
@role_required('admin')
def export_salary():
    year_month = request.args.get('month', get_taiwan_now().strftime('%Y-%m'))
    
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
            return row['total_hours'] * (row.get('hourly_wage') or 0)
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

# ==========================================
# 📋 管理員功能：查詢員工薪資
# ==========================================

@webclock_bp.route('/admin/records', methods=['GET'])
def admin_records():
    # 權限檢查
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'error': '權限不足'}), 403

    month = request.args.get('month')        # e.g., '2026-09'
    user_id = request.args.get('user_id')    # e.g., 'bobo123'

    # TODO: 替換為你的資料庫查詢邏輯 (以 SQLAlchemy 為例)
    # query = AttendanceRecord.query
    # if month:
    #     query = query.filter(AttendanceRecord.work_date.like(f"{month}%"))
    # if user_id:
    #     query = query.filter(AttendanceRecord.username.like(f"%{user_id}%"))
    # records = query.all()

    # 假資料範例輸出
    records_data = [
        {
            "username": user_id or "bobo123",
            "work_date": f"{month}-01" if month else "2026-09-01",
            "clock_in": "09:00:00",
            "clock_out": "18:00:00",
            "work_hours": 8.0
        }
    ]

    return jsonify({
        'success': True,
        'records': records_data
    })
