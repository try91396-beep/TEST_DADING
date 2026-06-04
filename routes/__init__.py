# 從各個檔案匯入藍圖物件
from .menu_routes import menu_bp
from .kitchen_routes import kitchen_bp
from .admin_routes import admin_bp
from .delivery_routes import delivery_bp

# 這樣以後在 app.py 就可以一次匯入所有路由
