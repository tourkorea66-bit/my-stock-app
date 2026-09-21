import os
import json
import re
import io
import zipfile
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import FinanceDataReader as fdr

# SSL 경고 메세지 감추기
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 페이지 기본 설정
st.set_page_config(page_title="POR 밴드 시뮬레이터", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CORP_CODE_CACHE_FILE = os.path.join(BASE_DIR, 'corp_code_map.json')
LOCAL_STORAGE_FILE = os.path.join(BASE_DIR, 'stocks_data.json')

# ==========================================
# 🔑 Open DART API 키 설정
DART_API_KEY = "28b4dc2f6fac759fc70daa06cb0e9761eda3c105".strip()

SHARED_STORE_URL = "https://149.28.223.197/MzdTavSteRuyBzBFpDorrt/scripts/por_stock_data"
# ==========================================

DEFAULT_STOCKS = {
    '반도체': {
        'SK하이닉스': {'code': '000660', 'ops': {}},
        '티엘비': {'code': '356860', 'ops': {}},
        '엠케이전자': {'code': '033160', 'ops': {}},
        'ISC': {'code': '095340', 'ops': {}},
        '엘티씨': {'code': '170920', 'ops': {}},
        '하나마이크론': {'code': '067310', 'ops': {}},
        '하나머티리얼즈': {'code': '166090', 'ops': {}},
        '코미코': {'code': '183300', 'ops': {}},
        '에프에스티': {'code': '036810', 'ops': {}}
    },
    '관심종목': {}
}

# --- 데이터 저장/로드 로직 (로컬 파일 최우선) ---
def load_stocks_data_public():
    if os.path.exists(LOCAL_STORAGE_FILE):
        try:
            with open(LOCAL_STORAGE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict) and len(data) > 0:
                    return data
        except Exception as e:
            st.sidebar.warning(f"로컬 파일 읽기 오류: {e}")

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        fetch_url = f"{SHARED_STORE_URL}?_t={int(time.time())}"
        res = requests.get(fetch_url, headers=headers, timeout=3, verify=False)
        if res.status_code == 200 and res.text.strip():
            data = res.json()
            if isinstance(data, dict) and len(data) > 0:
                return data
    except Exception:
        pass

    return DEFAULT_STOCKS.copy()

def save_stocks_data_public(data):
    success = False
    try:
        with open(LOCAL_STORAGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        success = True
    except Exception as e:
        st.sidebar.error(f"로컬 저장 실패: {e}")

    try:
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        json_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
        requests.post(SHARED_STORE_URL, data=json_bytes, headers=headers, timeout=3, verify=False)
    except Exception:
        pass

    return success

# Session State 초기화
if 'stock_categories' not in st.session_state:
    st.session_state.stock_categories = load_stocks_data_public()

if 'ops_data' not in st.session_state:
    st.session_state.ops_data = {}

# KRX 상장 종목 데이터 (캐싱)
@st.cache_data(ttl=86400)
def get_krx_stock_list():
    try:
        return fdr.StockListing('KRX')
    except Exception:
        return pd.DataFrame()

krx_df = get_krx_stock_list()

# --- DART 고유번호 매핑 로컬 파일 캐싱 ---
@st.cache_data(ttl=86400 * 30)
def get_dart_corp_code_map(api_key):
    if os.path.exists(CORP_CODE_CACHE_FILE):
        try:
            with open(CORP_CODE_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass

    corp_map = {}
    clean_key = str(api_key).strip()
    if not clean_key or clean_key == "YOUR_DART_API_KEY_HERE":
        return corp_map
    
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={clean_key}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                xml_data = z.read('CORPCODE.xml')
                root = ET.fromstring(xml_data)
                for list_item in root.findall('list'):
                    stock_code = list_item.findtext('stock_code', '').strip()
                    corp_code = list_item.findtext('corp_code', '').strip()
                    if stock_code and corp_code:
                        corp_map[stock_code.zfill(6)] = corp_code
            with open(CORP_CODE_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(corp_map, f)
    except Exception:
        pass
    return corp_map

def _fetch_single_year_dart(args):
    b_year, clean_key, corp_code = args
    try:
        url = f"https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?crtfc_key={clean_key}&corp_code={corp_code}&bsns_year={b_year}&reprt_code=11011"
        res = requests.get(url, timeout=3)
        if res.status_code == 200 and res.text.strip():
            data = res.json()
            if data.get('status') == '000' and 'list' in data:
                for item in data['list']:
                    account_nm = item.get('account_nm', '')
                    if ('영업이익' in account_nm or '영업손실' in account_nm) and '률' not in account_nm:
                        val_str = item.get('thstrm_amount', '0').replace(',', '').strip()
                        if val_str and val_str != '-':
                            return b_year, float(val_str)
    except Exception:
        pass
    return b_year, 0.0

@st.cache_data(ttl=86400)
def fetch_operating_profit_dart(code, api_key):
    ops = {'2021': 0.0, '2022': 0.0, '2023': 0.0, '2024': 0.0, '2025': 0.0, '2026': 0.0}
    clean_key = str(api_key).strip()
    
    if not clean_key or clean_key == "YOUR_DART_API_KEY_HERE":
        return ops

    corp_map = get_dart_corp_code_map(clean_key)
    corp_code = corp_map.get(str(code).zfill(6))
    if not corp_code:
        return ops

    years = ['2021', '2022', '2023', '2024', '2025', '2026']
    tasks = [(yr, clean_key, corp_code) for yr in years]

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = executor.map(_fetch_single_year_dart, tasks)
        for yr, val in results:
            ops[yr] = val

    return ops

# ==================== 사이드바 ====================
st.sidebar.title("⚙️ 카테고리 & 종목 관리")

if st.sidebar.button("🔄 데이터 다시 불러오기"):
    st.session_state.stock_categories = load_stocks_data_public()
    st.session_state.ops_data = {}
    st.sidebar.success("최신 데이터 동기화 완료!")
    st.rerun()

if st.sidebar.button("💾 변경사항 전체 저장", type="primary"):
    if save_stocks_data_public(st.session_state.stock_categories):
        st.sidebar.success("성공적으로 저장되었습니다!")

if DART_API_KEY == "YOUR_DART_API_KEY_HERE":
    dart_key_input = st.sidebar.text_input("🔑 Open DART API 키 입력", type="password")
    if dart_key_input:
        DART_API_KEY = dart_key_input.strip()

# --- 📁 카테고리 추가 ---
with st.sidebar.expander("📁 카테고리 추가"):
    new_cat_name = st.text_input("새 카테고리 이름", key="new_cat_input").strip()
    if st.button("카테고리 생성"):
        if new_cat_name and new_cat_name not in st.session_state.stock_categories:
            st.session_state.stock_categories[new_cat_name] = {}
            save_stocks_data_public(st.session_state.stock_categories)
            st.success(f"'{new_cat_name}' 카테고리가 생성되었습니다.")
            st.rerun()

# --- ➕ 신규 종목 추가 ---
with st.sidebar.expander("➕ 신규 종목 추가"):
    if not krx_df.empty:
        search_options = [f"{row['Name']} ({row['Code']})" for _, row in krx_df.iterrows() if 'Name' in row and 'Code' in row]
        selected_search = st.selectbox("KRX 종목 검색:", options=["선택하세요..."] + search_options)
        target_cat = st.selectbox("추가할 카테고리 선택:", list(st.session_state.stock_categories.keys()))
        
        if st.button("종목 추가"):
            if selected_search != "선택하세요...":
                name = selected_search.split(" (")[0]
                code = selected_search.split(" (")[1].replace(")", "")
                
                if target_cat in st.session_state.stock_categories:
                    st.session_state.stock_categories[target_cat][name] = {'code': code, 'ops': {}}
                    save_stocks_data_public(st.session_state.stock_categories)
                    st.success(f"'{name}' 종목이 추가되었습니다!")
                    st.rerun()

st.sidebar.markdown("---")
st.sidebar.title("🔍 분석 대상 선택")
category_list = [cat for cat, stocks in st.session_state.stock_categories.items() if stocks]

if not category_list:
    st.warning("등록된 종목이 없습니다. 카테고리나 종목을 추가해주세요.")
    st.stop()

selected_category = st.sidebar.selectbox("카테고리 선택:", category_list)
available_stocks = st.session_state.stock_categories[selected_category]

# 세션에 삭제/이동 반영 시 KeyError 방지
stock_names = list(available_stocks.keys())
selected_stock = st.sidebar.selectbox("종목 선택:", stock_names)

stock_info = available_stocks[selected_stock]
stock_code = stock_info['code']

if st.sidebar.button(f"❌ {selected_stock} 삭제"):
    del st.session_state.stock_categories[selected_category][selected_stock]
    save_stocks_data_public(st.session_state.stock_categories)
    st.success(f"{selected_stock} 삭제 완료")
    st.rerun()

# ==================== 메인 화면 ====================
st.title(f"📈 [{selected_category}] {selected_stock} ({stock_code}) POR 밴드 시뮬레이션")

past_years = ['2021', '2022', '2023', '2024', '2025']
saved_ops = stock_info.get('ops', {})

if selected_stock not in st.session_state.ops_data:
    st.session_state.ops_data[selected_stock] = {}
    
    dart_ops = fetch_operating_profit_dart(stock_code, DART_API_KEY)
    
    for yr in past_years:
        if yr in saved_ops and saved_ops[yr] != 0.0:
            st.session_state.ops_data[selected_stock][yr] = float(saved_ops[yr])
        else:
            st.session_state.ops_data[selected_stock][yr] = float(dart_ops.get(yr, 0.0) / 100_000_000.0)
            
    if '2026' in saved_ops and saved_ops['2026'] != 0.0:
        st.session_state.ops_data[selected_stock]['2026'] = float(saved_ops['2026'])
    else:
        st.session_state.ops_data[selected_stock]['2026'] = float(dart_ops.get('2026', 0.0) / 100_000_000.0)

st.subheader("📊 연도별 영업이익 현황 및 추정치 (단위: 억원)")

final_ops = {}
p_cols = st.columns(len(past_years))

has_negative_op = False

for idx, yr in enumerate(past_years):
    with p_cols[idx]:
        val_input = st.number_input(
            f"{yr}년 실적(억원)",
            value=st.session_state.ops_data[selected_stock].get(yr, 0.0),
            step=10.0,
            format="%.1f",
            key=f"input_past_{selected_stock}_{yr}"
        )
        st.session_state.ops_data[selected_stock][yr] = val_input
        st.session_state.stock_categories[selected_category][selected_stock]['ops'][yr] = val_input
        final_ops[yr] = val_input * 100_000_000.0
        
        if val_input < 0:
            st.markdown(f"<p style='color: #FF4B4B; font-weight: bold; margin-top: -10px;'>🔴 {val_input:,.1f} 억 (적자)</p>", unsafe_allow_html=True)
            has_negative_op = True
        else:
            st.markdown(f"<p style='color: #00C853; font-size: 0.85em; margin-top: -10px;'>🟢 흑자</p>", unsafe_allow_html=True)

f_cols = st.columns(4)
with f_cols[0]:
    input_2026 = st.number_input(
        "2026년 추정(억원)", 
        value=st.session_state.ops_data[selected_stock].get('2026', 0.0), 
        step=10.0, 
        format="%.1f",
        key=f"input_future_{selected_stock}_2026"
    )
    st.session_state.ops_data[selected_stock]['2026'] = input_2026
    st.session_state.stock_categories[selected_category][selected_stock]['ops']['2026'] = input_2026
    final_ops['2026'] = input_2026 * 100_000_000.0
    
    if input_2026 < 0:
        st.markdown(f"<p style='color: #FF4B4B; font-weight: bold; margin-top: -10px;'>🔴 {input_2026:,.1f} 억 (적자 추정)</p>", unsafe_allow_html=True)
        has_negative_op = True
    else:
        st.markdown(f"<p style='color: #00C853; font-size: 0.85em; margin-top: -10px;'>🟢 흑자 추정</p>", unsafe_allow_html=True)

if has_negative_op:
    st.warning("⚠️ 영업이익이 적자(마이너스)인 구간은 POR 산출 공식상 'N/A' 처리되어 차트선이 연결되지 않을 수 있습니다.")

# 주가 데이터 처리 및 차트 생성
end_date = datetime.today()
start_date = datetime(end_date.year - 5, 1, 1)

@st.cache_data(ttl=3600)
def get_stock_data_api(code, start, end):
    df = fdr.DataReader(code, start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d'))
    return df.reset_index()

try:
    stock_df = get_stock_data_api(stock_code, start_date, end_date)
except Exception as e:
    st.error(f"주가 데이터 불러오기 실패: {e}")
    st.stop()

if stock_df.empty:
    st.error("불러온 주가 데이터가 없습니다.")
    st.stop()

stock_df['날짜'] = pd.to_datetime(stock_df['Date'])
stock_df['종가'] = pd.to_numeric(stock_df['Close'], errors='coerce')
stock_df['연도'] = stock_df['날짜'].dt.year.astype(str)

if 'Marcap' in stock_df.columns and stock_df['Marcap'].notnull().sum() > 0:
    stock_df['시가총액'] = pd.to_numeric(stock_df['Marcap'], errors='coerce')
else:
    shares = 0
    if not krx_df.empty and 'Code' in krx_df.columns:
        matched = krx_df[krx_df['Code'] == stock_code]
        if not matched.empty:
            for col_name in ['Stocks', 'ListingShares', 'Shares', 'Marcap']:
                if col_name in matched.columns and pd.notnull(matched[col_name].values[0]):
                    val = float(matched[col_name].values[0])
                    if col_name == 'Marcap' and val > 0:
                        shares = val / matched['Close'].values[0] if 'Close' in matched.columns else 0
                    else:
                        shares = val
                    if shares > 0:
                        break
    stock_df['시가총액'] = stock_df['종가'] * shares if shares > 0 else np.nan

stock_df['수정_영업이익'] = stock_df['연도'].map(final_ops)
stock_df['수정_영업이익'] = pd.to_numeric(stock_df['수정_영업이익'], errors='coerce')

stock_df['수정_POR'] = np.where(
    (stock_df['수정_영업이익'].notnull()) & (stock_df['수정_영업이익'] > 0) & (stock_df['시가총액'].notnull()),
    stock_df['시가총액'] / stock_df['수정_영업이익'],
    np.nan
)

valid_por = stock_df['수정_POR'].dropna()

st.subheader("⚙️ POR 배수 범위 설정")

if not valid_por.empty and len(valid_por) > 0:
    min_por_val = float(valid_por.min())
    max_por_val = float(valid_por.max())
    med_por_val = float(valid_por.median())
    
    low_default = max(1.0, float(np.percentile(valid_por, 10)))
    mid_default = max(low_default + 1.0, med_por_val)
    high_default = max(mid_default + 1.0, float(np.percentile(valid_por, 90)))
else:
    min_por_val, max_por_val, med_por_val = 1.0, 30.0, 10.0
    low_default, mid_default, high_default = 5.0, 10.0, 15.0

col_b1, col_b2, col_b3 = st.columns(3)
with col_b1:
    por_low = st.number_input("저평가 배수 (Low)", value=round(low_default, 1), step=0.5, format="%.1f")
with col_b2:
    por_mid = st.number_input("적정 배수 (Mid)", value=round(mid_default, 1), step=0.5, format="%.1f")
with col_b3:
    por_high = st.number_input("고평가 배수 (High)", value=round(high_default, 1), step=0.5, format="%.1f")

stock_df['Band_Low'] = np.where(stock_df['수정_영업이익'] > 0, (stock_df['수정_영업이익'] * por_low) / (stock_df['시가총액'] / stock_df['종가']), np.nan)
stock_df['Band_Mid'] = np.where(stock_df['수정_영업이익'] > 0, (stock_df['수정_영업이익'] * por_mid) / (stock_df['시가총액'] / stock_df['종가']), np.nan)
stock_df['Band_High'] = np.where(stock_df['수정_영업이익'] > 0, (stock_df['수정_영업이익'] * por_high) / (stock_df['시가총액'] / stock_df['종가']), np.nan)

st.subheader("📈 주가 및 POR 밴드 차트")

fig = go.Figure()

fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['Band_High'], mode='lines', name=f'POR {por_high}x (고평가)', line=dict(color='rgba(239, 83, 80, 0.7)', width=1.5, dash='dash')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['Band_Mid'], mode='lines', name=f'POR {por_mid}x (적정)', line=dict(color='rgba(255, 179, 0, 0.8)', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['Band_Low'], mode='lines', name=f'POR {por_low}x (저평가)', line=dict(color='rgba(102, 187, 106, 0.7)', width=1.5, dash='dash')))

fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['종가'], mode='lines', name='실제 주가', line=dict(color='#2962FF', width=2.5)))

fig.update_layout(
    title=dict(text=f"<b>{selected_stock} ({stock_code}) POR Band Chart</b>", font=dict(size=18)),
    xaxis_title="날짜",
    yaxis_title="주가 (원)",
    hovermode="x unified",
    template="plotly_white",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=20, r=20, t=60, b=20)
)

st.plotly_chart(fig, use_container_width=True)

latest_row = stock_df.iloc[-1]
latest_price = latest_row['종가']
latest_por = latest_row['수정_POR']
latest_op = latest_row['수정_영업이익']

st.markdown("---")
st.subheader("💡 투자 지표 요약")

m1, m2, m3, m4 = st.columns(4)
m1.metric("현재 주가", f"{latest_price:,.0f} 원")

if pd.notnull(latest_por) and latest_por > 0:
    m2.metric("현재 POR (2026 추정 기준)", f"{latest_por:.2f} 배")
else:
    m2.metric("현재 POR", "N/A (적자)")

if pd.notnull(latest_op):
    m3.metric("2026년 영업이익 추정", f"{latest_op / 100_000_000.0:,.1f} 억원")
else:
    m3.metric("2026년 영업이익 추정", "미입력")

if not valid_por.empty:
    m4.metric("과거 5년 POR 중앙값", f"{med_por_val:.2f} 배")
