import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import FinanceDataReader as fdr
from datetime import datetime, timedelta
import os
import json
import requests
import zipfile
import io
import re
import xml.etree.ElementTree as ET

# 페이지 기본 설정
st.set_page_config(page_title="KRX 전종목 POR 밴드 시뮬레이터", layout="wide")

# JSON 파일 경로 안전하게 지정
JSON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'custom_stocks.json')

# ==========================================
# 🔑 Open DART API 키 설정
DART_API_KEY = "28b4dc2f6fac759fc70daa06cb0e9761eda3c105".strip() 
# ==========================================

DEFAULT_STOCKS = {
    '반도체': {
        'SK하이닉스': '000660',
        '티엘비': '356860',
        '엠케이전자': '033160',
        'ISC': '095340',
        '엘티씨': '170920',
        '하나마이크론': '067310',
        '하나머티리얼즈': '166090',
        '코미코': '183300',
        '에프에스티': '036810'
    },
    '관심종목': {}
}

# --- JSON 저장/로드 ---
def load_stocks_data():
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cleaned_data = {}
                for cat, stocks in data.items():
                    cleaned_data[cat] = {}
                    for name, val in stocks.items():
                        if isinstance(val, list):
                            cleaned_data[cat][name] = val[0]
                        else:
                            cleaned_data[cat][name] = val
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
    except Exception as e:
        st.error(f"종목 데이터 저장 중 오류 발생: {e}")

if 'stock_categories' not in st.session_state:
    st.session_state.stock_categories = load_stocks_data()

# KRX 상장 종목 데이터 (주식수 및 시가총액 정보 보완)
@st.cache_data(ttl=86400)
def get_krx_stock_list():
    try:
        df_krx = fdr.StockListing('KRX')
        return df_krx
    except Exception:
        return pd.DataFrame()

krx_df = get_krx_stock_list()

# --- Open DART 고유번호 매핑 캐시 ---
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

# --- DART 정식 재무제표 기반 과거 영업이익 수집 ---
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

    for b_year in ['2021', '2022', '2023', '2024', '2025']:
        try:
            url = f"https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?crtfc_key={clean_key}&corp_code={corp_code}&bsns_year={b_year}&reprt_code=11011"
            res = requests.get(url, timeout=5)
            data = res.json()
            
            if data.get('status') == '000' and 'list' in data:
                for item in data['list']:
                    account_nm = item.get('account_nm', '')
                    if ('영업이익' in account_nm or '영업손실' in account_nm) and '률' not in account_nm:
                        val_str = item.get('thstrm_amount', '0').replace(',', '').strip()
                        if val_str and val_str != '-':
                            ops[b_year] = float(val_str)
                        break
        except Exception:
            pass

    return ops

# --- 올해(2026) 추정 영업이익(컨센서스) 수집 ---
@st.cache_data(ttl=3600)
def fetch_consensus_operating_profit(code):
    consensus = {'2026': 0.0}
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Referer': f'https://finance.naver.com/item/main.naver?code={code}'
    })
    
    try:
        url = f"https://finance.naver.com/item/coinfoExecutionGrid.naver?code={code}&target=annual"
        res = session.get(url, timeout=5)
        
        if res.status_code == 200:
            tables = pd.read_html(res.text)
            for tbl in tables:
                if isinstance(tbl.columns, pd.MultiIndex):
                    tbl.columns = ['_'.join([str(c) for c in col if 'Unnamed' not in str(c)]).strip() for col in tbl.columns]
                else:
                    tbl.columns = [str(c) for c in tbl.columns]

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
                            break
    except Exception:
        pass
        
    return consensus

# ==================== 사이드바 ====================
st.sidebar.title("⚙️ 카테고리 & 종목 관리")

if DART_API_KEY == "YOUR_DART_API_KEY_HERE":
    dart_key_input = st.sidebar.text_input("🔑 Open DART API 키 입력", type="password")
    if dart_key_input:
        DART_API_KEY = dart_key_input.strip()

with st.sidebar.expander("📁 카테고리 추가"):
    new_cat_name = st.text_input("새 카테고리 이름", key="new_cat_input").strip()
    if st.button("카테고리 생성"):
        if new_cat_name and new_cat_name not in st.session_state.stock_categories:
            st.session_state.stock_categories[new_cat_name] = {}
            save_stocks_data(st.session_state.stock_categories)
            st.success(f"'{new_cat_name}' 카테고리가 추가되었습니다.")
            st.rerun()

