# routes/admin_routes.py
import io
import json
import threading
import traceback
import pandas as pd
import bcrypt  # 💡 新增：引入 bcrypt 用來驗證密碼

# 🛡️ 引入我們在 utils.py 寫好的雙重防護罩
from utils import login_required, role_required  
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_file, current_app, session

# 從資料庫模組匯入連線函式 (PostgreSQL)
from database import get_db_connection
# 從 utils 匯入發信功能
from utils import send_daily_report

admin_bp = Blueprint('admin', __name__)

# ==========================================
# 🛡️ 登入與登出系統
# ==========================================

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """處理管理員登入"""
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
                    return redirect(url_for('admin.admin_panel'))
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

@admin_bp.route('/logout')
def logout():
    """處理登出"""
    session.clear() 
    return redirect(url_for('admin.login'))

# ==========================================
# 核心路由：後台主面板
# ==========================================
@admin_bp.route('/', methods=['GET', 'POST'])
@login_required          
@role_required('admin')  
def admin_panel():
    conn = get_db_connection()
    cur = conn.cursor()
    msg = request.args.get('msg', '')
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        # --- 功能 1: 儲存一般設定 & 測試連線 ---
        if action == 'save_settings' or action == 'test_email':
            try:
                # 1. 先從資料庫讀取目前的最新設定作為基底
                cur.execute("SELECT key, value FROM settings")
                current_db_config = dict(cur.fetchall())

                # 2. 建立新設定字典，若前端沒給，自動沿用資料庫舊值（或給預設值）
                def get_safe_val(field_name, default_val=''):
                    form_val = request.form.get(field_name)
                    # 如果前端表單根本沒傳，或傳來空字串，就沿用資料庫舊值
                    if form_val is None or form_val.strip() == '':
                        return current_db_config.get(field_name, default_val)
                    return form_val.strip()

                new_config = {
                    'report_email': get_safe_val('report_email'),
                    'resend_api_key': get_safe_val('resend_api_key'), # 🛡️ 安全盾牌：沒傳就不會覆蓋！
                    'sender_email': get_safe_val('sender_email', 'onboarding@resend.dev'),
                    'shop_logo_url': get_safe_val('shop_logo_url'),                     
                    'shop_name': get_safe_val('shop_name'),
                    'shop_address': get_safe_val('shop_address'),
                    'shop_phone': get_safe_val('shop_phone'),
                    'shop_open_time': get_safe_val('shop_open_time'),
                    'shop_close_time': get_safe_val('shop_close_time'),
                    'shop_open_advance_hours': get_safe_val('shop_open_advance_hours'),
                    'shop_close_delay_hours': get_safe_val('shop_close_delay_hours')
                }

                # 3. 安全更新至資料庫
                for k, v in new_config.items():
                    cur.execute("""
                        INSERT INTO settings (key, value) 
                        VALUES (%s, %s) 
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """, (k, v))
                conn.commit()
                
                should_test = (request.form.get('test_connection') == 'on') or (action == 'test_email')

                if should_test:
                    try:
                        app_obj = current_app._get_current_object()
                        result_msg = send_daily_report(
                            app_obj, 
                            manual_config=new_config, 
                            is_test=True,
                            operator_name=session.get('username'),
                            operator_role=session.get('role')
                        )
                        
                        if "✅" in result_msg:
                            msg = f"✅ 設定已儲存 / {result_msg}"
                        else:
                            msg = f"⚠️ 設定已存，但連線測試失敗: {result_msg}"
                            
                    except Exception as e:
                        traceback.print_exc()
                        msg = f"✅ 設定已儲存 / ❌ 測試失敗: {str(e)}"
                else:
                    msg = "✅ 設定已儲存"
                    
            except Exception as e:
                conn.rollback()
                msg = f"❌ 儲存失敗: {e}"
            finally:
                cur.close(); conn.close()
            
            return redirect(url_for('admin.admin_panel', msg=msg))

        # --- 功能 2: 手動觸發日結報表 ---
        elif action == 'send_report_now':
            try:
                app_obj = current_app._get_current_object()
                current_user = session.get('username', '未知管理員')
                current_role = session.get('role', 'admin')

                threading.Thread(
                    target=send_daily_report, 
                    args=(app_obj,), 
                    kwargs={
                        'is_test': False, 
                        'operator_name': current_user, 
                        'operator_role': current_role
                    }
                ).start()
                
                msg = f"🚀 報表正在背景發送中 (發送者: {current_user})，請稍候檢查信箱"
            except Exception as e:
                traceback.print_exc()
                msg = f"❌ 無法啟動背景任務: {e}"
            
            cur.close(); conn.close()
            return redirect(url_for('admin.admin_panel', msg=msg))

        # --- 功能 3: 新增產品 ---
        elif action == 'add_product':
            try:
                cur.execute("""
                    INSERT INTO products (
                        name, price, category, print_category, image_url, sort_order,
                        name_en, name_jp, name_kr,
                        custom_options, custom_options_en, custom_options_jp, custom_options_kr,
                        category_en, category_jp, category_kr
                    ) VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    request.form.get('name'), request.form.get('price'), request.form.get('category'), 
                    request.form.get('print_category'), request.form.get('image_url'),
                    request.form.get('name_en'), request.form.get('name_jp'), request.form.get('name_kr'),
                    request.form.get('custom_options'), request.form.get('custom_options_en'), request.form.get('custom_options_jp'), request.form.get('custom_options_kr'),
                    request.form.get('category_en'), request.form.get('category_jp'), request.form.get('category_kr')
                ))
                conn.commit()
                msg = "✅ 品項已新增"
            except Exception as e:
                conn.rollback()
                msg = f"❌ 新增失敗: {e}"
            finally:
                cur.close(); conn.close()
            return redirect(url_for('admin.admin_panel', msg=msg))

    # --- GET: 讀取資料顯示頁面 ---
    try:
        cur.execute("SELECT key, value FROM settings")
        settings_rows = cur.fetchall()
        config = {row[0]: row[1] for row in settings_rows} 
        
        toggle_keys = ['shop_open', 'enable_delivery', 'delivery_enabled']
        for key in toggle_keys:
            val = config.get(key, '0') 
            config[key] = 1 if val == '1' else 0

        if 'enable_delivery' not in config:
            config['enable_delivery'] = config.get('delivery_enabled', 0)
        
        config.setdefault('delivery_min_price', '0')
        config.setdefault('delivery_fee_base', '0')
        config.setdefault('delivery_max_km', '5')
        config.setdefault('delivery_fee_per_km', '10')
        config.setdefault('shop_panda_url', '')

        cur.execute("""
            SELECT id, name, price, category, is_available, print_category, sort_order, image_url, 
                   name_en, name_jp, name_kr 
            FROM products 
            ORDER BY sort_order ASC, id DESC
        """)
        prods = cur.fetchall()
    finally:
        cur.close(); conn.close()
    
    return render_template('admin.html', config=config, prods=prods, msg=msg)

# ==========================================
# 外送詳細設定 (表單提交)
# ==========================================
@admin_bp.route('/settings/delivery', methods=['POST'])
@login_required
@role_required('admin')  
def update_delivery_settings():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        is_enabled = '1' if request.form.get('delivery_enabled') else '0'

        settings_to_update = {
            'delivery_enabled': is_enabled,
            'enable_delivery': is_enabled,
            'delivery_min_price': request.form.get('delivery_min_price') or '0',
            'delivery_fee_base': request.form.get('delivery_fee_base') or '0',
            'delivery_max_km': request.form.get('delivery_max_km') or '5',
            'delivery_fee_per_km': request.form.get('delivery_fee_per_km') or '10',
            'shop_logo_url': request.form.get('shop_logo_url') or '10',
            'shop_panda_url': request.form.get('shop_panda_url') or '10',
            'shop_name': request.form.get('shop_name') or '10',
            'shop_address': request.form.get('shop_address') or '10',
            'shop_phone': request.form.get('shop_phone') or '10',
            'shop_open_time': request.form.get('shop_open_time') or '10',
            'shop_close_time': request.form.get('shop_close_time') or '10',
            'shop_open_advance_hours': request.form.get('shop_open_advance_hours') or '',
            'shop_close_delay_hours': request.form.get('shop_close_delay_hours') or ''
        }

        for key, val in settings_to_update.items():
            cur.execute("""
                INSERT INTO settings (key, value) 
                VALUES (%s, %s) 
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, (key, str(val)))
        
        conn.commit()
        msg = "✅ 外送設定已更新 (含運費規則)"
    except Exception as e:
        conn.rollback()
        msg = f"❌ 設定更新失敗: {e}"
        traceback.print_exc()
    finally:
        cur.close(); conn.close()

    return redirect(url_for('admin.admin_panel', msg=msg))


