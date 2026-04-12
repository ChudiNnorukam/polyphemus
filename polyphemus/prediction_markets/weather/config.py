"""City configuration for Polymarket temperature markets.

Each city entry contains:
  lat/lon: coordinates for weather API lookup
  unit: temperature unit used in Polymarket markets ("F" or "C")
  resolution: official source used by Polymarket for resolution
"""

CITIES: dict[str, dict] = {
    # --- United States (Fahrenheit) ---
    "los-angeles": {
        "lat": 34.05,
        "lon": -118.24,
        "unit": "F",
        "resolution": "wunderground",
        "display": "Los Angeles",
    },
    "new-york-city": {
        "lat": 40.71,
        "lon": -74.01,
        "unit": "F",
        "resolution": "wunderground",
        "display": "New York City",
    },
    "san-francisco": {
        "lat": 37.77,
        "lon": -122.42,
        "unit": "F",
        "resolution": "wunderground",
        "display": "San Francisco",
    },
    "seattle": {
        "lat": 47.61,
        "lon": -122.33,
        "unit": "F",
        "resolution": "wunderground",
        "display": "Seattle",
    },
    "chicago": {
        "lat": 41.88,
        "lon": -87.63,
        "unit": "F",
        "resolution": "wunderground",
        "display": "Chicago",
    },
    "atlanta": {
        "lat": 33.75,
        "lon": -84.39,
        "unit": "F",
        "resolution": "wunderground",
        "display": "Atlanta",
    },
    "miami": {
        "lat": 25.77,
        "lon": -80.19,
        "unit": "F",
        "resolution": "wunderground",
        "display": "Miami",
    },
    "dallas": {
        "lat": 32.78,
        "lon": -96.80,
        "unit": "F",
        "resolution": "wunderground",
        "display": "Dallas",
    },
    "houston": {
        "lat": 29.76,
        "lon": -95.37,
        "unit": "F",
        "resolution": "wunderground",
        "display": "Houston",
    },
    "austin": {
        "lat": 30.27,
        "lon": -97.74,
        "unit": "F",
        "resolution": "wunderground",
        "display": "Austin",
    },
    "denver": {
        "lat": 39.74,
        "lon": -104.98,
        "unit": "F",
        "resolution": "wunderground",
        "display": "Denver",
    },
    # --- Canada (Celsius) ---
    "toronto": {
        "lat": 43.65,
        "lon": -79.38,
        "unit": "C",
        "resolution": "environment-canada",
        "display": "Toronto",
    },
    # --- Europe (Celsius) ---
    "london": {
        "lat": 51.51,
        "lon": -0.13,
        "unit": "C",
        "resolution": "metoffice",
        "display": "London",
    },
    "paris": {
        "lat": 48.85,
        "lon": 2.35,
        "unit": "C",
        "resolution": "meteofrance",
        "display": "Paris",
    },
    "madrid": {
        "lat": 40.42,
        "lon": -3.70,
        "unit": "C",
        "resolution": "aemet",
        "display": "Madrid",
    },
    "amsterdam": {
        "lat": 52.37,
        "lon": 4.90,
        "unit": "C",
        "resolution": "knmi",
        "display": "Amsterdam",
    },
    "munich": {
        "lat": 48.14,
        "lon": 11.58,
        "unit": "C",
        "resolution": "dwd",
        "display": "Munich",
    },
    "milan": {
        "lat": 45.46,
        "lon": 9.19,
        "unit": "C",
        "resolution": "meteoam",
        "display": "Milan",
    },
    "warsaw": {
        "lat": 52.23,
        "lon": 21.01,
        "unit": "C",
        "resolution": "imgw",
        "display": "Warsaw",
    },
    "helsinki": {
        "lat": 60.17,
        "lon": 24.94,
        "unit": "C",
        "resolution": "fmi",
        "display": "Helsinki",
    },
    "moscow": {
        "lat": 55.75,
        "lon": 37.62,
        "unit": "C",
        "resolution": "roshydromet",
        "display": "Moscow",
    },
    "istanbul": {
        "lat": 41.01,
        "lon": 28.96,
        "unit": "C",
        "resolution": "mgm",
        "display": "Istanbul",
    },
    "ankara": {
        "lat": 39.93,
        "lon": 32.85,
        "unit": "C",
        "resolution": "mgm",
        "display": "Ankara",
    },
    # --- Middle East (Celsius) ---
    "tel-aviv": {
        "lat": 32.08,
        "lon": 34.78,
        "unit": "C",
        "resolution": "ims",
        "display": "Tel Aviv",
    },
    # --- Asia (Celsius) ---
    "tokyo": {
        "lat": 35.68,
        "lon": 139.69,
        "unit": "C",
        "resolution": "jma",
        "display": "Tokyo",
    },
    "seoul": {
        "lat": 37.57,
        "lon": 126.98,
        "unit": "C",
        "resolution": "kma",
        "display": "Seoul",
    },
    "hong-kong": {
        "lat": 22.32,
        "lon": 114.17,
        "unit": "C",
        "resolution": "hko",
        "display": "Hong Kong",
    },
    "singapore": {
        "lat": 1.35,
        "lon": 103.82,
        "unit": "C",
        "resolution": "met-sg",
        "display": "Singapore",
    },
    "shanghai": {
        "lat": 31.23,
        "lon": 121.47,
        "unit": "C",
        "resolution": "cma",
        "display": "Shanghai",
    },
    "beijing": {
        "lat": 39.91,
        "lon": 116.39,
        "unit": "C",
        "resolution": "cma",
        "display": "Beijing",
    },
    "taipei": {
        "lat": 25.05,
        "lon": 121.53,
        "unit": "C",
        "resolution": "cwb",
        "display": "Taipei",
    },
    "jakarta": {
        "lat": -6.21,
        "lon": 106.85,
        "unit": "C",
        "resolution": "bmkg",
        "display": "Jakarta",
    },
    "kuala-lumpur": {
        "lat": 3.14,
        "lon": 101.69,
        "unit": "C",
        "resolution": "met-malaysia",
        "display": "Kuala Lumpur",
    },
    "shenzhen": {
        "lat": 22.54,
        "lon": 114.06,
        "unit": "C",
        "resolution": "cma",
        "display": "Shenzhen",
    },
    "wuhan": {
        "lat": 30.59,
        "lon": 114.30,
        "unit": "C",
        "resolution": "cma",
        "display": "Wuhan",
    },
    "chongqing": {
        "lat": 29.56,
        "lon": 106.55,
        "unit": "C",
        "resolution": "cma",
        "display": "Chongqing",
    },
    "chengdu": {
        "lat": 30.66,
        "lon": 104.07,
        "unit": "C",
        "resolution": "cma",
        "display": "Chengdu",
    },
    "busan": {
        "lat": 35.10,
        "lon": 129.04,
        "unit": "C",
        "resolution": "kma",
        "display": "Busan",
    },
    "lucknow": {
        "lat": 26.85,
        "lon": 80.95,
        "unit": "C",
        "resolution": "imd",
        "display": "Lucknow",
    },
    # --- Latin America (Celsius) ---
    "buenos-aires": {
        "lat": -34.61,
        "lon": -58.38,
        "unit": "C",
        "resolution": "smn",
        "display": "Buenos Aires",
    },
    "sao-paulo": {
        "lat": -23.55,
        "lon": -46.63,
        "unit": "C",
        "resolution": "inmet",
        "display": "Sao Paulo",
    },
    "mexico-city": {
        "lat": 19.43,
        "lon": -99.13,
        "unit": "C",
        "resolution": "conagua",
        "display": "Mexico City",
    },
    "panama": {
        "lat": 8.99,
        "lon": -79.52,
        "unit": "C",
        "resolution": "etesa",
        "display": "Panama",
    },
    # --- Oceania (Celsius) ---
    "auckland": {
        "lat": -36.87,
        "lon": 174.76,
        "unit": "C",
        "resolution": "metservice-nz",
        "display": "Auckland",
    },
}

# Reverse lookup: display name variants -> slug key.
# Handles multi-word city names that appear in Polymarket event titles.
DISPLAY_TO_KEY: dict[str, str] = {}
for _key, _cfg in CITIES.items():
    DISPLAY_TO_KEY[_cfg["display"].lower()] = _key
    # Also index the slug itself (handles "los-angeles" style)
    DISPLAY_TO_KEY[_key.lower()] = _key
