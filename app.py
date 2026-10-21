"""Real Exchange Rate App

Pulls monthly CPI and exchange-rate data from the IMF SDMX 3.0 API and computes
the real exchange rate between two countries.

Definitions
    S(t)   = units of Country 2 currency per 1 unit of Country 1 currency
           = FX2(t) / FX1(t), where FXi is IMF local currency per USD
    RER(t) = S(t) * P1(t) / P2(t)
    RER*   = mean of RER(t) over valid months in the selected sample
    Real Index(t) = 100 * RER(t) / RER*
"""

import io
import re
import time

import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from plotly.subplots import make_subplots

BASE_URL = "https://api.imf.org/external/sdmx/3.0"
START_PERIOD = "1976-01"
EURO_AREA = ("G163", "Euro area")

# Dataflows that hold each series in IMF's SDMX 3.0 catalogue.
CPI_FLOWS = ["IMF.STA/CPI"]
FX_FLOWS = ["IMF.STA/ER"]

# Euro area members are reported in euros in the IMF ER dataset, so users
# can pick either the member or G163. The U.S. is handled as FX = 1.
USA = "USA"

st.set_page_config(page_title="Real Exchange Rate App", layout="wide")


# ---------------------------------------------------------------------------
# Country list
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def country_options() -> dict:
    """Return {display label: ISO3 code}, with the Euro area first."""
    opts = {f"{EURO_AREA[1]} ({EURO_AREA[0]})": EURO_AREA[0]}
    rows = sorted(
        (c.name, c.alpha_3) for c in pycountry.countries
    )
    for name, code in rows:
        opts[f"{name} ({code})"] = code
    return opts


# ---------------------------------------------------------------------------
# IMF data retrieval
# ---------------------------------------------------------------------------
def _parse_period(p: str):
    """Convert IMF periods like '1976-M01', '1976-01' or '1976M01' to a month Timestamp."""
    m = re.match(r"^(\d{4})-?M?(\d{1,2})", str(p).strip())
    if not m:
        return pd.NaT
    return pd.Timestamp(year=int(m.group(1)), month=int(m.group(2)), day=1)


def _from_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    cols = {c.upper(): c for c in df.columns}
    if "TIME_PERIOD" not in cols or "OBS_VALUE" not in cols:
        raise ValueError("CSV response missing TIME_PERIOD or OBS_VALUE")
    s = pd.Series(
        pd.to_numeric(df[cols["OBS_VALUE"]], errors="coerce").values,
        index=df[cols["TIME_PERIOD"]].map(_parse_period),
    )
    return s


def _from_json(payload: dict) -> pd.Series:
    """Parse SDMX JSON 2.0 (dimensionAtObservation=TIME_PERIOD)."""
    data = payload.get("data", payload)
    structures = data.get("structures") or [data.get("structure", {})]
    struct = structures[0]
    obs_dims = struct["dimensions"]["observation"]
    time_dim = next(d for d in obs_dims if d["id"] == "TIME_PERIOD")
    periods = [v.get("id") or v.get("value") for v in time_dim["values"]]

    out = {}
    for ds in data.get("dataSets", []):
        for series in ds.get("series", {}).values():
            for idx, vals in series.get("observations", {}).items():
                key = int(str(idx).split(":")[0])
                val = vals[0] if isinstance(vals, list) else vals
                out[_parse_period(periods[key])] = val
    return pd.to_numeric(pd.Series(out), errors="coerce")


def _fetch_flow(flow: str, key: str) -> pd.Series:
    url = f"{BASE_URL}/data/dataflow/{flow}/+/{key}"
    params = {"c[TIME_PERIOD]": f"ge:{START_PERIOD}"}
    last_err = None

    attempts = [
        ("application/vnd.sdmx.data+csv;version=2.0.0", "csv"),
        ("application/vnd.sdmx.data+json;version=2.0.0", "json"),
    ]
    for accept, kind in attempts:
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, headers={"Accept": accept}, timeout=45)
                if r.status_code == 404:
                    raise LookupError(f"No data for {key} in {flow}")
                r.raise_for_status()
                s = _from_csv(r.text) if kind == "csv" else _from_json(r.json())
                s = s[s.index.notna()].dropna().sort_index()
                s = s[~s.index.duplicated(keep="last")]
                if s.empty:
                    raise LookupError(f"Empty series for {key} in {flow}")
                return s[s.index >= pd.Timestamp(START_PERIOD)]
            except LookupError as e:
                last_err = e
                break
            except Exception as e:  # network, parse, rate limit
                last_err = e
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(str(last_err))


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def fetch_series(kind: str, iso3: str) -> pd.Series:
    """kind is 'CPI' or 'FX'. Returns a monthly series indexed by month start."""
    if kind == "FX" and iso3 == USA:
        idx = pd.date_range(START_PERIOD, pd.Timestamp.today().normalize(), freq="MS")
        return pd.Series(1.0, index=idx)

    if kind == "CPI":
        flows, key = CPI_FLOWS, f"{iso3}.CPI._T.IX.M"
    else:
        flows, key = FX_FLOWS, f"{iso3}.XDC_USD.PA_RT.M"

    errors = []
    for flow in flows:
        try:
            return fetch_flow_cached(flow, key)
        except Exception as e:
            errors.append(f"{flow}: {e}")
    raise RuntimeError("; ".join(errors))