# ==========================================
# 通用設定切換路由 (AJAX) - 開關店、開關外送
# ==========================================
@admin_bp.route('/toggle_config', methods=['POST'])
@login_required          
@role_required('admin')  
def toggle_config():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        data = request.get_json()
        key = data.get('key')
        
        allowed_keys = ['shop_open', 'enable_delivery', 'delivery_enabled']
        if key not in allowed_keys:
            return jsonify({'status': 'error', 'message': '不允許的設定項目'}), 400

        cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cur.fetchone()

        current_val = row[0] if row else '0'
        new_val = '0' if current_val == '1' else '1'
        
        keys_to_update = [key]
        if key in ['enable_delivery', 'delivery_enabled']:
            keys_to_update = ['enable_delivery', 'delivery_enabled']

        for k in keys_to_update:
            cur.execute("""
                INSERT INTO settings (key, value) 
                VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, (k, new_val))

        conn.commit()
        return jsonify({'status': 'success', 'new_value': (new_val == '1')})

    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ==========================================
# 編輯產品 (獨立頁面)
# ==========================================
@admin_bp.route('/edit_product/<int:pid>', methods=['GET','POST'])
@login_required
@role_required('admin')  
def edit_product(pid):
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        try:
            cur.execute("""
                UPDATE products SET 
                name=%s, price=%s, category=%s, image_url=%s, custom_options=%s,
                name_en=%s, name_jp=%s, name_kr=%s,
                custom_options_en=%s, custom_options_jp=%s, custom_options_kr=%s,
                print_category=%s, sort_order=%s,
                category_en=%s, category_jp=%s, category_kr=%s
                WHERE id=%s
            """, (
                request.form.get('name'), request.form.get('price'), request.form.get('category'),
                request.form.get('image_url'), request.form.get('custom_options'),
                request.form.get('name_en'), request.form.get('name_jp'), request.form.get('name_kr'),
                request.form.get('custom_options_en'), request.form.get('custom_options_jp'), request.form.get('custom_options_kr'),
                request.form.get('print_category'), request.form.get('sort_order'),
                request.form.get('category_en'), request.form.get('category_jp'), request.form.get('category_kr'),
                pid
            ))
            conn.commit()
            return redirect(url_for('admin.admin_panel', msg="✅ 產品已更新"))
        except Exception as e:
            conn.rollback()
            return f"Update Error: {e}"
        finally:
            cur.close(); conn.close()

    cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
    if cur.description:
        columns = [desc[0] for desc in cur.description]
        row = cur.fetchone()
    else:
        row = None
        
    cur.close(); conn.close()
    
    if not row: return "找不到該產品", 404

    p = dict(zip(columns, row))
    def v(key): return p.get(key) if p.get(key) is not None else ""

    return f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>編輯產品</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/milligram/1.4.1/milligram.min.css">
    <style>
        body {{ padding: 20px; background: #f4f7f6; font-family: sans-serif; }}
        .container {{ background: white; padding: 30px; border-radius: 10px; max-width: 900px; margin: auto; }}
        h5 {{ background: #9b4dca; color: white; padding: 5px 10px; border-radius: 4px; margin-top: 25px; }}
        label {{ font-weight: bold; margin-top: 10px; }}
        .row {{ margin-bottom: 1rem; }}
    </style>
    </head>
    <body>
        <div class="container">
            <h3>📝 編輯產品 #{v('id')}</h3>
            <form method="POST">
                <h5>1. 基本資料</h5>
                <div class="row">
                    <div class="column column-40"><label>名稱 (中文)</label><input type="text" name="name" value="{v('name')}" required></div>
                    <div class="column"><label>價格</label><input type="number" name="price" value="{v('price')}" required></div>
                    <div class="column"><label>排序</label><input type="number" name="sort_order" value="{v('sort_order')}"></div>
                </div>
                <div class="row">
                    <div class="column">
                        <label>出單區域</label>
                        <select name="print_category">
                            <option value="Noodle" {'selected' if v('print_category')=='Noodle' else ''}>🍜 麵區</option>
                            <option value="Soup" {'selected' if v('print_category')=='Soup' else ''}>🍲 湯區</option>
                        </select>
                    </div>
                    <div class="column column-67"><label>圖片 URL</label><input type="text" name="image_url" value="{v('image_url')}"></div>
                </div>

                <h5>2. 分類 (Category)</h5>
                <div class="row">
                    <div class="column"><label>中文</label><input type="text" name="category" value="{v('category')}"></div>
                    <div class="column"><label>English</label><input type="text" name="category_en" value="{v('category_en')}"></div>
                    <div class="column"><label>日本語</label><input type="text" name="category_jp" value="{v('category_jp')}"></div>
                    <div class="column"><label>한국어</label><input type="text" name="category_kr" value="{v('category_kr')}"></div>
                </div>

                <h5>3. 多語品名 (Name)</h5>
                <div class="row">
                    <div class="column"><label>English</label><input type="text" name="name_en" value="{v('name_en')}"></div>
                    <div class="column"><label>日本語</label><input type="text" name="name_jp" value="{v('name_jp')}"></div>
                    <div class="column"><label>한국어</label><input type="text" name="name_kr" value="{v('name_kr')}"></div>
                </div>

                <h5>4. 客製化選項 (Options)</h5>
                <label>中文選項 (逗號分隔)</label>
                <input type="text" name="custom_options" value="{v('custom_options')}">
                <div class="row">
                    <div class="column"><label>English Options</label><input type="text" name="custom_options_en" value="{v('custom_options_en')}"></div>
                    <div class="column"><label>日本語 Options</label><input type="text" name="custom_options_jp" value="{v('custom_options_jp')}"></div>
                    <div class="column"><label>한국어 Options</label><input type="text" name="custom_options_kr" value="{v('custom_options_kr')}"></div>
                </div>

                <div style="margin-top:30px; text-align: right;">
                    <a href="{url_for('admin.admin_panel')}" class="button button-outline">❌ 取消</a>
                    <button type="submit">💾 儲存變更</button>
                </div>
            </form>
        </div>
    </body></html>"""

# ==========================================
# 匯入 / 匯出 / 重置 / 其他
# ==========================================

@admin_bp.route('/export_menu')
@login_required          # 必須登入
@role_required('admin')  # 限制只有 admin 角色可以存取
def export_menu():
    """
    將目前的產品菜單匯出為 Excel 檔案。
    """
    try:
        # 1. 從資料庫撈取所有產品，並依照排序欄位 (sort_order) 升序排列
        conn = get_db_connection()
        df = pd.read_sql("SELECT * FROM products ORDER BY sort_order ASC", conn)
        conn.close()
        
        # 2. 建立一個在記憶體中的二進位串流 (BytesIO)，用來暫存 Excel 檔案內容
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # 將 Pandas DataFrame 寫入 Excel，且不包含索引列 (index=False)
            df.to_excel(writer, index=False)
        output.seek(0) # 將游標移回檔案開頭，以便後續讀取
        
        # 3. 將記憶體中的 Excel 檔案作為附件回傳給使用者下載
        # 檔名會動態加上當前日期，例如: menu_export_20231025.xlsx
        return send_file(
            output, 
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            as_attachment=True, 
            download_name=f"menu_export_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx"
        )
    except Exception as e:
         # 若發生錯誤，導回後台首頁並顯示錯誤訊息
         return redirect(url_for('admin.admin_panel', msg=f"❌ 匯出失敗: {e}"))

@admin_bp.route('/import_menu', methods=['POST'])
@login_required          
@role_required('admin')  
def import_menu():
    """
    讀取使用者上傳的 Excel 檔案，並將資料批次匯入至產品菜單。
    """
    try:
        # 1. 獲取上傳的檔案，若無檔案則直接返回錯誤提示
        file = request.files.get('menu_file')
        if not file: return redirect(url_for('admin.admin_panel', msg="❌ 無檔案"))
        
        # 2. 使用 Pandas 讀取 Excel 檔案
        df = pd.read_excel(file, engine='openpyxl')
        # 將 DataFrame 中的 NaN 或 NaT 取代為 None，確保存入資料庫時為 NULL
        df = df.where(pd.notnull(df), None)
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cnt = 0 # 記錄成功匯入的筆數
        
        # 3. 逐列迭代 Excel 資料並寫入資料庫
        for _, p in df.iterrows():
            if not p.get('name'): continue # 略過沒有商品名稱的無效資料
            
            # 處理上下架狀態 (is_available) 的布林值轉換
            # 支援辨識 '1', 'true', 'yes', 't' 作為上架狀態
            is_avail = True
            if p.get('is_available') is not None:
                val = str(p.get('is_available')).lower()
                is_avail = val in ['1', 'true', 'yes', 't']

            # 定義插入產品的 SQL 語法 (包含多國語系與客製化選項)
            sql = """
                INSERT INTO products (
                    name, price, category, image_url, is_available, custom_options, sort_order,
                    name_en, name_jp, name_kr,
                    custom_options_en, custom_options_jp, custom_options_kr,
                    print_category,
                    category_en, category_jp, category_kr
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 
                    %s, %s, %s, 
                    %s, %s, %s, 
                    %s, 
                    %s, %s, %s
                )
            """
            
            # 整理要對應 SQL 變數的參數組
            params = (
                str(p.get('name')), p.get('price', 0), p.get('category'), p.get('image_url'),
                is_avail, p.get('custom_options'), p.get('sort_order', 0),
                p.get('name_en'), p.get('name_jp'), p.get('name_kr'),
                p.get('custom_options_en'), p.get('custom_options_jp'), p.get('custom_options_kr'),
                p.get('print_category', 'Noodle'), # 預設列印分類為 Noodle
                p.get('category_en'), p.get('category_jp'), p.get('category_kr')
            )
            
            cur.execute(sql, params)
            cnt += 1
            
        conn.commit()
        cur.close(); conn.close()
        return redirect(url_for('admin.admin_panel', msg=f"✅ 完整匯入成功！共 {cnt} 筆資料"))
        
    except Exception as e:
        # 印出詳細錯誤堆疊，並將簡要錯誤訊息回傳給前端
        traceback.print_exc()
        return redirect(url_for('admin.admin_panel', msg=f"❌ 匯入失敗: {e}"))

@admin_bp.route('/reset_menu')
@login_required
@role_required('admin')  
def reset_menu():
    """
    清空整個產品菜單 (危險操作)。
    """
    conn = get_db_connection(); cur = conn.cursor()
    # 使用 TRUNCATE 清空資料表
    # RESTART IDENTITY: 重置自增主鍵 (ID) 回到 1
    # CASCADE: 一併刪除或清空與此表有外部鍵 (Foreign Key) 關聯的資料
    cur.execute("TRUNCATE TABLE products RESTART IDENTITY CASCADE")
    conn.commit(); cur.close(); conn.close()
    return redirect(url_for('admin.admin_panel', msg="🗑️ 菜單已清空"))

@admin_bp.route('/reset_orders', methods=['POST'])
@login_required
@role_required('admin')  
def reset_orders():
    """
    清空歷史訂單資料，支援「全部清空」或「依照日期範圍清空」。
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        delete_mode = request.form.get('delete_mode') # 取得刪除模式 (all 或 range)
        
        if delete_mode == 'all':
            # 模式 1: 全部清空，並重置訂單 ID
            cur.execute("TRUNCATE TABLE orders RESTART IDENTITY CASCADE")
            msg = "💥 已清空所有歷史訂單，流水號已重置！"
            
        elif delete_mode == 'range':
            # 模式 2: 指定區間刪除
            start_date = request.form.get('start_date')
            end_date = request.form.get('end_date')
            
            if not start_date or not end_date:
                return redirect(url_for('admin.admin_panel', msg="❌ 請選擇完整的開始與結束日期"))
            
            # 將日期字串補上時間，涵蓋一整天
            start_ts = f"{start_date} 00:00:00"
            end_ts = f"{end_date} 23:59:59"
            
            # 執行範圍刪除
            # 注意: 這裡將 UTC 創建時間加上 8 小時 ('8 hours')，以轉換為台灣時區 (UTC+8) 進行準確的日期比對
            cur.execute("""
                DELETE FROM orders 
                WHERE (created_at + interval '8 hours') >= %s 
                  AND (created_at + interval '8 hours') <= %s
            """, (start_ts, end_ts))
            
            deleted_count = cur.rowcount # 取得受影響的資料筆數
            msg = f"🗑️ 已刪除 {start_date} 至 {end_date} 期間的訂單，共 {deleted_count} 筆。"
            
        else:
            msg = "❌ 無效的操作"

        conn.commit()
    except Exception as e:
        conn.rollback() # 發生錯誤時撤銷資料庫變更
        msg = f"❌ 刪除失敗: {str(e)}"
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('admin.admin_panel', msg=msg))

@admin_bp.route('/toggle_product/<int:pid>', methods=['POST'])
@login_required
@role_required('admin')  
def toggle_product(pid):
    """
    切換單一產品的上下架狀態 (is_available)
    主要設計給前端 AJAX 呼叫使用。
    """
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # 先查詢目前的狀態
        cur.execute("SELECT is_available FROM products WHERE id = %s", (pid,))
        row = cur.fetchone()
        
        if row:
            new_s = not row[0] # 反轉狀態 (True 變 False，False 變 True)
            # 更新狀態回資料庫
            cur.execute("UPDATE products SET is_available = %s WHERE id = %s", (new_s, pid))
            conn.commit()
            # 回傳 JSON 格式給前端，更新 UI
            return jsonify({'status': 'success', 'is_available': new_s})
        
        # 若找不到該產品 ID
        return jsonify({'status': 'error', 'message': 'Product not found'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

@admin_bp.route('/delete_product/<int:pid>')
@login_required          
@role_required('admin')  
def delete_product(pid):
    """
    刪除單一產品。
    """
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id = %s", (pid,))
    conn.commit(); cur.close(); conn.close()
    return redirect(url_for('admin.admin_panel', msg="🗑️ 產品已刪除"))

@admin_bp.route('/reorder_products', methods=['POST'])
@login_required          
@role_required('admin')  
def reorder_products():
    """
    更新產品的排列順序。
    前端通常會透過拖曳 (Drag and Drop) 介面發送包含最新順序的產品 ID 陣列。
    """
    data = request.json # 接收前端傳來的 JSON 資料 (例如: {'order': [3, 1, 5, 2]})
    conn = get_db_connection(); cur = conn.cursor()
    try:
        # 使用 enumerate 取得陣列索引 (idx) 作為新的排序權重
        for idx, pid in enumerate(data.get('order', [])):
            cur.execute("UPDATE products SET sort_order = %s WHERE id = %s", (idx, pid))
        conn.commit()
        return jsonify({'stat': 'success'})
    except Exception as e:
        conn.rollback() # 出錯則復原交易，避免部分排序更新造成資料混亂
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        cur.close(); conn.close()
