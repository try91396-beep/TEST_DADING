import os  # 匯入作業系統模組，用於讀取環境變數
import psycopg2  # 匯入 PostgreSQL 資料庫驅動模組 
from urllib.parse import urlparse  # 匯入網址解析工具
import bcrypt # 匯入 bcrypt 模組用於密碼雜湊 (需先安裝: pip install bcrypt)

# --- 資料庫基礎連線 --- 
def get_db_connection():
    """建立並回傳資料庫連線物件"""
    # 從作業系統環境變數中取得 DATABASE_URL（包含資料庫主機、帳密等資訊）
    db_uri = os.environ.get("DATABASE_URL")
    if not db_uri: 
        # 如果找不到連線資訊，拋出錯誤訊息
        raise ValueError("錯誤：找不到環境變數 DATABASE_URL")
    # 使用 psycopg2 套件建立與 PostgreSQL 的連線
    return psycopg2.connect(db_uri)

# --- 資料庫初始化 ---
def init_db():
    """
    建立所有必要的資料表與預設設定。
    回傳 True 表示成功，False 表示失敗。
    """
    conn = None # 預設連線變數為空
    cur = None  # 預設遊標（Cursor）變數為空
    try:
        conn = get_db_connection() # 取得資料庫連線
        conn.autocommit = True     # 設定為「自動提交」，每執行一個 SQL 指令即生效
        cur = conn.cursor()        # 開啟遊標以執行 SQL 指令

        # 1. 建立產品表 (products)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,            -- 自動遞增的主鍵 ID
                name VARCHAR(100) NOT NULL,       -- 產品名稱（必填）
                price INTEGER NOT NULL,           -- 價格（必填）
                category VARCHAR(50),             -- 分類名稱
                image_url TEXT,                   -- 圖片網址
                is_available BOOLEAN DEFAULT TRUE,-- 是否上架（預設為是）
                custom_options TEXT,              -- 自定義選項（如：辣度、冰塊）
                sort_order INTEGER DEFAULT 100,   -- 排序序號
                name_en VARCHAR(100),             -- 英文品名
                name_jp VARCHAR(100),             -- 日文品名
                name_kr VARCHAR(100),             -- 韓文品名
                custom_options_en TEXT,           -- 英文自定義選項
                custom_options_jp TEXT,           -- 日文自定義選項
                custom_options_kr TEXT,           -- 韓文自定義選項
                print_category VARCHAR(20) DEFAULT 'Noodle', -- 出單分類（用於廚房出單）
                category_en VARCHAR(50),          -- 英文分類名
                category_jp VARCHAR(50),          -- 日文分類名
                category_kr VARCHAR(50)           -- 韓文分類名
            );
        ''')
        
        # 2. 建立訂單表 (orders)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,            -- 訂單 ID
                table_number VARCHAR(10),         -- 桌號
                items TEXT NOT NULL,              -- 訂單項目內容（文字描述）
                total_price INTEGER NOT NULL,     -- 總金額
                status VARCHAR(20) DEFAULT 'Pending', -- 訂單狀態（預設為待處理）
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- 建立時間
                daily_seq INTEGER DEFAULT 0,      -- 當日流水號
                content_json TEXT,                -- 以 JSON 格式存儲的訂單明細
                need_receipt BOOLEAN DEFAULT FALSE, -- 是否需要收據/統編
                lang VARCHAR(10) DEFAULT 'zh',    -- 下單時使用的語系
                
                -- 外送相關欄位
                order_type VARCHAR(50) DEFAULT 'dine_in', -- 訂單類型（內用/外送/自取）
                delivery_info TEXT,               -- 綜合外送資訊
                customer_name TEXT,               -- 客戶姓名
                customer_phone TEXT,              -- 客戶電話
                customer_address TEXT,            -- 客戶地址
                scheduled_for TEXT,               -- 預約送達時間
                delivery_fee INTEGER DEFAULT 0    -- 外送費
            );
        ''')
        
        # 3. 建立系統設定表 (settings)
        cur.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);''')

        # 4. 插入預設設定 (新增了 shop_open 與其他外送參數)
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
            ('delivery_fee_per_km', '10'),              # 超過基礎距離後的每公里加價

            # --- 🆕 這裡是你新要求新增的店家相關資訊欄位 ---
            
            ('shop_name', '我的美味餐廳'),                       # 店家名稱
            ('shop_address', '台北市信義區OO路XX號'),            # 店家地址
            ('shop_phone', '02-12345678'),                      # 店家電話
            ('shop_open_time', '10:30'),                        # 開店時間 (建議使用 24 點制字串，方便前端解析)
            ('shop_close_time', '20:30'),                       # 閉店時間
            ('shop_logo_url', 'https://example.com/logo.png'),  # 商標網址 (Logo URL)
            ('shop_panda_url', 'https://panda.com'),            # 外送平台網址
            ('shop_open_advance_hours', '1'),                   # 提早開店
            ('shop_close_delay_hours', '1'),                    # 延後關店
            ('last_auto_open_date', '1'),                       # 紀錄開店
            ('last_auto_close_date', '1')                       # 記錄關店
        ]
        
        for k, v in default_settings:
            # 插入設定值，如果 Key 已經存在則跳過 (ON CONFLICT DO NOTHING)
            # 這樣可以確保新增加的設定 (如 shop_open) 會被寫入，而已存在的設定不會被覆蓋
            cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, v))


        # === 💡 關鍵修正：砍掉舊的 users 表格，確保能夠建立最新帶有 password_hash 的版本 ===
        #cur.execute("DROP TABLE IF EXISTS users CASCADE;")
        
        # 建立使用者資料表 (users)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,            -- 使用者 ID
                username VARCHAR(50) UNIQUE NOT NULL, -- 帳號名稱 (必須唯一)
                password_hash TEXT NOT NULL,      -- 密碼的雜湊值 (絕對不存明文)
                role VARCHAR(20) DEFAULT 'admin', -- 角色權限 (例如: admin, staff)
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- 建立時間
            );
        ''')

        # 建立一個預設的 Admin 帳號 (因為上面我們 DROP TABLE 了，所以這裡一定會重新建立)
        cur.execute("SELECT COUNT(*) FROM users")
        user_count = cur.fetchone()[0]
        
        if user_count == 0:
            print("👤 尚未建立任何使用者，正在建立預設的 Admin 帳號...")
            default_username = "admin"
            default_password = "password123" # ⚠️ 請在登入後台後立即更改此密碼！
            
            # 使用 bcrypt 對密碼進行雜湊處理
            # 必須將字串轉為 bytes (encode('utf-8'))
            salt = bcrypt.gensalt()
            hashed_password = bcrypt.hashpw(default_password.encode('utf-8'), salt).decode('utf-8')
            
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                (default_username, hashed_password, 'admin')
            )
            print(f"✅ 預設 Admin 帳號建立完成。帳號: {default_username} / 密碼: {default_password}")


        # 5. 【關鍵】欄位自動補全 (Migration)
        # 此段確保如果資料表已經存在，但缺少新開發的欄位時，會自動新增欄位
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
            
            # --- Products 表格補全 (防止舊資料庫缺少多語系欄位) ---
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
        
        print("🔄 正在檢查資料庫欄位結構...")
        for cmd in alters:
            try:
                cur.execute(cmd) # 執行增加欄位的指令
            except Exception as e:
                # 攔截錯誤，如果是「重複欄位」或「已存在」的報錯則忽略，其餘印出警告
                # PostgreSQL 的 ADD COLUMN IF NOT EXISTS 在舊版本可能不支援，
                # 所以這裡保留 try-except 以確保相容性
                if 'duplicate' not in str(e).lower() and 'exists' not in str(e).lower():
                    print(f"⚠️ Warning during migration: {e}")

        print("✅ 資料庫初始化檢查完成 (含 order_type, delivery_info, products 多語系欄位, 及 users 表格)")
        return True

    except Exception as e:
        # 捕獲初始化過程中的任何重大錯誤
        print(f"❌ 資料庫初始化錯誤: {e}")
        return False
    
    finally:
        # 無論成功或失敗，最後都必須關閉遊標與連線，釋放資源
        if cur:
            cur.close()
        if conn:
            conn.close()

if __name__ == "__main__":
    # 當直接執行此 .py 檔案時，啟動初始化程序
    init_db()

