import os
import json
import requests
from datetime import datetime, timedelta

# --- Configuration ---
# NOTE: TFL_APP_ID and TFL_APP_KEY must be set as environment variables
TFL_APP_ID = os.getenv("TFL_APP_ID", "")
TFL_APP_KEY = os.getenv("TFL_APP_KEY", "")
OUTPUT_FILE = "live_data.json"

# Journey parameters
ORIGIN = "Streatham Common Rail Station"
DESTINATION = "Imperial Wharf Rail Station"

# TFL API endpoint
TFL_BASE_URL = "https://api.tfl.gov.uk"
NUM_JOURNEYS = 4 # Target the next four journeys

# --- Utility Functions (Kept as is) ---

def get_journey_plan(origin, destination):
    """Fetch journey plans from TFL Journey Planner API."""
    url = f"{TFL_BASE_URL}/Journey/JourneyResults/{origin}/to/{destination}"
    
    # mode: ensures only train-based modes are considered (National Rail & Overground)
    params = {
        "mode": "overground,national-rail",
        "timeIs": "Departing",
        "journeyPreference": "LeastTime",
        "alternativeRoute": "true"
    }
    
    # Add API credentials if provided
    if TFL_APP_ID and TFL_APP_KEY:
        params["app_id"] = TFL_APP_ID
        params["app_key"] = TFL_APP_KEY
    
    try:
        print(f"Fetching journeys from {origin} to {destination}...")
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to fetch TFL data: {e}")
        return None


def parse_datetime(dt_string):
    """Parse TFL datetime string."""
    try:
        return datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def format_time(dt):
    """Format datetime to HH:MM."""
    if dt:
        return dt.strftime("%H:%M")
    return "N/A"


def extract_platform_from_instruction(instruction_text):
    """Try to extract platform from instruction text."""
    if not instruction_text:
        return None
    
    text_lower = instruction_text.lower()
    
    # Look for "platform X" pattern
    if 'platform' in text_lower:
        parts = text_lower.split('platform')
        if len(parts) > 1:
            after = parts[1].strip()
            platform = ''
            for char in after:
                if char.isdigit() or char.isalpha():
                    platform += char
                elif platform:
                    break
            if platform:
                return platform.upper()
    
    return None

# --- New/Modified Core Logic ---

