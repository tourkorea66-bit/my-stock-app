import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import FinanceDataReader as fdr
from datetime import datetime, timedelta
import os
import json

# 페이지 기본 설정
st.set_page_config(page_title="반도체 & KRX 전종목 POR 밴드 시뮬레이터", layout="wide")

# 로컬 저장 파일 설정
JSON_FILE = 'custom_stocks.json'
EXCEL_FILE = 'sigmahunting.xlsx'

# 기본 초기 데이터 (카테고리별 구조)
DEFAULT_STOCKS = {
    '반도체': {
        'SK하이닉스': ('000660', 'SK하이닉스1'),
        '티엘비': ('356860', '티엘비1'),
        '엠케이전자': ('033160', '엠케이전자1'),
        'ISC': ('095340', 'ISC1'),
        '엘티씨': ('170920', '엘티씨1'),
        '하나마이크론': ('067310', '하나마이크론1'),
        '하나머티리얼즈': ('166090', '하나머티리얼즈1'),
        '코미코': ('183300', '코미코1'),
        '에프에스티': ('036810', '에프에스티1 ')
    },
    '관심종목': {}
}

# --- JSON 로드 및 저장 함수 ---
def load_stocks_data():
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return DEFAULT_STOCKS.copy()
    return DEFAULT_STOCKS.copy()

def save_stocks_data(data):
    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# 세션 상태 초기화
if 'stock_categories' not in st.session_state:
    st.session_state.stock_categories = load_stocks_data()

# KRX 전체 상장 종목 가져오기
@st.cache_data(ttl=86400)
def get_krx_stock_list():
    try:
        df_krx = fdr.StockListing('KRX')
        cols = ['Code', 'Name']
        if 'Stocks' in df_krx.columns:
            cols.append('Stocks')
        return df_krx[cols].dropna(subset=['Code', 'Name'])
    except Exception as e:
        st.error(f"상장 종목 리스트를 불러오는 중 오류 발생: {e}")
        return pd.DataFrame(columns=['Code', 'Name', 'Stocks'])

krx_df = get_krx_stock_list()

@st.cache_resource
def load_excel_file(file_path):
    if os.path.exists(file_path):
        return pd.ExcelFile(file_path)
    return None

xls = load_excel_file(EXCEL_FILE)

# ==================== 사이드바 ====================
st.sidebar.title("⚙️ 카테고리 & 종목 관리")

# 1. 신규 카테고리 추가 UI
with st.sidebar.expander("📁 카테고리 추가"):
    new_cat_name = st.text_input("새 카테고리 이름", key="new_cat_input").strip()
    if st.button("카테고리 생성"):
        if new_cat_name and new_cat_name not in st.session_state.stock_categories:
            st.session_state.stock_categories[new_cat_name] = {}
            save_stocks_data(st.session_state.stock_categories)
            st.success(f"'{new_cat_name}' 카테고리가 추가되었습니다.")
            st.rerun()

# 2. 신규 종목 검색 및 추가 UI
with st.sidebar.expander("➕ 신규 종목 추가"):
    if not krx_df.empty:
        search_options = [f"{row['Name']} ({row['Code']})" for _, row in krx_df.iterrows()]
        selected_search = st.selectbox("KRX 종목 검색:", options=["선택하세요..."] + search_options)
        
        target_cat = st.selectbox("추가할 카테고리 선택:", list(st.session_state.stock_categories.keys()))
        
        if st.button("종목 저장"):
            if selected_search != "선택하세요...":
                name = selected_search.split(" (")[0]
                code = selected_search.split(" (")[1].replace(")", "")
                
                # 중복 검사
                exists = any(name in stocks for stocks in st.session_state.stock_categories.values())
                if not exists:
                    st.session_state.stock_categories[target_cat][name] = [code, None]
                    save_stocks_data(st.session_state.stock_categories)
                    st.success(f"'{name}' 종목이 '{target_cat}'에 저장되었습니다!")
                    st.rerun()
                else:
                    st.info(f"'{name}' 종목은 이미 목록에 존재합니다.")

st.sidebar.markdown("---")

# 3. 종목 분석 선택 영역
st.sidebar.title("🔍 분석 대상 선택")
category_list = [cat for cat, stocks in st.session_state.stock_categories.items() if stocks]

if not category_list:
    st.warning("등록된 종목이 없습니다. 종목을 추가해주세요.")
    st.stop()

