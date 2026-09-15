import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as ob

# 페이지 기본 설정
st.set_page_config(page_title="반도체 POR 밴드 시뮬레이터", layout="wide")

EXCEL_FILE = 'sigmahunting(반도체)_ver0.2.xlsx'

# 종목 및 시트 매핑
STOCKS = {
    '티엘비': ('티엘비1', '티엘비2'),
    '엠케이전자': ('엠케이전자1', '엠케이전자2'),
    'ISC': ('ISC1', 'ISC2'),
    '엘티씨': ('엘티씨1', '엘티씨2'),
    '하나마이크론': ('하나마이크론1', '하나마이크론2'),
    '하나머티리얼즈': ('하나머티리얼즈1', '하나머티리얼즈2'),
    '코미코': ('코미코1', '코미코2'),
    '에프에스티': ('에프에스티1 ', '에프에스티2'),
    'SK하이닉스': ('SK하이닉스1', 'SK하이닉스2')
}

@st.cache_data
def load_excel():
    return pd.ExcelFile(EXCEL_FILE)

try:
    xls = load_excel()
except Exception as e:
    st.error(f"엑셀 파일('{EXCEL_FILE}')을 읽는데 실패했습니다. 동일 경로에 파일이 있는지 확인해 주세요.")
    st.stop()

# 사이드바: 종목 선택
st.sidebar.title("종목 선택")
selected_stock = st.sidebar.selectbox("분석할 종목을 선택하세요:", list(STOCKS.keys()))

sheet1_name, sheet2_name = STOCKS[selected_stock]

# 데이터 불러오기
df1 = pd.read_excel(xls, sheet_name=sheet1_name)
df2 = pd.read_excel(xls, sheet_name=sheet2_name)

# 오늘 이후(종가가 비어있거나 0인 데이터) 필터링
df2['종가'] = pd.to_numeric(df2['종가'], errors='coerce')
valid_df = df2[df2['종가'].notnull() & (df2['종가'] > 0)].copy()

st.title(f"📈 {selected_stock} POR 밴드 & 영업이익 시뮬레이션")

# 1. 시트1에서 기본 연도별 영업이익 파싱
years = ['2021', '2022', '2023', '2024', '2025', '2026', '2027']
default_ops = {}

if len(df1) >= 3:
    row_yr = df1.iloc[1].tolist()
    row_op = df1.iloc[2].tolist()
    for yr_col, op_val in zip(row_yr, row_op):
        yr_str = str(yr_col).replace('(E)', '').strip()
        if yr_str in years:
            try:
                default_ops[yr_str] = float(op_val)
            except:
                default_ops[yr_str] = 0.0

# 세션 상태 초기화
if f"op_{selected_stock}" not in st.session_state:
    st.session_state[f"op_{selected_stock}"] = default_ops.copy()

# 2. 영업이익 입력 테이블 UI
st.subheader("⚙️ 연도별 추정 영업이익(원) 수동 입력")
cols = st.columns(len(years))
updated_ops = {}

for idx, yr in enumerate(years):
    with cols[idx]:
        init_val = st.session_state[f"op_{selected_stock}"].get(yr, 0.0)
        val = st.number_input(
            f"{yr}년", 
            value=float(init_val), 
            step=100000000.0, 
            format="%.0f",
            key=f"input_{selected_stock}_{yr}"
        )
        updated_ops[yr] = val

# 버튼: 기본값 복원
if st.button("엑셀 기본값으로 복원"):
    st.session_state[f"op_{selected_stock}"] = default_ops.copy()
    st.rerun()

# 3. POR 및 밴드 재계산
valid_df['날짜'] = pd.to_datetime(valid_df['날짜'])
valid_df['연도'] = valid_df['날짜'].dt.year.astype(str)

# 입력된 영업이익 매핑 적용
valid_df['수정_영업이익'] = valid_df['연도'].map(updated_ops)
valid_df['수정_POR'] = np.where(
    valid_df['수정_영업이익'] > 0,
    valid_df['시가총액'] / valid_df['수정_영업이익'],
    0
)

# Mean, STDEV 산출
por_series = valid_df[valid_df['수정_POR'] > 0]['수정_POR']
mean_val = por_series.mean()
std_val = por_series.std()

valid_df['Mean'] = mean_val
valid_df['+1σ'] = mean_val + std_val
valid_df['+2σ'] = mean_val + (std_val * 2)
valid_df['-1σ'] = mean_val - std_val
valid_df['-2σ'] = mean_val - (std_val * 2)

# 4. Plotly 차트 시각화
fig = ob.Figure()

fig.add_trace(ob.Scatter(x=valid_df['날짜'], y=valid_df['수정_POR'], mode='lines', name='POR (재계산)', line=dict(color='black', width=2)))
fig.add_trace(ob.Scatter(x=valid_df['날짜'], y=valid_df['Mean'], mode='lines', name='Mean', line=dict(color='green', dash='dash')))
fig.add_trace(ob.Scatter(x=valid_df['날짜'], y=valid_df['+1σ'], mode='lines', name='+1σ', line=dict(color='orange', dash='dot')))
fig.add_trace(ob.Scatter(x=valid_df['날짜'], y=valid_df['+2σ'], mode='lines', name='+2σ', line=dict(color='red', dash='dot')))
fig.add_trace(ob.Scatter(x=valid_df['날짜'], y=valid_df['-1σ'], mode='lines', name='-1σ', line=dict(color='teal', dash='dot')))
fig.add_trace(ob.Scatter(x=valid_df['날짜'], y=valid_df['-2σ'], mode='lines', name='-2σ', line=dict(color='gray', dash='dot')))

fig.update_layout(
    title=f"{selected_stock} POR 밴드 차트 (오늘 자 데이터까지)",
    xaxis_title="날짜",
    yaxis_title="POR",
    hovermode="x unified",
    height=550
)

st.plotly_chart(fig, use_container_width=True)
