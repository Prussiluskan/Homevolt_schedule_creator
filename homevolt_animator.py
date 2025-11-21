# -*- coding: utf-8 -*-
"""
Visualiserare (Interaktiv) för Homevolt Batteri-optimerare (v2.2 - Fixade Legender)
Klicka i fönstret för att byta steg.
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
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

# Importera
try:
    from homevolt_optimizer import run_optimizer
    from homevolt_optimizer_settings import (
        BATTERY_ENERGY_WH, 
        GRID_SETPOINT_BIAS_W,
        PREVIOUS_MONTHLY_HOUR_PEAK_POWER_KW
    )
except ImportError as e:
    print(f"Fel vid import: {e}")
    print("Se till att alla filer ligger i samma mapp.")
    exit()

def animate_optimization():
    print("Kör optimerare och hämtar historik...")
    
    # Hämta historik
    history = run_optimizer(return_history=True)
    
    if not history:
        print("Ingen historik mottagen. Kontrollera 'homevolt_optimizer.py'.")
        return

    print(f"Laddade {len(history)} steg.")
    print("Fönster öppnat: Klicka med musen för att bläddra.")

    # Konstanter
    bias_w = GRID_SETPOINT_BIAS_W
    monthly_peak_w = PREVIOUS_MONTHLY_HOUR_PEAK_POWER_KW * 1000.0
    
    # Skapa figur - Lite högre för att ge plats åt legender
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 12), sharex=True)
    plt.subplots_adjust(hspace=0.3) # Mer luft mellan graferna
    
    # State container
    state = {'idx': 0}

    def draw_frame(frame_idx):
        ax1.clear()
        ax2.clear()
        
        snapshot = history[frame_idx]
        timeline = snapshot["timeline"]
        title_step = snapshot["title"]
        
        # Data prep
        times = []
        grid_power = []
        load_power = []
        soc_pct = []
        prices = []
        
        # Förbereda tim-aggregering för denna frame
        hourly_data = {} 
        
        for item in timeline:
            t_str = item["time"]
            dt = datetime.strptime(t_str, "%H:%M")
            times.append(dt)
            
            actual_grid_w = (item["grid_wh"] * 4.0) + bias_w
            net_load_w = (item["cons_wh"] * 4.0) - (item["solar_wh"] * 4.0)
            
            grid_power.append(actual_grid_w)
            load_power.append(net_load_w)
            
            soc = (item["batt_wh"] / BATTERY_ENERGY_WH) * 100.0
            soc_pct.append(soc)
            prices.append(item["price"])

            # Samla data för tim-snitt
            h_key = dt.strftime("%Y-%m-%d %H")
            if h_key not in hourly_data: hourly_data[h_key] = {"vals": [], "dt": dt}
            hourly_data[h_key]["vals"].append(actual_grid_w)

        # Beräkna tim-staplar
        hourly_times = []
        hourly_avgs = []
        for k, v in hourly_data.items():
            clean_dt = v["dt"].replace(minute=0)
            hourly_times.append(clean_dt)
            avg = sum(v["vals"]) / len(v["vals"])
            hourly_avgs.append(avg)
            
        # --- GRAF 1 ---
        step_info = f"Steg {frame_idx+1} av {len(history)}"
        # Pad title för att ge plats åt legend
        ax1.set_title(f"{step_info}: {title_step}", fontsize=16, fontweight='bold', color='darkblue', pad=40)
        ax1.set_ylabel("Effekt (W)", fontsize=12)
        ax1.grid(True, linestyle='--', alpha=0.5)
        
        # 1a. Rita Tim-snitt i bakgrunden
        hour_width = (1.0 / 24.0) * 0.98
        ax1.bar(hourly_times, hourly_avgs, width=hour_width, color=COLOR_HOURLY_AVG, alpha=0.3, label="Tim-medel", align='edge')

        # 1b. Rita Konturlinje för Tim-snitt
        if hourly_times:
            times_line = hourly_times + [hourly_times[-1] + timedelta(hours=1)]
            avgs_line = hourly_avgs + [hourly_avgs[-1]]
            ax1.step(times_line, avgs_line, where='post', color=COLOR_HOURLY_AVG_LINE, linewidth=1.5, label='_nolegend_')

        # 2. Husets Last
        ax1.step(times, load_power, color=COLOR_LOAD_LINE, alpha=0.3, where='post', label="Husets Last")
        ax1.fill_between(times, load_power, step='post', color=COLOR_LOAD_LINE, alpha=0.1)
        
        # 3. Nät (Kvartar)
        colors = [COLOR_GRID_IMPORT if p > 0 else COLOR_GRID_EXPORT for p in grid_power]
        width = 1.0/96.0
        ax1.bar(times, grid_power, width=width, color=colors, alpha=0.8, label="Kvart (Nät)", align='edge')
        
        # 4. Månadstopp
        ax1.axhline(y=monthly_peak_w, color=COLOR_PEAK_LIMIT_LINE, linestyle='--', linewidth=2, label="Månadstopp")
        
        # 5. Pris (Höger axel)
        ax1_p = ax1.twinx()
        ax1_p.set_ylabel("Elpris (öre)", color=COLOR_PRICE_LINE)
        ax1_p.step(times, prices, color=COLOR_PRICE_LINE, where='post', linewidth=1.5, alpha=0.6, label="Elpris")
        ax1_p.tick_params(axis='y', labelcolor=COLOR_PRICE_LINE)
        
        # --- LEGEND 1 (Vänsterställd & Mindre) ---
        h1, l1 = ax1.get_legend_handles_labels()
        h2, l2 = ax1_p.get_legend_handles_labels()
        
        ax1.legend(h1+h2, l1+l2, 
                   loc='lower left', bbox_to_anchor=(0.0, 1.02), 
                   ncol=4, frameon=True, fontsize=9, handlelength=1.5)
        
        # Instruktionstext
        ax1.text(0.02, 0.90, "Klicka för att bläddra", transform=ax1.transAxes, 
                 fontsize=8, color='gray', bbox=dict(facecolor='white', alpha=0.8, edgecolor='none'))

        # --- GRAF 2 ---
        ax2.set_ylabel("Batteri SoC (%)", fontsize=12, color=COLOR_BATTERY_LINE)
        ax2.set_ylim(0, 105)
        ax2.grid(True, linestyle='--', alpha=0.5)
        
        ax2.plot(times, soc_pct, color=COLOR_BATTERY_LINE, linewidth=2, label="Batterinivå")
        ax2.fill_between(times, soc_pct, color=COLOR_BATTERY_LINE, alpha=0.1)
        
        ax2.axhline(y=15, color='red', linestyle=':', linewidth=1, alpha=0.5, label="Low Power Limit (15%)")
        
        # --- LEGEND 2 (Vänsterställd & Mindre) ---
        ax2.legend(loc='lower left', bbox_to_anchor=(0.0, 1.02), 
                   ncol=2, frameon=True, fontsize=9)
        
        ax2.set_xlabel("Tid", fontsize=12)
        ax2.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        fig.autofmt_xdate()
        
        fig.canvas.draw()

    # Event Handler
    def on_click(event):
        if event.button == 1: # Vänsterklick
            state['idx'] = (state['idx'] + 1) % len(history)
        elif event.button == 3: # Högerklick
            state['idx'] = (state['idx'] - 1) % len(history)
        draw_frame(state['idx'])

    def on_key(event):
        if event.key == 'right' or event.key == ' ':
            state['idx'] = (state['idx'] + 1) % len(history)
        elif event.key == 'left':
            state['idx'] = (state['idx'] - 1) % len(history)
        draw_frame(state['idx'])

    fig.canvas.mpl_connect('button_press_event', on_click)
    fig.canvas.mpl_connect('key_press_event', on_key)

    draw_frame(0)
    plt.show()

if __name__ == "__main__":
    animate_optimization()