# -*- coding: utf-8 -*-
# ==============================================================================
# Homevolt Battery Optimizer
# Version: v4.0 (Animation Support)
# ==============================================================================

import requests
import math
import sys
import copy
from datetime import datetime, timedelta

# Explicit import av inställningar
try:
    from homevolt_optimizer_settings import (
        PRICE_AREA,
        TARGET_DATE_STR,
        OPTIMIZE_FROM_TIME,
        OPTIMIZE_OVERRIDE_CURRENT_SOC_KWH,
        BATTERY_CAPACITY_KWH,
        BATTERY_ENERGY_WH,
        START_BATTERY_WH,
        MAX_BATTERY_OUTPUT_W,
        MAX_BATTERY_OUTPUT_PER_QUARTER_WH,
        LOW_SOC_THROTTLE_PERCENT,
        LOW_SOC_THROTTLE_WH,
        LOW_SOC_MAX_OUTPUT_W,
        LOW_SOC_MAX_OUTPUT_PER_QUARTER_WH,
        ADDITIONAL_FEES_TAX_ORE,
        ADDITIONAL_FEES_GRID_ORE,
        GRID_SETPOINT_BIAS_W,
        BIAS_WH_PER_QUARTER,
        MIN_BATTERY_BUFFER_PERCENT,
        MIN_BATTERY_BUFFER_WH,
        HOUSE_CONSUMPTION_FUDGE_FACTOR,
        EXPECTED_TOTAL_CONSUMPTION_KWH,
        CONSUMPTION_SANITY_TOLERANCE_PCT,
        TRY_KEEP_HOURS_BELOW_WH,
        PREVIOUS_MONTHLY_HOUR_PEAK_POWER_KW,
        ENABLE_ARBITRAGE,
        MIN_PRICE_DIFF_ORE_FOR_SELLING,
        DEFAULT_INITIAL_PRICE_ORE,
        BATTERY_CYCLE_COST_ORE,
        SIMULATION_START_TIME_DEFAULT,
        SIMULATION_END_TIME,
        NIGHT_CHARGE_WINDOW_START,
        NIGHT_CHARGE_WINDOW_END,
        CHARGE_DURATION_HOURS,
        USE_COLORED_OUTPUT,
        SHOW_DETAILED_TABLE,
        CONSUMPTION_SCHEDULE_RAW,
        SOLAR_PRODUCTION_SCHEDULE_RAW
    )
except ImportError:
    print("CRITICAL ERROR: Could not find 'homevolt_optimizer_settings.py'")
    sys.exit(1)

def print_flush(text):
    print(text)
    sys.stdout.flush()

print_flush("--- Startar optimeringsskript (v4.0 - Animation Ready) ---")

# ==============================================================================
# 0. DERIVED CONSTANTS
# ==============================================================================

START_BATTERY_WH_DEFAULT = BATTERY_ENERGY_WH 
TOTAL_ADDITIONAL_FEES = ADDITIONAL_FEES_TAX_ORE + ADDITIONAL_FEES_GRID_ORE
PREVIOUS_PEAK_WH = PREVIOUS_MONTHLY_HOUR_PEAK_POWER_KW * 1000.0

# ==============================================================================
# 1. VISUALS & HELPERS
# ==============================================================================

class Colors:
    RED = '\033[91m'    
    GREEN = '\033[92m'  
    BLUE = '\033[94m'   
    YELLOW = '\033[93m' 
    CYAN = '\033[96m'   
    GRAY = '\033[37m'   
    RESET = '\033[0m'   

def get_row_color(batt_net_w, grid_w):
    if not USE_COLORED_OUTPUT: return ""
    if grid_w < -0.1: return Colors.BLUE    
    if batt_net_w > 0.1: return Colors.RED  
    elif batt_net_w < -0.1: return Colors.GREEN 
    elif grid_w > 0.1: return Colors.GRAY   
    else: return ""

def colorize(text, color_code):
    if not USE_COLORED_OUTPUT or not color_code: return text
    return f"{color_code}{text}{Colors.RESET}"

def round_time_down_to_quarter(time_str):
    if not time_str: return None
    try:
        h, m = map(int, time_str.split(":"))
        if m < 15: m = 0
        elif m < 30: m = 15
        elif m < 45: m = 30
        else: m = 45
        return f"{h:02d}:{m:02d}"
    except:
        return None

# ==============================================================================
# 2. API FETCHING
# ==============================================================================

