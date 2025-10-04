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
ORIGIN_CRS = "SRC"  # Streatham Common
INTERCHANGE_CRS = "CLJ"  # Clapham Junction
DESTINATION_CRS = "IMW"  # Imperial Wharf
MINIMUM_INTERCHANGE_MINUTES = 4

# --- ESTIMATED TRAVEL TIMES ---
STR_TO_CLJ_MINUTES = 8
CLJ_TO_IMW_MINUTES = 10


def get_departure_board(client, header_value, crs, filter_crs=None, num_rows=10):
    """Fetch departure board for a station."""
    if filter_crs:
        return client.service.GetDepartureBoard(
            numRows=num_rows,
            crs=crs,
            filterCrs=filter_crs,
            _soapheaders=[header_value]
        )
    else:
        return client.service.GetDepartureBoard(
            numRows=num_rows,
            crs=crs,
            _soapheaders=[header_value]
        )


def parse_time(time_str):
    """Parse time string to datetime object."""
    try:
        return datetime.strptime(time_str, "%H:%M").replace(
            year=datetime.now().year,
            month=datetime.now().month,
            day=datetime.now().day
        )
    except (ValueError, TypeError):
        return None


def get_departure_time_and_status(service):
    """Extract departure time and status from a service."""
    std_str = service.std
    etd_str = service.etd
    
    # Determine status and actual departure time
    if etd_str == "On time":
        status = "On Time"
        actual_departure_str = std_str
    elif etd_str == "Cancelled":
        status = "Cancelled"
        actual_departure_str = std_str
    elif etd_str in ["Delayed", "No Report"]:
        status = "Delayed"
        actual_departure_str = std_str
    else:
        # etd_str is an actual time
        status = f"Expected {etd_str}"
        actual_departure_str = etd_str
    
    return actual_departure_str, status


def find_connection_pairs(client, header_value, num_pairs=2):
    """Find pairs of connections: Origin -> Interchange -> Destination."""
    print(f"Finding connection pairs: {ORIGIN_CRS} -> {INTERCHANGE_CRS} -> {DESTINATION_CRS}")
    
    # Get trains from Origin to Interchange
    leg1_board = get_departure_board(client, header_value, ORIGIN_CRS, INTERCHANGE_CRS, num_rows=10)
    
    if not hasattr(leg1_board, 'trainServices') or leg1_board.trainServices is None:
        print(f"No trains found from {ORIGIN_CRS} to {INTERCHANGE_CRS}")
        return []
    
    leg1_services = leg1_board.trainServices.service
    print(f"Found {len(leg1_services)} trains from {ORIGIN_CRS} to {INTERCHANGE_CRS}")
    
    # Get trains from Interchange to Destination
    leg2_board = get_departure_board(client, header_value, INTERCHANGE_CRS, DESTINATION_CRS, num_rows=20)
    
    if not hasattr(leg2_board, 'trainServices') or leg2_board.trainServices is None:
        print(f"No trains found from {INTERCHANGE_CRS} to {DESTINATION_CRS}")
        return []
    
    leg2_services = leg2_board.trainServices.service
    print(f"Found {len(leg2_services)} trains from {INTERCHANGE_CRS} to {DESTINATION_CRS}")
    
    connection_pairs = []
    
    # For each train on leg 1, find the next available train on leg 2
    for leg1 in leg1_services[:num_pairs * 2]:  # Check more trains to ensure we get enough pairs
        leg1_depart_str, leg1_status = get_departure_time_and_status(leg1)
        leg1_depart_time = parse_time(leg1_depart_str)
        
        if not leg1_depart_time or leg1_status == "Cancelled":
            continue
        
        # Calculate arrival at interchange
        interchange_arrival = leg1_depart_time + timedelta(minutes=STR_TO_CLJ_MINUTES)
        # Calculate minimum departure time from interchange
        min_leg2_depart = interchange_arrival + timedelta(minutes=MINIMUM_INTERCHANGE_MINUTES)
        
        # Find next available train on leg 2
        for leg2 in leg2_services:
            leg2_depart_str, leg2_status = get_departure_time_and_status(leg2)
            leg2_depart_time = parse_time(leg2_depart_str)
            
            if not leg2_depart_time or leg2_status == "Cancelled":
                continue
            
            # Check if this train departs after minimum interchange time
            if leg2_depart_time >= min_leg2_depart:
                # Found a valid connection!
                leg2_arrival = leg2_depart_time + timedelta(minutes=CLJ_TO_IMW_MINUTES)
                total_duration = int((leg2_arrival - leg1_depart_time).total_seconds() / 60)
                actual_interchange_time = int((leg2_depart_time - interchange_arrival).total_seconds() / 60)
                
                connection_pairs.append({
                    "id": len(connection_pairs) + 1,
                    "departureTime": leg1_depart_time.strftime("%H:%M"),
                    "arrivalTime": leg2_arrival.strftime("%H:%M"),
                    "totalDuration": f"{total_duration} min",
                    "status": leg1_status if leg1_status != "On Time" else "On Time",
                    "live_updated_at": datetime.now().strftime("%H:%M:%S"),
                    "legs": [
                        {
                            "origin": ORIGIN_CRS,
                            "destination": INTERCHANGE_CRS,
                            "departure": leg1_depart_str,
                            "arrival": interchange_arrival.strftime("%H:%M"),
                            "status": leg1_status,
                            "platform": getattr(leg1, 'platform', None) or "TBC",
                            "operator": getattr(leg1, 'operator', None) or "Unknown"
                        },
                        {
                            "operator": "Change trains",
                            "duration": f"{actual_interchange_time} min",
                            "status": "Connection"
                        },
                        {
                            "origin": INTERCHANGE_CRS,
                            "destination": DESTINATION_CRS,
                            "departure": leg2_depart_str,
                            "arrival": leg2_arrival.strftime("%H:%M"),
                            "status": leg2_status,
                            "platform": getattr(leg2, 'platform', None) or "TBC",
                            "operator": getattr(leg2, 'operator', None) or "Unknown"
                        }
                    ]
                })
                
                # Stop searching for this leg1 train once we found a connection
                break
        
        # Stop if we have enough pairs
        if len(connection_pairs) >= num_pairs:
            break
    
    return connection_pairs


def fetch_and_process_darwin_data(debug=False):
    """Fetches data from Darwin LDB API using Zeep."""
    if not DARWIN_API_KEY:
        print("ERROR: DARWIN_API_KEY environment variable is missing.")
        return []

    print(f"[{datetime.now().isoformat()}] Fetching REAL LDB data...")

    try:
        # Create Zeep client with history plugin for debugging
        history = HistoryPlugin()
        client = Client(wsdl=WSDL_URL, plugins=[history] if debug else [])

        # Create the header with access token using the correct namespace
        header = client.get_element('{http://thalesgroup.com/RTTI/2013-11-28/Token/types}AccessToken')
        header_value = header(TokenValue=DARWIN_API_KEY)

        # Find connection pairs
        data = find_connection_pairs(client, header_value, num_pairs=2)
        
        print(f"Successfully generated {len(data)} connection pairs.")
        return data

    except Exception as e:
        print(f"ERROR: Failed to connect to LDB API: {e}")
        if debug:
            import traceback
            traceback.print_exc()
        return []


def main():
    data = fetch_and_process_darwin_data(debug=False)

    if data:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Successfully saved REAL data to {OUTPUT_FILE}")
    else:
        print("No REAL data generated (no valid connections found).")


if __name__ == "__main__":
    main()