with st.sidebar.expander("➕ 신규 종목 추가"):
    if not krx_df.empty:
        search_options = [f"{row['Name']} ({row['Code']})" for _, row in krx_df.iterrows() if 'Name' in row and 'Code' in row]
        selected_search = st.selectbox("KRX 종목 검색:", options=["선택하세요..."] + search_options)
        target_cat = st.selectbox("추가할 카테고리 선택:", list(st.session_state.stock_categories.keys()))
        
        if st.button("종목 저장"):
            if selected_search != "선택하세요...":
                name = selected_search.split(" (")[0]
                code = selected_search.split(" (")[1].replace(")", "")
                
                st.session_state.stock_categories[target_cat][name] = code
                save_stocks_data(st.session_state.stock_categories)
                st.success(f"'{name}' 종목이 추가되었습니다!")
                st.rerun()

st.sidebar.markdown("---")
st.sidebar.title("🔍 분석 대상 선택")
category_list = [cat for cat, stocks in st.session_state.stock_categories.items() if stocks]

if not category_list:
    st.warning("등록된 종목이 없습니다. 종목을 추가해주세요.")
    st.stop()

selected_category = st.sidebar.selectbox("카테고리 선택:", category_list)
available_stocks = st.session_state.stock_categories[selected_category]
selected_stock = st.sidebar.selectbox("종목 선택:", list(available_stocks.keys()))

stock_code = available_stocks[selected_stock]

if st.sidebar.button(f"❌ {selected_stock} 삭제"):
    del st.session_state.stock_categories[selected_category][selected_stock]
    save_stocks_data(st.session_state.stock_categories)
    st.rerun()

# ==================== 메인 화면 ====================
st.title(f"📈 [{selected_category}] {selected_stock} ({stock_code}) POR 밴드 시뮬레이션")

# DART API 통한 과거 실적 수집
hist_ops = fetch_operating_profit_dart(stock_code, DART_API_KEY)

# 올해(2026) 추정치 자동 수집
est_ops = fetch_consensus_operating_profit(stock_code)

past_years = ['2021', '2022', '2023', '2024', '2025']

st.subheader("📊 연도별 영업이익 현황 및 추정치 (단위: 억원)")

st.markdown("**(1) 과거 실적 영업이익 (DART 자동 수집 / 수동 수정 가능)**")
p_cols = st.columns(len(past_years))
final_ops = {}

for idx, yr in enumerate(past_years):
    with p_cols[idx]:
        auto_val_100m = hist_ops.get(yr, 0.0) / 100_000_000.0
        val_input = st.number_input(
            f"{yr}년 실적(억원)",
            value=float(auto_val_100m),
            step=10.0,
            format="%.1f",
            key=f"past_{selected_stock}_{yr}"
        )
        final_ops[yr] = val_input * 100_000_000.0

st.markdown("---")

st.markdown("**(2) 올해 추정 영업이익 (컨센서스 자동 조회 / 수동 수정 가능)**")
f_cols = st.columns(4)

with f_cols[0]:
    auto_est_100m = est_ops.get('2026', 0.0) / 100_000_000.0
    input_100m = st.number_input(
        "2026년 추정(억원)", 
        value=float(auto_est_100m), 
        step=10.0, 
        format="%.1f",
        key=f"future_{selected_stock}_2026"
    )
    final_ops['2026'] = input_100m * 100_000_000.0

# 주가 데이터 수집
end_date = datetime.today()
start_date = end_date - timedelta(days=5 * 365)