def fetch_prices_as_quarters(date_str, area):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    url = f"https://www.elprisetjustnu.se/api/v1/prices/{dt.year}/{dt.strftime('%m-%d')}_{area}.json"
    print_flush(f"Fetching prices from: {url}")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print_flush(f"Error fetching prices: {e}")
        return None

    raw_prices = {}
    for entry in data:
        ts_str = entry['time_start']
        dt_obj = datetime.fromisoformat(ts_str)
        time_key = dt_obj.strftime("%H:%M")
        price_ore = entry['SEK_per_kWh'] * 100
        raw_prices[time_key] = price_ore

    quarter_prices = {}
    for hour in range(24):
        for minute in [0, 15, 30, 45]:
            quarter_key = f"{hour:02d}:{minute:02d}"
            hour_key = f"{hour:02d}:00"
            if quarter_key in raw_prices:
                quarter_prices[quarter_key] = raw_prices[quarter_key]
            elif hour_key in raw_prices:
                quarter_prices[quarter_key] = raw_prices[hour_key]
            else:
                quarter_prices[quarter_key] = 0.0
    return quarter_prices

# ==============================================================================
# 3. DATA SETUP
# ==============================================================================

def setup_timeline():
    # Live Mode Logic
    current_battery_wh = START_BATTERY_WH_DEFAULT
    sim_start_time = SIMULATION_START_TIME_DEFAULT

    if OPTIMIZE_FROM_TIME:
        rounded_start = round_time_down_to_quarter(OPTIMIZE_FROM_TIME)
        if rounded_start:
            sim_start_time = rounded_start
            print_flush(colorize(f"LIVE MODE: Starting from {rounded_start} (Input: {OPTIMIZE_FROM_TIME})", Colors.YELLOW))
    
    if OPTIMIZE_OVERRIDE_CURRENT_SOC_KWH is not None:
        current_battery_wh = OPTIMIZE_OVERRIDE_CURRENT_SOC_KWH * 1000
        pct = (current_battery_wh / BATTERY_ENERGY_WH) * 100
        print_flush(colorize(f"LIVE MODE: Initial SoC Override: {pct:.1f}% ({OPTIMIZE_OVERRIDE_CURRENT_SOC_KWH} kWh)", Colors.YELLOW))

    target_date = TARGET_DATE_STR if TARGET_DATE_STR else datetime.now().strftime("%Y-%m-%d")
    all_day_prices = fetch_prices_as_quarters(target_date, PRICE_AREA)
    if not all_day_prices: return None, None, None

    # Determine Start Price
    night_prices = [p for t, p in all_day_prices.items() if NIGHT_CHARGE_WINDOW_START <= t < NIGHT_CHARGE_WINDOW_END]
    
    quarters_needed = int(CHARGE_DURATION_HOURS * 4)
    
    if len(night_prices) >= quarters_needed:
        night_prices.sort() 
        cheapest_quarters = night_prices[:quarters_needed]
        spot_avg = sum(cheapest_quarters) / len(cheapest_quarters)
        base_price = spot_avg + TOTAL_ADDITIONAL_FEES
        print_flush(f"Start Energy Cost: {base_price:.2f} ore/kWh (Based on cheapest {CHARGE_DURATION_HOURS}h night)")
    else:
        base_price = DEFAULT_INITIAL_PRICE_ORE
        print_flush(f"Start Energy Cost: {base_price:.2f} ore/kWh (Default fallback)")

    elpriser_filtered = {k: v for k, v in all_day_prices.items() if sim_start_time <= k <= SIMULATION_END_TIME}
    valid_quarters = sorted([t for t in elpriser_filtered.keys() if t < SIMULATION_END_TIME])
    
    timeline = []
    cons_watts = 0
    solar_watts = 0
    
    sorted_schedule_keys = sorted(list(set(list(CONSUMPTION_SCHEDULE_RAW.keys()) + list(SOLAR_PRODUCTION_SCHEDULE_RAW.keys()) + valid_quarters)))
    
    for t in sorted_schedule_keys:
        if t in CONSUMPTION_SCHEDULE_RAW:
            cons_watts = CONSUMPTION_SCHEDULE_RAW[t] * HOUSE_CONSUMPTION_FUDGE_FACTOR
        if t in SOLAR_PRODUCTION_SCHEDULE_RAW:
            solar_watts = SOLAR_PRODUCTION_SCHEDULE_RAW[t]
        
        if t in valid_quarters:
            q_idx = valid_quarters.index(t)
            timeline.append({
                "index": q_idx,
                "time": t,
                "hour": t.split(":")[0],
                "price": elpriser_filtered[t],
                "cons_wh": cons_watts / 4.0,
                "solar_wh": solar_watts / 4.0,
                "grid_wh": 0.0, 
                "batt_wh": 0.0, 
                "base_net_load_wh": (cons_watts / 4.0) - (solar_watts / 4.0)
            })

    return timeline, current_battery_wh, base_price

