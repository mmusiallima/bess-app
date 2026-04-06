import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

# Konfiguracja strony
st.set_page_config(page_title="BESS Arbitrage Dashboard", layout="wide")

st.title("🔋 BESS Arbitrage Optimizer")
st.write("Model optymalizacji magazynu energii (1 cykl dziennie, 100% sprawności, logika chronologiczna)")

# --- BOCZNY PANEL (Ustawienia) ---
st.sidebar.header("Ustawienia modelu")
url = "https://docs.google.com/spreadsheets/d/1Z5Xgo6TKyAfzkIpjK8zOGvKREqNTm2CyFqMVPERI9Lo/export?format=csv"

duration_2h = st.sidebar.slider("Czas trwania systemu A (h)", 1, 6, 2)
duration_4h = st.sidebar.slider("Czas trwania systemu B (h)", 1, 12, 4)

# --- LOGIKA OBLICZENIOWA ---
@st.cache_data # Dzięki temu aplikacja działa szybciej
def load_data(url):
    df = pd.read_csv(url)
    df.rename(columns={'Data': 'Date', 'Cena': 'Price'}, inplace=True, errors='ignore')
    df['Date'] = pd.to_datetime(df['Date'])
    df['Date_only'] = df['Date'].dt.date
    df['Hour'] = df['Date'].dt.hour
    return df

def calculate_bess_chronological(data, duration_hours):
    results = []
    for date, group in data.groupby('Date_only'):
        daily_prices = group.copy()
        daily_prices['Action'] = 'Idle'
        if len(daily_prices) < duration_hours * 2: continue
        
        best_profit = -float('inf')
        best_charge_hours, best_discharge_hours = [], []
        
        for H in range(duration_hours, 24 - duration_hours + 1):
            window_charge = daily_prices[daily_prices['Hour'] < H]
            window_discharge = daily_prices[daily_prices['Hour'] >= H]
            charge_candidates = window_charge.sort_values(by='Price').head(duration_hours)
            discharge_candidates = window_discharge.sort_values(by='Price', ascending=False).head(duration_hours)
            
            if len(charge_candidates) == duration_hours and len(discharge_candidates) == duration_hours:
                profit = discharge_candidates['Price'].sum() - charge_candidates['Price'].sum()
                if profit > best_profit:
                    best_profit = profit
                    best_charge_hours = charge_candidates['Hour'].values
                    best_discharge_hours = discharge_candidates['Hour'].values
        
        if best_profit > 0:
            daily_prices.loc[daily_prices['Hour'].isin(best_charge_hours), 'Action'] = 'Charge'
            daily_prices.loc[daily_prices['Hour'].isin(best_discharge_hours), 'Action'] = 'Discharge'
            p = best_profit
        else: p = 0
            
        results.append({'Date': date, 'Profit': p, 'Schedule': daily_prices})
    
    summary = pd.DataFrame([{'Date': r['Date'], 'Profit': r['Profit']} for r in results])
    schedules = pd.concat([r['Schedule'] for r in results])
    return summary, schedules

# --- URUCHOMIENIE ---
try:
    df = load_data(url)
    s2h, sch2h = calculate_bess_chronological(df, duration_2h)
    s4h, sch4h = calculate_bess_chronological(df, duration_4h)

    # Statystyki w kolumnach
    col1, col2 = st.columns(2)
    col1.metric(f"Zysk Total ({duration_2h}h)", f"{s2h['Profit'].sum():.2f}")
    col2.metric(f"Zysk Total ({duration_4h}h)", f"{s4h['Profit'].sum():.2f}")

    # Wykresy
    st.subheader("Wyniki finansowe i harmonogram")
    fig, axs = plt.subplots(2, 1, figsize=(10, 10))
    
    # Skumulowany zysk
    axs[0].plot(s2h['Date'], s2h['Profit'].cumsum(), label=f"BESS {duration_2h}h")
    axs[0].plot(s4h['Date'], s4h['Profit'].cumsum(), label=f"BESS {duration_4h}h")
    axs[0].set_title("Skumulowany Zysk")
    axs[0].legend()

    # Przykładowy dzień
    active_days = s4h[s4h['Profit'] > 0]['Date'].values
    sample_date = active_days[0] if len(active_days) > 0 else df['Date_only'].iloc[0]
    sample_df = sch4h[sch4h['Date_only'] == sample_date]
    
    bars = axs[1].bar(sample_df['Hour'], sample_df['Price'], color='lightgray')
    for i, row in sample_df.reset_index().iterrows():
        if row['Action'] == 'Charge': bars[int(row['Hour'])].set_color('blue')
        if row['Action'] == 'Discharge': bars[int(row['Hour'])].set_color('red')
    axs[1].set_title(f"Logika pracy w dniu {sample_date} (System {duration_4h}h)")
    
    st.pyplot(fig)

except Exception as e:
    st.error(f"Błąd ładowania danych: {e}")
