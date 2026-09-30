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
# 🧮 假勤與薪資計算輔助函式
# ==========================================

def calculate_user_salary(salary_type, hourly_wage, monthly_wage, records):
    """
    依據出勤與請假紀錄計算試算薪資與時數統計
    records 格式為: [(work_hours, status), ...]
    """
    hourly_wage = float(hourly_wage) if hourly_wage is not None else 183.0
    monthly_wage = float(monthly_wage) if monthly_wage is not None else 27470.0
    hourly_rate_for_monthly = monthly_wage / 240.0  # 月薪制之時薪基準 (每月以 30天/240小時 計算)

    work_hours_total = 0.0        # 實際出勤時數
    full_pay_leave_hours = 0.0    # 特休、公假、婚喪假 (全薪)
    half_pay_leave_hours = 0.0    # 病假 (半薪)
    unpaid_leave_hours = 0.0      # 事假 (無薪)

    for hrs, status in records:
        hrs = float(hrs or 0)
        status_str = str(status or '').lower()

        if 'sick' in status_str or '病假' in status_str:
            half_pay_leave_hours += hrs
        elif 'personal' in status_str or '事假' in status_str:
            unpaid_leave_hours += hrs
        elif 'annual' in status_str or 'official' in status_str or '特休' in status_str or '公假' in status_str or 'leave' in status_str:
            full_pay_leave_hours += hrs
        else:
            work_hours_total += hrs

    if salary_type == 'hourly':
        # 時薪制：(出勤工時 * 時薪) + (全薪假時數 * 時薪) + (半薪假時數 * 時薪 * 0.5)
        estimated_salary = (work_hours_total * hourly_wage) + \
                           (full_pay_leave_hours * hourly_wage) + \
                           (half_pay_leave_hours * hourly_wage * 0.5)
    else:
        # 月薪制：底薪 - (半薪假扣款) - (事假無薪扣款)
        sick_deduction = half_pay_leave_hours * hourly_rate_for_monthly * 0.5
        personal_deduction = unpaid_leave_hours * hourly_rate_for_monthly * 1.0
        estimated_salary = monthly_wage - sick_deduction - personal_deduction

    total_recorded_hours = work_hours_total + full_pay_leave_hours + half_pay_leave_hours + unpaid_leave_hours

    return round(total_recorded_hours, 2), int(round(estimated_salary))

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
    """打卡首頁 (若為管理員則一併查詢並傳送待審核申請單)"""
    current_month = get_taiwan_now().strftime('%Y-%m')
    pending_requests = []

    if session.get('role') == 'admin':
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT r.id, u.username, r.request_type, r.target_date, 
                       r.start_time, r.end_time, r.reason, r.leave_type, r.status, r.user_id
                FROM attendance_requests r
                JOIN users u ON r.user_id = u.id
                WHERE r.status = 'pending'
                ORDER BY r.id DESC
            """)
            rows = cur.fetchall()
            for row in rows:
                req_id, username, req_type, target_date, start_time, end_time, reason, leave_type, status, user_id = row
                
                start_str = start_time.strftime('%H:%M:%S') if isinstance(start_time, datetime) else (str(start_time)[11:19] if start_time else '')
                end_str = end_time.strftime('%H:%M:%S') if isinstance(end_time, datetime) else (str(end_time)[11:19] if end_time else '')
                
                pending_requests.append({
                    'id': req_id,
                    'username': username,
                    'user_id': username,
                    'request_type': req_type,
                    'target_date': str(target_date) if target_date else '',
                    'start_time': start_str,
                    'end_time': end_str,
                    'reason': reason or '',
                    'leave_type': leave_type or '',
                    'status': status
                })
        except Exception as e:
            print(f"Fetch Index Pending Requests Error: {e}")
        finally:
            cur.close()
            conn.close()

    return render_template('webclock.html', current_month=current_month, pending_requests=pending_requests)

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
                SELECT id, clock_in 
                FROM clock_records 
                WHERE user_id = %s AND clock_out IS NULL 
                ORDER BY clock_in DESC 
                LIMIT 1
            """, (user_id,))
            active_record = cur.fetchone()
            
            if active_record:
                return jsonify({
                    "success": False, 
                    "error": "您目前已有進行中的班次（尚未打下班卡），請先打下班卡後再重新上班！"
                }), 400
            
            cur.execute("""
                INSERT INTO clock_records (user_id, work_date, clock_in) 
                VALUES (%s, %s, %s) RETURNING id
            """, (user_id, today, now))
            
        elif action == 'out':
            cur.execute("""
                SELECT id, clock_in 
                FROM clock_records 
                WHERE user_id = %s AND clock_out IS NULL 
                ORDER BY clock_in DESC 
                LIMIT 1
            """, (user_id,))
            
            result = cur.fetchone()
            
            if result:
                record_id, clock_in = result[0], result[1]
                hours = (now - clock_in).total_seconds() / 3600
                
                if hours > 36:
                    return jsonify({
                        "success": False, 
                        "error": "距離上次打卡已超過 36 小時，請聯絡管理員或申請補打卡！"
                    }), 400

                cur.execute("""
                    UPDATE clock_records 
                    SET clock_out = %s, work_hours = %s 
                    WHERE id = %s
                """, (now, round(hours, 2), record_id))
            else:
                return jsonify({"success": False, "error": "找不到進行中的上班打卡紀錄！"}), 400
                
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
    user_id = session.get('user_id')
    month = request.args.get('month', get_taiwan_now().strftime('%Y-%m'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT hourly_wage, salary_type, monthly_wage FROM users WHERE id = %s", (user_id,))
        user_row = cur.fetchone()
        
        salary_type = user_row[1] if user_row and user_row[1] else 'hourly'
        hourly_wage = float(user_row[0]) if user_row and user_row[0] is not None else 183.0
        monthly_wage = float(user_row[2]) if user_row and user_row[2] is not None else 27470.0
        
        cur.execute("""
            SELECT work_date, clock_in, clock_out, work_hours, status 
            FROM clock_records 
            WHERE user_id = %s AND TO_CHAR(work_date, 'YYYY-MM') = %s
            ORDER BY work_date DESC
        """, (user_id, month))
        
        raw_records = cur.fetchall()
        records_display = []
        calc_tuples = []

        for r in raw_records:
            hrs = float(r[3] or 0)
            status = r[4] or 'normal'
            calc_tuples.append((hrs, status))
            records_display.append({
                "work_date": str(r[0]),
                "clock_in": r[1].strftime('%H:%M:%S') if isinstance(r[1], datetime) else (str(r[1]) if r[1] else None),
                "clock_out": r[2].strftime('%H:%M:%S') if isinstance(r[2], datetime) else (str(r[2]) if r[2] else None),
                "work_hours": hrs,
                "status": status
            })
            
        total_hours, estimated_salary = calculate_user_salary(salary_type, hourly_wage, monthly_wage, calc_tuples)
        
        return jsonify({
            "success": True,
            "total_hours": total_hours,
            "estimated_salary": estimated_salary,
            "records": records_display
        })
    except Exception as e:
        print(f"Fetch Records Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ==========================================
# 📋 申請單提交與審核管理 API
# ==========================================

@webclock_bp.route('/requests', methods=['POST'])
@login_required
def submit_request():
    """處理員工提交補打卡或請假申請"""
    user_id = session['user_id']
    data = request.json or {}
    
    request_type = data.get('request_type')  # 'missed_punch' 或 'leave'
    target_date = data.get('target_date') or None
    reason = data.get('reason', '')
    leave_type = data.get('leave_type') or None
    
    start_time_str = data.get('start_time') if data.get('start_time') else None
    end_time_str = data.get('end_time') if data.get('end_time') else None
    
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
        print(f"Submit Request Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@webclock_bp.route('/admin/requests', methods=['GET'])
@login_required
@role_required('admin')
def get_admin_requests():
    """管理員取得待審核與歷史申請紀錄列表 API"""
    status_filter = request.args.get('status', 'pending')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        sql = """
            SELECT r.id, u.username, r.request_type, r.target_date, 
                   r.start_time, r.end_time, r.reason, r.leave_type, r.status
            FROM attendance_requests r
            JOIN users u ON r.user_id = u.id
            WHERE 1=1
        """
        params = []
        if status_filter and status_filter != 'all':
            sql += " AND r.status = %s"
            params.append(status_filter)
            
        sql += " ORDER BY r.id DESC"
        
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        
        requests_list = []
        for row in rows:
            req_id, username, req_type, target_date, start_time, end_time, reason, leave_type, status = row
            
            start_str = start_time.strftime('%Y-%m-%d %H:%M:%S') if isinstance(start_time, datetime) else (str(start_time) if start_time else '')
            end_str = end_time.strftime('%Y-%m-%d %H:%M:%S') if isinstance(end_time, datetime) else (str(end_time) if end_time else '')
            
            requests_list.append({
                'id': req_id,
                'username': username,
                'request_type': req_type,
                'target_date': str(target_date) if target_date else '',
                'start_time': start_str,
                'end_time': end_str,
                'reason': reason or '',
                'leave_type': leave_type or '',
                'status': status
            })
            
        return jsonify({'success': True, 'requests': requests_list})
    except Exception as e:
        print(f"Fetch Admin Requests Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@webclock_bp.route('/admin/requests/<int:req_id>/approve', methods=['POST'])
@login_required
@role_required('admin')
def approve_request(req_id):
    """同意補打卡或請假申請」"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id, request_type, target_date, start_time, end_time, leave_type FROM attendance_requests WHERE id = %s", (req_id,))
        req = cur.fetchone()
        
        if req:
            user_id, req_type, target_date, start_time, end_time, leave_type = req
            
            if req_type == 'missed_punch' and start_time and end_time:
                if isinstance(start_time, str):
                    start_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
                if isinstance(end_time, str):
                    end_time = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
                    
                hours = (end_time - start_time).total_seconds() / 3600
                
                cur.execute("""
                    INSERT INTO clock_records (user_id, work_date, clock_in, clock_out, work_hours, status)
                    VALUES (%s, %s, %s, %s, %s, 'missed_fixed')
                    ON CONFLICT (user_id, work_date) DO UPDATE 
                    SET clock_in = EXCLUDED.clock_in, clock_out = EXCLUDED.clock_out, work_hours = EXCLUDED.work_hours, status = 'missed_fixed'
                """, (user_id, target_date, start_time, end_time, round(hours, 2)))
                
            elif req_type == 'leave':
                # 計算請假起迄時間與請假總時數
                hours = 8.0
                target_dt = datetime.strptime(str(target_date), '%Y-%m-%d') if isinstance(target_date, str) else target_date

                if start_time and end_time:
                    if isinstance(start_time, str):
                        start_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
                    if isinstance(end_time, str):
                        end_time = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
                    hours = (end_time - start_time).total_seconds() / 3600
                elif start_time and not end_time:
                    if isinstance(start_time, str):
                        start_time = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
                    end_time = start_time + timedelta(hours=8)
                    hours = 8.0
                else:
                    # 預設時間帶入當天 09:00 至 18:00
                    start_time = datetime.combine(target_dt, datetime.min.time()).replace(hour=9, minute=0)
                    end_time = datetime.combine(target_dt, datetime.min.time()).replace(hour=18, minute=0)
                    hours = 8.0

                status_str = f'leave_{leave_type}' if leave_type else 'leave'

                # 寫入出勤紀錄（包含起迄時間、請假時數、假別狀態）
                cur.execute("""
                    INSERT INTO clock_records (user_id, work_date, clock_in, clock_out, work_hours, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, work_date) DO UPDATE 
                    SET clock_in = EXCLUDED.clock_in, 
                        clock_out = EXCLUDED.clock_out, 
                        work_hours = EXCLUDED.work_hours, 
                        status = EXCLUDED.status
                """, (user_id, target_date, start_time, end_time, round(hours, 2), status_str))
                
            cur.execute("UPDATE attendance_requests SET status = 'approved' WHERE id = %s", (req_id,))
            conn.commit()
            return jsonify({"success": True})
            
        return jsonify({"success": False, "error": "找不到該申請單"}), 404
    except Exception as e:
        conn.rollback()
        print(f"Approve Request Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@webclock_bp.route('/admin/requests/<int:req_id>/reject', methods=['POST'])
@login_required
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

# ==========================================
# 💰 員工薪資與時薪設定 (限 Admin)
# ==========================================

@webclock_bp.route('/salaries', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def manage_salaries():
    """管理員設定員工薪資與權限"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if request.method == 'POST':
            user_id = request.form.get('user_id')
            salary_type = request.form.get('salary_type')
            
            raw_hourly = request.form.get('hourly_wage')
            raw_monthly = request.form.get('monthly_wage')

            if salary_type == 'monthly':
                hourly_wage = None
                try:
                    monthly_wage = float(raw_monthly) if raw_monthly else 27470
                except (ValueError, TypeError):
                    monthly_wage = 27470
            else:
                salary_type = 'hourly'
                monthly_wage = None
                try:
                    hourly_wage = float(raw_hourly) if raw_hourly else 183
                except (ValueError, TypeError):
                    hourly_wage = 183

            cur.execute("""
                UPDATE users 
                SET salary_type = %s,
                    hourly_wage = %s,
                    monthly_wage = %s
                WHERE id = %s
            """, (salary_type, hourly_wage, monthly_wage, user_id))
            
            conn.commit()
            flash("薪資設定已成功更新！", "success")
            return redirect(url_for('webclock.manage_salaries'))

        cur.execute("""
            SELECT id, username, role, salary_type, hourly_wage, monthly_wage 
            FROM users 
            ORDER BY id ASC
        """)
        rows = cur.fetchall()
        
        users = []
        for r in rows:
            if isinstance(r, dict):
                users.append(r)
            else:
                users.append({
                    'id': r[0],
                    'username': r[1],
                    'role': r[2],
                    'salary_type': r[3] or 'hourly',
                    'hourly_wage': r[4],
                    'monthly_wage': r[5]
                })

        return render_template('salaries.html', users=users)

    except Exception as e:
        if request.method == 'POST':
            conn.rollback()
            flash(f"儲存失敗：{e}", "danger")
            return redirect(url_for('webclock.manage_salaries'))
        else:
            flash(f"資料載入失敗：{e}", "danger")
            return render_template('salaries.html', users=[])
    finally:
        cur.close()
        conn.close()

# ==========================================
# 📊 管理員功能：薪資報表匯出與出勤查詢
# ==========================================

@webclock_bp.route('/admin/export_salary', methods=['GET'])
@login_required
@role_required('admin')
def export_salary():
    """匯出當月薪資報表 Excel"""
    year_month = request.args.get('month', get_taiwan_now().strftime('%Y-%m'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, username, salary_type, hourly_wage, monthly_wage FROM users ORDER BY id ASC")
        users = cur.fetchall()

        data = []
        for u in users:
            u_id, username, salary_type, hourly_wage, monthly_wage = u
            cur.execute("""
                SELECT work_hours, status 
                FROM clock_records 
                WHERE user_id = %s AND TO_CHAR(work_date, 'YYYY-MM') = %s
            """, (u_id, year_month))
            user_records = cur.fetchall()

            total_hrs, est_pay = calculate_user_salary(salary_type, hourly_wage, monthly_wage, user_records)

            data.append({
                '員工姓名': username,
                '計薪方式': '時薪' if salary_type == 'hourly' else '月薪',
                '時薪/月薪': hourly_wage if salary_type == 'hourly' else monthly_wage,
                '當月累計時數': total_hrs,
                '試算應發薪資': est_pay
            })

        df = pd.DataFrame(data)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='薪資明細表')
        output.seek(0)

        return send_file(output, as_attachment=True, download_name=f'Salary_{year_month}.xlsx')
    finally:
        cur.close()
        conn.close()

@webclock_bp.route('/admin/records', methods=['GET'])
@login_required
@role_required('admin')
def admin_records():
    """管理員查詢員工出勤紀錄"""
    month = request.args.get('month', get_taiwan_now().strftime('%Y-%m'))
    search_query = request.args.get('user_id', '').strip()

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        sql = """
            SELECT u.username, c.work_date, c.clock_in, c.clock_out, c.work_hours, c.status
            FROM clock_records c
            JOIN users u ON c.user_id = u.id
            WHERE 1=1
        """
        params = []

        if month:
            sql += " AND TO_CHAR(c.work_date, 'YYYY-MM') = %s"
            params.append(month)

        if search_query and search_query != 'all':
            sql += " AND (u.username ILIKE %s OR CAST(u.id AS TEXT) = %s)"
            params.append(f"%{search_query}%")
            params.append(search_query)

        sql += " ORDER BY c.work_date DESC, c.clock_in DESC"

        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

        records = []
        for row in rows:
            username, work_date, clock_in, clock_out, work_hours, status = row
            
            clock_in_str = clock_in.strftime('%H:%M:%S') if clock_in else '--:--:--'
            clock_out_str = clock_out.strftime('%H:%M:%S') if clock_out else '--:--:--'
            
            records.append({
                'username': username,
                'work_date': str(work_date),
                'clock_in': clock_in_str,
                'clock_out': clock_out_str,
                'status': status or '正常',
                'work_hours': round(float(work_hours or 0), 1)
            })

        return jsonify({'success': True, 'records': records})

    except Exception as e:
        print(f"Admin Records Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()
