import os
import json
from datetime import datetime
import time

# --- 1. Credentials (Read from GitHub Secrets via Environment Variables) ---
DARWIN_API_KEY = os.getenv("DARWIN_API_KEY")
OUTPUT_FILE = "live_data.json"
# Note: We are using the single DARWIN_API_KEY (the OpenLDBWS Token)
# for the polling strategy, instead of a separate username/password pair.

def fetch_and_process_darwin_data():
    """
    Simulates connecting to the Darwin LDB API using the secure token
     and generates a simplified journey list structure.
    
    In a real-world scenario, you would use the 'requests' library to call 
    the LDB Web Service endpoint (e.g., SOAP/XML request) and use the API key
    for authentication.
    """
    
    if not DARWIN_API_KEY:
        # Check for the single API key now, which is what the GitHub Action provides.
        print("ERROR: DARWIN_API_KEY environment variable is missing.")
        return []

    # In a real app, this would be an HTTP request to the LDB endpoint:
    # headers = {'Content-Type': 'application/soap+xml'}
    # response = requests.post(LDB_URL, data=SOAP_PAYLOAD, headers=headers)
    
    print(f"[{datetime.now().isoformat()}] Securely simulating LDB data fetch using token...")
    
    # --- SIMULATION of Darwin API call ---
    # We simulate the real-time update logic by adding a "live" timestamp 
    # and randomly changing the status of the second train.
    
    current_time_str = datetime.now().strftime("%H:%M:%S")
    
    # Randomize the status for simulation
    statuses = ["On Time", "Delayed (Expected 20:05)", "Cancelled", "Delayed (Expected 19:45)"]
    # Generates a pseudo-random index based on the 5-minute interval
    current_status_index = int(time.time() // (5 * 60)) % len(statuses)
    
    mock_journey_data = [
        {
            "id": 1,
            "departureTime": "19:25",
            "arrivalTime": "19:55",
            "totalDuration": "30 min",
            "status": "On Time",
            "live_updated_at": current_time_str,
            "legs": [
                {"origin": "SRC", "destination": "CLJ", "departure": "19:25", "status": "On Time", "platform": "2", "operator": "Southern"},
                {"operator": "Change Train", "duration": "5 min", "status": "Connection"},
                {"origin": "CLJ", "destination": "IMW", "departure": "19:40", "status": "On Time", "platform": "17", "operator": "Southern/Overground"}
            ]
        },
        {
            "id": 2,
            "departureTime": "19:40",
            "arrivalTime": "20:15",
            "totalDuration": "35 min",
            "status": statuses[(current_status_index + 1) % len(statuses)], # Random status
            "live_updated_at": current_time_str,
            "legs": [
                {"origin": "SRC", "destination": "CLJ", "departure": "19:40", "status": "Varies", "platform": "2", "operator": "Southern"},
                {"operator": "Change Train", "duration": "6 min", "status": "Connection"},
                {"origin": "CLJ", "destination": "IMW", "departure": "19:56", "status": "Varies", "platform": "17", "operator": "Southern/Overground"}
            ]
        }
    ]
    
    print(f"Generated {len(mock_journey_data)} journeys.")
    return mock_journey_data

def main():
    """Fetches data and saves it to the static JSON file."""
    data = fetch_and_process_darwin_data()
    
    if data:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Successfully saved data to {OUTPUT_FILE}")
    else:
        print("No data generated, skipping file save.")

if __name__ == "__main__":
    main()

