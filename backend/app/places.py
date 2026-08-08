"""Turning photo coordinates into a place name — entirely on this machine.

Every reverse-geocoding service in the world would do this better. None of them
may be used here: sending a customer's photo coordinates to a third party is
precisely the thing §1 of the project guide forbids, and a map of where someone
has been is about as personal as a record gets. So the lookup is a small table
compiled in, and the answer is "the nearest place we know of", not an address.

That has an honest failure mode and it is the reason for `MAX_KM`. A photo taken
somewhere not in the table gets a coordinate label rather than being quietly
attributed to a city 400 km away — which would read as fact and be wrong.
"""
from __future__ import annotations

import math

# Nearest-city radius. Beyond this the table has nothing useful to say, so we
# say the coordinates instead. 60 km covers a city and its surroundings without
# claiming a photo from open country belongs to the nearest metro.
MAX_KM = 60.0

# Cluster radius. Photos within this of each other are "the same place".
# A city is the unit people think in — one entry for Bengaluru, not one per
# neighbourhood — and 25 km is about the radius of a large one.
CLUSTER_KM = 25.0

# (name, region, lat, lon). India in depth because that is where this app is
# used; the rest of the world thinly, so a holiday photo still lands somewhere
# recognisable. Adding a row is safe and needs no migration.
CITIES: list[tuple[str, str, float, float]] = [
    # --- Karnataka ---
    ("Bengaluru", "Karnataka", 12.9716, 77.5946),
    ("Mysuru", "Karnataka", 12.2958, 76.6394),
    ("Ramanagara", "Karnataka", 12.7217, 77.2800),
    ("Kanakapura", "Karnataka", 12.5460, 77.4200),
    ("Channapatna", "Karnataka", 12.6510, 77.2070),
    ("Tumakuru", "Karnataka", 13.3392, 77.1140),
    ("Mangaluru", "Karnataka", 12.9141, 74.8560),
    ("Hubballi", "Karnataka", 15.3647, 75.1240),
    ("Belagavi", "Karnataka", 15.8497, 74.4977),
    ("Davanagere", "Karnataka", 14.4644, 75.9218),
    ("Shivamogga", "Karnataka", 13.9299, 75.5681),
    ("Ballari", "Karnataka", 15.1394, 76.9214),
    ("Kalaburagi", "Karnataka", 17.3297, 76.8343),
    ("Udupi", "Karnataka", 13.3409, 74.7421),
    ("Hassan", "Karnataka", 13.0072, 76.0962),
    ("Chikkamagaluru", "Karnataka", 13.3161, 75.7720),
    ("Madikeri", "Karnataka", 12.4244, 75.7382),
    ("Hampi", "Karnataka", 15.3350, 76.4600),
    ("Chitradurga", "Karnataka", 14.2251, 76.3980),
    ("Kolar", "Karnataka", 13.1367, 78.1292),
    ("Nandi Hills", "Karnataka", 13.3702, 77.6835),
    ("Mandya", "Karnataka", 12.5218, 76.8951),
    # --- the rest of India ---
    ("Mumbai", "Maharashtra", 19.0760, 72.8777),
    ("Pune", "Maharashtra", 18.5204, 73.8567),
    ("Nagpur", "Maharashtra", 21.1458, 79.0882),
    ("Nashik", "Maharashtra", 19.9975, 73.7898),
    ("Delhi", "Delhi", 28.6139, 77.2090),
    ("Gurugram", "Haryana", 28.4595, 77.0266),
    ("Noida", "Uttar Pradesh", 28.5355, 77.3910),
    ("Chennai", "Tamil Nadu", 13.0827, 80.2707),
    ("Coimbatore", "Tamil Nadu", 11.0168, 76.9558),
    ("Madurai", "Tamil Nadu", 9.9252, 78.1198),
    ("Ooty", "Tamil Nadu", 11.4102, 76.6950),
    ("Kodaikanal", "Tamil Nadu", 10.2381, 77.4892),
    ("Tiruchirappalli", "Tamil Nadu", 10.7905, 78.7047),
    ("Hyderabad", "Telangana", 17.3850, 78.4867),
    ("Warangal", "Telangana", 17.9689, 79.5941),
    ("Visakhapatnam", "Andhra Pradesh", 17.6868, 83.2185),
    ("Vijayawada", "Andhra Pradesh", 16.5062, 80.6480),
    ("Tirupati", "Andhra Pradesh", 13.6288, 79.4192),
    ("Kochi", "Kerala", 9.9312, 76.2673),
    ("Thiruvananthapuram", "Kerala", 8.5241, 76.9366),
    ("Kozhikode", "Kerala", 11.2588, 75.7804),
    ("Munnar", "Kerala", 10.0889, 77.0595),
    ("Alappuzha", "Kerala", 9.4981, 76.3388),
    ("Kolkata", "West Bengal", 22.5726, 88.3639),
    ("Darjeeling", "West Bengal", 27.0360, 88.2627),
    ("Ahmedabad", "Gujarat", 23.0225, 72.5714),
    ("Surat", "Gujarat", 21.1702, 72.8311),
    ("Vadodara", "Gujarat", 22.3072, 73.1812),
    ("Rajkot", "Gujarat", 22.3039, 70.8022),
    ("Jaipur", "Rajasthan", 26.9124, 75.7873),
    ("Udaipur", "Rajasthan", 24.5854, 73.7125),
    ("Jodhpur", "Rajasthan", 26.2389, 73.0243),
    ("Jaisalmer", "Rajasthan", 26.9157, 70.9083),
    ("Lucknow", "Uttar Pradesh", 26.8467, 80.9462),
    ("Varanasi", "Uttar Pradesh", 25.3176, 82.9739),
    ("Agra", "Uttar Pradesh", 27.1767, 78.0081),
    ("Kanpur", "Uttar Pradesh", 26.4499, 80.3319),
    ("Prayagraj", "Uttar Pradesh", 25.4358, 81.8463),
    ("Bhopal", "Madhya Pradesh", 23.2599, 77.4126),
    ("Indore", "Madhya Pradesh", 22.7196, 75.8577),
    ("Jabalpur", "Madhya Pradesh", 23.1815, 79.9864),
    ("Patna", "Bihar", 25.5941, 85.1376),
    ("Bodh Gaya", "Bihar", 24.6959, 84.9866),
    ("Bhubaneswar", "Odisha", 20.2961, 85.8245),
    ("Puri", "Odisha", 19.8135, 85.8312),
    ("Guwahati", "Assam", 26.1445, 91.7362),
    ("Shillong", "Meghalaya", 25.5788, 91.8933),
    ("Gangtok", "Sikkim", 27.3389, 88.6065),
    ("Chandigarh", "Chandigarh", 30.7333, 76.7794),
    ("Amritsar", "Punjab", 31.6340, 74.8723),
    ("Ludhiana", "Punjab", 30.9010, 75.8573),
    ("Shimla", "Himachal Pradesh", 31.1048, 77.1734),
    ("Manali", "Himachal Pradesh", 32.2432, 77.1892),
    ("Dharamshala", "Himachal Pradesh", 32.2190, 76.3234),
    ("Dehradun", "Uttarakhand", 30.3165, 78.0322),
    ("Rishikesh", "Uttarakhand", 30.0869, 78.2676),
    ("Nainital", "Uttarakhand", 29.3919, 79.4542),
    ("Haridwar", "Uttarakhand", 29.9457, 78.1642),
    ("Srinagar", "Jammu and Kashmir", 34.0837, 74.7973),
    ("Leh", "Ladakh", 34.1526, 77.5771),
    ("Jammu", "Jammu and Kashmir", 32.7266, 74.8570),
    ("Panaji", "Goa", 15.4909, 73.8278),
    ("Madgaon", "Goa", 15.2832, 73.9862),
    ("Raipur", "Chhattisgarh", 21.2514, 81.6296),
    ("Ranchi", "Jharkhand", 23.3441, 85.3096),
    ("Jamshedpur", "Jharkhand", 22.8046, 86.2029),
    ("Port Blair", "Andaman and Nicobar", 11.6234, 92.7265),
    ("Puducherry", "Puducherry", 11.9416, 79.8083),
    # --- elsewhere, thinly ---
    ("Colombo", "Sri Lanka", 6.9271, 79.8612),
    ("Kathmandu", "Nepal", 27.7172, 85.3240),
    ("Dhaka", "Bangladesh", 23.8103, 90.4125),
    ("Malé", "Maldives", 4.1755, 73.5093),
    ("Dubai", "UAE", 25.2048, 55.2708),
    ("Abu Dhabi", "UAE", 24.4539, 54.3773),
    ("Doha", "Qatar", 25.2854, 51.5310),
    ("Muscat", "Oman", 23.5880, 58.3829),
    ("Riyadh", "Saudi Arabia", 24.7136, 46.6753),
    ("Singapore", "Singapore", 1.3521, 103.8198),
    ("Kuala Lumpur", "Malaysia", 3.1390, 101.6869),
    ("Bangkok", "Thailand", 13.7563, 100.5018),
    ("Phuket", "Thailand", 7.8804, 98.3923),
    ("Bali", "Indonesia", -8.4095, 115.1889),
    ("Jakarta", "Indonesia", -6.2088, 106.8456),
    ("Hong Kong", "Hong Kong", 22.3193, 114.1694),
    ("Tokyo", "Japan", 35.6762, 139.6503),
    ("Osaka", "Japan", 34.6937, 135.5023),
    ("Seoul", "South Korea", 37.5665, 126.9780),
    ("Beijing", "China", 39.9042, 116.4074),
    ("Shanghai", "China", 31.2304, 121.4737),
    ("Sydney", "Australia", -33.8688, 151.2093),
    ("Melbourne", "Australia", -37.8136, 144.9631),
    ("Auckland", "New Zealand", -36.8485, 174.7633),
    ("London", "United Kingdom", 51.5074, -0.1278),
    ("Manchester", "United Kingdom", 53.4808, -2.2426),
    ("Edinburgh", "United Kingdom", 55.9533, -3.1883),
    ("Paris", "France", 48.8566, 2.3522),
    ("Amsterdam", "Netherlands", 52.3676, 4.9041),
    ("Berlin", "Germany", 52.5200, 13.4050),
    ("Munich", "Germany", 48.1351, 11.5820),
    ("Zurich", "Switzerland", 47.3769, 8.5417),
    ("Rome", "Italy", 41.9028, 12.4964),
    ("Milan", "Italy", 45.4642, 9.1900),
    ("Venice", "Italy", 45.4408, 12.3155),
    ("Barcelona", "Spain", 41.3874, 2.1686),
    ("Madrid", "Spain", 40.4168, -3.7038),
    ("Lisbon", "Portugal", 38.7223, -9.1393),
    ("Istanbul", "Turkey", 41.0082, 28.9784),
    ("Cairo", "Egypt", 30.0444, 31.2357),
    ("Nairobi", "Kenya", -1.2921, 36.8219),
    ("Cape Town", "South Africa", -33.9249, 18.4241),
    ("New York", "USA", 40.7128, -74.0060),
    ("San Francisco", "USA", 37.7749, -122.4194),
    ("Los Angeles", "USA", 34.0522, -118.2437),
    ("Chicago", "USA", 41.8781, -87.6298),
    ("Seattle", "USA", 47.6062, -122.3321),
    ("Boston", "USA", 42.3601, -71.0589),
    ("Toronto", "Canada", 43.6532, -79.3832),
    ("Vancouver", "Canada", 49.2827, -123.1207),
    ("Mexico City", "Mexico", 19.4326, -99.1332),
    ("São Paulo", "Brazil", -23.5505, -46.6333),
    ("Buenos Aires", "Argentina", -34.6037, -58.3816),
]


