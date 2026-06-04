from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from datetime import datetime, timedelta, time
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError
from haversine import haversine, Unit
from database import get_db_connection
import re
import random

delivery_bp = Blueprint('delivery', __name__)

# --- 🗺️ 全域座標快取變數 (避免每次重複向地圖伺服器查詢，防止被封鎖且提升速度) ---
_CACHED_SHOP_ADDRESS = None
_CACHED_SHOP_COORDS = (25.054358, 121.543468)  # 初始預設座標

def get_dynamic_restaurant_coords(shop_address):
    """ 自動將資料庫的中文地址轉換為經緯度座標 """
    global _CACHED_SHOP_ADDRESS, _CACHED_SHOP_COORDS
    
    if not shop_address:
        return _CACHED_SHOP_COORDS
        
    # 如果地址跟上次查詢的一模一樣，直接回傳快取，一毫秒都不浪費
    if shop_address == _CACHED_SHOP_ADDRESS:
        return _CACHED_SHOP_COORDS
        
    try:
        # 建立地圖定位器
        geolocator = Nominatim(user_agent="my_food_delivery_system_v1")
        location = geolocator.geocode(shop_address, timeout=5)
        if location:
            _CACHED_SHOP_ADDRESS = shop_address
            _CACHED_SHOP_COORDS = (location.latitude, location.longitude)
            print(f"🗺️ 餐廳地址已成功重新定位中心點: {_CACHED_SHOP_COORDS}")
            return _CACHED_SHOP_COORDS
    except Exception as e:
        print(f"⚠️ 餐廳地址解析失敗 ({e})，暫時沿用歷史座標")
        
    return _CACHED_SHOP_COORDS

def get_delivery_settings():
    """ 從資料庫讀取外送與店家設定，並整合動態地址解析 """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings")
    rows = cur.fetchall()
    conn.close()
    
    # 轉成字典，如 {'shop_address': '台北市...', 'delivery_fee_base': '50'}
    s = {row[0]: row[1] for row in rows}
    
    # 獲取中文地址並動態計算最新經緯度
    shop_address = s.get('shop_address', '')
    restaurant_coords = get_dynamic_restaurant_coords(shop_address)
    
    return {
        'enabled': s.get('delivery_enabled', '1') == '1',
        'min_price': int(s.get('delivery_min_price', 500)),
        'max_km': float(s.get('delivery_max_km', 5.0)),          # 最大外送公里
        'base_fee': int(s.get('delivery_fee_base', 0)),          # 基礎運費
        'fee_per_km': int(s.get('delivery_fee_per_km', 10)),      # 每公里加價
        'shop_name': s.get('shop_name', '我的美味餐廳'),
        'shop_logo_url': s.get('shop_logo_url', ''),
        'shop_address': shop_address,
        'restaurant_coords': restaurant_coords                    # 動態產出的經緯度元組 (lat, lng)
    }

def normalize_address(addr):
    """ 基本清洗：移除郵遞區號與樓層 """
    addr = re.sub(r'^\d{3,5}\s?', '', addr) # 移除開頭郵遞區號
    addr = re.sub(r'(\d+[Ff樓].*)|(B\d+.*)|(地下.*)|(室.*)', '', addr) # 移除樓層
    return addr.strip()

def extract_road_only(addr):
    """ 終極手段：只抓取路名 """
    match = re.search(r'.+?[縣市].+?[區鄉鎮市].+?[路街大道巷]', addr)
    if match:
        return match.group(0)
    match_simple = re.search(r'.+?[路街大道]', addr)
    if match_simple:
        return match_simple.group(0)
    return addr 

def generate_time_slots(base_date):
    """ 產生單日可外送時段 """
    slots = []
    start_time = time(10, 30)
    end_time = time(20, 30)
    block_start = time(11, 30)
    block_end = time(13, 30)
    
    current_dt = datetime.combine(base_date, start_time)
    end_dt = datetime.combine(base_date, end_time)
    now_tw = datetime.utcnow() + timedelta(hours=8)
    
    while current_dt <= end_dt:
        t = current_dt.time()
        # 如果是「今天」，過濾掉已經過去的時間 (保留30分鐘緩衝)
        if base_date == now_tw.date() and current_dt < (now_tw + timedelta(minutes=30)):
            current_dt += timedelta(minutes=30)
            continue
            
        in_forbidden_zone = (t >= block_start and t <= block_end)
        
        if not in_forbidden_zone:
            time_str = current_dt.strftime("%H:%M")
            slots.append(time_str)
            
        current_dt += timedelta(minutes=30)
        
    return slots

