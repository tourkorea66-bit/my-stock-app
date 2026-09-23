import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import FinanceDataReader as fdr
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ==========================================
# 0. 기본 설정 & 데이터 로드
# ==========================================
st.set_page_config(
    page_title="주가 Valuation Band & Screening Dashboard",
    page_icon="📈",
    layout="wide",
)

DATA_FILE = "stock_data.json"


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            st.error(f"데이터 파일 읽기 오류: {e}")
    return {
        "반도체": {
            "삼성전자": {
                "code": "059300",
                "op_2021": "516339",
                "op_2022": "433766",
                "op_2023": "65670",
                "op_2024": "320000",
                "op_2025": "450000",
                "op_2026": "500000",
            }
        }
    }


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


if "categories" not in st.session_state:
    st.session_state.categories = load_data()


# ==========================================
# 1. 주가 데이터 수집 (FinanceDataReader)
# ==========================================
@st.cache_data(ttl=43200, show_spinner=False)  # 12시간 캐싱
def get_stock_data(code: str, start_date: datetime, end_date: datetime):
    """FinanceDataReader를 활용하여 일자별 주가 데이터를 수집합니다."""
    try:
        formatted_code = str(code).zfill(6)
        df = fdr.DataReader(formatted_code, start_date, end_date)

        if df.empty:
            return pd.DataFrame()

        df = df.reset_index()
        df.rename(columns={"Date": "Date", "Close": "Close"}, inplace=True)
        return df
    except Exception as e:
        return pd.DataFrame()


# ==========================================
# 2. Valuation (POR) 계산 로직
# ==========================================
def process_stock_valuation(code, start_date, end_date, ops_dict):
    df = get_stock_data(code, start_date, end_date)
    if df.empty:
        return pd.DataFrame()

    df["Date"] = pd.to_datetime(df["Date"])
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df["연도"] = df["Date"].dt.year.astype(str)

    # 연도별 영업이익 매핑 (단위: 억원)
    ops_mapped = {k: float(v) for k, v in ops_dict.items() if v}
    df["영업이익_억원"] = df["연도"].map(ops_mapped)

    # POR (주가 / 영업이익 수치 상대 배수) 산출
    df["수정_POR"] = np.where(
        (df["영업이익_억원"].notnull()) & (df["영업이익_억원"] > 0),
        df["Close"] / (df["영업이익_억원"] / 1000),
        np.nan,
    )

    return df


# ==========================================
# 3. 스크리닝 병렬 처리 함수
# ==========================================
def analyze_single_stock_task(task):
    cat_name, s_name, s_info, start_date, end_date = task
    code = s_info.get("code")
    if not code:
        return None, None

    ops_dict = {}
    for yr in ["2021", "2022", "2023", "2024", "2025", "2026"]:
        val = s_info.get(f"op_{yr}", "0")
        try:
            ops_dict[yr] = float(val) if val else 0.0
        except ValueError:
            ops_dict[yr] = 0.0

    df = process_stock_valuation(code, start_date, end_date, ops_dict)
    if df.empty or len(df) < 10:
        return None, None

    cur_close = df["Close"].iloc[-1]
    por_1s_res, por_2s_res = None, None

    v_por = df["수정_POR"].dropna()
    if len(v_por) >= 10:
        m_por, s_por = v_por.mean(), v_por.std()
        t_por_1s, t_por_2s = m_por - s_por, m_por - (s_por * 2)
        cur_por = v_por.iloc[-1]

        base_info = {
            "카테고리": cat_name,
            "종목명": s_name,
            "코드": code,
            "현재가": f"{cur_close:,.0f}원",
            "현재 POR 상대지표": f"{cur_por:.2f}",
            "평균 지표": f"{m_por:.2f}",
        }
        if cur_por <= t_por_1s:
            diff = ((cur_por - t_por_1s) / t_por_1s) * 100
            por_1s_res = {
                **base_info,
                "-1σ 기준": f"{t_por_1s:.2f}",
                "괴리율": f"{diff:.1f}%",
            }
        if cur_por <= t_por_2s:
            diff = ((cur_por - t_por_2s) / t_por_2s) * 100
            por_2s_res = {
                **base_info,
                "-2σ 기준": f"{t_por_2s:.2f}",
                "괴리율": f"{diff:.1f}%",
            }

    return por_1s_res, por_2s_res


# ==========================================
# 4. Streamlit UI 대시보드
# ==========================================
st.title("📈 Valuation Band & Screening Dashboard")

st.sidebar.header("⚙️ 분석 및 종목 관리")

# 종목 추가/수정 폼
with st.sidebar.expander("➕ 종목 추가 / 수정", expanded=False):
    cats = list(st.session_state.categories.keys())
    sel_cat = st.selectbox("카테고리 선택", cats + ["새 카테고리 추가"])
    if sel_cat == "새 카테고리 추가":
        sel_cat = st.text_input("신규 카테고리명")

    new_name = st.text_input("종목명 (예: 삼성전자)")
    new_code = st.text_input("종목코드 6자리 (예: 059300)")

    st.markdown("**연도별 영업이익 (단위: 억원)**")
    c1, c2 = st.columns(2)
    op_2021 = c1.text_input("2021", "0")
    op_2022 = c2.text_input("2022", "0")
    op_2023 = c1.text_input("2023", "0")
    op_2024 = c2.text_input("2024", "0")
    op_2025 = c1.text_input("2025", "0")
    op_2026 = c2.text_input("2026 (추정)", "0")

    if st.button("저장하기"):
        if new_code and new_name and sel_cat:
            formatted_code = new_code.zfill(6)
            if sel_cat not in st.session_state.categories:
                st.session_state.categories[sel_cat] = {}

            st.session_state.categories[sel_cat][new_name] = {
                "code": formatted_code,
                "op_2021": op_2021,
                "op_2022": op_2022,
                "op_2023": op_2023,
                "op_2024": op_2024,
                "op_2025": op_2025,
                "op_2026": op_2026,
            }
            save_data(st.session_state.categories)
            st.success(f"'{new_name}' 저장 완료!")
            st.rerun()

