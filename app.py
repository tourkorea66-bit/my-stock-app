import os
import json
import re
import io
import zipfile
import copy
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
st.set_page_config(page_title="KRX 전종목 POR 밴드 시뮬레이터", layout="wide")

# ==========================================
# 🔑 KVdb 엔드포인트 및 DART API 키
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

# --- 🔄 KVdb 네트워크 로직 ---
def load_stocks_data_from_kvdb():
    try:
        res = requests.get(KVDB_URL, timeout=5)
        if res.status_code == 200 and res.text.strip():
            saved_data = res.json()
            if isinstance(saved_data, dict) and len(saved_data) > 0:
                cleaned_data = {}
                for cat, stocks in saved_data.items():
                    cleaned_data[cat] = {}
                    if isinstance(stocks, dict):
                        for name, val in stocks.items():
                            if isinstance(val, dict):
                                code = val.get('code', '')
                                ops = val.get('ops', {})
                            else:
                                code = str(val)
                                ops = {}
                            cleaned_data[cat][name] = {'code': code, 'ops': ops}
                return cleaned_data
    except Exception:
        pass
    return copy.deepcopy(DEFAULT_STOCKS)

def save_stocks_data_to_kvdb(data):
    try:
        json_payload = json.dumps(data, ensure_ascii=False)
        res = requests.post(
            KVDB_URL, 
            data=json_payload.encode('utf-8'), 
            headers={"Content-Type": "application/json; charset=utf-8"}, 
            timeout=5
        )
        return res.status_code in [200, 201]
    except Exception:
        return False

# Session State 초기화 (최초 1회)
if 'stock_categories' not in st.session_state:
    st.session_state.stock_categories = load_stocks_data_from_kvdb()

# KRX 상장 종목 데이터
@st.cache_data(ttl=86400)
def get_krx_stock_list():
    try:
        return fdr.StockListing('KRX')
    except Exception:
        return pd.DataFrame()

krx_df = get_krx_stock_list()

# DART 매핑
@st.cache_data(ttl=86400 * 30)
def get_dart_corp_code_map(api_key):
    corp_map = {}
    clean_key = str(api_key).strip()
    if not clean_key:
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

def _fetch_single_year_dart(args):
    b_year, clean_key, corp_code = args
    try:
        url = f"https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?crtfc_key={clean_key}&corp_code={corp_code}&bsns_year={b_year}&reprt_code=11011"
        res = requests.get(url, timeout=5)
        data = res.json()
        if data.get('status') == '000' and 'list' in data:
            for item in data['list']:
                acc_id = item.get('account_id', '')
                account_nm = item.get('account_nm', '')
                
                # account_id 표준 ID 검사 및 계정명 보완 검사
                is_op = (
                    'OperatingProfitLoss' in acc_id or
                    (('영업이익' in account_nm or '영업손실' in account_nm) and '률' not in account_nm and '이익률' not in account_nm)
                )
                if is_op:
                    val_str = item.get('thstrm_amount', '0').replace(',', '').strip()
                    if val_str and val_str != '-':
                        return b_year, round(float(val_str) / 100_000_000.0, 1)
    except Exception:
        pass
    return b_year, 0.0

@st.cache_data(ttl=86400)
def fetch_operating_profit_dart(code, api_key):
    ops = {'2021': 0.0, '2022': 0.0, '2023': 0.0, '2024': 0.0, '2025': 0.0}
    clean_key = str(api_key).strip()
    if not clean_key:
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

def get_full_stock_ops(code):
    all_years = ['2021', '2022', '2023', '2024', '2025']
    hist_ops = fetch_operating_profit_dart(code, DART_API_KEY)
    
    full_ops = {yr: float(hist_ops.get(yr, 0.0)) for yr in all_years}
    # 네이버 크롤링 제거: 2026년 추정치는 기본 0.0으로 설정하여 사용자가 trực tiếp 입력하도록 처리
    full_ops['2026'] = 0.0
    return full_ops

# ==================== 사이드바 ====================
st.sidebar.title("⚙️ 데이터 동기화 관리")

btn_col1, btn_col2 = st.sidebar.columns(2)
with btn_col1:
    if st.button("🔄 불러오기", use_container_width=True):
        st.session_state.stock_categories = load_stocks_data_from_kvdb()
        st.sidebar.success("서버 데이터 로드 완료")
        st.rerun()

