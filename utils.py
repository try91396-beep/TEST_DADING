import json
import urllib.request
import urllib.error
import threading
import time
import ssl
import traceback
from datetime import datetime, timedelta
from database import get_db_connection

# === 🛡️ 引入 Flask 相關工具 ===
from flask import session, redirect, url_for, request, jsonify, has_request_context
from functools import wraps
from werkzeug.routing import BuildError

# ==========================================
# 0. 🛡️ 權限防護罩
# ==========================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Unauthorized'}), 401
            bp = request.blueprint or ''
            target = 'try_debug.login' if bp in ['try', 'try_debug'] else f'{bp}.login'
            return redirect(url_for(target) if bp else url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                bp = request.blueprint or ''
                return redirect(url_for(f'{bp}.login' if bp else 'admin.login'))
            if session.get('role') not in allowed_roles:
                return "<h3>❌ 權限不足</h3>", 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ==========================================
# 1. Email 報告發送核心 (新增作廢明細版)
# ==========================================
def send_daily_report(app, manual_config=None, is_test=False, operator_name=None, operator_role=None):
    """
    發送日結報表。
    """
    conn, cur = None, None
    
    with app.app_context():
        try:
            # 💡 判定值班人員
            final_name = operator_name
            final_role = operator_role

            if not final_name:
                if has_request_context():
                    final_name = session.get('username', '系統自動發送')
                    final_role = session.get('role', 'System')
                else:
                    final_name = "系統自動發送"
                    final_role = "System"

            conn = get_db_connection()
            cur = conn.cursor()
            
            # 讀取設定
            if manual_config:
                config = manual_config
            else:
                cur.execute("SELECT key, value FROM settings")
                config = dict(cur.fetchall())

            api_key = config.get('resend_api_key', '').strip()
            to_email = config.get('report_email', '').strip()
            sender_email = (config.get('sender_email') or 'onboarding@resend.dev').strip()

            if not api_key or not to_email:
                print("⚠️ Email 設定不完整，取消任務")
                return "❌ 設定不完整"

            tw_now = datetime.utcnow() + timedelta(hours=8)
            today_str = tw_now.strftime('%Y-%m-%d')

            if is_test:
                subject = f"【測試】Resend API 設定確認 ({today_str})"
                email_content = (
                    f"👤 值班人員: {final_name} ({final_role})\n"
                    f"------------------------\n"
                    f"✅ 連線測試成功！\n"
                    f"寄件者: {sender_email}\n"
                    f"收件者: {to_email}"
                )
            else:
                # 時間過濾器
                tw_start = tw_now.replace(hour=0, minute=0, second=0, microsecond=0)
                utc_start = tw_start - timedelta(hours=8)
                utc_end = utc_start + timedelta(hours=24)
                time_filter = "created_at >= %s AND created_at < %s"
                params = (utc_start, utc_end)

                # --- 1. 有效訂單統計 ---
                cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status != 'Cancelled'", params)
                v_res = cur.fetchone()
                v_count, v_total = (v_res[0] or 0), (float(v_res[1] or 0))

                cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status != 'Cancelled'", params)
                v_stats = {}
                for r in cur.fetchall():
                    try:
                        items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                        if isinstance(items, dict): items = [items]
                        for i in items:
                            n = i.get('name_zh', i.get('name', '未知'))
                            v_stats[n] = v_stats.get(n, 0) + int(i.get('qty', 0))
                    except: continue
                v_text = "\n".join([f"• {k}: {v}" for k, v in sorted(v_stats.items(), key=lambda x:x[1], reverse=True)]) or "(無銷量)"

                # --- 2. 作廢訂單統計 (新增明細邏輯) ---
                cur.execute(f"SELECT COUNT(*), SUM(total_price) FROM orders WHERE {time_filter} AND status = 'Cancelled'", params)
                x_res = cur.fetchone()
                x_count, x_total = (x_res[0] or 0), (float(x_res[1] or 0))

                cur.execute(f"SELECT content_json FROM orders WHERE {time_filter} AND status = 'Cancelled'", params)
                x_stats = {}
                for r in cur.fetchall():
                    try:
                        items = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                        if isinstance(items, dict): items = [items]
                        for i in items:
                            n = i.get('name_zh', i.get('name', '未知'))
                            x_stats[n] = x_stats.get(n, 0) + int(i.get('qty', 0))
                    except: continue
                x_text = "\n".join([f"• {k}: {v}" for k, v in sorted(x_stats.items(), key=lambda x:x[1], reverse=True)]) or "(無作廢品項)"

                # --- 3. 組合內容 ---
                subject = f"【日結單】{today_str} 營業報告"
                email_content = (
                    f"👤 值班人員: {final_name} ({final_role})\n"
                    f"🍴 餐廳日結 ({today_str})\n"
                    f"------------------------\n"
                    f"✅ 有效: {v_count} 筆 (${int(v_total):,})\n"
                    f"{v_text}\n"
                    f"------------------------\n"
                    f"❌ 作廢: {x_count} 筆 (${int(x_total):,})\n"
                    f"{x_text}\n"
                    f"------------------------\n"
                    f"💰 實收總計: ${int(v_total):,}"
                )

            # --- API 發送 ---
            payload = {"from": sender_email, "to": [to_email], "subject": subject, "text": email_content}
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request("https://api.resend.com/emails", 
                                          data=json.dumps(payload).encode('utf-8'), 
                                          headers=headers, method='POST')
            
            with urllib.request.urlopen(req, context=ctx, timeout=15) as res:
                return "✅ 發送成功"

        except Exception as e:
            traceback.print_exc()
            return f"❌ 錯誤: {str(e)}"
        finally:
            if cur: cur.close()
            if conn: conn.close()

# ==========================================
# 2. 背景維護工作 (Aiven DB 連線優化版)
# ==========================================
def run_maintenance_tasks(app):
    print("⏳ 背景任務等待啟動中 (Wait 30s)...")
    time.sleep(30)
    print("🚀 背景維護執行緒已正式啟動")
    
    last_sent_time = ""
    last_shop_toggle_time = ""  # 紀錄上一次更新狀態的時間，防止在同一個分鐘內重複塞入 SQL
    next_ping_time = datetime.now()

    while True:
        try:
            now_obj = datetime.now()
            now_str = now_obj.strftime("%H:%M:%S")

            # 取得台灣時間 (UTC+8)
            tw_time = datetime.utcnow() + timedelta(hours=8)
            current_hm = tw_time.strftime("%H:%M")
            current_weekday = tw_time.weekday()  # 💡 0=週一, 1=週二, ..., 5=週六, 6=週日

            # --- A. 自動發信檢查 ---
            target_times = ["13:00", "18:00", "20:30"]
            if current_hm in target_times and current_hm != last_sent_time:
                print(f"[{current_hm}] ⏰ 執行自動發信...")
                send_daily_report(app)
                last_sent_time = current_hm

            # --- 🏪 B. 每日定時強寫 shop_open 狀態 ---
            # =====================================================================
            # 1. 動態從資料庫讀取設定的營業時間與偏移時數
            # =====================================================================
            from datetime import datetime, timedelta
            
            # 設定基礎預設值
            raw_open_time = "09:00"
            raw_close_time = "21:00"
            open_advance_hours = 1  # 預設開店提早 1 小時
            close_delay_hours = 1   # 預設閉店延後 1 小時
            
            try:
                conn = get_db_connection()
                with conn.cursor() as cur:
                    # 💡 SQL 查詢加入我們新增的兩個變數 key
                    cur.execute("""
                        SELECT key, value FROM settings 
                        WHERE key IN (
                            'shop_open_time', 
                            'shop_close_time', 
                            'shop_open_advance_hours', 
                            'shop_close_delay_hours'
                        );
                    """)
                    rows = cur.fetchall()
                    for key, val in rows:
                        val = val.strip()
                        if not val:
                            continue
                            
                        if key == 'shop_open_time':
                            raw_open_time = val
                        elif key == 'shop_close_time':
                            raw_close_time = val
                        elif key == 'shop_open_advance_hours': # 欄位名稱不變，把它當「分鐘」解析
                            try:
                                open_advance_minutes = int(val) # 轉成整數分鐘
                            except ValueError:
                                open_advance_minutes = 60 # 失敗的話防呆預設為 60 分鐘
                        elif key == 'shop_close_delay_hours':
                            try:
                                close_delay_minutes = int(val)  # 轉成整數分鐘
                            except ValueError:
                                close_delay_minutes = 60
                conn.close()
            except Exception as db_err:
                print(f"[{now_str}] ⚠️ 讀取動態營業設定失敗，使用備用預設值: {db_err}")
            
           # --- 💡 核心邏輯：依照資料庫抓到的「分鐘數」進行增減 ---
            try:
                # 處理開店時間 (提早幾分鐘)
                open_dt = datetime.strptime(raw_open_time, "%H:%M")
                shop_open_time = (open_dt - timedelta(minutes=open_advance_minutes)).strftime("%H:%M") # 💡 改為 minutes
                
                # 處理閉店時間 (延後幾分鐘)
                close_dt = datetime.strptime(raw_close_time, "%H:%M")
                shop_close_time = (close_dt + timedelta(minutes=close_delay_minutes)).strftime("%H:%M") # 💡 改為 minutes
            except Exception as time_err:
                shop_open_time = "08:00"
                shop_close_time = "22:00"
                print(f"[{now_str}] ⚠️ 時間格式解析失敗，啟用手動計算防呆值")
            
            
            # =====================================================================
            # 2. 使用動態時間變數進行 current_hm 判斷 (下方邏輯完全不用動)
            # =====================================================================
            if current_hm == shop_open_time and current_hm != last_shop_toggle_time:
                target_val = '0' if current_weekday == 5 else '1'
                log_text = "台灣時間週六，強制設定 shop_open = 0 (不開門)" if current_weekday == 5 else "強制設定 shop_open = 1"
                
                print(f"[{current_hm}] 🏪 開店時間到！(表定 {raw_open_time}，已提早 {open_advance_hours} 小時) {log_text}")
                try:
                    conn = get_db_connection()
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO settings (key, value) VALUES ('shop_open', %s)
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
                        """, (target_val,))
                    conn.commit()
                    conn.close()
                    
                    last_shop_toggle_time = current_hm
                    print(f"[{now_str}] 🏪 已成功寫入 shop_open = {target_val}")
                except Exception as db_err:
                    print(f"[{now_str}] ❌ 更新 shop_open={target_val} 失敗: {db_err}")
            
            elif current_hm == shop_close_time and current_hm != last_shop_toggle_time:
                print(f"[{current_hm}] 🏪 閉店時間到！(表定 {raw_close_time}，已延後 {close_delay_hours} 小時) 強制設定 shop_open = 0")
                try:
                    conn = get_db_connection()
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO settings (key, value) VALUES ('shop_open', '0')
                            ON CONFLICT (key) DO UPDATE SET value = '0';
                        """)
                    conn.commit()
                    conn.close()
                    
                    last_shop_toggle_time = current_hm
                    print(f"[{now_str}] 🏪 已寫入 shop_open = 0")
                except Exception as db_err:
                    print(f"[{now_str}] ❌ 更新 shop_open=0 失敗: {db_err}")

            # --- C. 防休眠 Ping (Web + Aiven DB) ---
            if now_obj >= next_ping_time:
                # 1. Ping 網站
                try:
                    urllib.request.urlopen("https://ding-dong-tipi.onrender.com", timeout=5)
                    print(f"[{now_str}] ✅ Web Ping 成功")
                except Exception as web_err: 
                    print(f"[{now_str}] ⚠️ Web Ping 失敗: {web_err}")
                
                # 2. Ping Aiven 資料庫 (發送真實指令維持連線)
                try:
                    conn = get_db_connection()
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1;") 
                        cur.fetchone()
                    conn.close()
                    print(f"[{now_str}] 💓 Aiven DB Heartbeat 成功 (SELECT 1)")
                except Exception as db_ping_err: 
                    print(f"[{now_str}] ⚠️ DB Heartbeat 失敗: {db_ping_err}")
                
                next_ping_time = now_obj + timedelta(seconds=300)

            time.sleep(30) 
        except Exception as e:
            print(f"⚠️ 背景任務主要迴圈錯誤: {e}")
            time.sleep(60)

def start_background_tasks(app):
    t = threading.Thread(target=run_maintenance_tasks, args=(app,), daemon=True)
    t.start()

# ==========================================
# 3. 👤 自動注入登入資訊 (Context Processor)
# ==========================================
def inject_user_info():
    current_username = session.get('username')
    current_bp = request.blueprint
    try:
        logout_url = url_for(f'{current_bp}.logout') if current_username and current_bp else '#'
    except:
        logout_url = '#'
    return {
        'current_username': current_username,
        'current_role': session.get('role', '未知角色'),
        'logout_url': logout_url
    }