def run_sanity_check():
    start_h, start_m = map(int, SIMULATION_START_TIME_DEFAULT.split(":"))
    end_h, end_m = map(int, SIMULATION_END_TIME.split(":"))
    start_mins = start_h * 60 + start_m
    end_mins = end_h * 60 + end_m
    total_wh = 0
    for m in range(start_mins, end_mins, 15):
        h_curr = m // 60
        m_curr = m % 60
        time_str = f"{h_curr:02d}:{m_curr:02d}"
        current_power = 0
        for t_sched in sorted(CONSUMPTION_SCHEDULE_RAW.keys()):
            if t_sched <= time_str:
                current_power = CONSUMPTION_SCHEDULE_RAW[t_sched]
            else: break
        power_with_fudge = current_power * HOUSE_CONSUMPTION_FUDGE_FACTOR
        total_wh += (power_with_fudge / 4.0)
    total_kwh = total_wh / 1000.0
    
    if EXPECTED_TOTAL_CONSUMPTION_KWH > 0:
        diff_pct = ((total_kwh - EXPECTED_TOTAL_CONSUMPTION_KWH) / EXPECTED_TOTAL_CONSUMPTION_KWH) * 100.0
    else:
        diff_pct = 0.0

    print_flush(f"Sanity Check ({SIMULATION_START_TIME_DEFAULT}-{SIMULATION_END_TIME}):")
    print_flush(f"  Cons: {total_kwh:.2f} kWh ({diff_pct:+.1f}%)")
    if abs(diff_pct) > CONSUMPTION_SANITY_TOLERANCE_PCT:
        print_flush(colorize("\nWARNING: High consumption deviation!\n", Colors.RED))
    print_flush("-" * 60)

# ==============================================================================
# 4. CORE ALGORITHM FUNCTIONS
# ==============================================================================

def calculate_min_peak_limit(timeline, available_energy_wh):
    low = 0.0; high = 10000.0; best = high
    for _ in range(20):
        mid = (low + high) / 2.0; possible = True
        for item in timeline:
            usable = mid - BIAS_WH_PER_QUARTER
            if usable < 0: usable = 0
            needed = item["base_net_load_wh"] - usable
            if needed < 0: needed = 0
            if needed > MAX_BATTERY_OUTPUT_PER_QUARTER_WH: possible = False; break
        
        total_needed = sum(max(0, x["base_net_load_wh"] - (mid - BIAS_WH_PER_QUARTER)) for x in timeline)
        if possible and total_needed <= available_energy_wh:
            best = mid; high = mid
        else: low = mid
    return best

def calculate_battery_profile_from_list(soc_start, grid_cmds, timeline):
    curr = soc_start; profile = []
    for i, g in enumerate(grid_cmds):
        actual = g + BIAS_WH_PER_QUARTER
        change = actual - timeline[i]["base_net_load_wh"]
        curr += change
        if curr > BATTERY_ENERGY_WH: curr = BATTERY_ENERGY_WH
        profile.append(curr)
    return profile

def get_hourly_usage_map(timeline):
    u = {}
    for x in timeline:
        h = x["hour"]; g = x["grid_wh"] + BIAS_WH_PER_QUARTER
        if g > 0: u[h] = u.get(h, 0) + g
    return u

def distribute_smart_safety_fill(timeline, needed, end_index, current_battery_wh):
    hourly_usage = get_hourly_usage_map(timeline)

    def get_candidates(ignore_peak_limit=False):
        cands = []
        temp_grid = [x["grid_wh"] for x in timeline]
        base_profile = calculate_battery_profile_from_list(current_battery_wh, temp_grid, timeline)
        for i in range(end_index + 1):
            item = timeline[i]
            rel = base_profile[i : end_index + 1]
            max_s = max(rel) if rel else BATTERY_ENERGY_WH
            cap_h = BATTERY_ENERGY_WH - max_s
            if cap_h <= 1.0: continue
            
            if not ignore_peak_limit:
                peak_h = PREVIOUS_PEAK_WH - hourly_usage.get(item["hour"], 0)
                if peak_h <= 1.0: continue
                act_h = min(cap_h, peak_h)
            else:
                act_h = min(cap_h, MAX_BATTERY_OUTPUT_PER_QUARTER_WH)
            
            cands.append({"index": i, "price": item["price"], "headroom": act_h, "hour": item["hour"]})
        return cands

    # Pass 1
    candidates = get_candidates(False)
    candidates.sort(key=lambda x: x["price"])
    rem = needed
    for c in candidates:
        if rem <= 0: break
        take = min(rem, c["headroom"])
        timeline[c["index"]]["grid_wh"] += take
        hourly_usage[c["hour"]] += take
        rem -= take
    
    # Pass 2 (Fallback)
    if rem > 0:
        cands2 = get_candidates(True)
        cands2.sort(key=lambda x: x["price"])
        for c in cands2:
            if rem <= 0: break
            take = min(rem, c["headroom"])
            timeline[c["index"]]["grid_wh"] += take
            rem -= take

    if rem > 0:
        timeline[0]["grid_wh"] += rem

