import os  # 匯入作業系統模組，用於讀取環境變數
import psycopg2  # 匯入 PostgreSQL 資料庫驅動模組 
from urllib.parse import urlparse  # 匯入網址解析工具
import bcrypt  # 匯入 bcrypt 模組用於密碼雜湊 (需先安裝: pip install bcrypt)

# --- 資料庫基礎連線 --- 
def get_db_connection():
    """建立並回傳 PostgreSQL 資料庫連線物件"""
    # 從作業系統環境變數中取得 DATABASE_URL
    db_uri = os.environ.get("DATABASE_URL")
    if not db_uri: 
        raise ValueError("錯誤：找不到環境變數 DATABASE_URL")
    
    # 修正 Render / Heroku 等雲端平台的舊版 URI 相容性問題 (postgres:// -> postgresql://)
    if db_uri.startswith("postgres://"):
        db_uri = db_uri.replace("postgres://", "postgresql://", 1)

    # 使用 psycopg2 套件建立與 PostgreSQL 的連線
    return psycopg2.connect(db_uri)

# --- 資料庫初始化與欄位 Migration ---
def init_db():
    """
    建立所有必要的資料表與預設設定。
    回傳 True 表示成功，False 表示失敗。
    """
    conn = None  # 預設連線變數為空
    cur = None   # 預設遊標（Cursor）變數為空
    try:
        conn = get_db_connection()  # 取得資料庫連線
        conn.autocommit = True      # 設定為「自動提交」，每執行一個 SQL 指令即生效
        cur = conn.cursor()         # 開啟遊標以執行 SQL 指令

        # 1. 建立產品表 (products)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,              -- 自動遞增的主鍵 ID
                name VARCHAR(100) NOT NULL,        -- 產品名稱（必填）
                price INTEGER NOT NULL,            -- 價格（必填）
                category VARCHAR(50),              -- 分類名稱
                image_url TEXT,                    -- 圖片網址
                is_available BOOLEAN DEFAULT TRUE, -- 是否上架（預設為是）
                custom_options TEXT,               -- 自定義選項（如：辣度、冰塊）
                sort_order INTEGER DEFAULT 100,    -- 排序序號
                name_en VARCHAR(100),              -- 英文品名
                name_jp VARCHAR(100),              -- 日文品名
                name_kr VARCHAR(100),              -- 韓文品名
                custom_options_en TEXT,            -- 英文自定義選項
                custom_options_jp TEXT,            -- 日文自定義選項
                custom_options_kr TEXT,            -- 韓文自定義選項
                print_category VARCHAR(20) DEFAULT 'Noodle', -- 出單分類（用於廚房出單）
                category_en VARCHAR(50),           -- 英文分類名
                category_jp VARCHAR(50),           -- 日文分類名
                category_kr VARCHAR(50)            -- 韓文分類名
            );
        ''')

        # 2. 建立訂單表 (orders)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,              -- 訂單 ID
                table_number VARCHAR(10),          -- 桌號
                items TEXT NOT NULL,               -- 訂單項目內容（文字描述）
                total_price INTEGER NOT NULL,      -- 總金額
                status VARCHAR(20) DEFAULT 'Pending', -- 訂單狀態（預設為待處理）
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- 建立時間
                daily_seq INTEGER DEFAULT 0,       -- 當日流水號
                content_json TEXT,                  -- 以 JSON 格式存儲的訂單明細
                need_receipt BOOLEAN DEFAULT FALSE, -- 是否需要收據/統編
                lang VARCHAR(10) DEFAULT 'zh',     -- 下單時使用的語系

                -- 外送相關欄位
                order_type VARCHAR(50) DEFAULT 'dine_in', -- 訂單類型（內用/外送/自取）
                delivery_info TEXT,                -- 綜合外送資訊
                customer_name TEXT,                -- 客戶姓名
                customer_phone TEXT,               -- 客戶電話
                customer_address TEXT,              -- 客戶地址
                scheduled_for TEXT,                -- 預約送達時間
                delivery_fee INTEGER DEFAULT 0     -- 外送費
            );
        ''')

        # 3. 建立系統設定表 (settings)
        cur.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);''')

        # 4. 插入預設設定
        default_settings = [
            ('sender_email', 'onboarding@resend.dev'), # 預設發信人郵件
            ('report_email', 'onboarding@resend.dev'), # 預設收信人郵件
            ('resend_api_key', ''),                    # resend_api_key
            ('shop_open', '1'),                        # 預設全店營業中 (1: 開啟)
            ('delivery_enabled', '1'),                 # 是否啟用外送功能 (後端用)
            ('enable_delivery', '1'),                  # 前端按鈕可能使用的 key (保持相容)
            ('delivery_min_price', '500'),             # 外送起送價
            ('delivery_fee_base', '0'),                # 基礎外送費
            ('delivery_max_km', '5'),                  # 最大外送距離 (公里)
            ('delivery_fee_per_km', '10'),             # 超過基礎距離後的每公里加價

            # --- 店家相關資訊欄位 ---
            ('shop_name', '我的美味餐廳'),                       # 店家名稱
            ('shop_address', '台北市信義區OO路XX號'),            # 店家地址
            ('shop_phone', '02-12345678'),                      # 店家電話
            ('shop_open_time', '10:30'),                        # 開店時間
            ('shop_close_time', '20:30'),                       # 閉店時間
            ('shop_logo_url', 'https://example.com/logo.png'),  # 商標網址
            ('shop_panda_url', 'https://panda.com'),            # 外送平台網址
            ('shop_open_advance_hours', '1'),                   # 提早開店
            ('shop_close_delay_hours', '1'),                    # 延後關店
            ('last_auto_open_date', '1'),                       # 紀錄開店
            ('last_auto_close_date', '1')                       # 記錄關店
        ]

        for k, v in default_settings:
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, v))

        # 5. 建立使用者資料表 (users)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,              -- 使用者 ID
                username VARCHAR(50) UNIQUE NOT NULL, -- 帳號名稱 (必須唯一)
                password_hash TEXT NOT NULL,       -- 密碼的雜湊值 (絕對不存明文)
                role VARCHAR(20) DEFAULT 'admin',  -- 角色權限 (例如: admin, staff)
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- 建立時間
                salary_type VARCHAR(20) DEFAULT 'hourly',     -- 薪資類型 ('hourly' 或 'monthly')
                hourly_wage NUMERIC(10, 2) DEFAULT 183,       -- 時薪預設值 (改用 NUMERIC 支援小數)
                monthly_wage NUMERIC(10, 2) DEFAULT 27470     -- 月薪預設值 (改用 NUMERIC 支援小數)
            );
        ''')

        # 6. 建立打卡紀錄表 (clock_records)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS clock_records (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                work_date DATE NOT NULL,          -- 工作日期 (用於快速查詢特定月份/年份)
                clock_in TIMESTAMP,               -- 上班時間
                clock_out TIMESTAMP,              -- 下班時間
                work_hours NUMERIC(5, 2) DEFAULT 0, -- 結算工時
                status VARCHAR(20) DEFAULT 'normal', -- 'normal'(正常), 'leave'(請假), 'missed_fixed'(補登)
                break_start TIME DEFAULT '12:00:00', -- 休息開始時間
                break_end TIME DEFAULT '13:00:00'    -- 休息結束時間
            );
            CREATE INDEX IF NOT EXISTS idx_work_date ON clock_records(work_date);
        ''')

        # 7. 建立請假與補打卡申請表 (attendance_requests)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS attendance_requests (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                request_type VARCHAR(20) NOT NULL, -- 'leave' (請假) 或 'missed_punch' (補打卡)
                leave_type VARCHAR(50),             -- 請假類別 (事假/病假/特休等)
                target_date DATE NOT NULL,          -- 申請日期
                start_time TIMESTAMP,               -- 請假/補打卡 開始時間
                end_time TIMESTAMP,                -- 請假/補打卡 結束時間
                reason TEXT,                        -- 申請理由
                status VARCHAR(20) DEFAULT 'pending', -- 'pending'(待審), 'approved'(通過), 'rejected'(退回)
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_by INTEGER REFERENCES users(id), -- 審核人 ID
                reviewed_at TIMESTAMP,                    -- 審核時間
                break_start TIME,                         -- 休息開始時間
                break_end TIME,                           -- 休息結束時間
                original_record_id INTEGER,               -- 原始紀錄 ID
                original_clock_in TIMESTAMP,              -- 原始上班打卡快照
                original_clock_out TIMESTAMP              -- 原始下班打卡快照
            );
        ''')

        # 8. 建立預設 Admin 帳號 (若資料表完全沒有使用者時自動產生)
        cur.execute("SELECT COUNT(*) FROM users")
        user_count = cur.fetchone()[0]

        if user_count == 0:
            print("👤 尚未建立任何使用者，正在建立預設的 Admin 帳號...")
            default_username = "admin"
            default_password = "password123"  # ⚠️ 請在登入後台後立即更改此密碼！

            # 使用 bcrypt 對密碼進行雜湊處理
            salt = bcrypt.gensalt()
            hashed_password = bcrypt.hashpw(default_password.encode('utf-8'), salt).decode('utf-8')

            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                (default_username, hashed_password, 'admin')
            )
            print(f"✅ 預設 Admin 帳號建立完成。帳號: {default_username} / 密碼: {default_password}")

        # 9. 欄位自動補全 (Migration)
        alters = [
            # --- Orders 表格補全 ---
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS lang VARCHAR(10) DEFAULT 'zh';",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS content_json TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_type VARCHAR(50) DEFAULT 'dine_in';",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_info TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_phone TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_address TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS scheduled_for TEXT;",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_fee INTEGER DEFAULT 0;",
            
            # --- Users 表格補全 ---
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS salary_type VARCHAR(20) DEFAULT 'hourly';",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS hourly_wage NUMERIC(10, 2) DEFAULT 183;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_wage NUMERIC(10, 2) DEFAULT 27470;",

            # --- Clock Records 表格補全 ---
            "ALTER TABLE clock_records ADD COLUMN IF NOT EXISTS break_start TIME DEFAULT '12:00:00';",
            "ALTER TABLE clock_records ADD COLUMN IF NOT EXISTS break_end TIME DEFAULT '13:00:00';",

            # --- Attendance Requests 表格補全 ---
            "ALTER TABLE attendance_requests ADD COLUMN IF NOT EXISTS leave_type VARCHAR(50);",
            "ALTER TABLE attendance_requests ADD COLUMN IF NOT EXISTS reviewed_by INTEGER REFERENCES users(id);",
            "ALTER TABLE attendance_requests ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP;",
            "ALTER TABLE attendance_requests ADD COLUMN IF NOT EXISTS break_start TIME;",
            "ALTER TABLE attendance_requests ADD COLUMN IF NOT EXISTS break_end TIME;",
            "ALTER TABLE attendance_requests ADD COLUMN IF NOT EXISTS original_record_id INTEGER;",
            "ALTER TABLE attendance_requests ADD COLUMN IF NOT EXISTS original_clock_in TIMESTAMP;",
            "ALTER TABLE attendance_requests ADD COLUMN IF NOT EXISTS original_clock_out TIMESTAMP;",

            # --- Products 表格補全 ---
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 100;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS print_category VARCHAR(20) DEFAULT 'Noodle';",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_en VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_jp VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS name_kr VARCHAR(100);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS category_en VARCHAR(50);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS category_jp VARCHAR(50);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS category_kr VARCHAR(50);",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_en TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_jp TEXT;",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_options_kr TEXT;"
        ]

        print("🔄 正在檢查資料庫欄位結構與 Migration...")
        for cmd in alters:
            try:
                cur.execute(cmd)
            except Exception as e:
                if 'duplicate' not in str(e).lower() and 'exists' not in str(e).lower():
                    print(f"⚠️ Migration 警告: {e}")

        print("✅ 資料庫初始化檢查完成！")
        return True

    except Exception as e:
        print(f"❌ 資料庫初始化錯誤: {e}")
        return False

    finally:
        # 釋放與關閉資源
        if cur:
            cur.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    # 當直接執行此 .py 檔案時，啟動初始化程序
    init_db()
