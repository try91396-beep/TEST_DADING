# routes/try_routes.py
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from database import get_db_connection
import psycopg2
import bcrypt
# 🛡️ 引入安全防護罩 (請確保 utils.py 已存在)
from utils import login_required, role_required  

try_bp = Blueprint('try_debug', __name__)

# --- 完整對應 database.py 的中英對照表 ---
COLUMN_MAP = {
    'products': {
        'id': '自動遞增 ID',
        'name': '產品名稱 (必填)',
        'price': '價格 (必填)',
        'category': '分類名稱',
        'image_url': '圖片網址',
        'is_available': '是否上架',
        'custom_options': '中文自定義選項',
        'sort_order': '排序序號',
        'name_en': '英文品名',
        'name_jp': '日文品名',
        'name_kr': '韓文品名',
        'custom_options_en': '英文選項',
        'custom_options_jp': '日文選項',
        'custom_options_kr': '韓文選項',
        'print_category': '出單分類 (如: Noodle)',
        'category_en': '英文分類',
        'category_jp': '日文分類',
        'category_kr': '韓文分類'
    },
    'orders': {
        'id': '訂單 ID',
        'table_number': '桌號',
        'items': '文字描述',
        'total_price': '總金額',
        'status': '狀態 (Pending/Completed)',
        'created_at': '建立時間',
        'daily_seq': '當日流水號',
        'content_json': 'JSON 明細',
        'need_receipt': '需收據',
        'lang': '下單語系',
        'order_type': '訂單類型 (dine_in/delivery/pickup)',
        'delivery_info': '外送綜合資訊',
        'customer_name': '客戶姓名',
        'customer_phone': '客戶電話',
        'customer_address': '客戶地址',
        'scheduled_for': '預約送達時間',
        'delivery_fee': '外送費',
        'invoice_number': '發票號碼',
        'invoice_status': '發票狀態',
        'tax_id': '統一編號',
        'carrier_type': '載具類別',
        'carrier_num': '載具條碼'
    },
    'settings': {
        'key': '設定鍵 (Key)',
        'value': '設定值 (Value)'
    },
    'users': {
        'id': 'ID',
        'username': '帳號',
        'password_hash': '雜湊密碼',
        'role': '權限 (admin/staff)',
        'created_at': '建立時間'
    }
}

# --- 🆕 專門給 settings 資料表每一列用的中文對照表 ---
SETTINGS_KEY_MAP = {
    'sender_email': '預設發信人郵件 (Resend)',
    'shop_open': '店面營業狀態 (1:營業中, 0:休息中)',
    'delivery_enabled': '啟用外送功能 (後端邏輯開關)',
    'enable_delivery': '顯示外送按鈕 (前端介面開關)',
    'delivery_min_price': '外送起送價 (元)',
    'delivery_fee_base': '外送基礎運費 (元)',
    'delivery_max_km': '最大外送距離 (公里)',
    'delivery_fee_per_km': '超出基礎距離後每公里加價 (元)',
    
    # 這裡對應你新建立的店家資訊
    'shop_name': '🏪 店家名稱',
    'shop_address': '📍 店家地址',
    'shop_phone': '📞 店家電話',
    'shop_open_time': '⏰ 自動開店時間 (HH:MM)',
    'shop_close_time': '⏳ 自動 營業結束時間 (HH:MM)',
    'shop_logo_url': '🖼️ 店家商標網址 (Logo URL)'
}

# ==========================================
# 🛡️ 認證系統 (登入/登出)
# ==========================================

@try_bp.route('/login', methods=['GET', 'POST'])
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
            
            if user:
                user_id, hashed_pw, role = user
                # 比對 bcrypt 密碼
                if bcrypt.checkpw(password.encode('utf-8'), hashed_pw.encode('utf-8')):
                    session['user_id'] = user_id
                    session['username'] = username
                    session['role'] = role
                    return redirect(url_for('try_debug.show_db_structure'))
                else:
                    return render_template('login.html', error="密碼錯誤")
            else:
                return render_template('login.html', error="找不到此帳號")
        except Exception as e:
            print(f"Login Error: {e}")
            return render_template('login.html', error="資料庫連線失敗")
        finally:
            cur.close()
            conn.close()
    return render_template('login.html')

@try_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('try_debug.login'))

# ==========================================
# 📊 資料庫監控視窗 (Admin Only)
# ==========================================