@st.cache_data(ttl=3600)
def get_stock_data_api(code, start, end):
    df = fdr.DataReader(code, start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d'))
    return df.reset_index()

try:
    stock_df = get_stock_data_api(stock_code, start_date, end_date)
except Exception as e:
    st.error(f"주가 데이터를 불러오는 중 오류가 발생했습니다: {e}")
    st.stop()

if stock_df.empty:
    st.error("불러온 주가 데이터가 없습니다.")
    st.stop()

# Dataframe 가공 및 시가총액 계산 (시가총액 누락 예방 보완)
stock_df['날짜'] = pd.to_datetime(stock_df['Date'])
stock_df['종가'] = pd.to_numeric(stock_df['Close'], errors='coerce')
stock_df['연도'] = stock_df['날짜'].dt.year.astype(str)

# Marcap 컬럼 유무 및 상장주식수 계산 방식 보완
if 'Marcap' in stock_df.columns and stock_df['Marcap'].notnull().sum() > 0:
    stock_df['시가총액'] = pd.to_numeric(stock_df['Marcap'], errors='coerce')
else:
    shares = 0
    if not krx_df.empty and 'Code' in krx_df.columns:
        matched = krx_df[krx_df['Code'] == stock_code]
        for col_name in ['Stocks', 'ListingShares', 'Shares']:
            if col_name in matched.columns and pd.notnull(matched[col_name].values[0]):
                shares = float(matched[col_name].values[0])
                break
    
    if shares > 0:
        stock_df['시가총액'] = stock_df['종가'] * shares
    else:
        # 주식수를 구하지 못한 경우 최신 시가총액 추정치 적용 예비책
        latest_marcap = stock_df['종가'].iloc[-1]
        stock_df['시가총액'] = stock_df['종가'] * (latest_marcap / stock_df['종가'].iloc[-1] if stock_df['종가'].iloc[-1] > 0 else 1)

# 영업이익 매핑 및 POR 계산
stock_df['수정_영업이익'] = stock_df['연도'].map(final_ops)
stock_df['수정_영업이익'] = pd.to_numeric(stock_df['수정_영업이익'], errors='coerce')

stock_df['수정_POR'] = np.where(
    (stock_df['수정_영업이익'].notnull()) & (stock_df['수정_영업이익'] > 0) & (stock_df['시가총액'].notnull()),
    stock_df['시가총액'] / stock_df['수정_영업이익'],
    np.nan
)

# Mean & Standard Deviation 계산
valid_por = stock_df['수정_POR'].dropna()

if len(valid_por) > 0:
    mean_val = valid_por.mean()
    std_val = valid_por.std()
else:
    mean_val, std_val = 0.0, 0.0

stock_df['Mean'] = mean_val
stock_df['+1σ'] = mean_val + std_val
stock_df['+2σ'] = mean_val + (std_val * 2)
stock_df['-1σ'] = mean_val - std_val
stock_df['-2σ'] = mean_val - (std_val * 2)

# 주요 지표 요약 (현재 시가총액 포함)
c1, c2, c3, c4, c5 = st.columns(5)
latest_close = stock_df['종가'].iloc[-1] if not stock_df.empty else 0
latest_marcap_val = stock_df['시가총액'].iloc[-1] if not stock_df.empty and pd.notnull(stock_df['시가총액'].iloc[-1]) else 0

c1.metric("최신 종가", f"{latest_close:,.0f} 원")
c2.metric("현재 시가총액", f"{latest_marcap_val / 100_000_000:,.1f} 억원" if latest_marcap_val > 0 else "N/A")
c3.metric("5년 평균 POR (Mean)", f"{mean_val:.2f}")
c4.metric("표준편차 (STDEV)", f"{std_val:.2f}")
c5.metric("+2σ 밴드 상단", f"{(mean_val + std_val*2):.2f}")

# Plotly 차트 시각화
fig = go.Figure()

fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['수정_POR'], mode='lines', name='POR (5년 실시간)', line=dict(color='#FFFFFF', width=2)))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['+2σ'], mode='lines', name='+2σ (상단)', line=dict(color='#FF5555', width=1.5, dash='dash')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['+1σ'], mode='lines', name='+1σ', line=dict(color='#FFB86C', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['Mean'], mode='lines', name='Mean (평균)', line=dict(color='#50FA7B', width=2, dash='solid')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['-1σ'], mode='lines', name='-1σ', line=dict(color='#8BE9FD', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['-2σ'], mode='lines', name='-2σ (하단)', line=dict(color='#BD93F9', width=1.5, dash='dash')))

fig.update_layout(
    title=dict(text=f"<b>{selected_stock} 최근 5년치 POR 밴드 차트</b>", font=dict(color='#FFFFFF', size=20)),
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