def phase_1_peak_shaving(timeline, current_battery_wh):
    usable = current_battery_wh - MIN_BATTERY_BUFFER_WH
    if usable < 0: usable = 0
    
    if TRY_KEEP_HOURS_BELOW_WH is not None:
        optimal = TRY_KEEP_HOURS_BELOW_WH / 4.0
        print_flush(colorize(f"Using Manual Import Limit: {optimal:.1f} Wh/q", Colors.CYAN))
    else:
        optimal = calculate_min_peak_limit(timeline, usable)
        print_flush(colorize(f"Calculated Min Necessary Import: {optimal:.1f} Wh/q", Colors.CYAN))
    
    monthly_peak = PREVIOUS_PEAK_WH / 4.0
    warning_flag = False
    if optimal > monthly_peak:
        print_flush(colorize(f"WARNING: New Peak Expected! (+{(optimal-monthly_peak)*4:.0f} W)", Colors.RED))
        warning_flag = True
    else:
        print_flush(colorize(f"Safe! Below previous peak.", Colors.GREEN))

    curr_soc = current_battery_wh
    for item in timeline:
        use_grid = optimal - BIAS_WH_PER_QUARTER
        if use_grid < 0: use_grid = 0
        dis = item["base_net_load_wh"] - use_grid
        if dis < 0: dis = 0
        if dis > MAX_BATTERY_OUTPUT_PER_QUARTER_WH: dis = MAX_BATTERY_OUTPUT_PER_QUARTER_WH
        if curr_soc - dis < MIN_BATTERY_BUFFER_WH: dis = max(0, curr_soc - MIN_BATTERY_BUFFER_WH)
        
        curr_soc -= dis
        item["grid_wh"] = (item["base_net_load_wh"] - dis) - BIAS_WH_PER_QUARTER
        item["batt_wh"] = curr_soc
        
    return optimal, warning_flag

def phase_2_safety_checks(timeline, current_battery_wh):
    safety_iter = 0
    while safety_iter < 10:
        safety_iter += 1
        violation = False
        temp_grid = [x["grid_wh"] for x in timeline]
        profile = calculate_battery_profile_from_list(current_battery_wh, temp_grid, timeline)
        
        for i, item in enumerate(timeline):
            start_soc = current_battery_wh if i == 0 else profile[i-1]
            act_grid = item["grid_wh"] + BIAS_WH_PER_QUARTER
            dis = item["base_net_load_wh"] - act_grid
            
            if dis > LOW_SOC_MAX_OUTPUT_PER_QUARTER_WH:
                if start_soc < LOW_SOC_THROTTLE_WH:
                    needed = LOW_SOC_THROTTLE_WH - start_soc + 1.0
                    distribute_smart_safety_fill(timeline, needed, i, current_battery_wh)
                    violation = True; break
        if not violation: break

def phase_smart_fill_cheap_hours(timeline, current_battery_wh, limit_wh):
    """Fills cheap hours up to limit_wh to save battery. No net charging."""
    print_flush("Running Smart Fill (Reduce Discharge on Cheap Hours)...")
    if timeline:
        avg_p = sum(x['price'] for x in timeline) / len(timeline)
    else: avg_p = 0
    cheap_candidates = [x for x in timeline if x["price"] < avg_p]
    cheap_candidates.sort(key=lambda x: x["price"])
    hourly_usage = get_hourly_usage_map(timeline)
    
    def check_capacity():
        tg = [x["grid_wh"] for x in timeline]
        prof = calculate_battery_profile_from_list(current_battery_wh, tg, timeline)
        return max(prof) <= BATTERY_ENERGY_WH

    for item in cheap_candidates:
        curr_h_sum = hourly_usage.get(item["hour"], 0)
        room_to_peak = limit_wh - curr_h_sum
        if room_to_peak < 1.0: continue
        
        curr_grid = item["grid_wh"] + BIAS_WH_PER_QUARTER
        net_load = item["base_net_load_wh"]
        room_to_load = net_load - curr_grid
        if room_to_load < 1.0: continue
        
        take = min(room_to_peak, room_to_load)
        item["grid_wh"] += take
        hourly_usage[item["hour"]] += take
        
        if not check_capacity():
            item["grid_wh"] -= take
            hourly_usage[item["hour"]] -= take

