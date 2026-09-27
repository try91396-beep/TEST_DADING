import io
import math
import pandas as pd
import bcrypt
from flask import Blueprint, render_template, request, jsonify, session, send_file, redirect, url_for, flash
from datetime import datetime, timedelta, time, timezone
from database import get_db_connection
from utils import login_required, role_required

# 定義台灣時區 (UTC+8)
TAIWAN_TZ = timezone(timedelta(hours=8))

def get_taiwan_now():
    """取得台灣當前時間 (無時區標籤)"""
    return datetime.now(TAIWAN_TZ).replace(tzinfo=None)

def parse_datetime_safe(val):
    """安全解析多種格式的 Datetime/Time 物件或字串"""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, time):
        return val
    if isinstance(val, str):
        val = val.strip()
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%H:%M:%S', '%H:%M'):
            try:
                dt = datetime.strptime(val, fmt)
                return dt if 'Y' in fmt else dt.time()
            except ValueError:
                continue
    return None

webclock_bp = Blueprint('webclock', __name__)

# ==========================================
# ⏱️ 5分鐘取整與工時/薪資計算邏輯
# ==========================================

def round_clock_in_5min(dt):
    """上班打卡：無條件進位 (Ceil) 至下一個 5 分鐘"""
    if not dt: return None
    minute = dt.minute
    second = dt.second
    if minute % 5 == 0 and second == 0:
        return dt.replace(second=0, microsecond=0)
    add_min = 5 - (minute % 5)
    rounded = dt + timedelta(minutes=add_min)
    return rounded.replace(second=0, microsecond=0)

def round_clock_out_5min(dt):
    """下班打卡：無條件捨去 (Floor) 至前一個 5 分鐘"""
    if not dt: return None
    minute = dt.minute
    minus_min = minute % 5
    rounded = dt - timedelta(minutes=minus_min)
    return rounded.replace(second=0, microsecond=0)

def calculate_net_work_hours(clock_in, clock_out, break_start_time=None, break_end_time=None):
    """
    計算實質工時：
    1. 採用 5分鐘進捨規則計算有效上班/下班時間
    2. 自動扣除休息/午休時間 (如 12:00 - 13:00)
    """
    clock_in = parse_datetime_safe(clock_in)
    clock_out = parse_datetime_safe(clock_out)

    if not clock_in or not clock_out or not isinstance(clock_in, datetime) or not isinstance(clock_out, datetime):
        return 0.0

    eff_in = round_clock_in_5min(clock_in)
    eff_out = round_clock_out_5min(clock_out)

    if eff_out <= eff_in:
        return 0.0

    total_seconds = (eff_out - eff_in).total_seconds()

    # 計算休息時間扣除
    break_seconds = 0.0
    if break_start_time and break_end_time:
        try:
            bs_time = parse_datetime_safe(break_start_time)
            be_time = parse_datetime_safe(break_end_time)

            if isinstance(bs_time, datetime): bs_time = bs_time.time()
            if isinstance(be_time, datetime): be_time = be_time.time()

            if isinstance(bs_time, time) and isinstance(be_time, time):
                bs_dt = datetime.combine(eff_in.date(), bs_time)
                be_dt = datetime.combine(eff_in.date(), be_time)

                overlap_start = max(eff_in, bs_dt)
                overlap_end = min(eff_out, be_dt)
                if overlap_end > overlap_start:
                    break_seconds = (overlap_end - overlap_start).total_seconds()
        except Exception as e:
            print(f"Break time parse error: {e}")

    net_seconds = max(0, total_seconds - break_seconds)
    return round(net_seconds / 3600.0, 2)

def calculate_user_salary(salary_type, hourly_wage, monthly_wage, records):
    """依據出勤與請假紀錄計算當月試算薪資"""
    hourly_wage = float(hourly_wage) if hourly_wage is not None else 183.0
    monthly_wage = float(monthly_wage) if monthly_wage is not None else 27470.0
    hourly_rate_monthly = monthly_wage / 240.0 

    work_hours_total = 0.0
    full_pay_leave_hours = 0.0
    half_pay_leave_hours = 0.0
    unpaid_leave_hours = 0.0

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
        estimated_salary = (work_hours_total * hourly_wage) + \
                           (full_pay_leave_hours * hourly_wage) + \
                           (half_pay_leave_hours * hourly_wage * 0.5)
    else:
        sick_deduction = half_pay_leave_hours * hourly_rate_monthly * 0.5
        personal_deduction = unpaid_leave_hours * hourly_rate_monthly * 1.0
        estimated_salary = monthly_wage - sick_deduction - personal_deduction

    total_hours = work_hours_total + full_pay_leave_hours + half_pay_leave_hours + unpaid_leave_hours
    return round(total_hours, 2), int(round(estimated_salary))

