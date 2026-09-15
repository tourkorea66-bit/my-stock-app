import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import FinanceDataReader as fdr
from datetime import datetime, timedelta
import os

# 페이지 기본 설정
st.set_page_config(page_title="반도체 & KRX 전종목 POR 밴드 시뮬레이터", layout="wide")

# 엑셀 파일명 지정 (기본 종목의 연도별 영업이익 로드용)
EXCEL_FILE = 'sigmahunting.xlsx'

# 기본 제공 종목 리스트 (종목명: (종목코드, 시트1이름))
DEFAULT_STOCKS = {
    'SK하이닉스': ('000660', 'SK하이닉스1'),
    '티엘비': ('356860', '티엘비1'),
    '엠케이전자': ('033160', '엠케이전자1'),
    'ISC': ('095340', 'ISC1'),
    '엘티씨': ('170920', '엘티씨1'),
    '하나마이크론': ('067310', '하나마이크론1'),
    '하나머티리얼즈': ('166090', '하나머티리얼즈1'),
    '코미코': ('183300', '코미코1'),
    '에프에스티': ('036810', '에프에스티1 ')
}

# 1. KRX 전체 상장 종목 리스트 가져오기 (캐싱 적용)
@st.cache_data(ttl=86400) # 하루에 한번만 갱신
def get_krx_stock_list():
    try:
        # KOSPI, KOSDAQ 상장 종목 전체 가져오기
        df_krx = fdr.StockListing('KRX')
        # Code, Name 컬럼 추출
        return df_krx[['Code', 'Name']].dropna()
    except Exception as e:
        st.error(f"상장 종목 리스트를 불러오는 중 오류 발생: {e}")
        return pd.DataFrame(columns=['Code', 'Name'])

krx_df = get_krx_stock_list()

# 세션 상태 초기화 (사용자가 추가한 종목 저장용)
if 'custom_stocks' not in st.session_state:
    st.session_state.custom_stocks = DEFAULT_STOCKS.copy()

@st.cache_resource
def load_excel_file(file_path):
    if os.path.exists(file_path):
        return pd.ExcelFile(file_path)
    return None

xls = load_excel_file(EXCEL_FILE)

# ==================== 사이드바 ====================
st.sidebar.title("🔍 종목 검색 및 선택")

# 종목 검색 및 추가 UI
st.sidebar.subheader("신규 종목 검색/추가")
if not krx_df.empty:
    # 종목명(종목코드) 형식으로 검색 드롭다운 생성
    search_options = [f"{row['Name']} ({row['Code']})" for _, row in krx_df.iterrows()]
    selected_search = st.sidebar.selectbox("KRX 상장 종목 검색:", options=["선택하세요..."] + search_options)
    
    if st.sidebar.button("➕ 종목 추가"):
        if selected_search != "선택하세요...":
            name = selected_search.split(" (")[0]
            code = selected_search.split(" (")[1].replace(")", "")
            
            if name not in st.session_state.custom_stocks:
                st.session_state.custom_stocks[name] = (code, None) # 엑셀 시트명은 None
                st.sidebar.success(f"'{name}' 종목이 추가되었습니다!")
            else:
                st.sidebar.info(f"'{name}' 종목은 이미 리스트에 있습니다.")

st.sidebar.markdown("---")

# 종목 선택 드롭다운
selected_stock = st.sidebar.selectbox(
    "분석할 종목을 선택하세요:", 
    list(st.session_state.custom_stocks.keys())
)

stock_code, sheet1_name = st.session_state.custom_stocks[selected_stock]

# ==================== 메인 화면 ====================
st.title(f"📈 {selected_stock} ({stock_code}) 5년치 POR 밴드 시뮬레이션")

# 연도 설정 (2021 ~ 2027)
years = ['2021', '2022', '2023', '2024', '2025', '2026', '2027']
default_ops = {yr: 0.0 for yr in years}