def phase_optimize_within_hours(timeline, current_battery_wh):
    print_flush("Running Intra-Hour Optimization (Cost Shaving)...")
    unique_hours = sorted(list(set([t["hour"] for t in timeline])))
    current_soc = current_battery_wh
    
    for hour in unique_hours:
        indices = [i for i, x in enumerate(timeline) if x["hour"] == hour]
        if not indices: continue
        quarters = [timeline[i] for i in indices]
        
        hour_total_grid = sum(q["grid_wh"] + BIAS_WH_PER_QUARTER for q in quarters)
        if hour_total_grid <= 0: 
            for i in indices:
                g = timeline[i]["grid_wh"] + BIAS_WH_PER_QUARTER
                d = timeline[i]["base_net_load_wh"] - g
                current_soc -= d
            continue

        quarters.sort(key=lambda x: x["price"])
        allocated = {q["index"]: 0.0 for q in quarters}
        remaining_budget = hour_total_grid
        
        for q in quarters:
            max_take = max(0, q["base_net_load_wh"])
            take = min(remaining_budget, max_take)
            allocated[q["index"]] = take
            remaining_budget -= take
            
        if remaining_budget > 0:
            for q in quarters: 
                curr_val = allocated[q["index"]]
                max_grid_charge = q["base_net_load_wh"] + MAX_BATTERY_OUTPUT_PER_QUARTER_WH
                room = max_grid_charge - curr_val
                if room > 0:
                    take = min(remaining_budget, room)
                    allocated[q["index"]] += take
                    remaining_budget -= take
                if remaining_budget <= 0: break

        original_grids = {i: timeline[i]["grid_wh"] for i in indices}
        for i in indices: timeline[i]["grid_wh"] = allocated[i] - BIAS_WH_PER_QUARTER
            
        temp_soc = current_soc
        valid = True
        sorted_indices = sorted(indices)
        for i in sorted_indices:
            act = timeline[i]["grid_wh"] + BIAS_WH_PER_QUARTER
            dis = timeline[i]["base_net_load_wh"] - act
            if dis > MAX_BATTERY_OUTPUT_PER_QUARTER_WH: valid = False
            if temp_soc < LOW_SOC_THROTTLE_WH and dis > LOW_SOC_MAX_OUTPUT_PER_QUARTER_WH: valid = False
            if temp_soc - dis < MIN_BATTERY_BUFFER_WH: valid = False
            temp_soc -= dis
            if temp_soc > BATTERY_ENERGY_WH: temp_soc = BATTERY_ENERGY_WH
        
        if valid:
            current_soc = temp_soc
        else:
            for i in indices: timeline[i]["grid_wh"] = original_grids[i]
            for i in indices:
                act = timeline[i]["grid_wh"] + BIAS_WH_PER_QUARTER
                dis = timeline[i]["base_net_load_wh"] - act
                current_soc -= dis

def phase_3_price_optimization(timeline, current_battery_wh, swap_ceiling_wh):
    print_flush("Running Price Optimization (Energy Swap)...")
    curr_max = 0
    for x in timeline:
         g = x["grid_wh"] + BIAS_WH_PER_QUARTER
         if g > curr_max: curr_max = g
    if curr_max > swap_ceiling_wh: swap_ceiling_wh = curr_max
    
    swap_iter = 0
    while swap_iter < 500:
        swap_iter += 1
        best_swap = None; max_spread = 0
        temp_grid = [x["grid_wh"] for x in timeline]
        curr_prof = calculate_battery_profile_from_list(current_battery_wh, temp_grid, timeline)
        
        for t_c, item_c in enumerate(timeline):
            act_c = item_c["grid_wh"] + BIAS_WH_PER_QUARTER
            dis_c = item_c["base_net_load_wh"] - act_c
            if dis_c <= 0: continue
            if act_c >= swap_ceiling_wh: continue
            
            for t_e, item_e in enumerate(timeline):
                if t_c == t_e: continue
                p_diff = item_e["price"] - item_c["price"]
                if p_diff < BATTERY_CYCLE_COST_ORE: continue
                
                act_e = item_e["grid_wh"] + BIAS_WH_PER_QUARTER
                dis_e = item_e["base_net_load_wh"] - act_e
                if dis_e >= MAX_BATTERY_OUTPUT_PER_QUARTER_WH: continue
                start_soc_e = current_battery_wh if t_e==0 else curr_prof[t_e-1]
                if start_soc_e < LOW_SOC_THROTTLE_WH and dis_e >= LOW_SOC_MAX_OUTPUT_PER_QUARTER_WH: continue
                
                if t_c > t_e: continue 
                path_ok = True
                for k in range(t_c, t_e):
                    if curr_prof[k] >= BATTERY_ENERGY_WH - 1.0: path_ok = False; break
                if not path_ok: continue
                
                if p_diff > max_spread: max_spread = p_diff; best_swap = (t_c, t_e)
        
        if not best_swap: break
        s, d = best_swap
        timeline[s]["grid_wh"] += 50.0; timeline[d]["grid_wh"] -= 50.0

