import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import os

# 페이지 기본 설정
st.set_page_config(page_title="반도체 POR 밴드 시뮬레이터", layout="wide")

# 엑셀 파일명 지정
EXCEL_FILE = 'sigmahunting.xlsx'

# 파일 존재 여부 체크
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

@st.cache_resource
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

st.title(f"📈 {selected_stock} POR 밴드 & 영업이익 시뮬레이션")

# 1. 시트1에서 연도별 영업이익 추출
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

# 2. 영업이익 입력 테이블 UI
st.subheader("⚙️ 연도별 추정 영업이익(원) 수동 입력")
cols = st.columns(len(years))
updated_ops = {}

for idx, yr in enumerate(years):
    with cols[idx]:
        # 기본값 로드
        init_val = default_ops.get(yr, 0.0)
        val = st.number_input(
            f"{yr}년", 
            value=float(init_val), 
            step=1000000000.0, 
            format="%.0f",
            key=f"input_{selected_stock}_{yr}"
        )
        updated_ops[yr] = val

# 3. 데이터 가공 및 안전한 매핑
valid_df = df2.copy()
valid_df['종가'] = pd.to_numeric(valid_df['종가'], errors='coerce')
valid_df = valid_df[valid_df['종가'].notnull() & (valid_df['종가'] > 0)].copy()

valid_df['날짜'] = pd.to_datetime(valid_df['날짜'], errors='coerce')
valid_df['연도'] = valid_df['날짜'].dt.year.astype(str)

valid_df['시가총액'] = pd.to_numeric(valid_df['시가총액'], errors='coerce')

# 영업이익 매핑 (문자열 연도와 정확히 일치)
valid_df['수정_영업이익'] = valid_df['연도'].map(updated_ops)
valid_df['수정_영업이익'] = pd.to_numeric(valid_df['수정_영업이익'], errors='coerce')

# POR 계산
valid_df['수정_POR'] = np.where(
    (valid_df['수정_영업이익'].notnull()) & (valid_df['수정_영업이익'] > 0),
    valid_df['시가총액'] / valid_df['수정_영업이익'],
    np.nan
)

# Mean & Standard Deviation 계산
valid_por = valid_df['수정_POR'].dropna()

if len(valid_por) > 0:
    mean_val = valid_por.mean()
    std_val = valid_por.std()
else:
    mean_val, std_val = 0.0, 0.0

valid_df['Mean'] = mean_val
valid_df['+1σ'] = mean_val + std_val
valid_df['+2σ'] = mean_val + (std_val * 2)
valid_df['-1σ'] = mean_val - std_val
valid_df['-2σ'] = mean_val - (std_val * 2)

# 주요 지표 요약 출력
c1, c2, c3 = st.columns(3)
c1.metric("평균 POR (Mean)", f"{mean_val:.2f}")
c2.metric("표준편차 (STDEV)", f"{std_val:.2f}")
c3.metric("+2σ 밴드 상단", f"{(mean_val + std_val*2):.2f}")

# 4. Plotly 차트 그리시
fig = go.Figure()

# POR 선
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['수정_POR'], mode='lines', name='POR (실시간 재계산)', line=dict(color='black', width=2)))

# 밴드 선들
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['+2σ'], mode='lines', name='+2σ (상단 밴드)', line=dict(color='#dc3545', width=1.5, dash='dash')))
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['+1σ'], mode='lines', name='+1σ', line=dict(color='#ffc107', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['Mean'], mode='lines', name='Mean (평균)', line=dict(color='#28a745', width=2, dash='solid')))
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['-1σ'], mode='lines', name='-1σ', line=dict(color='#17a2b8', width=1.5, dash='dot')))
fig.add_trace(go.Scatter(x=valid_df['날짜'], y=valid_df['-2σ'], mode='lines', name='-2σ (하단 밴드)', line=dict(color='#6c757d', width=1.5, dash='dash')))

fig.update_layout(
    title=f"<b>{selected_stock} POR 밴드 시뮬레이션 차트</b>",
    xaxis_title="날짜",
    yaxis_title="POR",
    hovermode="x unified",
    height=600,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

st.plotly_chart(fig, use_container_width=True)