# 엑셀 파일에서 기본 영업이익 불러오기 (기본 제공 종목이고 엑셀 시트가 존재하는 경우)
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
    except Exception as e:
        pass

# 영업이익 수동 입력 UI
st.subheader("⚙️ 연도별 추정 영업이익(원) 입력/수정")
cols = st.columns(len(years))
updated_ops = {}

for idx, yr in enumerate(years):
    with cols[idx]:
        init_val = default_ops.get(yr, 0.0)
        val = st.number_input(
            f"{yr}년", 
            value=float(init_val), 
            step=1000000000.0, 
            format="%.0f",
            key=f"input_{selected_stock}_{yr}"
        )
        updated_ops[yr] = val

# 최근 5년치 주가 데이터 수집 API
end_date = datetime.today()
start_date = end_date - timedelta(days=5 * 365)

@st.cache_data(ttl=3600)
def get_stock_data_api(code, start, end):
    df = fdr.DataReader(code, start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d'))
    df = df.reset_index()
    return df

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

if 'Marcap' in stock_df.columns:
    stock_df['시가총액'] = pd.to_numeric(stock_df['Marcap'], errors='coerce')
else:
    stock_df['시가총액'] = np.nan

# 영업이익 매핑 및 POR 계산
stock_df['수정_영업이익'] = stock_df['연도'].map(updated_ops)
stock_df['수정_영업이익'] = pd.to_numeric(stock_df['수정_영업이익'], errors='coerce')

if stock_df['시가총액'].notnull().sum() > 0:
    stock_df['수정_POR'] = np.where(
        (stock_df['수정_영업이익'].notnull()) & (stock_df['수정_영업이익'] > 0),
        stock_df['시가총액'] / stock_df['수정_영업이익'],
        np.nan
    )
else:
    stock_df['수정_POR'] = np.where(
        (stock_df['수정_영업이익'].notnull()) & (stock_df['수정_영업이익'] > 0),
        (stock_df['종가'] * 1000000) / stock_df['수정_영업이익'],
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

# Plotly 차트 그리시
fig = go.Figure()

# POR 선 (하얀색)
fig.add_trace(go.Scatter(
    x=stock_df['날짜'], 
    y=stock_df['수정_POR'], 
    mode='lines', 
    name='POR (5년 실시간)', 
    line=dict(color='#FFFFFF', width=2)
))

# 밴드 라인들
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['+2σ'], mode='lines', name='+2σ (상단)', line=dict(color='#FF5555', width=1.5, dash='dash')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['+1σ'], mode='lines', name='+1σ', line=dict(color='#FFB86C', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['Mean'], mode='lines', name='Mean (평균)', line=dict(color='#50FA7B', width=2, dash='solid')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['-1σ'], mode='lines', name='-1σ', line=dict(color='#8BE9FD', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=stock_df['날짜'], y=stock_df['-2σ'], mode='lines', name='-2σ (하단)', line=dict(color='#BD93F9', width=1.5, dash='dash')))

# 차트 디자인 설정
fig.update_layout(
    title=dict(text=f"<b>{selected_stock} 최근 5년치 POR 밴드 차트</b>", font=dict(color='#FFFFFF', size=20)),
    paper_bgcolor='#1E1E1E',
    plot_bgcolor='#141414',
    font=dict(color='#FFFFFF'),
    xaxis=dict(
        title="날짜",
        showgrid=True,
        gridcolor='#333333',
        color='#FFFFFF'
    ),
    yaxis=dict(
        title="POR",
        showgrid=True,
        gridcolor='#333333',
        color='#FFFFFF'
    ),
    hovermode="x unified",
    height=600,
    legend=dict(
        orientation="h", 
        yanchor="bottom", 
        y=1.02, 
        xanchor="right", 
        x=1,
        font=dict(color='#FFFFFF')
    )
)

st.plotly_chart(fig, use_container_width=True)
