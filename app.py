# Crypto dashboard — Streamlit version of the Session 3 Part B React app.
#
# Same idea as the React dashboard: six Binance USDT pairs, one large panel
# and five small ones, each with a time-window selector, plus the option to
# pause the rotation of the coin in the large panel. Instead of a separate
# Python WebSocket server, Streamlit fetches the public Binance price
# history directly and refreshes the charts on a timer, so the whole app
# can be hosted on Streamlit Community Cloud.

import time

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

try:  # use the operating system's certificates (fixes TLS-intercepting networks)
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

st.set_page_config(page_title="Crypto dashboard", layout="wide")

# ---- configuration (mirrors frontend/src/config/config.json) ------------
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"]
COLORS = {
    "BTCUSDT": "#F7931A", "ETHUSDT": "#627EEA", "BNBUSDT": "#F3BA2F",
    "XRPUSDT": "#23292F", "ADAUSDT": "#0033AD", "DOGEUSDT": "#C3A634",
}
MINUTE = 60
# interval -> (label, window in seconds, seconds per record, cache seconds)
WINDOWS = {
    "1s":  ("Thirty Minutes", 30 * MINUTE,          1,          5),
    "1m":  ("Three Hours",    3 * 60 * MINUTE,      60,         20),
    "5m":  ("Three Days",     3 * 1440 * MINUTE,    300,        60),
    "30m": ("Three Weeks",    21 * 1440 * MINUTE,   1800,       300),
    "4h":  ("Three Months",   93 * 1440 * MINUTE,   14400,      900),
    "1d":  ("Three Years",    1095 * 1440 * MINUTE, 86400,      3600),
}
ORDER = list(WINDOWS)
ROTATE_SECONDS = 35          # the React app rotates every 30-40 s
REFRESH_SECONDS = 5          # how often the charts pull new prices

# Public market-data endpoints, tried in order. Binance blocks api.binance.com
# from some regions (including US cloud servers); data-api.binance.vision is
# its public market-data mirror, and binance.us is a last resort.
HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api.binance.us",
]


@st.cache_resource
def session():
    s = requests.Session()
    s.headers["User-Agent"] = "36104-crypto-dashboard"
    return s


def _klines(host, symbol, interval, start_ms, end_ms):
    # Binance returns at most 1000 records per request, so page through.
    rows, cursor = [], start_ms
    while cursor < end_ms:
        r = session().get(f"{host}/api/v3/klines", timeout=10, params={
            "symbol": symbol, "interval": interval,
            "startTime": cursor, "endTime": end_ms, "limit": 1000})
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows += batch
        cursor = batch[-1][6] + 1          # after the last close time
        if len(batch) < 1000:
            break
    return rows