# ==========================================
# ⚙️ 1. 初始化/設定外送時間頁面
# ==========================================
@delivery_bp.route('/setup')
def setup():
    settings = get_delivery_settings()
    if not settings['enabled']:
        return "<script>alert('抱歉，目前暫停外送服務'); window.location.href='/';</script>"

    date_options = []
    now = datetime.utcnow() + timedelta(hours=8)
    
    for i in range(3):
        d = (now + timedelta(days=i)).date()
        val = d.strftime("%Y-%m-%d")
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        label_date = f"{d.strftime('%m/%d')} ({weekdays[d.weekday()]})"
        if i == 0: label_date += " (今天)"
        
        slots = generate_time_slots(d)
        if slots:
            date_options.append({
                'value': val, 
                'label': label_date,
                'slots': slots
            })

    return render_template('delivery_setup.html', dates=date_options, settings=settings)

# ==========================================
# 📍 2. 地址校驗與運費計算核心 API
# ==========================================
@delivery_bp.route('/check', methods=['POST'])
def check_address():
    data = request.json or {}
    raw_address = data.get('address', '').strip()
    name = data.get('name')
    phone = data.get('phone')
    delivery_date = data.get('date')
    delivery_time = data.get('time')
    
    if not all([raw_address, name, phone, delivery_date, delivery_time]):
        return jsonify({'success': False, 'msg': '請填寫完整資訊 (含日期與時間)'})
    
    # 隨機 User-Agent 避免封鎖
    ua_string = f"mbdv_delivery_app_user_{random.randint(10000, 99999)}"
    geolocator = Nominatim(user_agent=ua_string)
    
    location = None
    fallback_level = 0
    settings = get_delivery_settings()  # 這裡會取得正確的 DB 設定與最新餐廳座標

    scheduled_for = f"{delivery_date} {delivery_time}"

    try:
        # --- 第一層：標準清洗 ---
        search_addr = normalize_address(raw_address)
        query = f"台灣 {search_addr}"
        location = geolocator.geocode(query, timeout=5)
        
        # --- 第二層：去連字號 ---
        if not location:
            addr_no_dash = re.sub(r'(\d+)[-‐‑]\d+號', r'\1號', search_addr)
            addr_no_dash = re.sub(r'(\d+號)之\d+', r'\1', addr_no_dash)
            if addr_no_dash != search_addr:
                print(f"嘗試降級搜尋 (去號): {addr_no_dash}")
                location = geolocator.geocode(f"台灣 {addr_no_dash}", timeout=5)
                fallback_level = 1

        # --- 第三層：只搜路名 ---
        if not location:
            road_only = extract_road_only(search_addr)
            if road_only != search_addr:
                print(f"嘗試終極搜尋 (只搜路名): {road_only}")
                location = geolocator.geocode(f"台灣 {road_only}", timeout=5)
                fallback_level = 2

        # --- 判斷結果 ---
        if location:
            user_coords = (location.latitude, location.longitude)
            
            # 💡 修正處：將原本寫死的 RESTAURANT_COORDS 替換為資料庫最新動態座標 settings['restaurant_coords']
            dist = haversine(settings['restaurant_coords'], user_coords, unit=Unit.KILOMETERS)
            
            # 使用從 DB 讀取的 max_km 進行安全範圍校驗
            if dist > settings['max_km']:
                return jsonify({
                    'success': False, 
                    'msg': f'超出外送範圍 (距離 {dist:.1f}km, 本店目前限制 {settings["max_km"]}km)'
                })

            # 計算運費：基本費 (從 DB delivery_fee_base 讀來) + (距離 * 每公里費率)
            shipping_fee = settings['base_fee'] + int(dist * settings['fee_per_km'])
            
            note = ""
            if fallback_level == 2:
                note = "(以路段中心估算)"

            session['delivery_data'] = {
                'name': name,
                'phone': phone,
                'address': raw_address,
                'scheduled_for': scheduled_for
            }
            session['delivery_info'] = {
                'is_delivery': True,
                'distance_km': round(dist, 1),
                'shipping_fee': shipping_fee,
                'min_price': settings['min_price'],
                'note': note
            }
            session['table_num'] = '外送'
            session.modified = True
            
            return jsonify({'success': True, 'redirect': url_for('menu.menu', lang='zh')})

        else:
            return jsonify({
                'success': False, 
                'msg': '找不到此地址，請確認路名是否正確。'
            })

    except Exception as e:
        print(f"Geo Error (切換至人工模式): {e}")
        
        # 發生錯誤時的容錯（故障轉移）機制，運費暫時設為基本費
        session['delivery_data'] = {
            'name': name,
            'phone': phone,
            'address': raw_address,
            'scheduled_for': scheduled_for
        }
        
        session['delivery_info'] = {
            'is_delivery': True,
            'distance_km': 0,
            'shipping_fee': settings['base_fee'],  # 使用 DB 設定的基礎運費
            'min_price': settings['min_price'],
            'note': "⚠️ 地圖連線忙碌，運費僅為預估，將由專人電話確認"
        }
        
        session['table_num'] = '外送'
        session.modified = True
        
        return jsonify({
            'success': True, 
            'redirect': url_for('menu.menu', lang='zh'),
            'msg': '地圖連線忙碌，將轉為人工確認模式'
        })
