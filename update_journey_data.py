import os
import json
import requests
from datetime import datetime, timedelta

# --- Configuration ---
TFL_APP_ID = os.getenv("TFL_APP_ID", "")
TFL_APP_KEY = os.getenv("TFL_APP_KEY", "")
OUTPUT_FILE = "live_data.json"

# Journey parameters - using the exact format that worked
ORIGIN = "Streatham Common Rail Station"
DESTINATION = "Imperial Wharf Rail Station"

# TFL API endpoint
TFL_BASE_URL = "https://api.tfl.gov.uk"


def get_journey_plan(origin, destination):
    """Fetch journey plans from TFL Journey Planner API."""
    url = f"{TFL_BASE_URL}/Journey/JourneyResults/{origin}/to/{destination}"
    
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


def check_journey_via_clapham(journey):
    """Check if journey goes via Clapham Junction."""
    legs = journey.get('legs', [])
    
    for leg in legs:
        instruction = leg.get('instruction', {})
        summary = instruction.get('summary', '')
        
        if 'Clapham Junction' in summary:
            return True
        
        # Check path stopPoints
        path = leg.get('path', {})
        stop_points = path.get('stopPoints', [])
        for stop in stop_points:
            if 'Clapham Junction' in stop.get('name', ''):
                return True
    
    return False


def process_journey(journey, journey_id):
    """Process a TFL journey that goes via Clapham Junction."""
    start_time = parse_datetime(journey.get('startDateTime'))
    arrival_time = parse_datetime(journey.get('arrivalDateTime'))
    duration_mins = journey.get('duration', 0)
    
    legs = journey.get('legs', [])
    
    # Find rail legs (filter out walking)
    rail_legs = []
    for leg in legs:
        mode_name = leg.get('mode', {}).get('name', '')
        if mode_name not in ['walking', 'walk']:
            rail_legs.append(leg)
    
    if len(rail_legs) < 2:
        return None
    
    # First leg: Streatham Common to Clapham Junction
    leg1 = rail_legs[0]
    leg1_depart = parse_datetime(leg1.get('departureTime'))
    leg1_arrive = parse_datetime(leg1.get('arrivalTime'))
    leg1_instruction = leg1.get('instruction', {})
    
    # Try to extract platform from summary and detailed instructions
    leg1_platform = (extract_platform_from_instruction(leg1_instruction.get('summary', '')) or 
                     extract_platform_from_instruction(leg1_instruction.get('detailed', '')))
    
    # Second leg: Clapham Junction to Imperial Wharf
    leg2 = rail_legs[1]
    leg2_depart = parse_datetime(leg2.get('departureTime'))
    leg2_arrive = parse_datetime(leg2.get('arrivalTime'))
    leg2_instruction = leg2.get('instruction', {})
    
    leg2_platform = (extract_platform_from_instruction(leg2_instruction.get('summary', '')) or 
                     extract_platform_from_instruction(leg2_instruction.get('detailed', '')))
    
    # Calculate transfer time
    if leg1_arrive and leg2_depart:
        transfer_mins = int((leg2_depart - leg1_arrive).total_seconds() / 60)
    else:
        transfer_mins = 0
    
    # Determine status
    status = "On Time"
    for leg in legs:
        if leg.get('disruptions'):
            status = "Disruption"
            break
        if leg.get('isDisrupted'):
            status = "Delayed"
            break
    
    # Extract line names
    leg1_route = leg1.get('routeOptions', [])
    leg1_line = leg1_route[0].get('name', 'Rail') if leg1_route else 'Rail'
    
    leg2_route = leg2.get('routeOptions', [])
    leg2_line = leg2_route[0].get('name', 'Rail') if leg2_route else 'Rail'
    
    return {
        "id": journey_id,
        "departureTime": format_time(start_time),
        "arrivalTime": format_time(arrival_time),
        "totalDuration": f"{duration_mins} min",
        "status": status,
        "live_updated_at": datetime.now().strftime("%H:%M:%S"),
        "legs": [
            {
                "origin": "Streatham Common",
                "destination": "Clapham Junction",
                "departure": format_time(leg1_depart),
                "arrival": format_time(leg1_arrive),
                "arrivalPlatform": leg1_platform or "TBC",
                "operator": leg1_line,
                "status": status
            },
            {
                "type": "transfer",
                "location": "Clapham Junction",
                "transferTime": f"{transfer_mins} min"
            },
            {
                "origin": "Clapham Junction",
                "destination": "Imperial Wharf",
                "departure": format_time(leg2_depart),
                "departurePlatform": leg2_platform or "TBC",
                "arrival": format_time(leg2_arrive),
                "operator": leg2_line,
                "status": status
            }
        ]
    }


def fetch_and_process_tfl_data(num_journeys=3):
    """Fetch and process TFL journey data."""
    print(f"[{datetime.now().isoformat()}] Fetching TFL Journey Planner data...")
    
    journey_data = get_journey_plan(ORIGIN, DESTINATION)
    
    if not journey_data or 'journeys' not in journey_data:
        print("ERROR: No journey data received from TFL API")
        return []
    
    journeys = journey_data.get('journeys', [])
    print(f"Found {len(journeys)} total journeys from TFL")
    
    # Process journeys that go via Clapham Junction
    processed = []
    for idx, journey in enumerate(journeys, 1):
        try:
            if check_journey_via_clapham(journey):
                processed_journey = process_journey(journey, len(processed) + 1)
                if processed_journey:
                    processed.append(processed_journey)
                    print(f"✓ Journey {len(processed)}: {processed_journey['departureTime']} → {processed_journey['arrivalTime']}")
                    
                    if len(processed) >= num_journeys:
                        break
        except Exception as e:
            print(f"ERROR processing journey {idx}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"Successfully processed {len(processed)} journeys via Clapham Junction")
    return processed


def main():
    data = fetch_and_process_tfl_data(num_journeys=3)
    
    if data:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"✓ Successfully saved {len(data)} journeys to {OUTPUT_FILE}")
    else:
        print("⚠ No journey data generated.")


if __name__ == "__main__":
    main()
