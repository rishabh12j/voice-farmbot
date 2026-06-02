"""Real weather data for the LLM reasoning layer.

Uses Open-Meteo (https://open-meteo.com) — free, no API key, ECMWF + ICON
ensemble forecasts. One request returns current conditions plus a 4-day
daily summary. Results are cached for 30 minutes — gardening questions
don't need second-by-second freshness and the public API has rate limits.

The summarised output is intentionally small and prose-friendly: the LLM
gets a tight, factual snapshot it can fold into a reply without burning
tokens on raw fields.

No new pip dependencies — uses ``urllib.request`` like the rest of the
package's HTTP code.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

__all__ = ["get_weather", "fetch_weather", "summarise_for_llm"]

_CACHE_TTL_S = 30 * 60
_REQUEST_TIMEOUT_S = 5.0

# (lat_rounded, lon_rounded) -> (fetched_at_unix, raw_response_dict)
_cache: Dict[Tuple[float, float], Tuple[float, dict]] = {}

# WMO weather interpretation codes — the LLM gets the human label
# (https://open-meteo.com/en/docs#weathervariables)
_WMO_CODE: Dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "light snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with light hail",
    99: "thunderstorm with heavy hail",
}


def _label(code: Optional[int]) -> str:
    if code is None:
        return "unknown"
    return _WMO_CODE.get(int(code), f"code {code}")


def fetch_weather(latitude: float, longitude: float) -> Optional[dict]:
    """Return the raw Open-Meteo response, cached for 30 minutes.

    Returns the cached payload on network failure if any prior fetch
    succeeded — better to serve slightly-stale weather than none at all.
    """
    key = (round(latitude, 3), round(longitude, 3))
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_S:
        return cached[1]

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}"
        "&current=temperature_2m,relative_humidity_2m,precipitation,"
        "weather_code,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min,"
        "precipitation_sum,weather_code"
        "&forecast_days=4&timezone=auto"
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "growmate/1.0"})
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return cached[1] if cached else None

    _cache[key] = (now, data)
    return data


def summarise_for_llm(data: Optional[dict]) -> Optional[Dict[str, Any]]:
    """Project the Open-Meteo payload into a compact LLM-friendly dict."""
    if not data:
        return None

    cur = data.get("current", {}) or {}
    daily = data.get("daily", {}) or {}

    now = {
        "temperature_c": cur.get("temperature_2m"),
        "humidity_percent": cur.get("relative_humidity_2m"),
        "precipitation_mm": cur.get("precipitation"),
        "wind_kmh": cur.get("wind_speed_10m"),
        "conditions": _label(cur.get("weather_code")),
    }

    dates = daily.get("time", []) or []
    highs = daily.get("temperature_2m_max", []) or []
    lows = daily.get("temperature_2m_min", []) or []
    rains = daily.get("precipitation_sum", []) or []
    codes = daily.get("weather_code", []) or []

    forecast = []
    for i, date in enumerate(dates[:4]):
        forecast.append({
            "date": date,
            "high_c": highs[i] if i < len(highs) else None,
            "low_c": lows[i] if i < len(lows) else None,
            "rain_mm": rains[i] if i < len(rains) else None,
            "conditions": _label(codes[i] if i < len(codes) else None),
        })

    return {
        "now": now,
        "forecast_next_4_days": forecast,
        "source": "open-meteo.com",
    }


def get_weather(latitude: float, longitude: float) -> Optional[Dict[str, Any]]:
    """Cached weather summary ready to inject into an LLM prompt context.

    Returns None if no network and no prior cache.
    """
    return summarise_for_llm(fetch_weather(latitude, longitude))