# ==========================================
# 🛡️ 登入與登出
# ==========================================

@webclock_bp.route('/login', methods=['GET', 'POST'])
def login():
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
            if user and bcrypt.checkpw(password.encode('utf-8'), user[1].encode('utf-8')):
                session['user_id'] = user[0]
                session['username'] = username
                session['role'] = user[2]
                return redirect(url_for('webclock.index'))
            else:
                return render_template('login.html', error="帳號或密碼錯誤")
        finally:
            cur.close()
            conn.close()
    return render_template('login.html')

@webclock_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('webclock.login'))

# ==========================================
# ⏱️ 首頁與即時打卡
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
                SELECT id FROM clock_records 
                WHERE user_id = %s AND clock_out IS NULL 
                ORDER BY clock_in DESC LIMIT 1
            """, (user_id,))
            if cur.fetchone():
                return jsonify({"success": False, "error": "您尚有進行中的班次，請先打下班卡！"}), 400
            
            cur.execute("""
                INSERT INTO clock_records (user_id, work_date, clock_in, break_start, break_end) 
                VALUES (%s, %s, %s, '12:00:00', '13:00:00')
                ON CONFLICT (user_id, work_date) DO UPDATE 
                SET clock_in = EXCLUDED.clock_in, clock_out = NULL, break_start = '12:00:00', break_end = '13:00:00'
            """, (user_id, today, now))
            
        elif action == 'out':
            cur.execute("""
                SELECT id, clock_in, break_start, break_end 
                FROM clock_records 
                WHERE user_id = %s AND clock_out IS NULL 
                ORDER BY clock_in DESC LIMIT 1
            """, (user_id,))
            rec = cur.fetchone()
            if rec:
                record_id, clock_in, b_start, b_end = rec
                net_hours = calculate_net_work_hours(clock_in, now, b_start, b_end)
                
                cur.execute("""
                    UPDATE clock_records 
                    SET clock_out = %s, work_hours = %s 
                    WHERE id = %s
                """, (now, net_hours, record_id))
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
# 📊 員工個人紀錄與打卡資料 API
# ==========================================

@webclock_bp.route('/my_records', methods=['GET'])
@login_required
def my_records():
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
            SELECT id, work_date, clock_in, clock_out, work_hours, status, break_start, break_end
            FROM clock_records 
            WHERE user_id = %s AND TO_CHAR(work_date, 'YYYY-MM') = %s
            ORDER BY work_date DESC, clock_in DESC
        """, (user_id, month))
        
        raw_records = cur.fetchall()
        records_display = []
        calc_tuples = []

        for r in raw_records:
            rec_id, work_date, c_in, c_out, hrs, status, b_s, b_e = r
            hrs = float(hrs or 0)
            status_str = status or 'normal'
            calc_tuples.append((hrs, status_str))
            
            records_display.append({
                "id": rec_id,
                "work_date": str(work_date),
                "clock_in": c_in.strftime('%H:%M:%S') if isinstance(c_in, datetime) else (str(c_in)[11:19] if c_in else '--:--:--'),
                "clock_out": c_out.strftime('%H:%M:%S') if isinstance(c_out, datetime) else (str(c_out)[11:19] if c_out else '--:--:--'),
                "work_hours": hrs,
                "status": status_str,
                "break_start": str(b_s) if b_s else '12:00:00',
                "break_end": str(b_e) if b_e else '13:00:00'
            })

        total_hours, estimated_salary = calculate_user_salary(salary_type, hourly_wage, monthly_wage, calc_tuples)

        return jsonify({
            "success": True,
            "total_hours": total_hours,
            "estimated_salary": estimated_salary,
            "records": records_display
        })
    finally:
        cur.close()
        conn.close()

# ==========================================
# 📋 申請提交與刪除打卡紀錄申請
# ==========================================

