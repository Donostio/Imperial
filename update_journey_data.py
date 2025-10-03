import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# --- 1. Credentials and Configuration ---
# API Key is passed via GitHub Secrets (DARWIN_API_KEY)
DARWIN_API_KEY = os.getenv("DARWIN_API_KEY")
OUTPUT_FILE = "live_data.json"
# OpenLDBWS Endpoint (WSDL URL is not used directly, but this is the service URL)
LDB_API_ENDPOINT = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb.asmx"

# --- JOURNEY DETAILS ---
ORIGIN_CRS = "STR" # Streatham Common
INTERCHANGE_CRS = "CLJ" # Clapham Junction
DESTINATION_CRS = "IMW" # Imperial Wharf (Targeted destination, not primary query)
MINIMUM_INTERCHANGE_MINUTES = 4

# --- ESTIMATED TRAVEL TIMES for Calculation ---
# Since LDB does not provide intermediate stops, we estimate travel time.
STR_TO_CLJ_MINUTES = 8  # Estimated time from Streatham Common to Clapham Junction
CLJ_TO_IMW_MINUTES = 10 # Estimated time for connection leg (Clapham Junc. to Imperial Wharf)

# XML Namespaces used in the LDB API response
NAMESPACES = {
    'soap': 'http://www.w3.org/2003/05/soap-envelope/',
    'ldb': 'http://thalesgroup.com/RTTI/2017-10-01/ldb/',
    'td': 'http://thalesgroup.com/RTTI/2013-11-28/Token/types'
}

def create_soap_payload(crs_code, token, num_rows=2):
    """Creates the XML body for the GetDepartureBoardRequest."""
    
    return f"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:ldb="http://thalesgroup.com/RTTI/2017-10-01/ldb/" xmlns:td="http://thalesgroup.com/RTTI/2013-11-28/Token/types">
    <soap:Header>
        <td:AccessToken>
            <td:TokenValue>{token}</td:TokenValue>
        </td:AccessToken>
    </soap:Header>
    <soap:Body>
        <ldb:GetDepartureBoardRequest>
            <ldb:numRows>{num_rows}</ldb:numRows>
            <ldb:crs>{crs_code}</ldb:crs>
        </ldb:GetDepartureBoardRequest>
    </soap:Body>