@try_bp.route('/')
@login_required 
@role_required('admin')
def show_db_structure():
    """讀取所有資料表與所有資料行，並顯示於網頁"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 抓取所有資料表
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE';")
    tables = [row[0] for row in cur.fetchall()]
    
    db_info = {}
    
    for table in tables:
        # 抓取該表所有欄位結構
        cur.execute(f"""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = '{table}'
            ORDER BY ordinal_position;
        """)
        raw_columns = cur.fetchall()
        
        col_names = [col[0] for col in raw_columns]
        columns_info = []
        for col in raw_columns:
            columns_info.append({
                'name': col[0],
                'type': col[1],
                'nullable': col[2],
                'default': col[3],
                'desc': COLUMN_MAP.get(table, {}).get(col[0], '-')
            })
        
        pk_col = 'key' if table == 'settings' else 'id'

        # --- 💡 關鍵修改區塊：處理設定表的預設顯示 ---
        if table == 'settings':
            try:
                cur.execute(f"SELECT * FROM {table} ORDER BY {pk_col} ASC")
                db_rows = cur.fetchall()
                # 轉成字典方便比對
                db_dict = {row[0]: row[1] for row in db_rows}
                
                sample_rows = []
                # 1. 優先確保字典檔 SETTINGS_KEY_MAP 裡的所有 key 都會出現在畫面上
                for k in SETTINGS_KEY_MAP.keys():
                    sample_rows.append((k, db_dict.get(k, '')))  # 找不到就給空字串
                    db_dict.pop(k, None)
                
                # 2. 補上資料庫有，但字典檔沒寫到的其他設定
                for k, v in db_dict.items():
                    sample_rows.append((k, v))
            except:
                sample_rows = []
        else:
            # 其他資料表維持原樣 (最新 50 筆)
            try:
                cur.execute(f"SELECT * FROM {table} ORDER BY {pk_col} DESC LIMIT 50")
                sample_rows = cur.fetchall()
            except:
                cur.execute(f"SELECT * FROM {table} LIMIT 50")
                sample_rows = cur.fetchall()
        # ---------------------------------------------
        
        db_info[table] = {
            'column_names': col_names, # 供前端渲染標題
            'schema': columns_info,
            'data': sample_rows,
            'pk_col': pk_col 
        }

    cur.close()
    conn.close()
    return render_template(
        'try.html', 
        db_info=db_info, 
        current_user=session.get('username'),
        settings_key_map=SETTINGS_KEY_MAP
    )

# ==========================================
# ✍️ 直接修改資料 (API)
# ==========================================

@try_bp.route('/update', methods=['POST'])
@login_required 
@role_required('admin')
def update_db_data():
    data = request.json
    table = data.get('table')
    pk_col = data.get('pk_col')
    pk_val = data.get('pk_val')
    column = data.get('column')
    new_value = data.get('value')

    if not all([table, pk_col, pk_val, column]):
        return jsonify({'success': False, 'error': '參數缺失'}) 

    # 🛡️ 安全檢查：限制只能操作我們宣告過的資料表，防止惡意破壞
    if table not in COLUMN_MAP:
        return jsonify({'success': False, 'error': '拒絕存取：未授權的資料表'})

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # --- 💡 關鍵修改區塊：UPSERT 邏輯 ---
        if table == 'settings':
            # 設定表使用 UPSERT (ON CONFLICT DO UPDATE)
            # 這樣就算資料庫原本沒有這個 Key，也可以直接從網頁新增進去
            query = f'''
                INSERT INTO "{table}" ("{pk_col}", "{column}") 
                VALUES (%s, %s) 
                ON CONFLICT ("{pk_col}") 
                DO UPDATE SET "{column}" = EXCLUDED."{column}"
            '''
            cur.execute(query, (pk_val, new_value))
        else:
            # 其他表維持一般的 UPDATE
            query = f'UPDATE "{table}" SET "{column}" = %s WHERE "{pk_col}" = %s'
            cur.execute(query, (new_value, pk_val))
        # ---------------------------------------------

        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        cur.close()
        conn.close()

# ==========================================
# 👤 帳號管理功能
# ==========================================

@try_bp.route('/add_user', methods=['GET', 'POST'])
@login_required 
@role_required('admin')
def add_user():
    if request.method == 'POST':
        new_username = request.form.get('username')
        new_password = request.form.get('password')
        role = request.form.get('role', 'staff')

        if not new_username or not new_password:
            return "欄位不可為空！ <a href='/try/add_user'>返回</a>"

        # 密碼雜湊處理
        salt = bcrypt.gensalt()
        hashed_pw = bcrypt.hashpw(new_password.encode('utf-8'), salt).decode('utf-8')

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                (new_username, hashed_pw, role)
            )
            conn.commit()
            return f"<h3>✅ 帳號 {new_username} 建立成功！</h3> <a href='/try'>回管理後台</a>"
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return "<h3>❌ 錯誤：帳號已存在！</h3> <a href='/try/add_user'>重新輸入</a>"
        finally:
            cur.close()
            conn.close()

    return """
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>新增帳號</title>
    <style>
        body { font-family: sans-serif; background: #f0f2f5; display: flex; justify-content: center; padding-top: 50px; }
        .card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); width: 350px; }
        input, select { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; }
        button { width: 100%; padding: 12px; background: #007bff; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 16px; }
        button:hover { background: #0056b3; }
        .back { display: block; text-align: center; margin-top: 15px; color: #666; text-decoration: none; font-size: 14px; }
    </style>
    </head>
    <body>
        <div class="card">
            <h2>👤 新增使用者</h2>
            <form method="POST">
                <input type="text" name="username" placeholder="帳號名稱" required>
                <input type="password" name="password" placeholder="登入密碼" required>
                <select name="role">
                    <option value="staff">員工 (Staff)</option>
                    <option value="admin">管理員 (Admin)</option>
                </select>
                <button type="submit">建立帳號</button>
            </form>
            <a href="/try" class="back">取消並返回</a>
        </div>
    </body>
    </html>
    """