def phase_4_active_arbitrage(timeline, current_battery_wh, base_price):
    print_flush("Running Active Arbitrage (Charge to Sell)...")
    hourly_usage = get_hourly_usage_map(timeline)
    
    arb_iter = 0
    while arb_iter < 500:
        arb_iter += 1
        best_arb = None; max_prof = 0
        temp_grid = [x["grid_wh"] for x in timeline]
        curr_prof = calculate_battery_profile_from_list(current_battery_wh, temp_grid, timeline)
        
        for t_b, item_b in enumerate(timeline):
            curr_h = hourly_usage.get(item_b["hour"], 0)
            if PREVIOUS_PEAK_WH - curr_h < 50.0: continue
            start_soc_b = current_battery_wh if t_b==0 else curr_prof[t_b-1]
            if start_soc_b >= BATTERY_ENERGY_WH - 1.0: continue
            
            for t_s, item_s in enumerate(timeline):
                if t_b >= t_s: continue
                buy_c = item_b["price"] + TOTAL_ADDITIONAL_FEES
                sell_r = item_s["price"]
                profit = sell_r - buy_c - BATTERY_CYCLE_COST_ORE
                if profit < MIN_PRICE_DIFF_ORE_FOR_SELLING: continue
                
                act_s = item_s["grid_wh"] + BIAS_WH_PER_QUARTER
                dis_s = item_s["base_net_load_wh"] - act_s
                if dis_s + 50.0 > MAX_BATTERY_OUTPUT_PER_QUARTER_WH: continue
                start_soc_s = curr_prof[t_s-1]
                if start_soc_s < LOW_SOC_THROTTLE_WH:
                    if (dis_s + 50.0) > LOW_SOC_MAX_OUTPUT_PER_QUARTER_WH: continue
                
                path_ok = True
                for k in range(t_b, t_s):
                    if curr_prof[k] + 50.0 > BATTERY_ENERGY_WH: path_ok = False; break
                if not path_ok: continue
                
                if profit > max_prof: max_prof = prof; best_arb = (t_b, t_s)
        
        if not best_arb: break
        b, s = best_arb
        timeline[b]["grid_wh"] += 50.0; timeline[s]["grid_wh"] -= 50.0
        hourly_usage[timeline[b]["hour"]] += 50.0

