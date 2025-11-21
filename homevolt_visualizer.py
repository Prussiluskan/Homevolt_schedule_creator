# -*- coding: utf-8 -*-
"""
Visualiserare för Homevolt Batteri-optimerare (v2.7 - Left Aligned Legends)
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
import sys
from datetime import datetime, timedelta

# --- VISUAL CONFIGURATION ---
COLOR_PRICE_LINE      = 'blue'        # Priskurvan
COLOR_BATTERY_LINE    = 'green'       # Batterinivå
COLOR_GRID_IMPORT     = 'red'         # Kvart: Import
COLOR_GRID_EXPORT     = 'dodgerblue'  # Kvart: Export
COLOR_HOURLY_AVG      = 'orange'      # Timme: Snitt (Bakgrund)
COLOR_HOURLY_AVG_LINE = 'darkorange'  # Timme: Snitt (Linje)
COLOR_LOAD_LINE       = 'gray'        # Husets förbrukning
COLOR_PEAK_LIMIT_LINE = 'black'       # Månadstopp (Gräns)

# Importera optimeraren och inställningar
try:
    from homevolt_optimizer import run_optimizer
    from homevolt_optimizer_settings import (
        BATTERY_ENERGY_WH, 
        GRID_SETPOINT_BIAS_W,
        PREVIOUS_MONTHLY_HOUR_PEAK_POWER_KW
    )
except ImportError as e:
    print(f"Fel vid import: {e}")
    print("Se till att alla filer ligger i samma mapp och att funktionsnamnen stämmer.")
    sys.exit(1)

def plot_optimization():
    # 1. Kör optimeringen och hämta data
    print("Kör optimerare för att hämta data...")
    timeline = run_optimizer()
    
    if not timeline:
        print("Ingen data mottagen från optimeraren.")
        return

    # 2. Förbered data för plottning
    times = []
    grid_power = []
    load_power = []
    soc_pct = []
    prices = []
    
    # Förbereda tim-aggregering
    hourly_data = {} 
    
    bias_w = GRID_SETPOINT_BIAS_W
    monthly_peak_w = PREVIOUS_MONTHLY_HOUR_PEAK_POWER_KW * 1000.0
    
    for item in timeline:
        # Time parsing
        t_str = item["time"]
        dt = datetime.strptime(t_str, "%H:%M")
        times.append(dt)
        
        # Power Calculations (Watt)
        actual_grid_w = (item["grid_wh"] * 4.0) + bias_w
        net_load_w = (item["cons_wh"] * 4.0) - (item["solar_wh"] * 4.0)
        
        grid_power.append(actual_grid_w)
        load_power.append(net_load_w)
        
        # Samla data för tim-snitt
        h_key = dt.strftime("%Y-%m-%d %H")
        if h_key not in hourly_data: hourly_data[h_key] = {"vals": [], "dt": dt}
        hourly_data[h_key]["vals"].append(actual_grid_w)
        
        # SoC & Price
        soc = (item["batt_wh"] / BATTERY_ENERGY_WH) * 100.0
        soc_pct.append(soc)
        prices.append(item["price"])

    # Beräkna tim-staplar
    hourly_times = []
    hourly_avgs = []
    for k, v in hourly_data.items():
        clean_dt = v["dt"].replace(minute=0)
        hourly_times.append(clean_dt)
        avg = sum(v["vals"]) / len(v["vals"])
        hourly_avgs.append(avg)

    # 3. Skapa Grafen
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 12), sharex=True)
    plt.subplots_adjust(hspace=0.3) 
    
    # --- GRAF 1: EFFEKT & PRIS ---
    ax1.set_title("Effektbalans & Elpris", fontsize=16, fontweight='bold', pad=40)
    ax1.set_ylabel("Effekt (W)", fontsize=12)
    ax1.grid(True, linestyle='--', alpha=0.5)
    
    # 1a. Rita Tim-snitt
    hour_width = (1.0 / 24.0) * 0.98
    ax1.bar(hourly_times, hourly_avgs, width=hour_width, color=COLOR_HOURLY_AVG, alpha=0.3, label="Tim-medel", align='edge')

    # 1b. Rita Konturlinje
    if hourly_times:
        times_line = hourly_times + [hourly_times[-1] + timedelta(hours=1)]
        avgs_line = hourly_avgs + [hourly_avgs[-1]]
        ax1.step(times_line, avgs_line, where='post', color=COLOR_HOURLY_AVG_LINE, linewidth=1.5, label='_nolegend_')

    # 2. Rita Net Load
    ax1.step(times, load_power, color=COLOR_LOAD_LINE, alpha=0.4, where='post', label="Husets Last")
    
    # 3. Rita Grid Import/Export
    colors = [COLOR_GRID_IMPORT if p > 0 else COLOR_GRID_EXPORT for p in grid_power]
    quarter_width = (1.0 / 96.0) * 0.9
    ax1.bar(times, grid_power, width=quarter_width, color=colors, alpha=0.9, label="Kvart (Nät)", align='edge')
    
    # 4. Rita in Månadstoppen
    ax1.axhline(y=monthly_peak_w, color=COLOR_PEAK_LIMIT_LINE, linestyle='--', linewidth=2, label="Månadstopp")
    
    # Pris på höger axel
    ax1_price = ax1.twinx()
    ax1_price.set_ylabel("Elpris (öre/kWh)", fontsize=12, color=COLOR_PRICE_LINE)
    ax1_price.step(times, prices, color=COLOR_PRICE_LINE, where='post', linewidth=2, label="Elpris")
    ax1_price.tick_params(axis='y', labelcolor=COLOR_PRICE_LINE)
    
    # --- LEGEND 1 (Vänsterställd & Mindre) ---
    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax1_price.get_legend_handles_labels()
    
    ax1.legend(handles1 + handles2, labels1 + labels2, 
               loc='lower left', bbox_to_anchor=(0.0, 1.02), 
               ncol=4, frameon=True, fontsize=9, handlelength=1.5)

    # --- GRAF 2: STATUS (SoC) ---
    ax2.set_ylabel("Batteri SoC (%)", fontsize=12, color=COLOR_BATTERY_LINE)
    ax2.set_ylim(0, 105)
    
    # Batterikurva
    ax2.plot(times, soc_pct, color=COLOR_BATTERY_LINE, linewidth=2, label="Batterinivå")
    ax2.fill_between(times, soc_pct, color=COLOR_BATTERY_LINE, alpha=0.1)
    ax2.tick_params(axis='y', labelcolor=COLOR_BATTERY_LINE)
    
    # --- LEGEND 2 (Vänsterställd & Mindre) ---
    ax2.legend(loc='lower left', bbox_to_anchor=(0.0, 1.02), 
               ncol=1, frameon=True, fontsize=9)

    # --- FORMATERING AV X-AXELN ---
    ax2.set_xlabel("Tid (HH:MM)", fontsize=12)
    ax2.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax2.xaxis.set_minor_locator(mdates.MinuteLocator(byminute=[15, 30, 45]))

    # Gridlines
    for ax in [ax1, ax2]:
        ax.grid(which='major', axis='x', linestyle='-', linewidth=1.5, color='black', alpha=0.3)
        ax.grid(which='major', axis='y', linestyle='--', linewidth=0.8, color='gray', alpha=0.5)
        ax.grid(which='minor', axis='x', linestyle=':', linewidth=0.5, color='gray', alpha=0.3)

    plt.xticks(rotation=45)
    
    print("Ritar graf...")
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    plot_optimization()