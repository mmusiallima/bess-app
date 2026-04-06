import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO
from datetime import datetime

st.set_page_config(
    page_title="BESS Arbitrage Dashboard - Green Capital",
    layout="wide",
    page_icon="🔋"
)

st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    [data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: 700; }
    div[data-testid="metric-container"] {
        background: #f0fdf8;
        border: 1px solid #d1fae5;
        border-radius: 10px;
        padding: 12px 16px;
    }
</style>
""", unsafe_allow_html=True)

SHEET_ID = "1Z5Xgo6TKyAfzkIpjK8zOGvKREqNTm2CyFqMVPERI9Lo"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"

COLOR_2H = "#10B981"
COLOR_4H = "#3B82F6"
COLOR_SKIP = "#E2E8F0"
COLOR_CHARGE = "#F59E0B"
COLOR_DISCHARGE = "#EF4444"
COLOR_IDLE = "#CBD5E1"

with st.sidebar:
    st.markdown("### ⚙️ Parametry modelu")
    bess_type = st.selectbox("Typ magazynu", ["2h", "4h", "Oba (porównanie)"], index=2)
    power_mw = st.number_input("Moc (MW)", min_value=0.1, value=10.0, step=0.5)
    st.markdown("---")
    if st.button("🔄 Odśwież dane z Sheets", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    st.caption("Green Capital S.A.\nTGE FIX1 | Sprawność 100%\n1 cykl dziennie")


@st.cache_data(ttl=3600, show_spinner=False)
def load_from_sheets(url):
    return pd.read_csv(url)


def normalize_cols(df):
    df = df.copy()
    cols = {c.strip().lower().replace(" ", "").replace("_", ""): c for c in df.columns}
    dk = next((cols[k] for k in cols if any(x in k for x in ["doba", "data", "date"])), None)
    hk = next((cols[k] for k in cols if any(x in k for x in ["oreb", "godzina", "hour", "godz"])), None)
    pk = next((cols[k] for k in cols if any(x in k for x in ["cena", "fix", "rdn", "price", "pln", "mwh"])), None)
    if not dk: raise ValueError(f"Brak kolumny daty. Kolumny: {list(df.columns)}")
    if not hk: raise ValueError(f"Brak kolumny godziny. Kolumny: {list(df.columns)}")
    if not pk: raise ValueError(f"Brak kolumny ceny. Kolumny: {list(df.columns)}")
    df = df.rename(columns={dk: "Date", hk: "Hour", pk: "Price"})[["Date", "Hour", "Price"]].copy()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date"])
    df["Date"] = df["Date"].dt.date
    if df["Price"].dtype == object:
        df["Price"] = df["Price"].astype(str).str.replace(",", ".").str.strip()
    df["Price"] = pd.to_numeric(df["Price"], errors="coerce")
    df["Hour"] = pd.to_numeric(df["Hour"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["Hour", "Price"])
    return df[df["Hour"].between(1, 24)]


def calc_arbitrage(df, hours, power_mw):
    results = []
    for date, grp in df.groupby("Date"):
        grp = grp.sort_values("Hour").reset_index(drop=True)
        n = len(grp)
        if n < hours * 2:
            results.append({"Date": date, "Active": False, "Reason": f"Za mało godzin ({n})",
                            "ChargeHours": [], "DischargeHours": [], "AvgCharge": None,
                            "AvgDischarge": None, "Spread": None, "Revenue": 0})
            continue
        prices = grp.set_index("Hour")["Price"].to_dict()
        hl = sorted(prices.keys())
        best_rev, best = 0, None
        for split in range(hours, n - hours + 1):
            left, right = hl[:split], hl[split:]
            if len(left) < hours or len(right) < hours:
                continue
            cheap = sorted(left, key=lambda h: prices[h])[:hours]
            exp = sorted(right, key=lambda h: prices[h], reverse=True)[:hours]
            avg_c = np.mean([prices[h] for h in cheap])
            avg_d = np.mean([prices[h] for h in exp])
            spread = avg_d - avg_c
            if spread > best_rev:
                best_rev = spread
                best = (sorted(cheap), sorted(exp), avg_c, avg_d, spread)
        if best and best[4] > 0:
            ch, dh, ac, ad, sp = best
            results.append({"Date": date, "Active": True, "Reason": "",
                            "ChargeHours": ch, "DischargeHours": dh,
                            "AvgCharge": round(ac, 2), "AvgDischarge": round(ad, 2),
                            "Spread": round(sp, 2), "Revenue": round(sp * power_mw * hours)})
        else:
            results.append({"Date": date, "Active": False, "Reason": "Spread ujemny",
                            "ChargeHours": [], "DischargeHours": [], "AvgCharge": None,
                            "AvgDischarge": None, "Spread": None, "Revenue": 0})
    return pd.DataFrame(results)


def fmt_pln(n):
    return f"{int(round(n)):,}".replace(",", " ") + " PLN"


def fig_revenue_bar(res, label, color):
    active = res[res["Active"]]
    skipped = res[~res["Active"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=pd.to_datetime(active["Date"]), y=active["Revenue"],
        name="Aktywny", marker_color=color, marker_line_width=0,
        hovertemplate="<b>%{x|%d.%m.%Y}</b><br>%{y:,.0f} PLN<extra></extra>"
    ))
    if len(skipped):
        fig.add_trace(go.Bar(
            x=pd.to_datetime(skipped["Date"]), y=[30] * len(skipped),
            name="Pominięty", marker_color=COLOR_SKIP, marker_line_width=0,
            hovertemplate="<b>%{x|%d.%m.%Y}</b><br>Pominięty<extra></extra>"
        ))
    fig.update_layout(
        title=dict(text=f"<b>Przychód dzienny — {label}</b>", font=dict(size=14)),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=0, r=0, t=50, b=0), height=280,
        xaxis=dict(showgrid=False, tickformat="%b %y", tickfont=dict(size=11), tickangle=-30),
        yaxis=dict(showgrid=True, gridcolor="#f0f0ee", tickformat=",.0f",
                   ticksuffix=" PLN", tickfont=dict(size=11), title=None),
        legend=dict(orientation="h", y=1.12, x=1, xanchor="right", font=dict(size=11)),
        bargap=0.08, hovermode="x unified", barmode="overlay"
    )
    return fig


def fig_cumulative(res_map, power_mw):
    fig = go.Figure()
    for h, color in [(2, COLOR_2H), (4, COLOR_4H)]:
        if h not in res_map:
            continue
        res = res_map[h]
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(res["Date"]), y=res["Revenue"].cumsum(),
            name=f"BESS {h}h / {power_mw} MW",
            line=dict(color=color, width=2.5),
            fill="tozeroy", fillcolor=color + "22",
            hovertemplate=f"<b>%{{x|%d.%m.%Y}}</b><br>Skumulowany ({h}h): <b>%{{y:,.0f}} PLN</b><extra></extra>"
        ))
    fig.update_layout(
        title=dict(text="<b>Skumulowany przychód z arbitrażu</b>", font=dict(size=14)),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=0, r=0, t=50, b=0), height=320,
        xaxis=dict(showgrid=False, tickformat="%b %y", tickfont=dict(size=11), tickangle=-30),
        yaxis=dict(showgrid=True, gridcolor="#f0f0ee", tickformat=",.0f",
                   ticksuffix=" PLN", tickfont=dict(size=11), title=None),
        legend=dict(orientation="h", y=1.12, x=1, xanchor="right", font=dict(size=11)),
        hovermode="x unified"
    )
    return fig


def fig_heatmap(res, hours):
    active = res[res["Active"]].copy()
    if len(active) == 0:
        return None
    active["Date_dt"] = pd.to_datetime(active["Date"])
    active["Month"] = active["Date_dt"].dt.strftime("%Y-%m")
    active["Day"] = active["Date_dt"].dt.day
    pivot = active.pivot_table(values="Spread", index="Month", columns="Day", aggfunc="mean")
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=pivot.columns.tolist(), y=pivot.index.tolist(),
        colorscale="RdYlGn", zmid=100,
        hovertemplate="Miesiąc: %{y}<br>Dzień: %{x}<br>Spread: <b>%{z:.1f} PLN/MWh</b><extra></extra>",
        colorbar=dict(title=dict(text="PLN/MWh", side="right"), tickfont=dict(size=10))
    ))
    fig.update_layout(
        title=dict(text=f"<b>Heatmapa spreadu — {hours}h (PLN/MWh)</b>", font=dict(size=14)),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=0, r=0, t=50, b=0),
        height=max(220, len(pivot) * 30 + 90),
        xaxis=dict(title="Dzień miesiąca", tickfont=dict(size=10), dtick=1),
        yaxis=dict(title=None, tickfont=dict(size=10), autorange="reversed")
    )
    return fig


def fig_sample_day(df_full, res, hours):
    active_res = res[res["Active"]]
    if len(active_res) == 0:
        return None
    sample_row = active_res.iloc[len(active_res) // 2]
    day_data = df_full[df_full["Date"] == sample_row["Date"]].sort_values("Hour")
    if len(day_data) == 0:
        return None

    groups = {"Ładowanie": ([], [], COLOR_CHARGE), "Rozładowanie": ([], [], COLOR_DISCHARGE), "Bezczynny": ([], [], COLOR_IDLE)}
    for _, row in day_data.iterrows():
        if row["Hour"] in sample_row["ChargeHours"]:
            groups["Ładowanie"][0].append(row["Hour"]); groups["Ładowanie"][1].append(row["Price"])
        elif row["Hour"] in sample_row["DischargeHours"]:
            groups["Rozładowanie"][0].append(row["Hour"]); groups["Rozładowanie"][1].append(row["Price"])
        else:
            groups["Bezczynny"][0].append(row["Hour"]); groups["Bezczynny"][1].append(row["Price"])

    fig = go.Figure()
    for name, (xs, ys, color) in groups.items():
        if xs:
            fig.add_trace(go.Bar(
                x=xs, y=ys, name=name, marker_color=color, marker_line_width=0,
                hovertemplate=f"<b>Godz. %{{x}}</b><br>%{{y:.2f}} PLN/MWh<br>{name}<extra></extra>"
            ))
    fig.add_hline(y=0, line_color="#94a3b8", line_width=1.2)
    fig.update_layout(
        title=dict(
            text=f"<b>{sample_row['Date']}</b> | Spread: {sample_row['Spread']} PLN/MWh | {fmt_pln(sample_row['Revenue'])}",
            font=dict(size=12)
        ),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=0, r=0, t=55, b=0), height=280,
        xaxis=dict(title="Godzina", tickmode="linear", dtick=1, tickfont=dict(size=10), showgrid=False),
        yaxis=dict(title="PLN/MWh", showgrid=True, gridcolor="#f0f0ee", tickfont=dict(size=10), zeroline=False),
        legend=dict(orientation="h", y=1.15, x=1, xanchor="right", font=dict(size=11)),
        barmode="overlay", bargap=0.15
    )
    return fig


# ── MAIN ──────────────────────────────────────────────────────────────────────
st.title("🔋 BESS Arbitrage Dashboard")

with st.spinner("Pobieranie danych z Google Sheets..."):
    try:
        df_raw = load_from_sheets(CSV_URL)
    except Exception as e:
        st.error(f"❌ Błąd pobierania: {e}")
        st.info("Arkusz musi być publiczny: Udostępnij → Każda osoba z linkiem → Przeglądający")
        st.stop()

try:
    df = normalize_cols(df_raw)
except ValueError as e:
    st.error(str(e))
    st.stop()

all_dates = [datetime.combine(d, datetime.min.time()) for d in sorted(df["Date"].unique())]

st.markdown("#### 📅 Zakres dat")
date_range = st.select_slider(
    "zakres_dat",
    options=all_dates,
    value=(all_dates[0], all_dates[-1]),
    format_func=lambda d: d.strftime("%d.%m.%Y"),
    label_visibility="collapsed"
)
d_from, d_to = date_range[0].date(), date_range[1].date()
df_f = df[(df["Date"] >= d_from) & (df["Date"] <= d_to)]
total_days = df_f["Date"].nunique()

if total_days == 0:
    st.warning("Brak danych w wybranym zakresie.")
    st.stop()

st.caption(f"📆 {d_from} – {d_to}  |  {total_days} dni  |  {len(df_f):,} wierszy")
st.markdown("---")

hours_map = {"2h": [2], "4h": [4], "Oba (porównanie)": [2, 4]}
hours_list = hours_map[bess_type]

res_map = {}
with st.spinner("Obliczanie arbitrażu..."):
    for h in hours_list:
        res_map[h] = calc_arbitrage(df_f, h, power_mw)

# ── METRYKI ───────────────────────────────────────────────────────────────────
mcols = st.columns(len(hours_list) * 4)
ci = 0
for h in hours_list:
    res = res_map[h]
    active = res[res["Active"]]
    total_rev = active["Revenue"].sum()
    annual = int(total_rev / total_days * 365) if total_days else 0
    avg_sp = active["Spread"].mean() if len(active) else 0
    skips = len(res) - len(active)
    mcols[ci].metric(f"💰 Łączny {h}h", fmt_pln(total_rev))
    mcols[ci + 1].metric(f"📈 Roczna est. {h}h", fmt_pln(annual))
    mcols[ci + 2].metric(f"⚡ Śr. spread {h}h", f"{avg_sp:.1f} PLN/MWh")
    mcols[ci + 3].metric(f"⏭️ Pominięte {h}h", f"{skips}/{total_days}",
                         delta=f"-{100 * skips / total_days:.0f}%", delta_color="inverse")
    ci += 4

st.markdown("---")

# ── WYKRESY ───────────────────────────────────────────────────────────────────
if bess_type == "Oba (porównanie)":
    st.plotly_chart(fig_cumulative(res_map, power_mw), use_container_width=True)

for h in hours_list:
    color = COLOR_2H if h == 2 else COLOR_4H
    icon = "🟢" if h == 2 else "🔵"
    st.markdown(f"### {icon} BESS {h}h — {power_mw} MW")
    c1, c2 = st.columns([3, 2])
    with c1:
        st.plotly_chart(fig_revenue_bar(res_map[h], f"{h}h / {power_mw} MW", color), use_container_width=True)
    with c2:
        f = fig_sample_day(df_f, res_map[h], h)
        if f:
            st.plotly_chart(f, use_container_width=True)
    fh = fig_heatmap(res_map[h], h)
    if fh:
        st.plotly_chart(fh, use_container_width=True)
    st.markdown("---")

# ── TABELA ────────────────────────────────────────────────────────────────────
st.markdown("### 📋 Szczegóły dni")
tabs = st.tabs([f"BESS {h}h" for h in hours_list])
for i, h in enumerate(hours_list):
    with tabs[i]:
        res = res_map[h].copy()
        res["ChargeHours"] = res["ChargeHours"].apply(lambda x: ", ".join(map(str, x)) if x else "-")
        res["DischargeHours"] = res["DischargeHours"].apply(lambda x: ", ".join(map(str, x)) if x else "-")
        res["Status"] = res["Active"].map({True: "✅ aktywny", False: "⬜ pominięty"})
        res["Revenue_fmt"] = res["Revenue"].apply(lambda x: fmt_pln(x) if x > 0 else "-")
        display = res[["Date", "Status", "ChargeHours", "AvgCharge", "DischargeHours",
                        "AvgDischarge", "Spread", "Revenue_fmt"]].rename(columns={
            "Date": "Data", "Status": "Status", "ChargeHours": "Godz. ładowania",
            "AvgCharge": "Cena śr. ład.", "DischargeHours": "Godz. rozładowania",
            "AvgDischarge": "Cena śr. rozład.", "Spread": "Spread (PLN/MWh)", "Revenue_fmt": "Przychód"
        })
        st.dataframe(display, use_container_width=True, height=420,
                     column_config={
                         "Spread (PLN/MWh)": st.column_config.NumberColumn(format="%.2f"),
                         "Cena śr. ład.": st.column_config.NumberColumn(format="%.2f"),
                         "Cena śr. rozład.": st.column_config.NumberColumn(format="%.2f"),
                     })
        buf = BytesIO()
        res_map[h].to_excel(buf, index=False)
        st.download_button(
            f"⬇️ Pobierz BESS {h}h (XLSX)", buf.getvalue(),
            file_name=f"BESS_{h}h_{d_from}_{d_to}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
