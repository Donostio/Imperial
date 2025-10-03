import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# --- 1. Credentials and Configuration ---
# API Key is passed via GitHub Secrets (DARWIN_API_KEY)
DARWIN_API_KEY = os.getenv("DARWIN_API_KEY")
OUTPUT_FILE = "live_data.json"
# Generic and stable LDB endpoint
LDB_API_ENDPOINT = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb.asmx"

# --- JOURNEY DETAILS ---
ORIGIN_CRS = "STR" # Streatham Common
INTERCHANGE_CRS = "CLJ" # Clapham Junction
DESTINATION_CRS = "IMW" # Imperial Wharf (Targeted destination, not primary query)
MINIMUM_INTERCHANGE_MINUTES = 4

# --- ESTIMATED TRAVEL TIMES for Calculation ---
STR_TO_CLJ_MINUTES = 8  # Estimated time from Streatham Common to Clapham Junction
CLJ_TO_IMW_MINUTES = 10 # Estimated time for connection leg (Clapham Junc. to Imperial Wharf)

# CRITICAL FIX: Using the LDB 2021-11-01 namespace as per documentation
LDB_NAMESPACE_URL = 'http://thalesgroup.com/RTTI/2021-11-01/ldb/'
# Token namespace remains consistent
TOKEN_NAMESPACE_URL = 'http://thalesgroup.com/RTTI/2013-11-28/Token/types'

NAMESPACES = {
    'soap': 'http://www.w3.org/2003/05/soap-envelope/',
    'ldb': LDB_NAMESPACE_URL,
    'typ': TOKEN_NAMESPACE_URL
}


def create_soap_payload(crs_code, token, num_rows=2):
    """Creates the XML body for the GetDepartureBoardRequest using the latest schema."""
    
    # Using the 2021-11-01 LDB namespace in the XML payload
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:ldb="{LDB_NAMESPACE_URL}"
               xmlns:typ="{TOKEN_NAMESPACE_URL}">
    <soap:Header>
        <typ:AccessToken>
            <typ:TokenValue>{token}</typ:TokenValue>
        </typ:AccessToken>
    </soap:Header>
    <soap:Body>
        <ldb:GetDepartureBoardRequest>
            <ldb:numRows>{num_rows}</ldb:numRows>
            <ldb:crs>{crs_code}</ldb:crs>
        </ldb:GetDepartureBoardRequest>
    </soap:Body>
</soap:Envelope>"""


def parse_and_map_data(xml_response):
    """Parses the LDB XML response and constructs the two-leg journey."""

    root = ET.fromstring(xml_response)

    # Note: XPath structure remains mostly consistent across LDB versions
    services_path = ".//ldb:GetDepartureBoardResponse/ldb:GetStationBoardResult/ldb:trainServices/ldb:service"
    services = root.findall(services_path, namespaces=NAMESPACES)

    mapped_services = []

    for i, service in enumerate(services[:2]):
        # The fields 'std', 'etd', 'platform', 'operator' are consistent across versions
        std_str = service.findtext('ldb:std', namespaces=NAMESPACES)
        etd_str = service.findtext('ldb:etd', namespaces=NAMESPACES)
        platform = service.findtext('ldb:platform', namespaces=NAMESPACES)
        operator = service.findtext('ldb:operator', namespaces=NAMESPACES)

        # --- Real-Time Status ---
        if etd_str == "On time" or etd_str is None or etd_str == std_str:
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

        # Parse actual departure time
        try:
            departure_dt = datetime.strptime(
                actual_departure_str, "%H:%M"
            ).replace(year=datetime.now().year,
                      month=datetime.now().month,
                      day=datetime.now().day)
        except ValueError:
            continue

        # --- Connection Calculation (Estimating CLJ arrival/departure) ---
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
    """Fetches data from Darwin LDB API."""

    if not DARWIN_API_KEY:
        print("ERROR: DARWIN_API_KEY environment variable is missing.")
        return []

    print(f"[{datetime.now().isoformat()}] Fetching REAL LDB data for {ORIGIN_CRS} using 2021 schema...")

    soap_request = create_soap_payload(ORIGIN_CRS, DARWIN_API_KEY, num_rows=2)

    headers = {
        'Content-Type': 'text/xml; charset=utf-8',
        # CRITICAL FIX: Updating SOAPAction to match the LDB 2021-11-01 namespace
        'SOAPAction': f'{LDB_NAMESPACE_URL}GetDepartureBoard'
    }

    try:
        response = requests.post(
            LDB_API_ENDPOINT,
            data=soap_request.encode('utf-8'),
            headers=headers,
            timeout=10
        )
        response.raise_for_status()

        # 🔍 Debug logs
        if debug:
            print("\n--- SOAP Request ---")
            print(soap_request)
            print("\n--- SOAP Response (raw) ---")
            print(response.text[:1000] + ("..." if len(response.text) > 1000 else ""))
            print("----------------------\n")

        if "soap:Fault" in response.text:
            print("ERROR: LDB API returned a SOAP Fault (invalid token or request).")
            # If debug is enabled, the full response will show the fault detail
            return []

        data = parse_and_map_data(response.text)
        print(f"Successfully fetched and generated {len(data)} REAL journey updates.")
        return data

    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to connect to LDB API: {e}")
        return []


def main():
    """Main entry: fetch and save data."""
    data = fetch_and_process_darwin_data(debug=True)

    if data:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Successfully saved REAL data to {OUTPUT_FILE}")
    else:
        print("No REAL data generated, skipping file save.")


if __name__ == "__main__":
    main()






