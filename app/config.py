from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    traccar_forward_token: str
    # Traccar REST API, used to create a GPS device for each driver
    traccar_url: str = "http://traccar:8082"
    traccar_admin_email: str
    traccar_admin_password: str
    # Server URL drivers type into the Traccar Client app
    traccar_client_url: str = ""
    # How often Traccar Client reports (put in the drivers' set-up QR code): every N seconds or N meters moved,
    # and accuracy (highest / high / low). The app's own default is 300 s / 75 m, too slow for the live map
    traccar_interval_seconds: int = 30
    traccar_distance_meters: int = 30
    traccar_accuracy: str = "high"
    # Self-hosted OSRM (docker compose's `osrm` service): the live map moves drivers along the roads between
    # GPS fixes. Empty: straight lines
    osrm_url: str = ""
    # Its walking map (docker compose's `osrm-foot` service): how far a customer walks to a meeting point.
    # Empty: walks aren't available
    osrm_foot_url: str = ""
    # A driver counts as online if their phone sent a GPS fix within this many minutes
    driver_online_minutes: int = 5
    # How long a driver has to accept an order offer before it goes to the next driver
    offer_timeout_seconds: int = 60
    telegram_bot_token: str = ""
    google_maps_api_key: str = ""
    # Live driver map (web): a second key, restricted to the web app's address, and the Map ID
    google_maps_browser_key: str = ""
    google_maps_map_id: str = ""
    # Addresses are looked up (and suggested while typing) only in this country, so a typo can't land abroad
    address_country: str = "GB"
    # The delivery area as "lat,lng,km" (e.g. "50.868,0.578,6" for Hastings and St Leonards): addresses are looked up
    # there first and never land outside it, and places can be found by name ("Railway pub"). Empty: the whole country
    address_area: str = ""
    # Address suggestions only in these towns, comma-separated (e.g. "Hastings,St Leonards"): Google's area limit
    # leaks at its edges (Battle, Bexhill). Empty: anywhere in the delivery area
    address_towns: str = ""
    # Customer phone numbers written without a country code ("07911 123456") are in this country
    phone_country_code: str = "44"
    # Driving times (route planning). "next-leg": live traffic only for the driver's next leg and "on the way"
    # checks, other trips without traffic (cheaper); "all": always live traffic; "none": never
    routes_traffic: Literal["next-leg", "all", "none"] = "next-leg"
    # At most this many Route Matrix pairs per day from Google; after that, estimates
    routes_daily_limit: int = 5000
    # Estimates (no Google key, Google failing, or the daily limit reached): average town driving speed
    fallback_speed_kmh: float = 25
    # Ask Google only about each place's this many nearest stops; farther trips are estimated
    routes_nearest: int = 8

    # Route planning (Plan 1). Off: today's nearest-first delivery list, no planned ETAs
    route_optimization: bool = False
    # Time at each stop until there's enough history (then the typical Arrived -> Delivered time)
    service_minutes: float = 5
    # A driver's stop sequence only changes when the new one saves at least this much
    reroute_threshold_minutes: float = 3
    # "On the way": a new stop goes before the current destination if the detour and the current
    # customer's delay stay within these (0 switches it off)
    on_the_way_max_detour_minutes: float = 3
    on_the_way_max_delay_minutes: float = 8
    # "Deliver by 14:00" means 14:00 today in this time zone
    app_timezone: str = "Europe/London"
    # Longest a customer should wait: a new order without a chosen "deliver by" gets placed + this (0 = none)
    max_wait_minutes: int = 60
    # When not every deadline can be kept (overload): each customer who'd be late costs as much as this many minutes
    # of lateness, so as many as possible are on time and a few wait longer (rule B). 0: only minutes late count, the
    # delay is shared (rule A)
    late_customer_penalty_minutes: float = 60
    # Meeting points (Plan 4). "parked": only "I'm parked here, ask the customer to come out" after Arrived; "all":
    # also "Meet the customer instead" on upcoming stops (with "Same place as last time")
    meeting_points: Literal["parked", "all"] = "parked"
    # A spot saving the driver less than this (middle of the range) is sent with a warning
    meeting_min_saving_minutes: float = 10

    # Signs login session cookies
    secret_key: str
    session_hours: int = 12
    # Must be true once the site is served over HTTPS
    cookie_secure: bool = False


settings = Settings()
