import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import FinanceDataReader as fdr
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# 페이지 기본 설정
st.set_page_config(page_title="POR 밴드 시뮬레이터", layout="wide")

# ==========================================
# 🔑 JSONBin & DART API 설정
JSONBIN_BIN_ID = "6ab0e792ac6210605ae50647".strip()
JSONBIN_API_KEY = "mysecretkey1234".strip()
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"

DART_API_KEY = "28b4dc2f6fac759fc70daa06cb0e9761eda3c105".strip()
# ==========================================

# 기본 종목 목록 (JSONBin 최초 접속 실패 시 fallback)
DEFAULT_STOCKS = {
    '반도체': {
        'SK하이닉스': {'code': '000660', 'op_2026': 0.0},
        '티엘비': {'code': '356860', 'op_2026': 0.0},
        '엠케이전자': {'code': '033160', 'op_2026': 0.0},
        'ISC': {'code': '095340', 'op_2026': 0.0},
        '엘티씨': {'code': '170920', 'op_2026': 0.0},
        '하나마이크론': {'code': '067310', 'op_2026': 0.0},
        '하나머티리얼즈': {'code': '166090', 'op_2026': 0.0},
        '코미코': {'code': '183300', 'op_2026': 0.0},
        '에프에스티': {'code': '036810', 'op_2026': 0.0},
    },
    '관심종목': {},
}


# --- 🔄 JSONBin 전용 로드 / 저장 함수 ---
def load_stocks_data():
    if not JSONBIN_BIN_ID or "여기에" in JSONBIN_BIN_ID:
        st.warning("⚠️ JSONBin BIN_ID가 설정되지 않았습니다. 기본 설정을 표시합니다.")
        return copy.deepcopy(DEFAULT_STOCKS)

    headers = {
        "X-Master-Key": JSONBIN_API_KEY,
        "X-Bin-Meta": "false",  # metadata 제외하고 raw record만 가져옴
    }

    try:
        res = requests.get(
            f"{JSONBIN_URL}/latest?cache=false", headers=headers, timeout=5
        )
        if res.status_code == 200:
            saved_data = res.json()

            if isinstance(saved_data, dict) and "record" in saved_data:
                saved_data = saved_data["record"]

            if isinstance(saved_data, dict) and saved_data:
                return saved_data
        else:
            st.error(f"❌ JSONBin 로드 실패 (응답 코드: {res.status_code}) - 기본값을 표시합니다.")
    except Exception as e:
        st.error(f"⚠️ JSONBin 통신 오류 (기본값을 표시합니다): {e}")

    return copy.deepcopy(DEFAULT_STOCKS)


def save_stocks_data(data):
    if not data:
        data = copy.deepcopy(DEFAULT_STOCKS)

    if not JSONBIN_BIN_ID or "여기에" in JSONBIN_BIN_ID:
        st.error("❌ JSONBin BIN_ID가 설정되지 않아 저장할 수 없습니다.")
        return False

    headers = {
        "Content-Type": "application/json",
        "X-Master-Key": JSONBIN_API_KEY,
    }

    try:
        res = requests.put(JSONBIN_URL, json=data, headers=headers, timeout=5)
        if res.status_code == 200:
            return True
        else:
            st.error(f"❌ JSONBin 저장 실패 (응답 코드: {res.status_code})")
            return False
    except Exception as e:
        st.error(f"❌ JSONBin 저장 중 예외 발생: {e}")
        return False


# 앱 시작 시 세션 상태에 JSONBin 최신 데이터 로드
if 'stock_categories' not in st.session_state:
    st.session_state.stock_categories = load_stocks_data()


# KRX 상장 종목 데이터 (캐싱)
@st.cache_data(ttl=86400)
def get_krx_stock_list():
    try:
        return fdr.StockListing('KRX')
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