</soap:Envelope>"""

def parse_and_map_data(xml_response):
    """
    Parses the LDB XML response, finds the next two services, and constructs 
    the two-leg journey (STR -> CLJ -> IMW) enforcing a 4-minute layover.
    """
    
    root = ET.fromstring(xml_response)
    
    # Navigate through the SOAP/LDB structure to find the actual train services
    services_path = ".//ldb:GetDepartureBoardResponse/ldb:GetStationBoardResult/ldb:trainServices/ldb:service"
    services = root.findall(services_path, namespaces=NAMESPACES)
    
    mapped_services = []
    
    for i, service in enumerate(services[:2]): # Process only the first two services
        std_str = service.findtext('ldb:std', namespaces=NAMESPACES) # Scheduled Time of Departure (e.g., "20:01")
        etd_str = service.findtext('ldb:etd', namespaces=NAMESPACES) # Estimated Time of Departure (e.g., "On time" or "20:05")
        final_destination_name = service.findtext('ldb:destination/ldb:location/ldb:locationName', namespaces=NAMESPACES)
        platform = service.findtext('ldb:platform', namespaces=NAMESPACES)
        operator = service.findtext('ldb:operator', namespaces=NAMESPACES)
        
        # --- Real-Time Status Determination ---
        if etd_str == "On time" or etd_str is None or etd_str == std_str:
            status = "On Time"
            actual_departure_str = std_str
        elif etd_str == "Cancelled":
            status = "Cancelled"
            actual_departure_str = std_str # Still use scheduled for timing, but status is cancelled
        elif etd_str in ["Delayed", "No Report"]:
             status = f"Delayed (Expected {std_str})"
             actual_departure_str = std_str # Use scheduled time if no estimated time is available
        else:
             status = f"Delayed (Expected {etd_str})" # Use the actual estimated time
             actual_departure_str = etd_str # Use ETD for calculation

        # Parse the actual departure time for calculations
        try:
            # Assuming the date is today for time parsing
            departure_dt = datetime.strptime(actual_departure_str, "%H:%M").replace(year=datetime.now().year, month=datetime.now().month, day=datetime.now().day)
        except ValueError:
            # Handle cases where status is "Cancelled" or unparseable, skip this service
            continue 

        # --- Connection Calculation (Leg 1: STR -> CLJ) ---
        # NOTE: CLJ arrival time is estimated based on STR_TO_CLJ_MINUTES (8 minutes)
        clj_arrival_dt = departure_dt + timedelta(minutes=STR_TO_CLJ_MINUTES)
        
        # --- Layover Enforcement ---
        # The connection (CLJ -> IMW) must leave after the CLJ arrival + 4 minutes minimum
        required_clj_departure_dt = clj_arrival_dt + timedelta(minutes=MINIMUM_INTERCHANGE_MINUTES)
        
        # --- Connection Calculation (Leg 2: CLJ -> IMW) ---
        # We assume the CLJ -> IMW service is available exactly when the layover finishes.
        imw_departure_dt = required_clj_departure_dt
        imw_arrival_dt = imw_departure_dt + timedelta(minutes=CLJ_TO_IMW_MINUTES)
        
        # --- Total Duration Calculation ---
        total_duration_minutes = (imw_arrival_dt - departure_dt).total_seconds() / 60
        
        # Build the final structured JSON object
        mapped_services.append({
            "id": i + 1,
            "departureTime": departure_dt.strftime("%H:%M"),
            "arrivalTime": imw_arrival_dt.strftime("%H:%M"),
            # Format total duration to approximate minutes
            "totalDuration": f"{int(total_duration_minutes)} min", 
            "status": status,
            "live_updated_at": datetime.now().strftime("%H:%M:%S"),
            "legs": [
                {
                    # Leg 1: Streatham Common to Clapham Junction
                    "origin": ORIGIN_CRS, 
                    "destination": INTERCHANGE_CRS, 
                    "departure": actual_departure_str, 
                    "status": status, 
                    "platform": platform or "TBC", 
                    "operator": operator or "Southern"
                },
                {
                    # Connection Layover
                    "operator": "Change Train", 
                    "duration": f"{MINIMUM_INTERCHANGE_MINUTES} min", 
                    "status": "Connection"
                },
                {
                    # Leg 2: Clapham Junction to Imperial Wharf
                    "origin": INTERCHANGE_CRS, 
                    "destination": DESTINATION_CRS, 
                    "departure": imw_departure_dt.strftime("%H:%M"), # Calculated departure after layover
                    "status": "On Time", # Assume connection is on time for simplicity
                    "platform": "TBC", # Cannot determine connection platform from LDB
                    "operator": "Southern/Overground"
                }
            ]
        })
        
    return mapped_services


def fetch_and_process_darwin_data():
    """
    Connects to the Darwin LDB API using the secure token and fetches
    the next two real train departures from Streatham Common (STR).
    """
    
    if not DARWIN_API_KEY:
        print("ERROR: DARWIN_API_KEY environment variable is missing. Cannot fetch real data.")
        return []

    print(f"[{datetime.now().isoformat()}] Fetching REAL LDB data for {ORIGIN_CRS}...")
    
    # 1. Create Request
    soap_request = create_soap_payload(ORIGIN_CRS, DARWIN_API_KEY, num_rows=2)
    
    # 2. Define Headers
    headers = {
        # CRITICAL FIX: Changing Content-Type to text/xml for better LDB Lite compatibility
        'Content-Type': 'text/xml; charset=utf-8', 
        # CRITICAL FIX: Changing SOAPAction to match the 2017 namespace in the payload
        'SOAPAction': 'http://thalesgroup.com/RTTI/2017-10-01/ldb/GetDepartureBoard'
    }

    try:
        # 3. Send Request
        response = requests.post(LDB_API_ENDPOINT, data=soap_request.encode('utf-8'), headers=headers, timeout=10)
        response.raise_for_status() # Raise exception for bad status codes (4xx or 5xx)
        
        # 4. Check for SOAP Faults (API errors)
        if "soap:Fault" in response.text:
            print("ERROR: LDB API returned a SOAP Fault (check your API token or request parameters).")
            # print(response.text) 
            return []
            
        # 5. Parse and Map Data
        data = parse_and_map_data(response.text)
        
        print(f"Successfully fetched and generated {len(data)} REAL journey updates.")
        return data

    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to connect to LDB API: {e}")
        return []

def main():
    """Fetches data and saves it to the static JSON file."""
    data = fetch_and_process_darwin_data()
    
    if data:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Successfully saved REAL data to {OUTPUT_FILE}")
    else:
        print("No REAL data generated, skipping file save.")

if __name__ == "__main__":
    main()



