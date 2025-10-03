import os
import json
from datetime import datetime, timedelta
from zeep import Client
from zeep.plugins import HistoryPlugin

# --- 1. Credentials and Configuration ---
DARWIN_API_KEY = os.getenv("DARWIN_API_KEY")
OUTPUT_FILE = "live_data.json"

# ✅ Use the 2021-11-01 WSDL
WSDL_URL = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/wsdl.aspx?ver=2021-11-01"

# --- JOURNEY DETAILS ---
ORIGIN_CRS = "STR"  # Streatham Common
INTERCHANGE_CRS = "CLJ"  # Clapham Junction
DESTINATION_CRS = "IMW"  # Imperial Wharf
MINIMUM_INTERCHANGE_MINUTES = 4

# --- ESTIMATED TRAVEL TIMES ---
STR_TO_CLJ_MINUTES = 8
CLJ_TO_IMW_MINUTES = 10


def parse_and_map_data(station_board):
    """Parses the LDB response and constructs the two-leg journey."""
    if not hasattr(station_board, 'trainServices') or station_board.trainServices is None:
        return []

    services = station_board.trainServices.service[:2]  # Get first 2 services
    mapped_services = []

    for i, service in enumerate(services):
        std_str = service.std
        etd_str = service.etd
        platform = getattr(service, 'platform', None)
        operator = getattr(service, 'operator', None)

        # --- Status logic ---
        if etd_str == "On time" or etd_str == std_str:
            status = "On Time"
            actual_departure_str = std_str
        elif etd_str == "Cancelled":
            status = "Cancelled"
            actual_departure_str = std_str
        elif etd_str in ["Delayed", "No Report"]:
            status = f"Delayed (Expected {std_str})"
            actual_departure_str = std_str
        else:
            status = f"Delayed (Expected {etd_str})"
            actual_departure_str = etd_str

        try:
            departure_dt = datetime.strptime(
                actual_departure_str, "%H:%M"
            ).replace(year=datetime.now().year,
                      month=datetime.now().month,
                      day=datetime.now().day)
        except (ValueError, TypeError):
            continue

        # Connection timings
        clj_arrival_dt = departure_dt + timedelta(minutes=STR_TO_CLJ_MINUTES)
        required_clj_departure_dt = clj_arrival_dt + timedelta(minutes=MINIMUM_INTERCHANGE_MINUTES)
        imw_departure_dt = required_clj_departure_dt
        imw_arrival_dt = imw_departure_dt + timedelta(minutes=CLJ_TO_IMW_MINUTES)

        total_duration_minutes = int((imw_arrival_dt - departure_dt).total_seconds() / 60)

        mapped_services.append({
            "id": i + 1,
            "departureTime": departure_dt.strftime("%H:%M"),
            "arrivalTime": imw_arrival_dt.strftime("%H:%M"),
            "totalDuration": f"{total_duration_minutes} min",
            "status": status,
            "live_updated_at": datetime.now().strftime("%H:%M:%S"),
            "legs": [
                {
                    "origin": ORIGIN_CRS,
                    "destination": INTERCHANGE_CRS,
                    "departure": actual_departure_str,
                    "status": status,
                    "platform": platform or "TBC",
                    "operator": operator or "Southern"
                },
                {
                    "operator": "Change Train",
                    "duration": f"{MINIMUM_INTERCHANGE_MINUTES} min",
                    "status": "Connection"
                },
                {
                    "origin": INTERCHANGE_CRS,
                    "destination": DESTINATION_CRS,
                    "departure": imw_departure_dt.strftime("%H:%M"),
                    "status": "On Time",
                    "platform": "TBC",
                    "operator": "Southern/Overground"
                }
            ]
        })

    return mapped_services


def fetch_and_process_darwin_data(debug=False):
    """Fetches data from Darwin LDB API using Zeep."""
    if not DARWIN_API_KEY:
        print("ERROR: DARWIN_API_KEY environment variable is missing.")
        return []

    print(f"[{datetime.now().isoformat()}] Fetching REAL LDB data for {ORIGIN_CRS}...")

    try:
        # Create Zeep client with history plugin for debugging
        history = HistoryPlugin()
        client = Client(wsdl=WSDL_URL, plugins=[history] if debug else [])

        # Create the header with access token
        header = client.get_element('ns2:AccessToken')
        header_value = header(TokenValue=DARWIN_API_KEY)

        # Call GetDepartureBoard
        response = client.service.GetDepartureBoard(
            numRows=2,
            crs=ORIGIN_CRS,
            _soapheaders=[header_value]
        )

        if debug:
            print("\n--- Last Request ---")
            print(history.last_sent)
            print("\n--- Last Response ---")
            print(history.last_received)
            print("----------------------\n")

        data = parse_and_map_data(response)
        print(f"Successfully fetched and generated {len(data)} REAL journey updates.")
        return data

    except Exception as e:
        print(f"ERROR: Failed to connect to LDB API: {e}")
        if debug:
            import traceback
            traceback.print_exc()
        return []


def main():
    data = fetch_and_process_darwin_data(debug=True)

    if data:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Successfully saved REAL data to {OUTPUT_FILE}")
    else:
        print("No REAL data generated, skipping file save.")


if __name__ == "__main__":
    main()









