import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# 1. 페이지 기본 설정
st.set_page_config(page_title="SigmaHunting POR 밴드 분석", layout="wide")
st.title("📈 SigmaHunting POR 밴드 분석 대시보드")

DEFAULT_FILE = "sigmahunting(JHY)_ver0.2.xlsx"

@st.cache_data
def load_data(file_path):
    xls = pd.ExcelFile(file_path)
    companies = sorted(list(set([s[:-1] for s in xls.sheet_names if s[-1] in ['1', '2']])))
    
    data_store = {}
    for comp in companies:
        df1 = pd.read_excel(xls, sheet_name=f"{comp}1")
        df2 = pd.read_excel(xls, sheet_name=f"{comp}2")
        df2['날짜'] = pd.to_datetime(df2['날짜'])
        data_store[comp] = {'info': df1, 'daily': df2}
    return companies, data_store

try:
    companies, data_store = load_data(DEFAULT_FILE)
    
    # 종목 선택 사이드바
    selected = st.sidebar.selectbox("🎯 분석 종목 선택", companies)
    comp_daily = data_store[selected]['daily']
    
    # [수정] 종가나 주요 수치에 빈 칸(NaN)이 있는 행 제거 후 최신 데이터 추출
    valid_daily = comp_daily.dropna(subset=['종가'])
    
    if valid_daily.empty:
        st.warning("표시할 수 있는 유효한 데이터가 없습니다.")
    else:
        latest = valid_daily.iloc[-1]
        
        # [수정] int() 변환 전 pd.isna() 안전 처리
        close_price = f"{int(latest['종가']):,} 원" if pd.notna(latest['종가']) else "-"
        mcap = f"{latest['시가총액']/1e8:,.1f} 억원" if pd.notna(latest['시가총액']) else "-"
        op_inc = f"{latest['영업이익']/1e8:,.1f} 억원" if pd.notna(latest['영업이익']) else "-"
        por_val = f"{latest['POR']:.2f} 배" if pd.notna(latest['POR']) else "-"

        # 핵심 지표 표시
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("최근 종가", close_price)
        c2.metric("시가총액", mcap)
        c3.metric("영업이익", op_inc)
        c4.metric("현재 POR", por_val)
        
        st.markdown("---")
        
        # Plotly 차트 생성
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=comp_daily['날짜'], y=comp_daily['POR'], name='실제 POR', line=dict(color='#1f77b4', width=2)))
        
        for col, color, dash in [('Mean','#7f7f7f','dash'), ('+1σ','#ff7f0e','dot'), ('+2σ','#d62728','dot'), ('-1σ','#2ca02c','dot'), ('-2σ','#9467bd','dot')]:
            if col in comp_daily.columns:
                fig.add_trace(go.Scatter(x=comp_daily['날짜'], y=comp_daily[col], name=col, line=dict(color=color, dash=dash)))
                
        fig.update_layout(title=f"{selected} POR 밴드 추이", template="plotly_white", height=500)
        st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"파일을 읽는 도중 오류가 발생했습니다: {e}")