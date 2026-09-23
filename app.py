import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import io
import xml.etree.ElementTree as ET
import zipfile

import FinanceDataReader as fdr
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# 1. 페이지 기본 설정
st.set_page_config(
    page_title="POR & PER 밴드 시뮬레이터",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ==========================================
# 🔑 JSONBin & DART API 설정
JSONBIN_BIN_ID = "6ab0e792ac6210605ae50647".strip()
JSONBIN_API_KEY = (
    "$2a$10$rD4B95ncdqx06uhoNoZx.e8D6bg7c7EKwxOHKb7siGAbfUW59G4Q6".strip()
)

DART_API_KEY = "28b4dc2f6fac759fc70daa06cb0e9761eda3c105".strip()
# ==========================================

# 기본 종목 목록
DEFAULT_STOCKS = {
    "반도체": {
        "SK하이닉스": {"code": "000660", "op_2026": 0.0, "np_2026": 0.0},
        "티엘비": {"code": "356860", "op_2026": 0.0, "np_2026": 0.0},
        "엠케이전자": {"code": "033160", "op_2026": 0.0, "np_2026": 0.0},
        "ISC": {"code": "095340", "op_2026": 0.0, "np_2026": 0.0},
        "엘티씨": {"code": "170920", "op_2026": 0.0, "np_2026": 0.0},
        "하나마이크론": {"code": "067310", "op_2026": 0.0, "np_2026": 0.0},
        "하나머티리얼즈": {"code": "166090", "op_2026": 0.0, "np_2026": 0.0},
        "코미코": {"code": "183300", "op_2026": 0.0, "np_2026": 0.0},
        "에프에스티": {"code": "036810", "op_2026": 0.0, "np_2026": 0.0},
    },
    "관심종목": {},
}


# --- 🔄 JSONBin 전용 로드 / 저장 함수 ---
def load_stocks_data():
    if not JSONBIN_BIN_ID or "여기에" in JSONBIN_BIN_ID:
        return copy.deepcopy(DEFAULT_STOCKS)

    headers = {
        "X-Master-Key": JSONBIN_API_KEY,
        "X-Access-Key": JSONBIN_API_KEY,
    }

    try:
        bin_id_clean = JSONBIN_BIN_ID.strip()
        url = f"https://api.jsonbin.io/v3/b/{bin_id_clean}/latest"

        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            saved_data = res.json()
            if isinstance(saved_data, dict) and "record" in saved_data:
                saved_data = saved_data["record"]

            if isinstance(saved_data, dict) and saved_data:
                return saved_data
    except Exception:
        pass

    return copy.deepcopy(DEFAULT_STOCKS)


def save_stocks_data(data):
    if not data:
        data = copy.deepcopy(DEFAULT_STOCKS)

    if not JSONBIN_BIN_ID or "여기에" in JSONBIN_BIN_ID:
        return False

    headers = {
        "Content-Type": "application/json",
        "X-Master-Key": JSONBIN_API_KEY,
        "X-Access-Key": JSONBIN_API_KEY,
    }

    try:
        bin_id_clean = JSONBIN_BIN_ID.strip()
        url = f"https://api.jsonbin.io/v3/b/{bin_id_clean}"

        res = requests.put(url, json=data, headers=headers, timeout=5)
        return res.status_code == 200
    except Exception:
        return False


# 세션 상태 로드
if "stock_categories" not in st.session_state:
    st.session_state.stock_categories = load_stocks_data()


# KRX 상장 종목 데이터 (캐싱)
@st.cache_data(ttl=86400)
def get_krx_stock_list():
    try:
        return fdr.StockListing("KRX")
    except Exception:
        return pd.DataFrame()


krx_df = get_krx_stock_list()


# --- DART 고유번호 매핑 캐싱 ---
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
                xml_data = z.read("CORPCODE.xml")
                root = ET.fromstring(xml_data)
                for list_item in root.findall("list"):
                    stock_code = list_item.findtext("stock_code", "").strip()
                    corp_code = list_item.findtext("corp_code", "").strip()
                    if stock_code and corp_code:
                        corp_map[stock_code.zfill(6)] = corp_code
    except Exception:
        pass
    return corp_map


# 단일 연도 DART API 호출 (영업이익 + 당기순이익 수집)
def _fetch_single_year_dart_financials(args):
    b_year, clean_key, corp_code = args
    reprt_codes = ["11011", "11014", "11012", "11013"]

    op_val, np_val = 0.0, 0.0
    found_op, found_np = False, False

    for reprt_code in reprt_codes:
        if found_op and found_np:
            break
        for fs_div in ["CFS", "OFS"]:
            if found_op and found_np:
                break
            try:
                url = f"https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?crtfc_key={clean_key}&corp_code={corp_code}&bsns_year={b_year}&reprt_code={reprt_code}&fs_div={fs_div}"
                res = requests.get(url, timeout=4)
                data = res.json()

                if data.get("status") == "000" and "list" in data:
                    for item in data["list"]:
                        acc_id = str(item.get("account_id", ""))
                        acc_nm = str(item.get("account_nm", "")).replace(" ", "").strip()

                        # 영업이익 확인
                        if not found_op:
                            is_op = (
                                "OperatingProfit" in acc_id
                                or acc_nm in ["영업이익", "영업이익(손실)", "영업손실(이익)", "영업손실"]
                                or ("영업이익" in acc_nm and "률" not in acc_nm and "이익률" not in acc_nm)
                            )
                            if is_op:
                                raw_val = str(item.get("thstrm_amount", "0")).replace(",", "").replace(" ", "").strip()
                                if raw_val and raw_val != "-":
                                    if raw_val.startswith("(") and raw_val.endswith(")"):
                                        raw_val = "-" + raw_val[1:-1]
                                    op_val = float(raw_val) / 100_000_000.0
                                    found_op = True

                        # 당기순이익 확인
                        if not found_np:
                            is_np = (
                                "NetIncome" in acc_id
                                or acc_nm in ["당기순이익", "당기순이익(손실)", "당기순손실(이익)", "당기순손실"]
                                or ("당기순이익" in acc_nm and "률" not in acc_nm and "이익률" not in acc_nm)
                            )
                            if is_np:
                                raw_val = str(item.get("thstrm_amount", "0")).replace(",", "").replace(" ", "").strip()
                                if raw_val and raw_val != "-":
                                    if raw_val.startswith("(") and raw_val.endswith(")"):
                                        raw_val = "-" + raw_val[1:-1]
                                    np_val = float(raw_val) / 100_000_000.0
                                    found_np = True
            except Exception:
                pass

    return b_year, round(op_val, 1), round(np_val, 1)


# DART 수집 함수 (영업이익 & 순이익)
@st.cache_data(ttl=3600)
def fetch_financials_dart(code, api_key):
    ops = {"2021": 0.0, "2022": 0.0, "2023": 0.0, "2024": 0.0, "2025": 0.0}
    nps = {"2021": 0.0, "2022": 0.0, "2023": 0.0, "2024": 0.0, "2025": 0.0}

    clean_key = str(api_key).strip()
    if not clean_key or clean_key == "YOUR_DART_API_KEY_HERE":
        return ops, nps

    corp_map = get_dart_corp_code_map(clean_key)
    corp_code = corp_map.get(str(code).zfill(6))
    if not corp_code:
        return ops, nps

    years = ["2021", "2022", "2023", "2024", "2025"]
    tasks = [(yr, clean_key, corp_code) for yr in years]

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(_fetch_single_year_dart_financials, tasks)
        for yr, o_val, n_val in results:
            ops[yr] = o_val
            nps[yr] = n_val

    return ops, nps


# ==================== 📱 초밀집 & 고대비 스타일링 (CSS) ====================
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 0.8rem !important;
        padding-bottom: 1.5rem !important;
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
    }

    div[data-testid="stMetric"] {
        background-color: #1E222A !important;
        padding: 4px 6px !important;
        border-radius: 6px !important;
        border: 1px solid #3A3F4D !important;
        margin-bottom: 4px !important;
        min-height: 50px !important;
    }
    
    div[data-testid="stMetricLabel"] p {
        font-size: 0.68rem !important;
        color: #DCDFE6 !important;
        font-weight: 600 !important;
        line-height: 1.1 !important;
        margin: 0 !important;
    }
    
    div[data-testid="stMetricValue"] div {
        font-size: 0.85rem !important;
        color: #FFFFFF !important;
        font-weight: 700 !important;
        line-height: 1.2 !important;
    }

    div[data-testid="stNumberInput"], div[data-testid="stTextInput"] {
        margin-bottom: 0px !important;
    }
    div[data-testid="stNumberInput"] label p, div[data-testid="stTextInput"] label p {
        font-size: 0.68rem !important;
        color: #DCDFE6 !important;
        font-weight: 600 !important;
        margin-bottom: 2px !important;
    }
    div[data-testid="stNumberInput"] input, div[data-testid="stTextInput"] input {
        height: 1.9rem !important;
        font-size: 0.80rem !important;
        color: #FFFFFF !important;
        background-color: #1E222A !important;
        border: 1px solid #3A3F4D !important;
        padding: 2px 4px !important;
    }

    div[data-testid="stSelectbox"] {
        margin-bottom: 4px !important;
    }
    div[data-testid="stSelectbox"] label p {
        font-size: 0.75rem !important;
        color: #DCDFE6 !important;
        font-weight: 600 !important;
        margin-bottom: 2px !important;
    }
    div[data-testid="stSelectbox"] div[role="combobox"] {
        min-height: 2.2rem !important;
    }

    hr {
        margin: 0.5rem 0 !important;
        border-color: #3A3F4D !important;
    }
    </style>