def process_journey(journey, journey_id):
    """Process a TFL journey for direct or two-train routes."""
    start_time = parse_datetime(journey.get('startDateTime'))
    arrival_time = parse_datetime(journey.get('arrivalDateTime'))
    duration_mins = journey.get('duration', 0)
    
    legs = journey.get('legs', [])
    
    # Filter for only rail legs (National Rail or Overground)
    rail_legs = [
        leg for leg in legs 
        if leg.get('mode', {}).get('name', '') not in ['walking', 'walk']
    ]
    
    if not rail_legs:
        return None # Exclude journeys with no rail legs
    
    # Check for non-train legs *between* train legs (e.g., bus/tube/multiple changes)
    # The requirement is for train *only* (direct or 1 change, where the change is only a walk)
    # A quick way to ensure this is to check if the total rail legs plus walking legs equals the total legs.
    allowed_modes = ['walking', 'walk', 'national-rail', 'overground']
    if any(leg.get('mode', {}).get('name', '') not in allowed_modes for leg in legs):
        return None # Exclude complex multi-modal journeys

    # Determine status
    status = "On Time"
    for leg in legs:
        if leg.get('disruptions') or leg.get('isDisrupted'):
            status = "Disruption/Delayed"
            break
            
    processed_legs = []
    transfer_mins = 0
    is_two_train = False

    # --- Direct Train (1 rail leg) ---
    if len(rail_legs) == 1:
        leg1 = rail_legs[0]
        leg1_depart = parse_datetime(leg1.get('departureTime'))
        leg1_arrive = parse_datetime(leg1.get('arrivalTime'))
        leg1_instruction = leg1.get('instruction', {})
        leg1_route = leg1.get('routeOptions', [])
        leg1_line = leg1_route[0].get('name', 'Rail') if leg1_route else 'Rail'
        
        # Departure platform (at Streatham Common)
        leg1_platform = (extract_platform_from_instruction(leg1_instruction.get('detailed', '')))
        
        # Arrival platform (at Imperial Wharf) - No platform data usually for arrival at destination
        
        processed_legs.append({
            "origin": "Streatham Common",
            "destination": "Imperial Wharf",
            "departure": format_time(leg1_depart),
            "arrival": format_time(leg1_arrive),
            "departurePlatform": leg1_platform or "TBC",
            "operator": leg1_line,
            "status": status
        })

    # --- Two-Train Journey (2 rail legs) ---
    elif len(rail_legs) == 2:
        is_two_train = True
        
        # Leg 1: Streatham Common to Interchange (e.g. Clapham Junction)
        leg1 = rail_legs[0]
        leg1_depart = parse_datetime(leg1.get('departureTime'))
        leg1_arrive = parse_datetime(leg1.get('arrivalTime'))
        leg1_instruction = leg1.get('instruction', {})
        leg1_route = leg1.get('routeOptions', [])
        leg1_line = leg1_route[0].get('name', 'Rail') if leg1_route else 'Rail'

        # Leg 2: Interchange to Imperial Wharf
        leg2 = rail_legs[1]
        leg2_depart = parse_datetime(leg2.get('departureTime'))
        leg2_arrive = parse_datetime(leg2.get('arrivalTime'))
        leg2_instruction = leg2.get('instruction', {})
        leg2_route = leg2.get('routeOptions', [])
        leg2_line = leg2_route[0].get('name', 'Rail') if leg2_route else 'Rail'

        # Interchange Station (e.g., Clapham Junction)
        interchange = leg1.get('arrivalPoint', {}).get('commonName', 'Interchange')

        # Extract Platform data for Clapham Junction
        leg1_arrival_platform = (extract_platform_from_instruction(leg1_instruction.get('detailed', '')))
        leg2_departure_platform = (extract_platform_from_instruction(leg2_instruction.get('detailed', '')))

        # Calculate transfer time
        if leg1_arrive and leg2_depart:
            transfer_mins = int((leg2_depart - leg1_arrive).total_seconds() / 60)
        
        # First Train Leg
        processed_legs.append({
            "origin": "Streatham Common",
            "destination": interchange,
            "departure": format_time(leg1_depart),
            "arrival": format_time(leg1_arrive),
            "arrivalPlatform_ClaphamJunction": leg1_arrival_platform or "TBC", # Specific request for arrival platform
            "operator": leg1_line,
            "status": status
        })
        
        # Transfer Detail
        processed_legs.append({
            "type": "transfer",
            "location": interchange,
            "transferTime": f"{transfer_mins} min"
        })
        
        # Second Train Leg
        processed_legs.append({
            "origin": interchange,
            "destination": "Imperial Wharf",
            "departure": format_time(leg2_depart),
            "arrival": format_time(leg2_arrive),
            "departurePlatform_ClaphamJunction": leg2_departure_platform or "TBC", # Specific request for departure platform
            "operator": leg2_line,
            "status": status
        })

    else:
        # Exclude journeys with 0, 3, or more rail legs to simplify output
        return None

    return {
        "id": journey_id,
        "type": "Direct" if len(rail_legs) == 1 else "One Change",
        "departureTime": format_time(start_time),
        "arrivalTime": format_time(arrival_time),
        "totalDuration": f"{duration_mins} min",
        "status": status,
        "live_updated_at": datetime.now().strftime("%H:%M:%S"),
        "legs": processed_legs
    }


def fetch_and_process_tfl_data(num_journeys):
    """Fetch and process TFL journey data for a fixed number of valid train journeys."""
    print(f"[{datetime.now().isoformat()}] Fetching TFL Journey Planner data...")
    
    journey_data = get_journey_plan(ORIGIN, DESTINATION)
    
    if not journey_data or 'journeys' not in journey_data:
        print("ERROR: No journey data received from TFL API")
        return []
    
    journeys = journey_data.get('journeys', [])
    print(f"Found {len(journeys)} total journeys from TFL")
    
    processed = []
    for idx, journey in enumerate(journeys, 1):
        try:
            processed_journey = process_journey(journey, len(processed) + 1)
            if processed_journey:
                processed.append(processed_journey)
                print(f"✓ Journey {len(processed)} ({processed_journey['type']}): {processed_journey['departureTime']} → {processed_journey['arrivalTime']}")
                
                if len(processed) >= num_journeys:
                    break
        except Exception as e:
            print(f"ERROR processing journey {idx}: {e}")
            # import traceback
            # traceback.print_exc()
            continue
    
    print(f"Successfully processed {len(processed)} train journeys (Direct or One Change)")
    return processed


def main():
    data = fetch_and_process_tfl_data(NUM_JOURNEYS)
    
    if data:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"✓ Successfully saved {len(data)} journeys to {OUTPUT_FILE}")
    else:
        print("⚠ No journey data generated.")


if __name__ == "__main__":
    main()