def fetch_history(symbol, interval):
    # Cached per window so fast windows refresh often and slow ones rarely.
    return _cached_history(symbol, interval,
                           int(time.time() // WINDOWS[interval][3]))


@st.cache_data(show_spinner=False, max_entries=200)
def _cached_history(symbol, interval, _bucket):
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - WINDOWS[interval][1] * 1000
    errors = []
    for host in HOSTS:
        try:
            rows = _klines(host, symbol, interval, start_ms, end_ms)
            if rows:
                df = pd.DataFrame({
                    # each record's closing time, as in backend/server.py
                    "time": pd.to_datetime([r[6] for r in rows], unit="ms",
                                           utc=True),
                    "open": [float(r[1]) for r in rows],
                    "high": [float(r[2]) for r in rows],
                    "low": [float(r[3]) for r in rows],
                    "price": [float(r[4]) for r in rows],
                    "volume": [float(r[5]) for r in rows],
                })
                return df.drop_duplicates("time").sort_values("time"), host
        except Exception as exc:          # try the next host
            errors.append(f"{host}: {type(exc).__name__}")
    raise RuntimeError("; ".join(errors))


def price_fmt(p):
    return f"{p:,.2f}" if p >= 100 else f"{p:,.4f}" if p >= 1 else f"{p:.5f}"


def chart(df, symbol, large):
    colour = COLORS[symbol]
    fig = go.Figure()
    for col in ("high", "low"):            # faint high and low lines first
        fig.add_trace(go.Scatter(
            x=df["time"], y=df[col], mode="lines", name=col,
            line=dict(color=colour, width=1 if large else 0.75),
            opacity=0.3, hovertemplate=f"{col} %{{y:,.6g}}<extra></extra>"))
    fig.add_trace(go.Scatter(                # the stronger close line on top
        x=df["time"], y=df["price"], mode="lines", name="close",
        line=dict(color=colour, width=2),
        hovertemplate="close %{y:,.6g}<extra></extra>"))
    last = df.iloc[-1]
    fig.add_trace(go.Scatter(                # dot on the latest point
        x=[last["time"]], y=[last["price"]], mode="markers",
        marker=dict(color=colour, size=8 if large else 6), hoverinfo="skip"))
    fig.update_layout(
        height=540 if large else 210, showlegend=False,
        margin=dict(l=0, r=8, t=6, b=0), hovermode="x unified",
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(size=12 if large else 10, color="#59616f"),
        xaxis=dict(showgrid=False, linecolor="#cbd1da", nticks=7 if large else 4),
        yaxis=dict(gridcolor="#e2e6ec", griddash="dot", tickformat=",.6~g",
                   zeroline=False),
    )
    return fig


def panel(symbol, large):
    head, pick = st.columns([3, 2] if large else [1, 1])
    interval = pick.selectbox(
        f"{symbol} time window", ORDER, index=0, key=f"win_{symbol}",
        format_func=lambda k: WINDOWS[k][0], label_visibility="collapsed")
    try:
        df, _host = fetch_history(symbol, interval)
    except Exception as exc:
        head.markdown(f"**{symbol}**")
        st.error(f"Could not load {symbol} from Binance ({exc}).")
        return
    first, last = df["price"].iloc[0], df["price"].iloc[-1]
    change = (last / first - 1) * 100
    arrow = "▲" if change >= 0 else "▼"
    tone = "#1e7d32" if change >= 0 else "#c62828"
    head.markdown(
        f"<div style='line-height:1.25'><b style='font-size:"
        f"{'1.25rem' if large else '1rem'}'>{symbol}</b><br>"
        f"<span style='color:{COLORS[symbol]};font-family:monospace;"
        f"font-weight:700'>{price_fmt(last)} USDT</span> "
        f"<span style='color:{tone};font-size:0.8rem'>{arrow} "
        f"{change:+.2f}% over {WINDOWS[interval][0].lower()}</span></div>",
        unsafe_allow_html=True)
    st.plotly_chart(chart(df, symbol, large), key=f"chart_{symbol}",
                    config={"displayModeBar": False})


# ---- page ----------------------------------------------------------------
st.markdown("""<style>
.block-container {padding-top: 3rem; max-width: 1300px;}
div[data-testid="stVerticalBlockBorderWrapper"] {background: #ffffff;}
</style>""", unsafe_allow_html=True)

st.markdown("### Live crypto prices (Binance, USDT pairs)")
c1, c2 = st.columns([1, 3])
rotate = c1.toggle("Rotate the large panel", value=True,
                   help="Off = hold one coin in the large panel.")
if rotate:
    c2.caption(f"The large panel changes coin every {ROTATE_SECONDS} seconds. "
               "Turn rotation off to hold a coin.")
    held = None
else:
    held = c2.selectbox("Coin in the large panel", SYMBOLS,
                        key="held_coin")


@st.fragment(run_every=REFRESH_SECONDS)
def dashboard():
    if held:
        big = held
    else:
        big = SYMBOLS[int(time.time() // ROTATE_SECONDS) % len(SYMBOLS)]
    others = [s for s in SYMBOLS if s != big]

    top_left, top_right = st.columns([2, 1], gap="medium")
    with top_left, st.container(border=True):
        panel(big, large=True)
    with top_right:
        for s in others[:2]:
            with st.container(border=True):
                panel(s, large=False)
    for col, s in zip(st.columns(3, gap="medium"), others[2:]):
        with col, st.container(border=True):
            panel(s, large=False)

    st.caption(
        f"Updated {time.strftime('%H:%M:%S', time.gmtime())} UTC · "
        f"refreshes every {REFRESH_SECONDS} s · each panel scales its own "
        "price axis, so compare the axes and time windows, not just the "
        "slopes · % change is from the first price in the chosen window.")


dashboard()
