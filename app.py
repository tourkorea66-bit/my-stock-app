import os
import json
import re
import io
import zipfile
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import FinanceDataReader as fdr

# 페이지 기본 설정
st.set_page_config(page_title="POR 밴드 시뮬레이터", layout="wide")

# ==========================================
# 🔑 고정 KVdb 엔드포인트 및 DART API 키 설정
KVDB_URL = "https://kvdb.io/MzdTavSteRuyBzBFpDorrt/scripts/por_stock_data"
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

# --- 🔄 KVdb 동기화 함수 (LOAD & SAVE) ---
def load_stocks_data_from_kvdb():
    base_data = DEFAULT_STOCKS.copy()
    try:
        res = requests.get(KVDB_URL, timeout=5)
        if res.status_code == 200 and res.text.strip():
            saved_data = res.json()
            if isinstance(saved_data, dict):
                for cat, stocks in saved_data.items():
                    if cat not in base_data:
                        base_data[cat] = {}
                    if isinstance(stocks, dict):
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
                            base_data[cat][name] = {'code': code, 'ops': ops}
            return base_data
    except Exception as e:
        st.sidebar.warning(f"KVdb 불러오기 임시 실패 (기본값 사용): {e}")
    return base_data

def save_stocks_data_to_kvdb(data):
    try:
        # json 전달 시 ensure_ascii=False 및 명시적 utf-8 인코딩 적용
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        headers = {"Content-Type": "application/json; charset=utf-8"}
        res = requests.post(KVDB_URL, data=payload, headers=headers, timeout=5)
        
        if res.status_code in [200, 201]:
            return True
        else:
            st.sidebar.error(f"KVdb 저장 실패 (응답코드: {res.status_code})")
            return False
    except Exception as e:
        st.sidebar.error(f"KVdb 저장 중 오류 발생: {e}")
        return False

# Session State 초기화
if 'stock_categories' not in st.session_state:
    st.session_state.stock_categories = load_stocks_data_from_kvdb()

# KRX 상장 종목 데이터 (캐싱)
@st.cache_data(ttl=86400)
def get_krx_stock_list():
    try:
        return fdr.StockListing('KRX')
    except Exception:
        return pd.DataFrame()

krx_df = get_krx_stock_list()

# --- DART 고유번호 매핑 메모리 캐싱 ---
@st.cache_data(ttl=86400 * 30)
def get_dart_corp_code_map(api_key):
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
    except Exception:
        pass
    return corp_map

# 단일 연도 DART API 호출
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
                        return b_year, round(float(val_str) / 100_000_000.0, 1)
    except Exception:
        pass
    return b_year, 0.0

# --- DART 5년치 데이터 병렬 API 수집 ---
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

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(_fetch_single_year_dart, tasks)
        for yr, val in results:
            ops[yr] = val

    return ops

# --- 올해 추정 영업이익 (네이버 컨센서스) ---
@st.cache_data(ttl=3600)
def fetch_consensus_operating_profit(code):
    consensus = {'2026': 0.0}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
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
                                        consensus['2026'] = float(clean_v)
                                        return consensus
    except Exception:
        pass
            
    return consensus

# ==================== 사이드바 ====================
st.sidebar.title("⚙️ KVdb 및 종목 관리")

col_btn1, col_btn2 = st.sidebar.columns(2)
with col_btn1:
    if st.button("🔄 불러오기"):
        st.session_state.stock_categories = load_stocks_data_from_kvdb()
        st.sidebar.success("로드 완료!")
        st.rerun()

with col_btn2:
    if st.button("💾 동기화 저장", type="primary"):
        if save_stocks_data_to_kvdb(st.session_state.stock_categories):
            st.sidebar.success("저장 완료!")

# --- 📁 카테고리 추가 ---
with st.sidebar.expander("📁 카테고리 추가"):
    new_cat_name = st.text_input("새 카테고리 이름", key="new_cat_input").strip()
    if st.button("카테고리 생성"):
        if new_cat_name and new_cat_name not in st.session_state.stock_categories:
            st.session_state.stock_categories[new_cat_name] = {}
            # 추가 즉시 서버 저장
            save_stocks_data_to_kvdb(st.session_state.stock_categories)
            st.success(f"'{new_cat_name}' 카테고리가 생성되고 저장되었습니다.")
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
                    # 종목 추가
                    st.session_state.stock_categories[target_cat][name] = {'code': code, 'ops': {}}
                    # 추가 즉시 서버 저장
                    if save_stocks_data_to_kvdb(st.session_state.stock_categories):
                        st.success(f"'{name}' 종목이 추가 및 KVdb에 저장되었습니다!")
                    else:
                        st.warning("화면에는 추가되었으나 KVdb 저장이 원활하지 않습니다.")
                    st.rerun()

st.sidebar.markdown("---")
st.sidebar.title("🔍 분석 대상 선택")
category_list = [cat for cat, stocks in st.session_state.stock_categories.items() if stocks]

if not category_list:
    st.warning("등록된 종목이 없습니다. 카테고리나 종목을 추가해주세요.")
    st.stop()

selected_category = st.sidebar.selectbox("카테고리 선택:", category_list)
available_stocks = st.session_state.stock_categories[selected_category]
selected_stock = st.sidebar.selectbox("종목 선택:", list(available_stocks.keys()))