def km_between(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Great-circle distance. Flat-earth arithmetic is tempting at these scales
    and breaks near the poles and across the date line for no saving worth
    having."""
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _coord_label(lat: float, lon: float) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.2f}°{ns}, {abs(lon):.2f}°{ew}"


def nearest(lat: float, lon: float) -> tuple[str, str | None, float | None]:
    """(label, region, km). Region is None when nothing was near enough — the
    caller can then tell a real place name from a fallback without re-deriving
    it from the string."""
    best = None
    best_km = MAX_KM
    for name, region, clat, clon in CITIES:
        d = km_between(lat, lon, clat, clon)
        if d < best_km:
            best, best_km = (name, region), d
    if best is None:
        return _coord_label(lat, lon), None, None
    return best[0], best[1], round(best_km, 1)


def cluster(points: list[tuple[int, float, float]],
            radius_km: float = CLUSTER_KM) -> list[dict]:
    """Group (id, lat, lon) into places, largest first.

    Greedy single-pass assignment against cluster centres rather than a grid.
    A grid splits a city in two whenever it happens to straddle a cell
    boundary, which is not a rare accident — Bengaluru sits almost exactly on
    77.6 — and produces two half-sized entries for one place.
    """
    groups: list[dict] = []
    for pid, lat, lon in points:
        for g in groups:
            if km_between(lat, lon, g["lat"], g["lon"]) <= radius_km:
                n = len(g["ids"])
                # Running mean, so the centre follows the photos rather than
                # being pinned to whichever one happened to arrive first.
                g["lat"] = (g["lat"] * n + lat) / (n + 1)
                g["lon"] = (g["lon"] * n + lon) / (n + 1)
                g["ids"].append(pid)
                break
        else:
            groups.append({"lat": lat, "lon": lon, "ids": [pid]})

    out = []
    for g in groups:
        label, region, km = nearest(g["lat"], g["lon"])
        out.append({
            "key": f"{g['lat']:.4f},{g['lon']:.4f}",
            "label": label,
            "region": region,
            "approx_km": km,
            "lat": round(g["lat"], 6),
            "lon": round(g["lon"], 6),
            "count": len(g["ids"]),
            "ids": g["ids"],
        })
    out.sort(key=lambda x: x["count"], reverse=True)
    return out