with btn_col2:
    if st.button("💾 강제 저장", type="primary", use_container_width=True):
        if save_stocks_data_to_kvdb(st.session_state.stock_categories):
            st.sidebar.success("KVdb 저장 성공!")
        else:
            st.sidebar.error("저장 실패 (네트워크 확인)")

st.sidebar.markdown("---")

# 1. 카테고리 추가
with st.sidebar.expander("📁 카테고리 추가"):
    new_cat = st.text_input("카테고리명").strip()
    if st.button("카테고리 추가"):
        if new_cat and new_cat not in st.session_state.stock_categories:
            st.session_state.stock_categories[new_cat] = {}
            save_stocks_data_to_kvdb(st.session_state.stock_categories)
            st.rerun()

# 2. 종목 추가
with st.sidebar.expander("➕ 종목 추가", expanded=True):
    if not krx_df.empty:
        stock_list = [f"{r['Name']} ({r['Code']})" for _, r in krx_df.iterrows() if 'Name' in r and 'Code' in r]
        selected_item = st.selectbox("종목 검색", ["선택하세요..."] + stock_list)
        
        cats = list(st.session_state.stock_categories.keys())
        if not cats:
            st.session_state.stock_categories['관심종목'] = {}
            cats = ['관심종목']
        
        target_c = st.selectbox("저장할 카테고리", cats)
        
        if st.button("종목 추가 및 KVdb 즉시 저장", type="primary", use_container_width=True):
            if selected_item != "선택하세요...":
                s_name = selected_item.split(" (")[0]
                s_code = selected_item.split(" (")[1].replace(")", "")
                
                with st.spinner("DART 실적 데이터 수집 중..."):
                    s_ops = get_full_stock_ops(s_code)
                
                # 메모리 등록
                st.session_state.stock_categories[target_c][s_name] = {'code': s_code, 'ops': s_ops}
                
                # KVdb 동기화
                ok = save_stocks_data_to_kvdb(st.session_state.stock_categories)
                if ok:
                    st.sidebar.success(f"'{s_name}' 저장 완료!")
                else:
                    st.sidebar.warning("메모리에 추가되었으나 KVdb 저장 실패")
                st.rerun()

st.sidebar.markdown("---")

# 종목 선택
valid_cats = [c for c, s in st.session_state.stock_categories.items() if len(s) > 0]

if not valid_cats:
    st.info("등록된 종목이 없습니다. 사이드바에서 종목을 추가해 주세요.")
    st.stop()

sel_cat = st.sidebar.selectbox("카테고리", valid_cats)
sel_stock = st.sidebar.selectbox("종목", list(st.session_state.stock_categories[sel_cat].keys()))

cur_code = st.session_state.stock_categories[sel_cat][sel_stock]['code']

if st.sidebar.button(f"🗑️ {sel_stock} 삭제", use_container_width=True):
    del st.session_state.stock_categories[sel_cat][sel_stock]
    save_stocks_data_to_kvdb(st.session_state.stock_categories)
    st.rerun()

# ==================== 메인 화면 ====================
st.title(f"📈 [{sel_cat}] {sel_stock} ({cur_code}) POR 밴드 분석")

cur_ops = st.session_state.stock_categories[sel_cat][sel_stock].get('ops', {})

# 실적 실시간 수정 콜백 (수정 시 자동으로 session_state 및 KVdb 저장)
def on_op_change(c, s, y):
    val = st.session_state[f"input_{s}_{y}"]
    st.session_state.stock_categories[c][s]['ops'][y] = val
    save_stocks_data_to_kvdb(st.session_state.stock_categories)

past_years = ['2021', '2022', '2023', '2024', '2025']
st.subheader("📊 연도별 영업이익 (단위: 억원)")

final_ops = {}
cols = st.columns(6)

# 2021~2025년 (과거 실적)
for idx, yr in enumerate(past_years):
    val = float(cur_ops.get(yr, 0.0))
    with cols[idx]:
        v_in = st.number_input(
            f"{yr}년", value=val, step=10.0, format="%.1f",
            key=f"input_{sel_stock}_{yr}",
            on_change=on_op_change, args=(sel_cat, sel_stock, yr)
        )
        final_ops[yr] = v_in * 100_000_000.0