stock_info = available_stocks[selected_stock]
stock_code = stock_info['code']

if st.sidebar.button(f"❌ {selected_stock} 삭제"):
    del st.session_state.stock_categories[selected_category][selected_stock]
    save_stocks_data_to_kvdb(st.session_state.stock_categories)
    st.success(f"{selected_stock} 삭제 및 저장 완료")
    st.rerun()

# ==================== 메인 화면 ====================
st.title(f"📈 [{selected_category}] {selected_stock} ({stock_code}) POR 밴드 시뮬레이션")

past_years = ['2021', '2022', '2023', '2024', '2025']
saved_ops = stock_info.get('ops', {})

# 저장된 실적이 없으면 API로 자동 로드 후 KVdb에 저장
if not saved_ops:
    hist_ops = fetch_operating_profit_dart(stock_code, DART_API_KEY)
    est_ops = fetch_consensus_operating_profit(stock_code)
    
    for yr in past_years:
        saved_ops[yr] = float(hist_ops.get(yr, 0.0))
    saved_ops['2026'] = float(est_ops.get('2026', 0.0))
    
    st.session_state.stock_categories[selected_category][selected_stock]['ops'] = saved_ops
    save_stocks_data_to_kvdb(st.session_state.stock_categories)

st.subheader("📊 연도별 영업이익 현황 및 추정치 (단위: 억원)")

final_ops = {}
p_cols = st.columns(len(past_years))
has_negative_op = False

# 입력값 변경 시 KVdb 자동 반영 콜백
def update_op_value(cat, stock, yr):
    widget_key = f"input_{stock}_{yr}"
    new_val = st.session_state[widget_key]
    st.session_state.stock_categories[cat][stock]['ops'][yr] = new_val
    save_stocks_data_to_kvdb(st.session_state.stock_categories)

for idx, yr in enumerate(past_years):
    current_val = float(saved_ops.get(yr, 0.0))
    with p_cols[idx]:
        val_input = st.number_input(
            f"{yr}년 실적(억원)",
            value=current_val,
            step=10.0,
            format="%.1f",
            key=f"input_{selected_stock}_{yr}",
            on_change=update_op_value,
            args=(selected_category, selected_stock, yr)
        )
        final_ops[yr] = val_input * 100_000_000.0
        
        if val_input < 0:
            st.markdown(f"<p style='color: #FF4B4B; font-weight: bold; margin-top: -10px;'>🔴 {val_input:,.1f} 억 (적자)</p>", unsafe_allow_html=True)
            has_negative_op = True
        else:
            st.markdown(f"<p style='color: #00C853; font-size: 0.85em; margin-top: -10px;'>🟢 흑자</p>", unsafe_allow_html=True)

f_cols = st.columns(4)
with f_cols[0]:
    val_2026 = float(saved_ops.get('2026', 0.0))
    input_2026 = st.number_input(
        "2026년 추정(억원)", 
        value=val_2026, 
        step=10.0, 
        format="%.1f",
        key=f"input_{selected_stock}_2026",
        on_change=update_op_value,
        args=(selected_category, selected_stock, '2026')
    )
    final_ops['2026'] = input_2026 * 100_000_000.0
    
    if input_2026 < 0:
        st.markdown(f"<p style='color: #FF4B4B; font-weight: bold; margin-top: -10px;'>🔴 {input_2026:,.1f} 억 (적자 추정)</p>", unsafe_allow_html=True)
        has_negative_op = True
    else:
        st.markdown(f"<p style='color: #00C853; font-size: 0.85em; margin-top: -10px;'>🟢 흑자 추정</p>", unsafe_allow_html=True)

if has_negative_op:
    st.warning("⚠️ 영업이익이 적자(마이너스)인 구간은 POR 산출 공식상 'N/A' 처리되어 차트선이 연결되지 않을 수 있습니다.")

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
            for col_name in ['Stocks', 'ListingShares', 'Shares']:
                if col_name in matched.columns and pd.notnull(matched[col_name].values[0]):
                    shares = float(matched[col_name].values[0])
                    if shares > 0:
                        break
    stock_df['시가총액'] = stock_df['종가'] * shares if shares > 0 else np.nan

stock_df['수정_영업이익'] = stock_df['연도'].map(final_ops)
stock_df['수정_영업이익'] = pd.to_numeric(stock_df['수정_영업이익'], errors='coerce')

# POR 계산
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

c1, c2, c3, c4, c5 = st.columns(5)
latest_close = stock_df['종가'].iloc[-1] if not stock_df.empty else 0
latest_marcap_val = stock_df['시가총액'].dropna().iloc[-1] if not stock_df['시가총액'].dropna().empty else 0

c1.metric("최신 종가", f"{latest_close:,.0f} 원")
c2.metric("현재 시가총액", f"{latest_marcap_val / 100_000_000:,.1f} 억원" if latest_marcap_val > 0 else "N/A")
c3.metric(f"평균 POR ({start_date.year}~현재)", f"{mean_val:.2f}")
c4.metric("표준편차 (STDEV)", f"{std_val:.2f}")
c5.metric("+2σ 밴드 상단", f"{(mean_val + std_val*2):.2f}")

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