st.sidebar.subheader("📅 조회 기간")
end_date = datetime.today()
start_date = end_date - timedelta(days=365 * 5)

tab1, tab2 = st.tabs(["📊 종목별 Valuation Band", "🔍 저평가 스크리닝 (-1σ / -2σ)"])

# ------------------------------------------
# TAB 1: 종목별 밴드 차트
# ------------------------------------------
with tab1:
    col_a, col_b = st.columns(2)
    with col_a:
        all_cats = list(st.session_state.categories.keys())
        if not all_cats:
            st.warning("등록된 종목이 없습니다. 사이드바에서 추가해 주세요.")
            st.stop()
        selected_cat = st.selectbox("카테고리 선택", all_cats, key="tab1_cat")
    with col_b:
        stocks_in_cat = list(st.session_state.categories[selected_cat].keys())
        selected_stock = st.selectbox("종목 선택", stocks_in_cat, key="tab1_stock")

    stock_info = st.session_state.categories[selected_cat][selected_stock]
    stock_code = stock_info["code"]

    ops_dict = {}
    for yr in ["2021", "2022", "2023", "2024", "2025", "2026"]:
        try:
            ops_dict[yr] = float(stock_info.get(f"op_{yr}", 0.0))
        except ValueError:
            ops_dict[yr] = 0.0

    with st.spinner(f"'{selected_stock}' 데이터를 불러오는 중..."):
        df_val = process_stock_valuation(stock_code, start_date, end_date, ops_dict)

    if df_val.empty:
        st.error("데이터를 가져올 수 없습니다. 종목코드를 확인해주세요.")
    else:
        curr_price = df_val["Close"].iloc[-1]
        curr_por = df_val["수정_POR"].dropna().iloc[-1] if not df_val["수정_POR"].dropna().empty else 0

        m1, m2 = st.columns(2)
        m1.metric("현재가", f"{curr_price:,.0f} 원")
        m2.metric("현재 POR 상대지표", f"{curr_por:.2f}")

        st.subheader("POR Valuation Multiple Band Chart")

        v_series = df_val["수정_POR"].dropna()
        if len(v_series) >= 10:
            mean_val = v_series.mean()
            std_val = v_series.std()

            p1s, p2s = mean_val + std_val, mean_val + (2 * std_val)
            m1s, m2s = mean_val - std_val, mean_val - (2 * std_val)

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df_val["Date"], y=df_val["수정_POR"], mode="lines", name="POR Multiple", line=dict(color="black", width=2)))
            fig.add_trace(go.Scatter(x=df_val["Date"], y=[mean_val]*len(df_val), mode="lines", name="Mean", line=dict(color="blue", dash="dash")))
            fig.add_trace(go.Scatter(x=df_val["Date"], y=[p1s]*len(df_val), mode="lines", name="+1σ", line=dict(color="red", dash="dot")))
            fig.add_trace(go.Scatter(x=df_val["Date"], y=[p2s]*len(df_val), mode="lines", name="+2σ", line=dict(color="darkred", dash="dot")))
            fig.add_trace(go.Scatter(x=df_val["Date"], y=[m1s]*len(df_val), mode="lines", name="-1σ", line=dict(color="green", dash="dot")))
            fig.add_trace(go.Scatter(x=df_val["Date"], y=[m2s]*len(df_val), mode="lines", name="-2σ", line=dict(color="darkgreen", dash="dot")))

            fig.update_layout(title=f"{selected_stock} POR Band", xaxis_title="Date", yaxis_title="Multiple", height=500)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("밴드를 계산하기 위한 충분한 데이터가 없습니다.")

# ------------------------------------------
# TAB 2: 전체 종목 스크리닝
# ------------------------------------------
with tab2:
    st.subheader("🔍 저평가 종목 스크리닝 (-1σ / -2σ 이하)")

    if st.button("🚀 스크리닝 실행", key="btn_screen"):
        tasks = []
        for cat_name, stocks in st.session_state.categories.items():
            for s_name, s_info in stocks.items():
                tasks.append((cat_name, s_name, s_info, start_date, end_date))

        por_1s_list, por_2s_list = [], []
        progress_bar = st.progress(0)
        status_text = st.empty()

        completed = 0
        total = len(tasks)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(analyze_single_stock_task, task) for task in tasks]
            for future in as_completed(futures):
                p1, p2 = future.result()
                if p1: por_1s_list.append(p1)
                if p2: por_2s_list.append(p2)

                completed += 1
                progress_bar.progress(completed / total)
                status_text.text(f"스크리닝 진행 중... ({completed}/{total})")

        status_text.success("스크리닝 완료!")

        t1, t2 = st.tabs(["POR -1σ 이하 (주의 관찰)", "POR -2σ 이하 (최저점 접근)"])
        with t1:
            if por_1s_list:
                st.dataframe(pd.DataFrame(por_1s_list), use_container_width=True)
            else:
                st.info("조건에 해당하는 종목이 없습니다.")
        with t2:
            if por_2s_list:
                st.dataframe(pd.DataFrame(por_2s_list), use_container_width=True)
            else:
                st.info("조건에 해당하는 종목이 없습니다.")