def fetch_flow_cached(flow: str, key: str) -> pd.Series:
    return _fetch_flow(flow, key)


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------
def build_frame(c1: str, c2: str) -> pd.DataFrame:
    fx1 = fetch_series("FX", c1)
    fx2 = fetch_series("FX", c2)
    p1 = fetch_series("CPI", c1)
    p2 = fetch_series("CPI", c2)

    df = pd.concat(
        {"FX1_LCU_per_USD": fx1, "FX2_LCU_per_USD": fx2, "P1_CPI": p1, "P2_CPI": p2},
        axis=1,
    ).sort_index()
    df.index.name = "Month"
    df["S_C2_per_C1"] = df["FX2_LCU_per_USD"] / df["FX1_LCU_per_USD"]
    df["RER"] = df["S_C2_per_C1"] * df["P1_CPI"] / df["P2_CPI"]
    return df


def apply_sample(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    out = df.loc[(df.index >= start) & (df.index <= end)].copy()
    valid = out["RER"].replace([float("inf"), float("-inf")], pd.NA).dropna()
    rer_star = valid.mean() if len(valid) else float("nan")
    out["RER_star"] = rer_star
    out["Real_Index"] = 100 * out["RER"] / rer_star
    out["Valid_RER"] = out["RER"].notna()
    return out


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("Real Exchange Rate App")
st.caption(
    "Monthly IMF data. S(t) is Country 2 currency per 1 unit of Country 1 currency. "
    "RER(t) = S(t) × P1(t) / P2(t). Real Index = 100 × RER(t) / RER*, where RER* is the "
    "average RER over valid months in the selected sample."
)

opts = country_options()
labels = list(opts.keys())
default_c1 = next(l for l in labels if opts[l] == "GBR")
default_c2 = next(l for l in labels if opts[l] == "USA")

col1, col2 = st.columns(2)
with col1:
    lab1 = st.selectbox("Country 1", labels, index=labels.index(default_c1))
with col2:
    lab2 = st.selectbox("Country 2", labels, index=labels.index(default_c2))
c1, c2 = opts[lab1], opts[lab2]
name1, name2 = lab1.rsplit(" (", 1)[0], lab2.rsplit(" (", 1)[0]

if c1 == c2:
    st.warning("Choose two different countries.")
    st.stop()

with st.spinner("Downloading IMF data..."):
    try:
        full = build_frame(c1, c2)
    except Exception as e:
        st.error(
            f"Could not retrieve IMF data for {name1} and/or {name2}. "
            f"The IMF API may be down or the series may not exist.\n\nDetails: {e}"
        )
        st.stop()

available = full.dropna(subset=["RER"])
if available.empty:
    st.error("No overlapping months with both CPI and exchange-rate data for this pair.")
    st.stop()

first, last = available.index.min(), available.index.max()
months = pd.date_range(first, last, freq="MS")
month_labels = [m.strftime("%Y-%m") for m in months]

sc1, sc2, sc3 = st.columns([1, 1, 1])
with sc1:
    start_lab = st.selectbox("Start month", month_labels, index=0)
with sc2:
    end_lab = st.selectbox("End month", month_labels, index=len(month_labels) - 1)
with sc3:
    view = st.radio("Chart", ["Real", "Nominal", "Both"], horizontal=True, index=2)

start, end = pd.Timestamp(start_lab), pd.Timestamp(end_lab)
if start > end:
    st.warning("Start month must be on or before end month.")
    st.stop()

sample = apply_sample(full, start, end)
rer_star = sample["RER_star"].iloc[0]
n_valid = int(sample["Valid_RER"].sum())

m1, m2, m3, m4 = st.columns(4)
m1.metric("RER*", f"{rer_star:,.6g}")
m2.metric("Valid months", f"{n_valid}")
m3.metric("Latest S(t)", f"{sample['S_C2_per_C1'].dropna().iloc[-1]:,.6g}")
m4.metric("Latest Real Index", f"{sample['Real_Index'].dropna().iloc[-1]:,.2f}")

s_label = f"S: {name2} currency per 1 {name1} currency"

if view == "Both":
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=sample.index, y=sample["Real_Index"], name="Real Index (RER*=100)"),
                  secondary_y=False)
    fig.add_trace(go.Scatter(x=sample.index, y=sample["S_C2_per_C1"], name=s_label),
                  secondary_y=True)
    fig.update_yaxes(title_text="Real Index", secondary_y=False)
    fig.update_yaxes(title_text=s_label, secondary_y=True)
    fig.add_hline(y=100, line_dash="dot", line_color="gray")
elif view == "Real":
    fig = go.Figure(go.Scatter(x=sample.index, y=sample["Real_Index"], name="Real Index"))
    fig.update_yaxes(title_text="Real Index (RER* = 100)")
    fig.add_hline(y=100, line_dash="dot", line_color="gray")
else:
    fig = go.Figure(go.Scatter(x=sample.index, y=sample["S_C2_per_C1"], name=s_label))
    fig.update_yaxes(title_text=s_label)

fig.update_layout(
    title=f"{name1} vs {name2}, {start_lab} to {end_lab}",
    hovermode="x unified",
    height=520,
    legend=dict(orientation="h", y=-0.15),
)
st.plotly_chart(fig, use_container_width=True)

export = sample.reset_index()
export["Month"] = export["Month"].dt.strftime("%Y-%m")
export.insert(1, "Country1", c1)
export.insert(2, "Country2", c2)
csv_bytes = export.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download CSV of sample",
    data=csv_bytes,
    file_name=f"rer_{c1}_{c2}_{start_lab}_{end_lab}.csv",
    mime="text/csv",
)

with st.expander("Data table"):
    st.dataframe(export, use_container_width=True)

st.caption(
    "Source: IMF SDMX 3.0 API (CPI: {ISO3}.CPI._T.IX.M, FX: {ISO3}.XDC_USD.PA_RT.M). "
    "Euro area uses G163. Data may have gaps; months missing any input are excluded from RER*."
)