# 단일 연도 DART API 호출 (개선된 버전)
def _fetch_single_year_dart(args):
    b_year, clean_key, corp_code = args
    reprt_codes = ['11011', '11014', '11012', '11013']  # 사업 -> 3분기 -> 반기 -> 1분기 순

    for reprt_code in reprt_codes:
        for fs_div in ['CFS', 'OFS']:  # 연결 -> 별도 순
            try:
                url = f"https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?crtfc_key={clean_key}&corp_code={corp_code}&bsns_year={b_year}&reprt_code={reprt_code}&fs_div={fs_div}"
                res = requests.get(url, timeout=4)
                data = res.json()

                if data.get('status') == '000' and 'list' in data:
                    for item in data['list']:
                        acc_id = str(item.get('account_id', ''))
                        acc_nm = str(item.get('account_nm', '')).replace(' ', '').strip()

                        # 영업이익 계정 매칭 조건 완화
                        is_op = (
                            'OperatingProfit' in acc_id
                            or acc_nm in ['영업이익', '영업이익(손실)', '영업손실(이익)', '영업손실']
                            or ('영업이익' in acc_nm and '률' not in acc_nm and '이익률' not in acc_nm)
                        )

                        if is_op:
                            raw_val = (
                                str(item.get('thstrm_amount', '0'))
                                .replace(',', '')
                                .replace(' ', '')
                                .strip()
                            )

                            if raw_val and raw_val != '-':
                                # (100) 형태의 마이너스 표기 변환
                                if raw_val.startswith('(') and raw_val.endswith(')'):
                                    raw_val = '-' + raw_val[1:-1]

                                val_float = float(raw_val) / 100_000_000.0  # 억원 단위
                                return b_year, round(val_float, 1)
            except Exception:
                pass
    return b_year, 0.0


# --- DART 과거 5년치 데이터 병렬 수집 (캐싱 적용) ---
@st.cache_data(ttl=3600)
def fetch_operating_profit_dart(code, api_key):
    ops = {'2021': 0.0, '2022': 0.0, '2023': 0.0, '2024': 0.0, '2025': 0.0}
    clean_key = str(api_key).strip()

    if not clean_key or clean_key == "YOUR_DART_API_KEY_HERE":
        return ops

    corp_map = get_dart_corp_code_map(clean_key)
    corp_code = corp_map.get(str(code).zfill(6))
    if not corp_code:
        return ops

    years = ['2021', '2022', '2023', '2024', '2025']
    tasks = [(yr, clean_key, corp_code) for yr in years]

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(_fetch_single_year_dart, tasks)
        for yr, val in results:
            ops[yr] = val

    return ops


# ==================== 사이드바 ====================
st.sidebar.title("⚙️ 종목 및 카테고리 관리")

# --- 📁 카테고리 추가 ---
with st.sidebar.expander("📁 카테고리 추가"):
    new_cat_name = st.text_input("새 카테고리 이름", key="new_cat_input").strip()
    if st.button("카테고리 생성"):
        if new_cat_name and new_cat_name not in st.session_state.stock_categories:
            st.session_state.stock_categories[new_cat_name] = {}
            save_stocks_data(st.session_state.stock_categories)
            st.toast(f"'{new_cat_name}' 카테고리가 생성되었습니다.", icon="✅")
            st.rerun()

# --- ➕ 신규 종목 추가 ---
with st.sidebar.expander("➕ 신규 종목 추가"):
    if not krx_df.empty:
        search_options = [
            f"{row['Name']} ({row['Code']})"
            for _, row in krx_df.iterrows()
            if 'Name' in row and 'Code' in row
        ]
        selected_search = st.selectbox(
            "KRX 종목 검색:", options=["선택하세요..."] + search_options
        )
        target_cat = st.selectbox(
            "추가할 카테고리 선택:",
            list(st.session_state.stock_categories.keys()),
        )

        if st.button("종목 추가"):
            if selected_search != "선택하세요...":
                name = selected_search.split(" (")[0]
                code = selected_search.split(" (")[1].replace(")", "")

                if target_cat in st.session_state.stock_categories:
                    st.session_state.stock_categories[target_cat][name] = {
                        'code': code,
                        'op_2026': 0.0,
                    }
                    save_stocks_data(st.session_state.stock_categories)
                    st.toast(f"'{name}' 종목이 추가되었습니다.", icon="✅")
                    st.rerun()

st.sidebar.markdown("---")
st.sidebar.title("🔍 분석 대상 선택")
category_list = [
    cat for cat, stocks in st.session_state.stock_categories.items() if stocks
]

if not category_list:
    st.warning("등록된 종목이 없습니다. 사이드바에서 카테고리나 종목을 추가해주세요.")
    st.stop()

selected_category = st.sidebar.selectbox("카테고리 선택:", category_list)
available_stocks = st.session_state.stock_categories[selected_category]
selected_stock = st.sidebar.selectbox("종목 선택:", list(available_stocks.keys()))

stock_info = available_stocks[selected_stock]
stock_code = stock_info['code']

if st.sidebar.button(f"❌ {selected_stock} 삭제"):
    del st.session_state.stock_categories[selected_category][selected_stock]
    save_stocks_data(st.session_state.stock_categories)
    st.toast(f"{selected_stock} 삭제 완료", icon="🗑️")
    st.rerun()

