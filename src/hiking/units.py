"""Unit conversions and formatting for AegisHiker.

All stored distances, elevations, speeds, and weights are metric (meters, m/s, kg, °C).
Display functions format values in either 'us' (miles, feet, mph, min/mi, lb, °F) or
'metric' (km, meters, km/h, min/km, kg, °C).
"""

M_PER_KM = 1000.0
M_PER_MILE = 1609.344
FT_PER_M = 3.280839895
LB_PER_KG = 2.20462262185
OZ_PER_L = 33.8140227


def m_to_km(meters: float) -> float:
    return meters / M_PER_KM


def km_to_m(km: float) -> float:
    return km * M_PER_KM


def m_to_miles(meters: float) -> float:
    return meters / M_PER_MILE


def miles_to_m(miles: float) -> float:
    return miles * M_PER_MILE


def m_to_ft(meters: float) -> float:
    return meters * FT_PER_M


def ft_to_m(feet: float) -> float:
    return feet / FT_PER_M


def kg_to_lb(kg: float) -> float:
    return kg * LB_PER_KG


def lb_to_kg(lb: float) -> float:
    return lb / LB_PER_KG


def liters_to_fl_oz(liters: float) -> float:
    return liters * OZ_PER_L


def fl_oz_to_liters(oz: float) -> float:
    return oz / OZ_PER_L


def c_to_f(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def f_to_c(fahrenheit: float) -> float:
    return (fahrenheit - 32.0) * 5.0 / 9.0


def mps_to_kmh(mps: float) -> float:
    return mps * 3.6


def mps_to_mph(mps: float) -> float:
    return mps * 3600.0 / M_PER_MILE


def speed_mps_to_pace_min_per_unit(mps: float, units: str = "us") -> float:
    """Convert m/s speed into minutes per mile ('us') or minutes per km ('metric')."""
    if mps <= 0.05:
        return 0.0
    unit_m = M_PER_MILE if units == "us" else M_PER_KM
    return (unit_m / mps) / 60.0


def format_distance(meters: float, units: str = "us", decimals: int = 1) -> str:
    if units == "us":
        mi = m_to_miles(meters)
        return f"{mi:.{decimals}f} mi"
    km = m_to_km(meters)
    return f"{km:.{decimals}f} km"


def format_elevation(meters: float, units: str = "us") -> str:
    if units == "us":
        return f"{round(m_to_ft(meters)):,} ft"
    return f"{round(meters):,} m"


def format_speed(mps: float, units: str = "us") -> str:
    if units == "us":
        return f"{mps_to_mph(mps):.1f} mph"
    return f"{mps_to_kmh(mps):.1f} km/h"


def format_pace(mps: float, units: str = "us") -> str:
    pace = speed_mps_to_pace_min_per_unit(mps, units)
    if pace <= 0 or pace > 90:
        return "—"
    mins = int(pace)
    secs = int(round((pace - mins) * 60))
    if secs == 60:
        mins += 1
        secs = 0
    suffix = "/mi" if units == "us" else "/km"
    return f"{mins}:{secs:02d} {suffix}"


def format_duration(seconds: float) -> str:
    total_sec = max(0, int(round(seconds)))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    if hrs > 0:
        return f"{hrs}h {mins:02d}m"
    return f"{mins}m"


def format_duration_min(minutes: float) -> str:
    """format_duration for values in minutes (route/session durations are stored in minutes)."""
    return format_duration(minutes * 60.0)


def format_water(liters: float, units: str = "us") -> str:
    if units == "us":
        return f"{liters:.1f} L ({round(liters_to_fl_oz(liters))} fl oz)"
    return f"{liters:.1f} L"


def format_weight(kg: float, units: str = "us") -> str:
    if units == "us":
        return f"{kg_to_lb(kg):.1f} lb"
    return f"{kg:.1f} kg"