@webclock_bp.route('/requests', methods=['POST'])
@login_required
def submit_request():
    """處理補打卡、請假或刪除打卡紀錄申請"""
    applicant_id = session['user_id']
    data = request.json or {}
    
    req_type = data.get('request_type')  # 'missed_punch', 'leave', 'delete_record'
    target_date = data.get('target_date') or None
    reason = data.get('reason', '')
    leave_type = data.get('leave_type') or None
    
    start_time_str = data.get('start_time') or None
    end_time_str = data.get('end_time') or None
    break_start_str = data.get('break_start') or '12:00:00'
    break_end_str = data.get('break_end') or '13:00:00'
    
    original_record_id = data.get('original_record_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        orig_in, orig_out = None, None
        
        if req_type == 'delete_record' and original_record_id:
            cur.execute("SELECT work_date, clock_in, clock_out FROM clock_records WHERE id = %s", (original_record_id,))
            record_to_del = cur.fetchone()
            if record_to_del:
                target_date = record_to_del[0]
                orig_in = record_to_del[1]
                orig_out = record_to_del[2]
            else:
                return jsonify({"success": False, "error": "找不到欲刪除的打卡紀錄"}), 404

        cur.execute("""
            INSERT INTO attendance_requests 
            (user_id, request_type, target_date, start_time, end_time, break_start, break_end,
             reason, leave_type, status, original_record_id, original_clock_in, original_clock_out)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s)
        """, (applicant_id, req_type, target_date, start_time_str, end_time_str, break_start_str, break_end_str,
              reason, leave_type, original_record_id, orig_in, orig_out))
        
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ==========================================
# 📜 申請與審核履歷查詢 (Admin與Staff權限分離)
# ==========================================

@webclock_bp.route('/requests/history', methods=['GET'])
@login_required
def get_requests_history():
    user_id = session['user_id']
    role = session.get('role')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        sql = """
            SELECT r.id, applicant.username AS applicant_name, r.request_type, r.target_date,
                   r.start_time, r.end_time, r.break_start, r.break_end, r.reason, r.leave_type, 
                   r.status, reviewer.username AS reviewer_name, r.reviewed_at,
                   r.original_clock_in, r.original_clock_out
            FROM attendance_requests r
            JOIN users applicant ON r.user_id = applicant.id
            LEFT JOIN users reviewer ON r.reviewed_by = reviewer.id
        """
        params = []
        if role != 'admin':
            sql += " WHERE r.user_id = %s"
            params.append(user_id)
            
        sql += " ORDER BY r.id DESC"
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        
        requests_list = []
        for row in rows:
            req_id, app_name, req_type, target_date, s_time, e_time, b_s, b_e, reason, leave_type, status, rev_name, rev_at, orig_in, orig_out = row
            
            requests_list.append({
                'id': req_id,
                'applicant_name': app_name,
                'request_type': req_type,
                'target_date': str(target_date) if target_date else '',
                'start_time': str(s_time) if s_time else '',
                'end_time': str(e_time) if e_time else '',
                'break_start': str(b_s) if b_s else '',
                'break_end': str(b_e) if b_e else '',
                'reason': reason or '',
                'leave_type': leave_type or '',
                'status': status,
                'reviewer_name': rev_name or '待審核',
                'reviewed_at': rev_at.strftime('%Y-%m-%d %H:%M:%S') if rev_at else '',
                'original_clock_in': orig_in.strftime('%Y-%m-%d %H:%M:%S') if orig_in else '',
                'original_clock_out': orig_out.strftime('%Y-%m-%d %H:%M:%S') if orig_out else ''
            })
            
        return jsonify({'success': True, 'requests': requests_list})
    finally:
        cur.close()
        conn.close()

# ==========================================
# ⚖️ 管理員審核 API (批准與駁回)
# ==========================================

@webclock_bp.route('/admin/requests/<int:req_id>/approve', methods=['POST'])
@login_required
@role_required('admin')
def approve_request(req_id):
    """管理員同意申請 (更新打卡紀錄並寫入審核人資訊)"""
    admin_id = session['user_id']
    now = get_taiwan_now()

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT user_id, request_type, target_date, start_time, end_time, 
                   break_start, break_end, leave_type, original_record_id 
            FROM attendance_requests WHERE id = %s
        """, (req_id,))
        req = cur.fetchone()

        if not req:
            return jsonify({"success": False, "error": "找不到該申請單"}), 404

        applicant_id, req_type, target_date, s_time, e_time, b_start, b_end, leave_type, orig_rec_id = req

        # 1. 處理「刪除打卡紀錄」申請
        if req_type == 'delete_record' and orig_rec_id:
            cur.execute("DELETE FROM clock_records WHERE id = %s", (orig_rec_id,))

        # 2. 處理「補打卡」申請
        elif req_type == 'missed_punch':
            s_dt = parse_datetime_safe(s_time)
            e_dt = parse_datetime_safe(e_time)

            if isinstance(s_dt, time) and target_date:
                s_dt = datetime.combine(target_date, s_dt)
            if isinstance(e_dt, time) and target_date:
                e_dt = datetime.combine(target_date, e_dt)

            net_hours = calculate_net_work_hours(s_dt, e_dt, b_start, b_end)

            cur.execute("""
                INSERT INTO clock_records (user_id, work_date, clock_in, clock_out, break_start, break_end, work_hours, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'missed_fixed')
                ON CONFLICT (user_id, work_date) DO UPDATE 
                SET clock_in = EXCLUDED.clock_in, clock_out = EXCLUDED.clock_out,
                    break_start = EXCLUDED.break_start, break_end = EXCLUDED.break_end,
                    work_hours = EXCLUDED.work_hours, status = 'missed_fixed'
            """, (applicant_id, target_date, s_dt, e_dt, b_start, b_end, net_hours))

        # 3. 處理「請假」申請
        elif req_type == 'leave':
            target_dt = datetime.strptime(str(target_date), '%Y-%m-%d').date() if isinstance(target_date, str) else target_date

            s_dt = parse_datetime_safe(s_time)
            e_dt = parse_datetime_safe(e_time)

            if not s_dt:
                s_dt = datetime.combine(target_dt, time(9, 0))
            elif isinstance(s_dt, time):
                s_dt = datetime.combine(target_dt, s_dt)

            if not e_dt:
                e_dt = datetime.combine(target_dt, time(18, 0))
            elif isinstance(e_dt, time):
                e_dt = datetime.combine(target_dt, e_dt)

            net_hours = calculate_net_work_hours(s_dt, e_dt, b_start, b_end)
            status_str = f'leave_{leave_type}' if leave_type else 'leave'

            cur.execute("""
                INSERT INTO clock_records (user_id, work_date, clock_in, clock_out, break_start, break_end, work_hours, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, work_date) DO UPDATE 
                SET clock_in = EXCLUDED.clock_in, clock_out = EXCLUDED.clock_out,
                    break_start = EXCLUDED.break_start, break_end = EXCLUDED.break_end,
                    work_hours = EXCLUDED.work_hours, status = EXCLUDED.status
            """, (applicant_id, target_date, s_dt, e_dt, b_start, b_end, net_hours, status_str))

        # 更新申請單狀態並紀錄審核人與審核時間
        cur.execute("""
            UPDATE attendance_requests 
            SET status = 'approved', reviewed_by = %s, reviewed_at = %s 
            WHERE id = %s
        """, (admin_id, now, req_id))

        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

@webclock_bp.route('/admin/requests/<int:req_id>/reject', methods=['POST'])
@login_required
@role_required('admin')
def reject_request(req_id):
    """管理員駁回申請"""
    admin_id = session['user_id']
    now = get_taiwan_now()

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE attendance_requests 
            SET status = 'rejected', reviewed_by = %s, reviewed_at = %s 
            WHERE id = %s
        """, (admin_id, now, req_id))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ==========================================