# 2026년 (추정 실적 - 사용자 자유 입력/수정)
with cols[5]:
    val_26 = float(cur_ops.get('2026', 0.0))
    v_in_26 = st.number_input(
        "2026년(추정)", value=val_26, step=10.0, format="%.1f",
        key=f"input_{sel_stock}_2026",
        on_change=on_op_change, args=(sel_cat, sel_stock, '2026')
    )
    final_ops['2026'] = v_in_26 * 100_000_000.0

# 주가 데이터 처리 및 차트
end_d = datetime.today()
start_d = datetime(end_d.year - 5, 1, 1)

@st.cache_data(ttl=3600)
def load_price_data(code, start, end):
    return fdr.DataReader(code, start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d')).reset_index()

try:
    df_price = load_price_data(cur_code, start_d, end_d)
except Exception as e:
    st.error(f"주가 로드 실패: {e}")
    st.stop()

if df_price.empty:
    st.error("주가 데이터가 존재하지 않습니다.")
    st.stop()

df_price['날짜'] = pd.to_datetime(df_price['Date'])
df_price['종가'] = pd.to_numeric(df_price['Close'], errors='coerce')
df_price['연도'] = df_price['날짜'].dt.year.astype(str)

if 'Marcap' in df_price.columns and df_price['Marcap'].notnull().sum() > 0:
    df_price['시가총액'] = pd.to_numeric(df_price['Marcap'], errors='coerce')
else:
    shares = 0
    if not krx_df.empty and 'Code' in krx_df.columns:
        m = krx_df[krx_df['Code'] == cur_code]
        if not m.empty:
            for col_n in ['Stocks', 'ListingShares', 'Shares']:
                if col_n in m.columns and pd.notnull(m[col_n].values[0]):
                    shares = float(m[col_n].values[0])
                    break
    df_price['시가총액'] = df_price['종가'] * shares if shares > 0 else np.nan

df_price['영업이익'] = df_price['연도'].map(final_ops)
df_price['POR'] = np.where(
    (df_price['영업이익'] > 0) & (df_price['시가총액'].notnull()),
    df_price['시가총액'] / df_price['영업이익'],
    np.nan
)

valid_por = df_price['POR'].dropna()
mean_val = valid_por.mean() if len(valid_por) > 0 else 0.0
std_val = valid_por.std() if len(valid_por) > 0 else 0.0

df_price['Mean'] = mean_val
df_price['+1σ'] = mean_val + std_val
df_price['+2σ'] = mean_val + (std_val * 2)
df_price['-1σ'] = mean_val - std_val
df_price['-2σ'] = mean_val - (std_val * 2)

c1, c2, c3, c4 = st.columns(4)
l_close = df_price['종가'].iloc[-1]
l_marcap = df_price['시가총액'].dropna().iloc[-1] if not df_price['시가총액'].dropna().empty else 0

c1.metric("최신 종가", f"{l_close:,.0f} 원")
c2.metric("시가총액", f"{l_marcap / 100_000_000:,.1f} 억원" if l_marcap > 0 else "N/A")
c3.metric("평균 POR", f"{mean_val:.2f}")
c4.metric("표준편차 (STDEV)", f"{std_val:.2f}")

fig = go.Figure()
fig.add_trace(go.Scatter(x=df_price['날짜'], y=df_price['POR'], mode='lines', name='POR', line=dict(color='#FFFFFF', width=2)))
fig.add_trace(go.Scatter(x=df_price['날짜'], y=df_price['+2σ'], mode='lines', name='+2σ', line=dict(color='#FF5555', width=1.5, dash='dash')))
fig.add_trace(go.Scatter(x=df_price['날짜'], y=df_price['+1σ'], mode='lines', name='+1σ', line=dict(color='#FFB86C', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=df_price['날짜'], y=df_price['Mean'], mode='lines', name='Mean', line=dict(color='#50FA7B', width=2, dash='solid')))
fig.add_trace(go.Scatter(x=df_price['날짜'], y=df_price['-1σ'], mode='lines', name='-1σ', line=dict(color='#8BE9FD', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=df_price['날짜'], y=df_price['-2σ'], mode='lines', name='-2σ', line=dict(color='#BD93F9', width=1.5, dash='dash')))

fig.update_layout(
    title=f"<b>{sel_stock} POR 밴드 차트</b>",
    paper_bgcolor='#1E1E1E', plot_bgcolor='#141414',
    font=dict(color='#FFFFFF'),
    xaxis=dict(gridcolor='#333333'), yaxis=dict(gridcolor='#333333'),
    hovermode="x unified", height=550
)

st.plotly_chart(fig, use_container_width=True)