def generate_reports(timeline, current_battery_wh, target_date, target_limit, optimal_limit, warning_flag):
    final_grid_cmds = [x["grid_wh"] for x in timeline]
    final_soc_profile = calculate_battery_profile_from_list(current_battery_wh, final_grid_cmds, timeline)
    for i, item in enumerate(timeline): item["batt_wh"] = final_soc_profile[i]
    
    if timeline:
        avg_p_day = sum(x['price'] for x in timeline) / len(timeline)
    else: avg_p_day = 0
    
    hourly_prices = {}
    for item in timeline:
        h = item["hour"]
        if h not in hourly_prices: hourly_prices[h] = []
        hourly_prices[h].append(item["price"])

    if SHOW_DETAILED_TABLE:
        print_flush("\n" + "="*165)
        print_flush(f"{'Time':<6} {'Price':<6} {'Load(W)':<8} {'Solar(W)':<8} {'Grid(W)':<8} {'Batt%':<6} {'Batt(W)':<8} {'Cost':<9} {'Grid Hour Acc(Wh)':<18} {'Mode':<24} {'Params'}")
        print_flush("-" * 165)
        
        current_hour = -1
        hour_acc = 0
        mode_str = "Charge/Discharge grid"
        
        for i, item in enumerate(timeline):
            this_h = int(item["hour"])
            if i > 0 and this_h != int(timeline[i-1]["hour"]):
                print_flush("-" * 165)

            act_grid = item["grid_wh"] + BIAS_WH_PER_QUARTER
            grid_w = item["grid_wh"] * 4
            act_w = act_grid * 4
            cons_w = item["cons_wh"] * 4
            sol_w = item["solar_wh"] * 4
            batt_net = act_w + sol_w - cons_w
            val = int(grid_w)
            p_str = f'{{"setpoint":{val}}}' if batt_net > 0.1 else f'{{"setpoint":{val}, "import_limitation":1}}'
            cost = (act_grid / 1000) * item["price"]
            soc = int((item["batt_wh"] / BATTERY_ENERGY_WH) * 100)
            grid_str = f"{int(act_w)}"
            batt_w_str = f"{int(batt_net):+d}"
            
            p_str_col = f"{item['price']:<6.2f}"
            if item['price'] < avg_p_day: p_str_col = colorize(p_str_col, Colors.GREEN)
            else: p_str_col = colorize(p_str_col, Colors.RED)
            
            if this_h != current_hour:
                current_hour = this_h
                hour_acc = 0
            if act_grid > 0: hour_acc += act_grid
            acc_str = f"{int(hour_acc)}"

            s = f"{item['time']:<6} {p_str_col} {int(cons_w):<8} {int(sol_w):<8} {grid_str:<8} {soc:<6} {batt_w_str:<8} {cost:>6.2f} ore  {acc_str:<18} {mode_str:<24} {p_str}"
            print_flush(colorize(s, get_row_color(batt_net, act_w)))

    print_flush("\n" + "="*148)
    print_flush(f"{'Time':<17} {'Price Ore':<10} {'Mode':<24} {'Params':<48} {'Batt SOC%':<14} {'Cost':>12} {'Cost/h':>12}")
    print_flush("="*148)

    agg_rows = []
    curr_block = None
    hourly_imp = {}

    for item in timeline:
        act_grid = item["grid_wh"] + BIAS_WH_PER_QUARTER
        h = item["hour"]
        if h not in hourly_imp: hourly_imp[h] = 0
        if act_grid > 0: hourly_imp[h] += act_grid
        
        grid_w = item["grid_wh"] * 4
        act_w = act_grid * 4
        batt_net = act_w + item["solar_wh"] * 4 - item["cons_wh"] * 4
        val = int(grid_w)
        p_str = f'{{"setpoint":{val}}}' if batt_net > 0.1 else f'{{"setpoint":{val}, "import_limitation":1}}'
        cost = (act_grid / 1000) * item["price"]
        price = item["price"]
        soc_end = int((item["batt_wh"] / BATTERY_ENERGY_WH) * 100)
        change = batt_net / 4.0
        soc_start = int(((item["batt_wh"] - change) / BATTERY_ENERGY_WH) * 100)
        
        h_t, m_t = map(int, item["time"].split(":"))
        m_t += 15
        if m_t == 60: h_t += 1; m_t = 0
        end_t = f"{h_t:02d}:{m_t:02d}"
        color = get_row_color(batt_net, act_w)
        
        if curr_block and curr_block['params'] == p_str and curr_block['color'] == color:
            curr_block['end_time'] = end_t; curr_block['soc_end'] = soc_end
            curr_block['cost'] += cost; curr_block['price_sum'] += price; curr_block['count'] += 1
        else:
            if curr_block: agg_rows.append(curr_block)
            curr_block = {'start_time': item["time"], 'end_time': end_t, 'mode': "Charge/Discharge grid", 'params': p_str, 'soc_start': soc_start, 'soc_end': soc_end, 'cost': cost, 'price_sum': price, 'count': 1, 'color': color}
    if curr_block: agg_rows.append(curr_block)

    for r in agg_rows:
        avg_p = r['price_sum'] / r['count']
        dur = r['count'] / 4.0
        cph = r['cost']/dur if dur > 0 else 0
        tr = f"{r['start_time']} - {r['end_time']}"
        soc = f"{r['soc_start']}% -> {r['soc_end']}%"
        
        p_str_col = f"{avg_p:<10.1f}"
        if avg_p < avg_p_day: p_str_col = colorize(p_str_col, Colors.GREEN)
        else: p_str_col = colorize(p_str_col, Colors.RED)
        
        s = f"{tr:<17} {p_str_col} {r['mode']:<24} {r['params']:<48} {soc:<14} {r['cost']:>8.2f} ore {cph:>8.0f} ore/h"
        print_flush(colorize(s, r['color']))

    print_flush("="*148)
    tot = sum(r['cost'] for r in agg_rows)
    print_flush(f"TOTAL COST (Aggregated): {tot/100:.2f} kr")

    print_flush("\n" + "="*75)
    target = PREVIOUS_PEAK_WH
    print_flush(f"   HOURLY IMPORT CHECK (Limit: {target:.0f} Wh)    |  AVG HOUR PRICE")
    print_flush("="*75)
    for h in sorted(hourly_imp.keys()):
        w = hourly_imp[h]
        if h in hourly_prices:
            avg_h_p = sum(hourly_prices[h]) / len(hourly_prices[h])
        else: avg_h_p = 0
        p_str = f"{avg_h_p:.1f} ore"
        if avg_h_p < avg_p_day: p_str = colorize(p_str, Colors.GREEN)
        else: p_str = colorize(p_str, Colors.RED)
        
        if w <= target + 5:
            stat = colorize("OK", Colors.GREEN)
        else:
            diff = w - target
            stat = colorize(f"EXCEEDED (+{w-target:.0f})", Colors.RED)
        print_flush(f"Hour {h}: {w:>5.0f} Wh  ->  {stat:<20} |  {p_str}")
    print_flush("="*75)
    
    # Capture state for animator
    history_entry = {
        "title": "Final Plan",
        "timeline": copy.deepcopy(timeline),
        "battery_wh": current_battery_wh,
        "peak_limit": target
    }
    return [history_entry] # Return list for animation compatibility