""",
    unsafe_allow_html=True,
)

# ==================== 🔍 메인 화면 상단 종목 검색/선택 ====================

cat_list = [cat for cat, stocks in st.session_state.stock_categories.items()]
if not cat_list:
    st.session_state.stock_categories["기본"] = {}
    cat_list = ["기본"]

top_col1, top_col2 = st.columns([1, 1])

with top_col1:
    selected_category = st.selectbox("📁 카테고리", cat_list, key="main_cat_select")

available_stocks = st.session_state.stock_categories.get(selected_category, {})
stock_names = list(available_stocks.keys())

with top_col2:
    if stock_names:
        selected_stock = st.selectbox("📈 종목 선택", stock_names, key="main_stock_select")
    else:
        selected_stock = None
        st.info("종목 없음")

with st.expander("⚙️ 카테고리 및 KRX 종목 추가 / 삭제"):
    st.markdown("<b>📁 신규 카테고리 생성</b>", unsafe_allow_html=True)
    cat_add_col1, cat_add_col2 = st.columns([3.5, 1])
    with cat_add_col1:
        new_cat_name = st.text_input("새 카테고리 이름", placeholder="예: 2차전지, 제약바이오", label_visibility="collapsed")
    with cat_add_col2:
        if st.button("카테고리 추가", use_container_width=True):
            clean_cat_name = new_cat_name.strip()
            if clean_cat_name:
                if clean_cat_name not in st.session_state.stock_categories:
                    st.session_state.stock_categories[clean_cat_name] = {}
                    save_stocks_data(st.session_state.stock_categories)
                    st.toast(f"카테고리 '{clean_cat_name}' 추가 완료", icon="📁")
                    st.rerun()
                else:
                    st.warning("이미 존재하는 카테고리입니다.")

    st.markdown("---")

    st.markdown("<b>📈 종목 추가 및 삭제</b>", unsafe_allow_html=True)
    add_col1, add_col2, add_col3 = st.columns([2, 1.5, 1])

    if not krx_df.empty:
        search_options = [
            f"{row['Name']} ({row['Code']})"
            for _, row in krx_df.iterrows()
            if "Name" in row and "Code" in row
        ]
        with add_col1:
            selected_search = st.selectbox("KRX 종목 검색", options=["선택..."] + search_options)

        with add_col2:
            default_cat_idx = cat_list.index(selected_category) if selected_category in cat_list else 0
            target_category = st.selectbox("추가할 카테고리", options=cat_list, index=default_cat_idx)

        with add_col3:
            st.markdown("<div style='height:18px;'></div>", unsafe_allow_html=True)
            if st.button("종목 추가", use_container_width=True):
                if selected_search != "선택...":
                    s_name = selected_search.split(" (")[0]
                    s_code = selected_search.split(" (")[1].replace(")", "")
                    
                    if target_category not in st.session_state.stock_categories:
                        st.session_state.stock_categories[target_category] = {}
                        
                    st.session_state.stock_categories[target_category][s_name] = {
                        "code": s_code,
                        "op_2026": 0.0,
                        "np_2026": 0.0,
                    }
                    save_stocks_data(st.session_state.stock_categories)
                    st.toast(f"'{target_category}'에 '{s_name}' 추가 완료", icon="✅")
                    st.rerun()

    if selected_stock:
        if st.button(f"🗑️ 현재 선택 종목({selected_stock}) 삭제", use_container_width=True):
            del st.session_state.stock_categories[selected_category][selected_stock]
            save_stocks_data(st.session_state.stock_categories)
            st.toast(f"'{selected_stock}' 삭제 완료", icon="🗑️")
            st.rerun()

if not selected_stock or selected_stock not in available_stocks:
    st.warning("선택된 종목이 없습니다. 상단 메뉴나 카테고리 관리에서 종목을 선택 또는 추가해주세요.")
    st.stop()

stock_info = available_stocks[selected_stock]
stock_code = stock_info["code"]

st.markdown("---")

# ==================== 메인 차트 및 실적 표시 ====================

st.markdown(f"### 📈 {selected_stock} ({stock_code})")

with st.spinner("DART 실적 조회 중..."):
    dart_ops, dart_nps = fetch_financials_dart(stock_code, DART_API_KEY)


def update_2026_estimates(cat, stock):
    op_key = f"input_op_{stock}_2026"
    np_key = f"input_np_{stock}_2026"
    new_op = st.session_state[op_key]
    new_np = st.session_state[np_key]
    
    st.session_state.stock_categories[cat][stock]["op_2026"] = new_op
    st.session_state.stock_categories[cat][stock]["np_2026"] = new_np
    
    if save_stocks_data(st.session_state.stock_categories):
        st.toast(f"2026년 추정치 저장 완료 (영업이익: {new_op:,.1f}억 / 순이익: {new_np:,.1f}억)", icon="💾")


final_ops = {}
final_nps = {}
past_years = ["2021", "2022", "2023", "2024", "2025"]

# 영업이익 표시
st.markdown("<b>📊 영업이익 (억원)</b>", unsafe_allow_html=True)
op_cols = st.columns(6)
for idx, yr in enumerate(past_years):
    val_op = float(dart_ops.get(yr, 0.0))
    final_ops[yr] = val_op * 100_000_000.0
    with op_cols[idx]:
        status_icon = "🔴" if val_op < 0 else "🟢"
        st.metric(label=f"{yr}년", value=f"{val_op:,.1f}억 {status_icon}")

with op_cols[5]:
    current_2026_op = float(stock_info.get("op_2026", 0.0))
    input_2026_op = st.number_input(
        "26년 추정 영업이익",
        value=current_2026_op,
        step=10.0,
        format="%.1f",
        key=f"input_op_{selected_stock}_2026",
        on_change=update_2026_estimates,
        args=(selected_category, selected_stock),
    )
    final_ops["2026"] = input_2026_op * 100_000_000.0

# 당기순이익 표시
st.markdown("<b>📊 당기순이익 (억원)</b>", unsafe_allow_html=True)
np_cols = st.columns(6)
for idx, yr in enumerate(past_years):
    val_np = float(dart_nps.get(yr, 0.0))
    final_nps[yr] = val_np * 100_000_000.0
    with np_cols[idx]:
        status_icon = "🔴" if val_np < 0 else "🟢"
        st.metric(label=f"{yr}년", value=f"{val_np:,.1f}억 {status_icon}")

with np_cols[5]:
    current_2026_np = float(stock_info.get("np_2026", 0.0))
    input_2026_np = st.number_input(
        "26년 추정 순이익",
        value=current_2026_np,
        step=10.0,
        format="%.1f",
        key=f"input_np_{selected_stock}_2026",
        on_change=update_2026_estimates,
        args=(selected_category, selected_stock),
    )
    final_nps["2026"] = input_2026_np * 100_000_000.0

# ==================== 주가 데이터 수집 및 계산 ====================
end_date = datetime.today()
start_date = datetime(end_date.year - 5, 1, 1)


@st.cache_data(ttl=3600)
def get_stock_data_api(code, start, end):
    df = fdr.DataReader(code, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
    return df.reset_index()


try:
    stock_df = get_stock_data_api(stock_code, start_date, end_date)
except Exception as e:
    st.error(f"주가 불러오기 실패: {e}")
    st.stop()

if stock_df.empty:
    st.error("불러온 주가 데이터가 없습니다.")
    st.stop()

stock_df["날짜"] = pd.to_datetime(stock_df["Date"])
stock_df["종가"] = pd.to_numeric(stock_df["Close"], errors="coerce")
stock_df["연도"] = stock_df["날짜"].dt.year.astype(str)

if "Marcap" in stock_df.columns and stock_df["Marcap"].notnull().sum() > 0:
    stock_df["시가총액"] = pd.to_numeric(stock_df["Marcap"], errors="coerce")
else:
    shares = 0
    if not krx_df.empty and "Code" in krx_df.columns:
        matched = krx_df[krx_df["Code"] == stock_code]
        if not matched.empty:
            for col_name in ["Stocks", "ListingShares", "Shares"]:
                if col_name in matched.columns and pd.notnull(matched[col_name].values[0]):
                    shares = float(matched[col_name].values[0])
                    if shares > 0:
                        break
    stock_df["시가총액"] = stock_df["종가"] * shares if shares > 0 else np.nan

# POR / PER 계산
stock_df["수정_영업이익"] = pd.to_numeric(stock_df["연도"].map(final_ops), errors="coerce")
stock_df["수정_순이익"] = pd.to_numeric(stock_df["연도"].map(final_nps), errors="coerce")

stock_df["수정_POR"] = np.where(
    (stock_df["수정_영업이익"].notnull()) & (stock_df["수정_영업이익"] > 0) & (stock_df["시가총액"].notnull()),
    stock_df["시가총액"] / stock_df["수정_영업이익"],
    np.nan,
)

stock_df["수정_PER"] = np.where(
    (stock_df["수정_순이익"].notnull()) & (stock_df["수정_순이익"] > 0) & (stock_df["시가총액"].notnull()),
    stock_df["시가총액"] / stock_df["수정_순이익"],
    np.nan,
)

# 밴드 시각화 함수
def draw_band_chart(df, col_name, title_name):
    valid_vals = df[col_name].dropna()
    m_val = valid_vals.mean() if len(valid_vals) > 0 else 0.0
    s_val = valid_vals.std() if len(valid_vals) > 0 else 0.0

    df["Mean"] = m_val
    df["+1σ"] = m_val + s_val
    df["+2σ"] = m_val + (s_val * 2)
    df["-1σ"] = m_val - s_val
    df["-2σ"] = m_val - (s_val * 2)

    latest_close = df["종가"].iloc[-1] if not df.empty else 0
    latest_marcap = df["시가총액"].dropna().iloc[-1] if not df["시가총액"].dropna().empty else 0
    latest_metric_val = valid_vals.iloc[-1] if len(valid_vals) > 0 else np.nan

    st.markdown(f"<div style='margin-top: 6px;'><b>📌 {title_name} 주요 지표 요약</b></div>", unsafe_allow_html=True)
    m_cols = st.columns(5)
    with m_cols[0]:
        st.metric("종가", f"{latest_close:,.0f}원")
    with m_cols[1]:
        st.metric("시총", f"{latest_marcap / 100_000_000:,.0f}억" if latest_marcap > 0 else "N/A")
    with m_cols[2]:
        st.metric(f"현재 {title_name}", f"{latest_metric_val:.1f}배" if pd.notnull(latest_metric_val) else "N/A")
    with m_cols[3]:
        st.metric(f"평균 {title_name}", f"{m_val:.1f}")
    with m_cols[4]:
        st.metric("+2σ 상단", f"{(m_val + s_val*2):.1f}")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["날짜"], y=df[col_name], mode="lines", name=title_name, line=dict(color="#FFFFFF", width=1.6)))
    fig.add_trace(go.Scatter(x=df["날짜"], y=df["+2σ"], mode="lines", name="+2σ", line=dict(color="#FF5555", width=1.1, dash="dash")))
    fig.add_trace(go.Scatter(x=df["날짜"], y=df["+1σ"], mode="lines", name="+1σ", line=dict(color="#FFB86C", width=1.1, dash="dot")))
    fig.add_trace(go.Scatter(x=df["날짜"], y=df["Mean"], mode="lines", name="평균", line=dict(color="#50FA7B", width=1.4, dash="solid")))
    fig.add_trace(go.Scatter(x=df["날짜"], y=df["-1σ"], mode="lines", name="-1σ", line=dict(color="#8BE9FD", width=1.1, dash="dot")))
    fig.add_trace(go.Scatter(x=df["날짜"], y=df["-2σ"], mode="lines", name="-2σ", line=dict(color="#BD93F9", width=1.1, dash="dash")))

    fig.update_layout(
        paper_bgcolor="#1E222A",
        plot_bgcolor="#14161D",
        font=dict(color="#FFFFFF", size=9),
        margin=dict(l=5, r=5, t=25, b=15),
        xaxis=dict(showgrid=True, gridcolor="#2E3440", color="#FFFFFF", tickfont=dict(size=8)),
        yaxis=dict(showgrid=True, gridcolor="#2E3440", color="#FFFFFF", tickfont=dict(size=8)),
        hovermode="x unified",
        height=380,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="center", x=0.5, font=dict(color="#FFFFFF", size=9)),
    )
    st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": False, "displayModeBar": False})


# 차트 탭 구분
chart_tab1, chart_tab2 = st.tabs(["📉 POR 밴드 (영업이익 기반)", "📉 PER 밴드 (당기순이익 기반)"])
with chart_tab1:
    draw_band_chart(stock_df.copy(), "수정_POR", "POR")

with chart_tab2:
    draw_band_chart(stock_df.copy(), "수정_PER", "PER")

# ==================== 🎯 병렬 스크리닝 함수 및 화면 출력 ====================
def analyze_single_stock_screening(task):
    cat_name, s_name, s_info, start, end, dart_key = task
    code = s_info.get("code")
    if not code:
        return None, None, None, None

    try:
        df = get_stock_data_api(code, start, end)
        d_ops, d_nps = fetch_financials_dart(code, dart_key)

        if df.empty:
            return None, None, None, None

        df["날짜"] = pd.to_datetime(df["Date"])
        df["종가"] = pd.to_numeric(df["Close"], errors="coerce")
        df["연도"] = df["날짜"].dt.year.astype(str)

        if "Marcap" in df.columns and df["Marcap"].notnull().sum() > 0:
            df["시가총액"] = pd.to_numeric(df["Marcap"], errors="coerce")
        else:
            df["시가총액"] = df["종가"] * 0

        # 영업이익/순이익 맵핑
        ops_dict = {yr: float(d_ops.get(yr, 0.0)) * 100_000_000.0 for yr in ["2021", "2022", "2023", "2024", "2025"]}
        ops_dict["2026"] = float(s_info.get("op_2026", 0.0)) * 100_000_000.0

        nps_dict = {yr: float(d_nps.get(yr, 0.0)) * 100_000_000.0 for yr in ["2021", "2022", "2023", "2024", "2025"]}
        nps_dict["2026"] = float(s_info.get("np_2026", 0.0)) * 100_000_000.0

        df["수정_영업이익"] = df["연도"].map(ops_dict)
        df["수정_순이익"] = df["연도"].map(nps_dict)

        df["수정_POR"] = np.where((df["수정_영업이익"] > 0) & (df["시가총액"] > 0), df["시가총액"] / df["수정_영업이익"], np.nan)
        df["수정_PER"] = np.where((df["수정_순이익"] > 0) & (df["시가총액"] > 0), df["시가총액"] / df["수정_순이익"], np.nan)

        cur_close = df["종가"].iloc[-1]

        # POR 스크리닝
        por_1s_res, por_2s_res = None, None
        v_por = df["수정_POR"].dropna()
        if len(v_por) >= 10:
            m_por = v_por.mean()
            s_por = v_por.std()
            t_por_1s = m_por - s_por
            t_por_2s = m_por - (s_por * 2)
            cur_por = v_por.iloc[-1]

            if cur_por <= t_por_1s:
                diff = ((cur_por - t_por_1s) / t_por_1s) * 100
                por_1s_res = {"카테고리": cat_name, "종목명": s_name, "코드": code, "종가": f"{cur_close:,.0f}원", "현재 POR": f"{cur_por:.2f}", "평균 POR": f"{m_por:.2f}", "-1σ": f"{t_por_1s:.2f}", "괴리율": f"{diff:.1f}%"}
            if cur_por <= t_por_2s:
                diff = ((cur_por - t_por_2s) / t_por_2s) * 100
                por_2s_res = {"카테고리": cat_name, "종목명": s_name, "코드": code, "종가": f"{cur_close:,.0f}원", "현재 POR": f"{cur_por:.2f}", "평균 POR": f"{m_por:.2f}", "-2σ": f"{t_por_2s:.2f}", "괴리율": f"{diff:.1f}%"}

        # PER 스크리닝
        per_1s_res, per_2s_res = None, None
        v_per = df["수정_PER"].dropna()
        if len(v_per) >= 10:
            m_per = v_per.mean()
            s_per = v_per.std()
            t_per_1s = m_per - s_per
            t_per_2s = m_per - (s_per * 2)
            cur_per = v_per.iloc[-1]

            if cur_per <= t_per_1s:
                diff = ((cur_per - t_per_1s) / t_per_1s) * 100
                per_1s_res = {"카테고리": cat_name, "종목명": s_name, "코드": code, "종가": f"{cur_close:,.0f}원", "현재 PER": f"{cur_per:.2f}", "평균 PER": f"{m_per:.2f}", "-1σ": f"{t_per_1s:.2f}", "괴리율": f"{diff:.1f}%"}
            if cur_per <= t_per_2s:
                diff = ((cur_per - t_per_2s) / t_per_2s) * 100
                per_2s_res = {"카테고리": cat_name, "종목명": s_name, "코드": code, "종가": f"{cur_close:,.0f}원", "현재 PER": f"{cur_per:.2f}", "평균 PER": f"{m_per:.2f}", "-2σ": f"{t_per_2s:.2f}", "괴리율": f"{diff:.1f}%"}

        return por_1s_res, por_2s_res, per_1s_res, per_2s_res

    except Exception:
        return None, None, None, None


st.markdown("---")
st.markdown("### 🎯 등록 종목 시그마(-1σ / -2σ) 이탈 스크리닝")

if st.button("🔍 전체 종목 POR/PER 스크리닝 실행", use_container_width=True):
    por_1s_list, por_2s_list = [], []
    per_1s_list, per_2s_list = [], []

    tasks = []
    for cat_name, stocks in st.session_state.stock_categories.items():
        for s_name, s_info in stocks.items():
            tasks.append((cat_name, s_name, s_info, start_date, end_date, DART_API_KEY))

    with st.spinner(f"등록된 {len(tasks)}개 종목을 병렬 분석 중입니다..."):
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_stock = {
                executor.submit(analyze_single_stock_screening, t): t for t in tasks
            }
            for future in as_completed(future_to_stock):
                p1, p2, pe1, pe2 = future.result()
                if p1: por_1s_list.append(p1)
                if p2: por_2s_list.append(p2)
                if pe1: per_1s_list.append(pe1)
                if pe2: per_2s_list.append(pe2)

    s_tab1, s_tab2, s_tab3, s_tab4 = st.tabs([
        "📌 POR -1σ 이하", 
        "🚨 POR -2σ 이하", 
        "📌 PER -1σ 이하", 
        "🚨 PER -2σ 이하"
    ])

    with s_tab1:
        if por_1s_list:
            st.success(f"총 {len(por_1s_list)}개 종목이 POR -1σ 이하에 위치해 있습니다.")
            st.dataframe(pd.DataFrame(por_1s_list), use_container_width=True)
        else:
            st.info("해당하는 종목이 없습니다.")

    with s_tab2:
        if por_2s_list:
            st.warning(f"총 {len(por_2s_list)}개 종목이 POR -2σ 이하에 위치해 있습니다.")
            st.dataframe(pd.DataFrame(por_2s_list), use_container_width=True)
        else:
            st.info("해당하는 종목이 없습니다.")

    with s_tab3:
        if per_1s_list:
            st.success(f"총 {len(per_1s_list)}개 종목이 PER -1σ 이하에 위치해 있습니다.")
            st.dataframe(pd.DataFrame(per_1s_list), use_container_width=True)
        else:
            st.info("해당하는 종목이 없습니다.")

    with s_tab4:
        if per_2s_list:
            st.warning(f"총 {len(per_2s_list)}개 종목이 PER -2σ 이하에 위치해 있습니다.")
            st.dataframe(pd.DataFrame(per_2s_list), use_container_width=True)
        else:
            st.info("해당하는 종목이 없습니다.")
