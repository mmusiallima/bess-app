import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO

st.set_page_config(
    page_title="BESS Arbitrage Dashboard - Green Capital",
    layout="wide",
    page_icon="🔋"
)

st.markdown("""
<style>
    .main { padding-top: 1rem; }
    [data-testid="stMetricValue"] { font-size: 1.4rem; }
</style>
""", unsafe_allow_html=True)

st.title("🔋 BESS Arbitrage Dashboard")
st.caption("Green Capital S.A. | Arbitraż cenowy FIX1 TGE | Sprawność 100% | 1 cykl / dzień")

# ── STAŁE ──────────────────────────────────────────────────────────────────────
SHEET_ID = "1Z5Xgo6TKyAfzkIpjK8zOGvKREqNTm2CyFqMVPERI9Lo"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"

# ── SIDEBAR ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Parametry modelu")

    st.subheader("Dane")
    if st.button("🔄 Odśwież dane z Google Sheets", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.subheader("Magazyn BESS")
    bess_type = st.selectbox("Typ magazynu", ["2h", "4h", "Oba (porównanie)"])
    power_mw = st.number_input("Moc (MW)", min_value=0.1, value=10.0, step=0.5)

    st.divider()
    st.subheader("Zakres dat")
    date_from = st.date_input("Od", value=None)
    date_to = st.date_input("Do", value=None)

# ── FUNKCJE ────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_from_sheets(url):
    df = pd.read_csv(url)
    return df


def normalize_cols(df):
    df = df.copy()
    cols = {c.strip().lower().replace(" ", "").replace("_", ""): c for c in df.columns}

    dk = next((cols[k] for k in cols if any(x in k for x in ["doba", "data", "date"])), None)
    hk = next((cols[k] for k in cols if any(x in k for x in ["oreb", "godzina", "hour", "godz"])), None)
    pk = next((cols[k] for k in cols if any(x in k for x in ["cena", "fix", "rdn", "price", "pln", "mwh"])), None)

    if not dk:
        raise ValueError(f"Brak kolumny daty. Kolumny w arkuszu: {list(df.columns)}")
    if not hk:
        raise ValueError(f"Brak kolumny godziny. Kolumny w arkuszu: {list(df.columns)}")
    if not pk:
        raise ValueError(f"Brak kolumny ceny. Kolumny w arkuszu: {list(df.columns)}")

    df = df.rename(columns={dk: "Date", hk: "Hour", pk: "Price"})
    df = df[["Date", "Hour", "Price"]].copy()

    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date"])
    df["Date"] = df["Date"].dt.date

    if df["Price"].dtype == object:
        df["Price"] = df["Price"].astype(str).str.replace(",", ".").str.strip()
    df["Price"] = pd.to_numeric(df["Price"], errors="coerce")
    df["Hour"] = pd.to_numeric(df["Hour"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["Hour", "Price"])
    df = df[df["Hour"].between(1, 24)]

    return df


def calc_arbitrage(df, hours, power_mw):
    results = []
    for date, grp in df.groupby("Date"):
        grp = grp.sort_values("Hour").reset_index(drop=True)
        n = len(grp)

        if n < hours * 2:
            results.append({"Date": date, "Active": False, "Reason": f"Za mało godzin ({n})",
                            "ChargeHours": [], "DischargeHours": [],
                            "AvgCharge": None, "AvgDischarge": None,
                            "Spread": None, "Revenue": 0})
            continue

        prices = grp.set_index("Hour")["Price"].to_dict()
        hours_list = sorted(prices.keys())
        best_rev = 0
        best = None

        for split in range(hours, n - hours + 1):
            left = hours_list[:split]
            right = hours_list[split:]
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
                            "Spread": round(sp, 2),
                            "Revenue": round(sp * power_mw * hours)})
        else:
            results.append({"Date": date, "Active": False,
                            "Reason": "Spread ujemny lub brak walidnej sekwencji",
                            "ChargeHours": [], "DischargeHours": [],
                            "AvgCharge": None, "AvgDischarge": None,
                            "Spread": None, "Revenue": 0})
    return pd.DataFrame(results)


def fmt_pln(n):
    return f"{int(round(n)):,}".replace(",", " ") + " PLN"