# ==============================================================================
# 5. CONTROLLER
# ==============================================================================

def run_optimizer(return_history=False):
    timeline, current_battery_wh, base_price = setup_timeline()
    if not timeline: return [] if return_history else None

    run_sanity_check()
    
    history = []
    
    def save_snapshot(name):
        if return_history:
            # Re-calc battery profile for display consistency in snapshot
            grid_cmds = [x["grid_wh"] for x in timeline]
            prof = calculate_battery_profile_from_list(current_battery_wh, grid_cmds, timeline)
            # Create a deep copy of timeline items with updated batt_wh
            snap_tl = copy.deepcopy(timeline)
            for i, x in enumerate(snap_tl): x["batt_wh"] = prof[i]
            
            history.append({
                "title": name,
                "timeline": snap_tl,
                "battery_wh": current_battery_wh,
                "peak_limit": PREVIOUS_PEAK_WH
            })

    # 0. Init State (Grid = Net Load, Battery Idle)
    # For visualization we want to see "Before Optimization"
    # Temporarily set grid to net load
    orig_grids = [x["grid_wh"] for x in timeline]
    for x in timeline: x["grid_wh"] = x["base_net_load_wh"] - BIAS_WH_PER_QUARTER
    save_snapshot("Fas 0: Utgångsläge (Ingen batteridrift)")
    # Restore
    for i, x in enumerate(timeline): x["grid_wh"] = orig_grids[i]

    # Phase 1
    optimal_limit, warning_flag = phase_1_peak_shaving(timeline, current_battery_wh)
    save_snapshot("Fas 1: Peak Shaving (Platt Kurva)")
    
    # Phase 2
    phase_2_safety_checks(timeline, current_battery_wh)
    save_snapshot("Fas 2: Säkerhetskontroll (15% Reserv)")
    
    # Phase 2.9
    fill_ceiling = PREVIOUS_PEAK_WH
    phase_smart_fill_cheap_hours(timeline, current_battery_wh, fill_ceiling)
    save_snapshot("Fas 2.9: Smart Fill (Billiga Timmar)")
    
    # Phase 2.5
    phase_optimize_within_hours(timeline, current_battery_wh)
    save_snapshot("Fas 2.5: Intra-Hour (Kvart-optimering)")

    # Phase 3 & 4
    if ENABLE_ARBITRAGE:
        monthly_peak_wh = PREVIOUS_PEAK_WH / 4.0 
        swap_ceiling_wh = monthly_peak_wh if not warning_flag else optimal_limit
        phase_3_price_optimization(timeline, current_battery_wh, swap_ceiling_wh)
        phase_4_active_arbitrage(timeline, current_battery_wh, base_price)
        save_snapshot("Fas 3 & 4: Arbitrage & Swap")
    else:
        print_flush("Arbitrage Disabled: Skipping Phase 3 & 4.")

    # Report
    target_for_display = PREVIOUS_PEAK_WH
    if not return_history:
        generate_reports(timeline, current_battery_wh, TARGET_DATE_STR, target_for_display, optimal_limit, warning_flag)
        return timeline
    else:
        # Final state is already in history? No, add final
        save_snapshot("Slutresultat")
        return history

if __name__ == "__main__":
    run_optimizer()