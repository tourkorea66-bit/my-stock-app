import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import os

# 페이지 기본 설정
st.set_page_config(page_title="반도체 POR 밴드 시뮬레이터", layout="wide")

# 엑셀 파일명 지정
EXCEL_FILE = 'sigmahunting.xlsx'

# 파일 존재 여부 먼저 강제 체크
if not os.path.exists(EXCEL_FILE):
    st.error(f"❌ '{EXCEL_FILE}' 파일을 동일한 폴더(GitHub 메인)에서 찾을 수 없습니다. 파일 이름을 확인해 주세요.")
    st.stop()

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

@st.cache_data(ttl=60)
def load_excel_file(file_path):
    return pd.ExcelFile(file_path)

try:
    xls = load_excel_file(EXCEL_FILE)
except Exception as e:
    st.error(f"엑셀 파일을 읽는 도중 오류가 발생했습니다: {e}")
    st.stop()

# 사이드바: 종목 선택
st.sidebar.title("종목 선택")
selected_stock = st.sidebar.selectbox("분석할 종목을 선택하세요:", list(STOCKS.keys()))

sheet1_name, sheet2_name = STOCKS[selected_stock]

# 데이터 불러오기
try:
    df1 = pd.read_excel(xls, sheet_name=sheet1_name)
    df2 = pd.read_excel(xls, sheet_name=sheet2_name)
except Exception as e:
    st.error(f"시트 데이터를 읽어오는 중 에러가 발생했습니다 ({selected_stock}): {e}")
    st.stop()

# 종가 유효 데이터 필터링
df2['종가'] = pd.to_numeric(df2['종가'], errors='coerce')
valid_df = df2[df2['종가'].notnull() & (df2['종가'] > 0)].copy()

st.title(f"📈 {selected_stock} POR 밴드 & 영업이익 시뮬레이션")

# 1. 시트1에서 연도별 영업이익 추출 (안전한 파싱)
years = ['2021', '2022', '2023', '2024', '2025', '2026', '2027']
default_ops = {yr: 0.0 for yr in years}

try:
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
    st.warning("영업이익 기본값을 파싱하는 중 일부 항목을 0으로 대체했습니다.")

# 세션 상태 관리
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
valid_df['날짜'] = pd.to_datetime(valid_df['날짜'], errors='coerce')
valid_df['연도'] = valid_df['날짜'].dt.year.astype(str)

valid_df['시가총액'] = pd.to_numeric(valid_df['시가총액'], errors='coerce')
valid_df['수정_영업이익'] = valid_df['연도'].map(updated_ops)

# POR 계산 (0 나누기 방지)
valid_df['수정_POR'] = np.where(
    (valid_df['수정_영업이익'].notnull()) & (valid_df['수정_영업이익'] > 0),
    valid_df['시가총액'] / valid_df['수정_영업이익'],
    np.nan
)

# Mean, STDEV 산출
por_series = valid_df['수정_POR'].dropna()

if len(por_series) > 0:
    mean_val = por_series.mean()
    std_val = por_series.std()
else:
    mean_val, std_val = 0.0, 0.0

valid_df['Mean'] = mean_val
valid_df['+1σ'] = mean_val + std_val
valid_df['+2σ'] = mean_val + (std_val * 2)
valid_df['-1σ'] = mean_val - std_val
valid_df['-2σ'] = mean_val - (std_val * 2)

# 4. Plotly 차트 시각화
fig = go.Figure()

fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['수정_POR'], mode='lines', name='POR (재계산)', line=dict(color='black', width=2)))
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['Mean'], mode='lines', name='Mean', line=dict(color='green', dash='dash')))
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['+1σ'], mode='lines', name='+1σ', line=dict(color='orange', dash='dot')))
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['+2σ'], mode='lines', name='+2σ', line=dict(color='red', dash='dot')))
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['-1σ'], mode='lines', name='-1σ', line=dict(color='teal', dash='dot')))
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['-2σ'], mode='lines', name='-2σ', line=dict(color='gray', dash='dot')))

fig.update_layout(
    title=f"{selected_stock} POR 밴드 차트 (오늘 자 데이터까지)",
    xaxis_title="날짜",
    yaxis_title="POR",
    hovermode="x unified",
    height=550
)

st.plotly_chart(fig, use_container_width=True)