def plot_daily_revenue(res, label, color):
    fig, ax = plt.subplots(figsize=(12, 3.5))
    dates = pd.to_datetime(res["Date"])
    revs = res["Revenue"].values
    bar_colors = [color if a else "#E0E0DC" for a in res["Active"].values]
    ax.bar(dates, revs, color=bar_colors, width=0.8, zorder=2)
    ax.set_ylabel("PLN", fontsize=10)
    ax.set_title(f"Przychód dzienny — {label}", fontsize=11, fontweight="bold")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    plt.xticks(rotation=30, fontsize=9)
    ax.tick_params(axis="y", labelsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}".replace(",", " ")))
    ax.grid(axis="y", alpha=0.2, zorder=1)
    ax.spines[["top", "right"]].set_visible(False)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=color, label="Aktywny"), Patch(color="#E0E0DC", label="Pominięty")],
              fontsize=9, loc="upper right")
    fig.tight_layout()
    return fig


def plot_cumulative(res2h, res4h, power_mw):
    fig, ax = plt.subplots(figsize=(12, 3.5))
    for res, label, color in [(res2h, f"2h / {power_mw} MW", "#1D9E75"), (res4h, f"4h / {power_mw} MW", "#185FA5")]:
        if res is not None:
            ax.plot(pd.to_datetime(res["Date"]), res["Revenue"].cumsum(),
                    label=label, color=color, linewidth=2)
    ax.set_ylabel("PLN (skumulowany)", fontsize=10)
    ax.set_title("Skumulowany przychód z arbitrażu", fontsize=11, fontweight="bold")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    plt.xticks(rotation=30, fontsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{int(x):,}".replace(",", " ")))
    ax.grid(alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=10)
    fig.tight_layout()
    return fig


def plot_sample_day(df_full, res, hours, date=None):
    active_res = res[res["Active"]]
    if len(active_res) == 0:
        return None
    sample_row = active_res[active_res["Date"] == date].iloc[0] if date and len(active_res[active_res["Date"] == date]) > 0 else active_res.iloc[0]
    day_data = df_full[df_full["Date"] == sample_row["Date"]].sort_values("Hour")

    fig, ax = plt.subplots(figsize=(10, 3.5))
    bar_colors = []
    for _, row in day_data.iterrows():
        if row["Hour"] in sample_row["ChargeHours"]: bar_colors.append("#1D9E75")
        elif row["Hour"] in sample_row["DischargeHours"]: bar_colors.append("#E24B4A")
        else: bar_colors.append("#D3D1C7")

    ax.bar(day_data["Hour"], day_data["Price"], color=bar_colors, width=0.75, zorder=2)
    ax.axhline(0, color="#aaa", linewidth=0.8)
    ax.set_xlabel("Godzina", fontsize=10)
    ax.set_ylabel("PLN/MWh", fontsize=10)
    ax.set_title(
        f"{sample_row['Date']} | {hours}h | Spread: {sample_row['Spread']} PLN/MWh | {fmt_pln(sample_row['Revenue'])}",
        fontsize=10, fontweight="bold")
    ax.set_xticks(range(1, 25))
    ax.tick_params(labelsize=9)
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    from matplotlib.patches import Patch
    ax.legend(handles=[
        Patch(color="#1D9E75", label=f"Ładowanie ({hours}h)"),
        Patch(color="#E24B4A", label=f"Rozładowanie ({hours}h)"),
        Patch(color="#D3D1C7", label="Bezczynny"),
    ], fontsize=9, loc="upper left")
    fig.tight_layout()
    return fig


# ── ŁADOWANIE DANYCH ───────────────────────────────────────────────────────────
with st.spinner("Pobieranie danych z Google Sheets..."):
    try:
        df_raw = load_from_sheets(CSV_URL)
        st.success(f"✅ Dane załadowane z Google Sheets — {len(df_raw)} wierszy")
    except Exception as e:
        st.error(f"❌ Nie można pobrać danych: {e}")
        st.info("Upewnij się że arkusz jest publiczny: Udostępnij → Każda osoba z linkiem → Przeglądający")
        st.stop()

try:
    df = normalize_cols(df_raw)
except ValueError as e:
    st.error(str(e))
    st.stop()

# Filtrowanie dat
if date_from:
    df = df[df["Date"] >= date_from]
if date_to:
    df = df[df["Date"] <= date_to]

if len(df) == 0:
    st.warning("Brak danych po zastosowaniu filtrów dat.")
    st.stop()

date_min = df["Date"].min()
date_max = df["Date"].max()
total_days = df["Date"].nunique()

# ── OBLICZENIA ─────────────────────────────────────────────────────────────────
hours_map = {"2h": [2], "4h": [4], "Oba (porównanie)": [2, 4]}
hours_list = hours_map[bess_type]

res_map = {}
with st.spinner("Obliczanie arbitrażu..."):
    for h in hours_list:
        res_map[h] = calc_arbitrage(df, h, power_mw)

# ── METRYKI ────────────────────────────────────────────────────────────────────
st.markdown(f"**Zakres danych:** {date_min} – {date_max} &nbsp;|&nbsp; {total_days} dni &nbsp;|&nbsp; {len(df):,} wierszy",
            unsafe_allow_html=True)
st.divider()

cols_metric = st.columns(len(hours_list) * 4)
ci = 0
for h in hours_list:
    res = res_map[h]
    active = res[res["Active"]]
    total_rev = active["Revenue"].sum()
    annual_est = int(total_rev / total_days * 365) if total_days > 0 else 0
    avg_spread = active["Spread"].mean() if len(active) > 0 else 0
    skip_count = len(res) - len(active)

    cols_metric[ci].metric(f"Przychód łączny ({h}h)", fmt_pln(total_rev))
    cols_metric[ci+1].metric(f"Estymacja roczna ({h}h)", fmt_pln(annual_est))
    cols_metric[ci+2].metric(f"Śr. spread ({h}h)", f"{avg_spread:.1f} PLN/MWh")
    cols_metric[ci+3].metric(f"Dni pominięte ({h}h)", f"{skip_count} / {total_days}")
    ci += 4

st.divider()

# ── WYKRESY ────────────────────────────────────────────────────────────────────
if bess_type == "Oba (porównanie)":
    st.subheader("Skumulowany przychód")
    st.pyplot(plot_cumulative(res_map.get(2), res_map.get(4), power_mw))

for h in hours_list:
    color = "#1D9E75" if h == 2 else "#185FA5"
    st.subheader(f"BESS {h}h — {power_mw} MW")
    c1, c2 = st.columns([2, 1])
    with c1:
        st.pyplot(plot_daily_revenue(res_map[h], f"{h}h / {power_mw} MW", color))
    with c2:
        fig_day = plot_sample_day(df, res_map[h], h)
        if fig_day:
            st.pyplot(fig_day)

# ── TABELA ─────────────────────────────────────────────────────────────────────
st.divider()
st.subheader("Szczegóły dni")
tabs = st.tabs([f"BESS {h}h" for h in hours_list])

for i, h in enumerate(hours_list):
    with tabs[i]:
        res = res_map[h].copy()
        res["ChargeHours"] = res["ChargeHours"].apply(lambda x: ", ".join(map(str, x)) if x else "-")
        res["DischargeHours"] = res["DischargeHours"].apply(lambda x: ", ".join(map(str, x)) if x else "-")
        res["Status"] = res["Active"].map({True: "✅ aktywny", False: "⬜ pominięty"})
        res["Revenue_fmt"] = res["Revenue"].apply(lambda x: fmt_pln(x) if x > 0 else "-")

        display = res[[
            "Date", "Status", "ChargeHours", "AvgCharge",
            "DischargeHours", "AvgDischarge", "Spread", "Revenue_fmt"
        ]].rename(columns={
            "Date": "Data", "Status": "Status",
            "ChargeHours": "Godz. ładowania", "AvgCharge": "Cena śr. ład.",
            "DischargeHours": "Godz. rozładowania", "AvgDischarge": "Cena śr. rozład.",
            "Spread": "Spread (PLN/MWh)", "Revenue_fmt": "Przychód"
        })

        st.dataframe(display, use_container_width=True, height=400)

        buf = BytesIO()
        res_map[h].to_excel(buf, index=False)
        st.download_button(
            f"⬇️ Pobierz wyniki {h}h (XLSX)",
            buf.getvalue(),
            file_name=f"BESS_{h}h_arbitrage.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