# 💰 薪資與報表管理
# ==========================================

@webclock_bp.route('/salaries', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def manage_salaries():
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
                monthly_wage = float(raw_monthly) if raw_monthly else 27470
            else:
                salary_type = 'hourly'
                monthly_wage = None
                hourly_wage = float(raw_hourly) if raw_hourly else 183

            cur.execute("""
                UPDATE users SET salary_type = %s, hourly_wage = %s, monthly_wage = %s
                WHERE id = %s
            """, (salary_type, hourly_wage, monthly_wage, user_id))
            conn.commit()
            flash("薪資設定已成功更新！", "success")
            return redirect(url_for('webclock.manage_salaries'))

        cur.execute("SELECT id, username, role, salary_type, hourly_wage, monthly_wage FROM users ORDER BY id ASC")
        rows = cur.fetchall()
        users = [{'id': r[0], 'username': r[1], 'role': r[2], 'salary_type': r[3] or 'hourly', 'hourly_wage': r[4], 'monthly_wage': r[5]} for r in rows]
        return render_template('salaries.html', users=users)
    finally:
        cur.close()
        conn.close()

@webclock_bp.route('/admin/export_salary', methods=['GET'])
@login_required
@role_required('admin')
def export_salary():
    year_month = request.args.get('month', get_taiwan_now().strftime('%Y-%m'))
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, username, salary_type, hourly_wage, monthly_wage FROM users ORDER BY id ASC")
        users = cur.fetchall()
        data = []
        for u in users:
            u_id, username, salary_type, hourly_wage, monthly_wage = u
            cur.execute("SELECT work_hours, status FROM clock_records WHERE user_id = %s AND TO_CHAR(work_date, 'YYYY-MM') = %s", (u_id, year_month))
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