selected_category = st.sidebar.selectbox("카테고리 선택:", category_list)
available_stocks = st.session_state.stock_categories[selected_category]

selected_stock = st.sidebar.selectbox("종목 선택:", list(available_stocks.keys()))

stock_info = available_stocks[selected_stock]
stock_code = stock_info[0]
sheet1_name = stock_info[1]

# 종목 삭제 버튼
if st.sidebar.button(f"❌ {selected_stock} 삭제"):
    del st.session_state.stock_categories[selected_category][selected_stock]
    save_stocks_data(st.session_state.stock_categories)
    st.sidebar.warning(f"'{selected_stock}' 종목이 삭제되었습니다.")
    st.rerun()

# ==================== 메인 화면 ====================
st.title(f"📈 [{selected_category}] {selected_stock} ({stock_code}) 5년치 POR 밴드 시뮬레이션")

# 연도 설정 (2021 ~ 2027)
years = ['2021', '2022', '2023', '2024', '2025', '2026', '2027']
default_ops = {yr: 0.0 for yr in years}

# 엑셀 파일에서 기본 영업이익 불러오기
if xls is not None and sheet1_name is not None:
    try:
        df1 = pd.read_excel(xls, sheet_name=sheet1_name)
        if len(df1) >= 3:
            row_yr = [str(x) for x in df1.iloc[1].tolist()]
            row_op = df1.iloc[2].tolist()
            
            for yr_col, op_val in zip(row_yr, row_op):
                clean_yr = yr_col.replace('(E)', '').replace('.0', '').strip()
                if clean_yr in years:
                    try:
                        default_ops[clean_yr] = float(op_val)
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass

# 영업이익 수동 입력 UI (억원 단위)
st.subheader("⚙️ 연도별 추정 영업이익(억원) 입력/수정")
cols = st.columns(len(years))
updated_ops = {}

for idx, yr in enumerate(years):
    with cols[idx]:
        init_val_100m = default_ops.get(yr, 0.0) / 100_000_000.0
        val_100m = st.number_input(
            f"{yr}년 (억원)", 
            value=float(init_val_100m), 
            step=10.0, 
            format="%.1f",
            key=f"input_{selected_stock}_{yr}"
        )
        updated_ops[yr] = val_100m * 100_000_000.0

# 주가 데이터 수집 API
end_date = datetime.today()
start_date = end_date - timedelta(days=5 * 365)

@st.cache_data(ttl=3600)
def get_stock_data_api(code, start, end):
    df = fdr.DataReader(code, start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d'))
    return df.reset_index()

try:
    stock_df = get_stock_data_api(stock_code, start_date, end_date)
except Exception as e:
    st.error(f"API에서 주가 데이터를 불러오는 중 오류가 발생했습니다: {e}")
    st.stop()

if stock_df.empty:
    st.error("불러온 주가 데이터가 없습니다.")
    st.stop()

# Dataframe 가공
stock_df['날짜'] = pd.to_datetime(stock_df['Date'])
stock_df['종가'] = pd.to_numeric(stock_df['Close'], errors='coerce')
stock_df['연도'] = stock_df['날짜'].dt.year.astype(str)

# 시가총액 계산
if 'Marcap' in stock_df.columns and stock_df['Marcap'].notnull().sum() > 0:
    stock_df['시가총액'] = pd.to_numeric(stock_df['Marcap'], errors='coerce')
else:
    stock_info_df = krx_df[krx_df['Code'] == stock_code]
    if not stock_info_df.empty and 'Stocks' in stock_info_df.columns:
        shares = stock_info_df['Stocks'].values[0]
        stock_df['시가총액'] = stock_df['종가'] * shares
    else:
        stock_df['시가총액'] = np.nan

# 영업이익 매핑 및 POR 계산
stock_df['수정_영업이익'] = stock_df['연도'].map(updated_ops)
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

# 주요 지표 요약
c1, c2, c3, c4 = st.columns(4)
latest_close = stock_df['종가'].iloc[-1] if not stock_df.empty else 0
c1.metric("최신 종가", f"{latest_close:,.0f} 원")
c2.metric("5년 평균 POR (Mean)", f"{mean_val:.2f}")
c3.metric("표준편차 (STDEV)", f"{std_val:.2f}")
c4.metric("+2σ 밴드 상단", f"{(mean_val + std_val*2):.2f}")

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
