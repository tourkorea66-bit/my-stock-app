import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import FinanceDataReader as fdr
from datetime import datetime, timedelta
import os
import json
import requests

# 페이지 기본 설정
st.set_page_config(page_title="반도체 & KRX 전종목 POR 밴드 시뮬레이터", layout="wide")

JSON_FILE = 'custom_stocks.json'
EXCEL_FILE = 'sigmahunting.xlsx'

DEFAULT_STOCKS = {
    '반도체': {
        'SK하이닉스': ['000660', 'SK하이닉스1'],
        '티엘비': ['356860', '티엘비1'],
        '엠케이전자': ['033160', '엠케이전자1'],
        'ISC': ['095340', 'ISC1'],
        '엘티씨': ['170920', '엘티씨1'],
        '하나마이크론': ['067310', '하나마이크론1'],
        '하나머티리얼즈': ['166090', '하나머티리얼즈1'],
        '코미코': ['183300', '코미코1'],
        '에프에스티': ['036810', '에프에스티1 ']
    },
    '관심종목': {}
}

# --- JSON 및 엑셀 로드 ---
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

if 'stock_categories' not in st.session_state:
    st.session_state.stock_categories = load_stocks_data()

@st.cache_resource
def load_excel_file(file_path):
    if os.path.exists(file_path):
        return pd.ExcelFile(file_path)
    return None

xls = load_excel_file(EXCEL_FILE)

# KRX 상장 종목 데이터
@st.cache_data(ttl=86400)
def get_krx_stock_list():
    try:
        df_krx = fdr.StockListing('KRX')
        cols = ['Code', 'Name']
        if 'Stocks' in df_krx.columns:
            cols.append('Stocks')
        return df_krx[cols].dropna(subset=['Code', 'Name'])
    except Exception as e:
        return pd.DataFrame(columns=['Code', 'Name', 'Stocks'])

krx_df = get_krx_stock_list()

# --- 과거 영업이익 로드 (엑셀 우선 ➔ Open API 백업) ---
def get_historical_operating_profit_reliable(code, sheet_name):
    ops = {'2021': 0.0, '2022': 0.0, '2023': 0.0, '2024': 0.0, '2025': 0.0}
    
    # 1. 엑셀 파일 시트에서 직접 읽기 (가장 확실함)
    if xls is not None and sheet_name is not None:
        try:
            df1 = pd.read_excel(xls, sheet_name=sheet_name)
            if len(df1) >= 3:
                row_yr = [str(x) for x in df1.iloc[1].tolist()]
                row_op = df1.iloc[2].tolist()
                
                for yr_col, op_val in zip(row_yr, row_op):
                    clean_yr = yr_col.replace('(E)', '').replace('.0', '').strip()
                    if clean_yr in ops:
                        try:
                            ops[clean_yr] = float(op_val)
                        except (ValueError, TypeError):
                            pass
        except Exception:
            pass

    # 2. 엑셀 데이터가 없을 경우 금융 Open API 타격
    if not any(ops.values()):
        try:
            url = f"https://finance.naver.com/item/coinfo.naver?code={code}"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
            tables = pd.read_html(res.text, encoding='euc-kr')
            for tbl in tables:
                if any('영업이익' in str(cell) for cell in tbl.iloc[:, 0]):
                    row_idx = tbl[tbl.iloc[:, 0].astype(str).str.contains('영업이익')].index[0]
                    for col_idx in range(1, len(tbl.columns)):
                        col_hdr = str(tbl.columns[col_idx])
                        val = tbl.iloc[row_idx, col_idx]
                        for yr in ops.keys():
                            if yr in col_hdr and pd.notnull(val):
                                try:
                                    ops[yr] = float(str(val).replace(',', '')) * 100_000_000.0
                                except ValueError:
                                    pass
                    break
        except Exception:
            pass

    return ops

# ==================== 사이드바 ====================
st.sidebar.title("⚙️ 카테고리 & 종목 관리")

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
        search_options = [f"{row['Name']} ({row['Code']})" for _, row in krx_df.iterrows()]
        selected_search = st.selectbox("KRX 종목 검색:", options=["선택하세요..."] + search_options)
        target_cat = st.selectbox("추가할 카테고리 선택:", list(st.session_state.stock_categories.keys()))
        
        if st.button("종목 저장"):
            if selected_search != "선택하세요...":
                name = selected_search.split(" (")[0]
                code = selected_search.split(" (")[1].replace(")", "")
                
                exists = any(name in stocks for stocks in st.session_state.stock_categories.values())
                if not exists:
                    st.session_state.stock_categories[target_cat][name] = [code, None]
                    save_stocks_data(st.session_state.stock_categories)
                    st.success(f"'{name}' 종목이 저장되었습니다!")
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

stock_info = available_stocks[selected_stock]
stock_code = stock_info[0]
sheet1_name = stock_info[1] if len(stock_info) > 1 else None

if st.sidebar.button(f"❌ {selected_stock} 삭제"):
    del st.session_state.stock_categories[selected_category][selected_stock]
    save_stocks_data(st.session_state.stock_categories)
    st.rerun()

# ==================== 메인 화면 ====================
st.title(f"📈 [{selected_category}] {selected_stock} ({stock_code}) POR 밴드 시뮬레이션")

# 과거 영업이익 수집 (엑셀 + API)
hist_ops = get_historical_operating_profit_reliable(stock_code, sheet1_name)

past_years = ['2021', '2022', '2023', '2024', '2025']
future_years = ['2026', '2027']

st.subheader("📊 연도별 영업이익 현황 및 추정치 (단위: 억원)")

st.markdown("**(1) 과거 실적 영업이익 (자동 로드 / 수동 수정 가능)**")
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

st.markdown("**(2) 올해/내년 추정 영업이익 (수동 입력)**")
f_cols = st.columns(len(future_years) + 3)

for idx, yr in enumerate(future_years):
    with f_cols[idx]:
        input_100m = st.number_input(
            f"{yr}년 추정(억원)", 
            value=0.0, 
            step=10.0, 
            format="%.1f",
            key=f"future_{selected_stock}_{yr}"
        )
        final_ops[yr] = input_100m * 100_000_000.0

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
    st.error(f"API에서 주가 데이터를 불러오는 중 오류가 발생했습니다: {e}")
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
    stock_info_df = krx_df[krx_df['Code'] == stock_code]
    if not stock_info_df.empty and 'Stocks' in stock_info_df.columns:
        shares = stock_info_df['Stocks'].values[0]
        stock_df['시가총액'] = stock_df['종가'] * shares
    else:
        stock_df['시가총액'] = np.nan

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
