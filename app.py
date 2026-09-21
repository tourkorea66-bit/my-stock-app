import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import FinanceDataReader as fdr
from datetime import datetime
import os
import json
import requests
import zipfile
import io
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

# 페이지 기본 설정
st.set_page_config(page_title="KRX 전종목 POR 밴드 시뮬레이터", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(BASE_DIR, 'custom_stocks.json')
CORP_CODE_CACHE_FILE = os.path.join(BASE_DIR, 'corp_code_map.json')

# ==========================================
# 🔑 Open DART API 키 설정
DART_API_KEY = "28b4dc2f6fac759fc70daa06cb0e9761eda3c105".strip() 
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

# --- JSON 저장/로드 로직 ---
def load_stocks_data():
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cleaned_data = {}
                for cat, stocks in data.items():
                    cleaned_data[cat] = {}
                    for name, val in stocks.items():
                        if isinstance(val, dict):
                            code = val.get('code', '')
                            ops = val.get('ops', {})
                        elif isinstance(val, list):
                            code = val[0]
                            ops = {}
                        else:
                            code = str(val)
                            ops = {}
                        cleaned_data[cat][name] = {'code': code, 'ops': ops}
                return cleaned_data
        except Exception:
            return DEFAULT_STOCKS.copy()
    else:
        save_stocks_data(DEFAULT_STOCKS)
        return DEFAULT_STOCKS.copy()

def save_stocks_data(data):
    try:
        with open(JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        st.sidebar.error(f"종목 저장 중 오류 발생: {e}")
        return False

# Session State 초기화
if 'stock_categories' not in st.session_state:
    st.session_state.stock_categories = load_stocks_data()

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

# --- ⚡ [최적화 1] DART 고유번호 매핑 로컬 파일 캐싱 ---
@st.cache_data(ttl=86400 * 30)
def get_dart_corp_code_map(api_key):
    # 로컬 JSON 파일이 이미 있으면 0.01초 만에 불러옴
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
            # 파일로 저장하여 다음 실행 시 속도 향상
            with open(CORP_CODE_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(corp_map, f)
    except Exception:
        pass
    return corp_map

# 단일 연도 DART API 호출 함수
def _fetch_single_year_dart(args):
    b_year, clean_key, corp_code = args
    try:
        url = f"https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?crtfc_key={clean_key}&corp_code={corp_code}&bsns_year={b_year}&reprt_code=11011"
        res = requests.get(url, timeout=3)
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

# --- ⚡ [최적화 2] DART 5년치 데이터 병렬 API 수집 ---
@st.cache_data(ttl=86400)
def fetch_operating_profit_dart(code, api_key):
    ops = {'2021': 0.0, '2022': 0.0, '2023': 0.0, '2024': 0.0, '2025': 0.0}
    clean_key = str(api_key).strip()
    
    if not clean_key or clean_key == "YOUR_DART_API_KEY_HERE":
        return ops

    corp_map = get_dart_corp_code_map(clean_key)
    corp_code = corp_map.get(str(code).zfill(6))
    if not corp_code:
        return ops

    years = ['2021', '2022', '2023', '2024', '2025']
    tasks = [(yr, clean_key, corp_code) for yr in years]

    # 5년치 요청을 동시에 병렬 실행 (속도 5배 향상)
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(_fetch_single_year_dart, tasks)
        for yr, val in results:
            ops[yr] = val

    return ops

# --- 올해 추정 영업이익 (네이버 컨센서스) ---
@st.cache_data(ttl=3600)
def fetch_consensus_operating_profit(code):
    consensus = {'2026': 0.0}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    url = f"https://finance.naver.com/item/coinfoExecutionGrid.naver?code={code}&target=annual"
    
    try:
        res = requests.get(url, headers=headers, timeout=3)
        if res.status_code == 200:
            tables = pd.read_html(io.StringIO(res.text))
            for tbl in tables:
                tbl_str = tbl.to_string()
                if '영업이익' in tbl_str:
                    for idx, row in tbl.iterrows():
                        row_title = str(row.iloc[0])
                        if '영업이익' in row_title and '률' not in row_title:
                            for col_idx in range(1, len(tbl.columns)):
                                col_hdr = str(tbl.columns[col_idx])
                                val = row.iloc[col_idx]
                                if '2026' in col_hdr and pd.notnull(val):
                                    clean_v = re.sub(r'[^0-9.-]', '', str(val))
                                    if clean_v and clean_v != '-':
                                        consensus['2026'] = float(clean_v) * 100_000_000.0
                                        return consensus
    except Exception:
        pass
            
    return consensus

# ==================== 사이드바 ====================
st.sidebar.title("⚙️ 카테고리 & 종목 관리")

if st.sidebar.button("💾 전체 종목 구성 및 수치 저장", type="primary"):
    if save_stocks_data(st.session_state.stock_categories):
        st.sidebar.success("custom_stocks.json 저장 완료!")

category_list = [cat for cat, stocks in st.session_state.stock_categories.items() if stocks]
if not category_list:
    st.warning("등록된 종목이 없습니다.")
    st.stop()

selected_category = st.sidebar.selectbox("카테고리 선택:", category_list)
available_stocks = st.session_state.stock_categories[selected_category]
selected_stock = st.sidebar.selectbox("종목 선택:", list(available_stocks.keys()))

stock_info = available_stocks[selected_stock]
stock_code = stock_info['code']

# ==================== 메인 화면 ====================
st.title(f"📈 [{selected_category}] {selected_stock} ({stock_code}) POR 밴드 시뮬레이션")

past_years = ['2021', '2022', '2023', '2024', '2025']

# ⚡ [최적화 3] 이미 저장된 값이 있다면 API 조회를 통째로 Skip
if selected_stock not in st.session_state.ops_data:
    saved_ops = stock_info.get('ops', {})
    st.session_state.ops_data[selected_stock] = {}
    
    # 1. 과거 실적 (저장된 값이 없으면 API 조회)
    has_all_past = all(yr in saved_ops and saved_ops[yr] != 0.0 for yr in past_years)
    if has_all_past:
        for yr in past_years:
            st.session_state.ops_data[selected_stock][yr] = float(saved_ops[yr])
    else:
        hist_ops = fetch_operating_profit_dart(stock_code, DART_API_KEY)
        for yr in past_years:
            if yr in saved_ops and saved_ops[yr] != 0.0:
                st.session_state.ops_data[selected_stock][yr] = float(saved_ops[yr])
            else:
                st.session_state.ops_data[selected_stock][yr] = float(hist_ops.get(yr, 0.0) / 100_000_000.0)
            
    # 2. 2026년 추정치 (저장된 값이 없으면 크롤링)
    if '2026' in saved_ops and saved_ops['2026'] != 0.0:
        st.session_state.ops_data[selected_stock]['2026'] = float(saved_ops['2026'])
    else:
        est_ops = fetch_consensus_operating_profit(stock_code)
        st.session_state.ops_data[selected_stock]['2026'] = float(est_ops.get('2026', 0.0) / 100_000_000.0)

st.subheader("📊 연도별 영업이익 현황 및 추정치 (단위: 억원)")

final_ops = {}
p_cols = st.columns(len(past_years))

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

# ==================== 주가 데이터 수집 ====================
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

# Dataframe 가공 및 시가총액 계산
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

# 영업이익 매핑 및 POR 계산
stock_df['수정_영업이익'] = stock_df['연도'].map(final_ops)
stock_df['수정_영업이익'] = pd.to_numeric(stock_df['수정_영업이익'], errors='coerce')

stock_df['수정_POR'] = np.where(
    (stock_df['수정_영업이익'].notnull()) & (stock_df['수정_영업이익'] > 0) & (stock_df['시가총액'].notnull()),
    stock_df['시가총액'] / stock_df['수정_영업이익'],
    np.nan
)

valid_por = stock_df['수정_POR'].dropna()
mean_val = valid_por.mean() if len(valid_por) > 0 else 0.0
std_val = valid_por.std() if len(valid_por) > 0 else 0.0

stock_df['Mean'] = mean_val
stock_df['+1σ'] = mean_val + std_val
stock_df['+2σ'] = mean_val + (std_val * 2)
stock_df['-1σ'] = mean_val - std_val
stock_df['-2σ'] = mean_val - (std_val * 2)

# 주요 지표 요약
c1, c2, c3, c4, c5 = st.columns(5)
latest_close = stock_df['종가'].iloc[-1] if not stock_df.empty else 0
latest_marcap_val = stock_df['시가총액'].dropna().iloc[-1] if not stock_df['시가총액'].dropna().empty else 0

c1.metric("최신 종가", f"{latest_close:,.0f} 원")
c2.metric("현재 시가총액", f"{latest_marcap_val / 100_000_000:,.1f} 억원" if latest_marcap_val > 0 else "N/A")
c3.metric(f"평균 POR ({start_date.year}~현재)", f"{mean_val:.2f}")
c4.metric("표준편차 (STDEV)", f"{std_val:.2f}")
c5.metric("+2σ 밴드 상단", f"{(mean_val + std_val*2):.2f}")

# Plotly 차트 시각화
fig = go.Figure()
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['수정_POR'], mode='lines', name='POR (실시간)', line=dict(color='#FFFFFF', width=2)))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['+2σ'], mode='lines', name='+2σ (상단)', line=dict(color='#FF5555', width=1.5, dash='dash')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['+1σ'], mode='lines', name='+1σ', line=dict(color='#FFB86C', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['Mean'], mode='lines', name='Mean (평균)', line=dict(color='#50FA7B', width=2, dash='solid')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['-1σ'], mode='lines', name='-1σ', line=dict(color='#8BE9FD', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['-2σ'], mode='lines', name='-2σ (하단)', line=dict(color='#BD93F9', width=1.5, dash='dash')))

fig.update_layout(
    title=dict(text=f"<b>{selected_stock} {start_date.year}년 1월 ~ 현재 POR 밴드 차트</b>", font=dict(color='#FFFFFF', size=20)),
    paper_bgcolor='#1E1E1E',
    plot_bgcolor='#141414',
    font=dict(color='#FFFFFF'),
    xaxis=dict(title="날짜", showgrid=True, gridcolor='#333333', color='#FFFFFF'),
    yaxis=dict(title="POR", showgrid=True, gridcolor='#333333', color='#FFFFFF'),
    hovermode="x unified",
    height=600,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color='#FFFFFF'))
)

st.plotly_chart(fig, use_container_width=True)
