import os
from flask import Flask
from database import init_db

# 引用原本的路由
from routes import menu_bp, kitchen_bp, admin_bp, delivery_bp

# --- 新增引用：引用剛剛建立的 try_routes (資料庫檢視功能) ---
from routes.try_routes import try_bp 

# 💡 修改引入：把我們剛剛在 utils.py 寫好的 inject_user_info 一起引進來
from utils import start_background_tasks, inject_user_info

def create_app():
    app = Flask(__name__)

    # --- 設定 Secret Key (Session 必要) ---
    # 如果環境變數沒設定，就使用後面的預設字串 (開發用)
    # 在正式上線環境 (Render) 建議在環境變數設定 SECRET_KEY
    app.secret_key = os.environ.get("SECRET_KEY", "dev_secret_key_change_this_123")

    # 1. 初始化資料庫 (確保啟動時資料表都已建立)
    with app.app_context():
        init_db()

    # 2. 註冊路由藍圖 (Blueprints)
    
    # 前台點餐 (根目錄 /)
    app.register_blueprint(menu_bp)
    
    # 廚房看板 (路徑 /kitchen)
    app.register_blueprint(kitchen_bp, url_prefix='/kitchen')
    
    # 後台管理 (路徑 /admin)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # 外送服務 (路徑 /delivery)
    app.register_blueprint(delivery_bp, url_prefix='/delivery')

    # --- 新增註冊：資料庫檢視頁面 (路徑 /try) ---
    # 這讓我們可以透過網址 /try 來查看資料庫欄位
    app.register_blueprint(try_bp, url_prefix='/try')

    # ==========================================
    # 💡 新增註冊：上下文處理器 (Context Processor)
    # 這樣一來，所有的 HTML 網頁就都能直接讀取到 current_username 和 logout_url 了！
    # ==========================================
    app.context_processor(inject_user_info)

    # 3. 啟動背景任務 (排程發信、防休眠 Ping)
    start_background_tasks(app)

    return app

app = create_app()

if __name__ == '__main__':
    # 這裡的設定適合 Render 部署與本地測試
    app.run(host='0.0.0.0', port=10000, debug=False)