# ==================== 메인 화면 ====================

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 2rem !important;
    }
    h1 {
        font-size: 1.35rem !important;
        font-weight: 700 !important;
        padding-bottom: 0.5rem !important;
        margin-bottom: 0.5rem !important;
    }
    h3 {
        font-size: 1.05rem !important;
        font-weight: 600 !important;
        margin-top: 0.5rem !important;
        margin-bottom: 0.5rem !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.75rem !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.05rem !important;
    }
    div[data-testid="stMetric"] {
        padding: 2px 4px !important;
    }
    div[data-testid="stNumberInput"] label p {
        font-size: 0.75rem !important;
    }
    div[data-testid="stNumberInput"] input {
        font-size: 0.85rem !important;
        padding: 4px 8px !important;
    }
    </style>
""",
    unsafe_allow_html=True,
)

st.title(
    f"📈 [{selected_category}] {selected_stock} ({stock_code}) POR 밴드 시뮬레이션"
)

# DART API를 통한 과거 실적 조회
with st.spinner("DART에서 과거 영업이익 데이터를 불러오는 중..."):
    dart_ops = fetch_operating_profit_dart(stock_code, DART_API_KEY)

st.subheader("📊 연도별 영업이익 현황 및 추정치 (단위: 억원)")


# 2026년 추정치 수정 시 즉시 JSONBin으로 저장
def update_2026_op(cat, stock):
    widget_key = f"input_{stock}_2026"
    new_val = st.session_state[widget_key]
    st.session_state.stock_categories[cat][stock]['op_2026'] = new_val
    if save_stocks_data(st.session_state.stock_categories):
        st.toast(f"2026년 추정치 ({new_val:,.1f} 억원) JSONBin 저장 완료", icon="💾")


final_ops = {}
p_cols = st.columns(6)
has_negative_op = False
past_years = ['2021', '2022', '2023', '2024', '2025']

# DART 실적 표시
for idx, yr in enumerate(past_years):
    val_dart = float(dart_ops.get(yr, 0.0))
    final_ops[yr] = val_dart * 100_000_000.0

    with p_cols[idx]:
        st.metric(label=f"{yr}년 실적 (DART)", value=f"{val_dart:,.1f} 억")
        if val_dart < 0:
            st.markdown(
                "<p style='color: #FF4B4B; font-size: 0.72rem; font-weight: bold; margin-top: -14px;'>🔴 적자</p>",
                unsafe_allow_html=True,
            )
            has_negative_op = True
        else:
            st.markdown(
                "<p style='color: #00C853; font-size: 0.72rem; margin-top: -14px;'>🟢 흑자</p>",
                unsafe_allow_html=True,
            )

# 2026년 추정치 입력란
with p_cols[5]:
    current_2026_val = float(stock_info.get('op_2026', 0.0))
    input_2026 = st.number_input(
        "2026년 추정(억원)",
        value=current_2026_val,
        step=10.0,
        format="%.1f",
        key=f"input_{selected_stock}_2026",
        on_change=update_2026_op,
        args=(selected_category, selected_stock),
    )
    final_ops['2026'] = input_2026 * 100_000_000.0

    if input_2026 < 0:
        st.markdown(
            "<p style='color: #FF4B4B; font-size: 0.72rem; font-weight: bold; margin-top: -6px;'>🔴 적자 추정</p>",
            unsafe_allow_html=True,
        )
        has_negative_op = True
    else:
        st.markdown(
            "<p style='color: #00C853; font-size: 0.72rem; margin-top: -6px;'>🟢 흑자 추정</p>",
            unsafe_allow_html=True,
        )

if has_negative_op:
    st.warning(
        "⚠️ 영업이익이 적자(마이너스)인 구간은 POR 산출 공식상 'N/A' 처리되어 차트선이 연결되지 않을 수 있습니다."
    )

# ==================== 주가 데이터 수집 ====================
end_date = datetime.today()
start_date = datetime(end_date.year - 5, 1, 1)


@st.cache_data(ttl=3600)
def get_stock_data_api(code, start, end):
    df = fdr.DataReader(
        code, start=start.strftime('%Y-%m-%d'), end=end.strftime('%Y-%m-%d')
    )
    return df.reset_index()


try:
    stock_df = get_stock_data_api(stock_code, start_date, end_date)
except Exception as e:
    st.error(f"주가 데이터 불러오기 실패: {e}")
    st.stop()

if stock_df.empty:
    st.error("불러온 주가 데이터가 없습니다.")
    st.stop()

stock_df['날짜'] = pd.to_datetime(stock_df['Date'])
stock_df['종가'] = pd.to_numeric(stock_df['Close'], errors='coerce')
stock_df['연도'] = stock_df['날짜'].dt.year.astype(str)

if 'Marcap' in stock_df.columns and stock_df['Marcap'].notnull().sum() > 0:
    stock_df['시가총액'] = pd.to_numeric(stock_df['Marcap'], errors='coerce')
else:
    shares = 0
    if not krx_df.empty and 'Code' in krx_df.columns:
        matched = krx_df[krx_df['Code'] == stock_code]
        if not matched.empty:
            for col_name in ['Stocks', 'ListingShares', 'Shares']:
                if col_name in matched.columns and pd.notnull(matched[col_name].values[0]):
                    shares = float(matched[col_name].values[0])
                    if shares > 0:
                        break
    stock_df['시가총액'] = (
        stock_df['종가'] * shares if shares > 0 else np.nan
    )

stock_df['수정_영업이익'] = stock_df['연도'].map(final_ops)
stock_df['수정_영업이익'] = pd.to_numeric(
    stock_df['수정_영업이익'], errors='coerce'
)

# POR 계산
stock_df['수정_POR'] = np.where(
    (stock_df['수정_영업이익'].notnull())
    & (stock_df['수정_영업이익'] > 0)
    & (stock_df['시가총액'].notnull()),
    stock_df['시가총액'] / stock_df['수정_영업이익'],
    np.nan,
)

valid_por = stock_df['수정_POR'].dropna()
mean_val = valid_por.mean() if len(valid_por) > 0 else 0.0
std_val = valid_por.std() if len(valid_por) > 0 else 0.0

stock_df['Mean'] = mean_val
stock_df['+1σ'] = mean_val + std_val
stock_df['+2σ'] = mean_val + (std_val * 2)
stock_df['-1σ'] = mean_val - std_val
stock_df['-2σ'] = mean_val - (std_val * 2)

c1, c2, c3, c4, c5 = st.columns(5)
latest_close = stock_df['종가'].iloc[-1] if not stock_df.empty else 0
latest_marcap_val = (
    stock_df['시가총액'].dropna().iloc[-1]
    if not stock_df['시가총액'].dropna().empty
    else 0
)

c1.metric("최신 종가", f"{latest_close:,.0f} 원")
c2.metric(
    "현재 시가총액",
    f"{latest_marcap_val / 100_000_000:,.1f} 억원"
    if latest_marcap_val > 0
    else "N/A",
)
c3.metric(f"평균 POR ({start_date.year}~현재)", f"{mean_val:.2f}")
c4.metric("표준편차 (STDEV)", f"{std_val:.2f}")
c5.metric("+2σ 밴드 상단", f"{(mean_val + std_val*2):.2f}")

fig = go.Figure()
fig.add_trace(
    go.Scatter(
        x=stock_df['날짜'],
        y=stock_df['수정_POR'],
        mode='lines',
        name='POR (실시간)',
        line=dict(color='#FFFFFF', width=2),
    )
)
fig.add_trace(
    go.Scatter(
        x=stock_df['날짜'],
        y=stock_df['+2σ'],
        mode='lines',
        name='+2σ (상단)',
        line=dict(color='#FF5555', width=1.5, dash='dash'),
    )
)
fig.add_trace(
    go.Scatter(
        x=stock_df['날짜'],
        y=stock_df['+1σ'],
        mode='lines',
        name='+1σ',
        line=dict(color='#FFB86C', width=1.5, dash='dot'),
    )
)
fig.add_trace(
    go.Scatter(
        x=stock_df['날짜'],
        y=stock_df['Mean'],
        mode='lines',
        name='Mean (평균)',
        line=dict(color='#50FA7B', width=2, dash='solid'),
    )
)
fig.add_trace(
    go.Scatter(
        x=stock_df['날짜'],
        y=stock_df['-1σ'],
        mode='lines',
        name='-1σ',
        line=dict(color='#8BE9FD', width=1.5, dash='dot'),
    )
)
fig.add_trace(
    go.Scatter(
        x=stock_df['날짜'],
        y=stock_df['-2σ'],
        mode='lines',
        name='-2σ (하단)',
        line=dict(color='#BD93F9', width=1.5, dash='dash'),
    )
)

fig.update_layout(
    title=dict(
        text=f"<b>{selected_stock} {start_date.year}년 1월 ~ 현재 POR 밴드 차트</b>",
        font=dict(color='#FFFFFF', size=13),
    ),
    paper_bgcolor='#1E1E1E',
    plot_bgcolor='#141414',
    font=dict(color='#FFFFFF'),
    xaxis=dict(
        title="날짜", showgrid=True, gridcolor='#333333', color='#FFFFFF'
    ),
    yaxis=dict(
        title="POR", showgrid=True, gridcolor='#333333', color='#FFFFFF'
    ),
    hovermode="x unified",
    height=550,
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1,
        font=dict(color='#FFFFFF', size=11),
    ),
)

st.plotly_chart(fig, use_container_width=True)
