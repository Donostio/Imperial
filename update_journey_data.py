import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# --- 1. Credentials and Configuration ---
DARWIN_API_KEY = os.getenv("DARWIN_API_KEY")
OUTPUT_FILE = "live_data.json"

# ✅ Correct endpoint for 2021-11-01 schema
LDB_API_ENDPOINT = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb11.asmx"

# --- JOURNEY DETAILS ---
ORIGIN_CRS = "STR"  # Streatham Common
INTERCHANGE_CRS = "CLJ"  # Clapham Junction
DESTINATION_CRS = "IMW"  # Imperial Wharf
MINIMUM_INTERCHANGE_MINUTES = 4

# --- ESTIMATED TRAVEL TIMES ---
STR_TO_CLJ_MINUTES = 8
CLJ_TO_IMW_MINUTES = 10

# --- Namespaces ---
LDB_NAMESPACE_URL = "http://thalesgroup.com/RTTI/2021-11-01/ldb/"
TOKEN_NAMESPACE_URL = "http://thalesgroup.com/RTTI/2013-11-28/Token/types"

NAMESPACES = {
    'soap': 'http://www.w3.org/2003/05/soap-envelope/',
    'ldb': LDB_NAMESPACE_URL,
    'typ': TOKEN_NAMESPACE_URL
}


def create_soap_payload(crs_code, token, num_rows=2):
    """Creates SOAP body for GetDepartureBoard using 2021 schema."""
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
        <ldb:GetDepartureBoard>
            <ldb:numRows>{num_rows}</ldb:numRows>
            <ldb:crs>{crs_code}</ldb:crs>
        </ldb:GetDepartureBoard>
    </soap:Body>
</soap:Envelope>"""


def parse_and_map_data(xml_response):
    """Parses the LDB XML response and constructs the two-leg journey."""
    root = ET.fromstring(xml_response)
    services_path = ".//ldb:GetDepartureBoardResponse/ldb:GetStationBoardResult/ldb:trainServices/ldb:service"
    services = root.findall(services_path, namespaces=NAMESPACES)

    mapped_services = []
    for i, service in enumerate(services[:2]):
        std_str = service.findtext('ldb:std', namespaces=NAMESPACES)
        etd_str = service.findtext('ldb:etd', namespaces=NAMESPACES)
        platform = service.findtext('ldb:platform', namespaces=NAMESPACES)
        operator = service.findtext('ldb:operator', namespaces=NAMESPACES)

        # --- Status logic ---
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

        try:
            departure_dt = datetime.strptime(
                actual_departure_str, "%H:%M"
            ).replace(year=datetime.now().year,
                      month=datetime.now().month,
                      day=datetime.now().day)
        except ValueError:
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


def extract_fault(xml_response):
    """Extract SOAP Fault details if present."""
    try:
        root = ET.fromstring(xml_response)
        fault = root.find(".//soap:Fault", namespaces=NAMESPACES)
        if fault is not None:
            faultcode = fault.findtext("faultcode")
            faultstring = fault.findtext("faultstring")
            detail = fault.findtext("detail")
            return f"SOAP Fault: code={faultcode}, string={faultstring}, detail={detail}"
    except ET.ParseError:
        return "SOAP Fault (unparseable response)"
    return None


def fetch_and_process_darwin_data(debug=False):
    """Fetches data from Darwin LDB API."""
    if not DARWIN_API_KEY:
        print("ERROR: DARWIN_API_KEY environment variable is missing.")
        return []

    print(f"[{datetime.now().isoformat()}] Fetching REAL LDB data for {ORIGIN_CRS} using 2021 schema...")

    soap_request = create_soap_payload(ORIGIN_CRS, DARWIN_API_KEY, num_rows=2)

    headers = {
        'Content-Type': 'text/xml; charset=utf-8',
        'SOAPAction': f'{LDB_NAMESPACE_URL}GetDepartureBoard'
    }

    try:
        response = requests.post(
            LDB_API_ENDPOINT,
            data=soap_request.encode('utf-8'),
            headers=headers,
            timeout=10
        )
        # Do not raise immediately; inspect body for SOAP faults
        if response.status_code >= 400:
            print(f"ERROR: HTTP {response.status_code} from API")
            if debug:
                print(response.text[:1000])
            return []

        if debug:
            print("\n--- SOAP Request ---")
            print(soap_request)
            print("\n--- SOAP Response (raw) ---")
            print(response.text[:1000] + ("..." if len(response.text) > 1000 else ""))
            print("----------------------\n")

        # Check for SOAP Faults
        fault_msg = extract_fault(response.text)
        if fault_msg:
            print(f"ERROR: {fault_msg}")
            return []

        data = parse_and_map_data(response.text)
        print(f"Successfully fetched and generated {len(data)} REAL journey updates.")
        return data

    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to connect to LDB API: {e}")
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








