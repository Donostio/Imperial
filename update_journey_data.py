import os
import json
import requests
from datetime import datetime, timedelta

# --- Configuration ---
TFL_APP_ID = os.getenv("TFL_APP_ID", "")
TFL_APP_KEY = os.getenv("TFL_APP_KEY", "")
OUTPUT_FILE = "live_data.json"

# Station NaPTAN IDs (more reliable than names)
STREATHAM_COMMON = "910GSTRHMC"  # Streatham Common
CLAPHAM_JUNCTION = "910GCLPHMJ"  # Clapham Junction
IMPERIAL_WHARF = "910GIMPRLW"    # Imperial Wharf

# TFL API endpoint
TFL_BASE_URL = "https://api.tfl.gov.uk"


def get_auth_params():
    """Return authentication parameters if available."""
    if TFL_APP_ID and TFL_APP_KEY:
        return {"app_id": TFL_APP_ID, "app_key": TFL_APP_KEY}
    return {}


def get_arrivals_for_station(naptan_id):
    """Get live arrivals/departures for a station to extract platform info."""
    url = f"{TFL_BASE_URL}/StopPoint/{naptan_id}/Arrivals"
    params = get_auth_params()
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except:
        return []


def get_platform_from_arrivals(naptan_id, line_name, departure_time):
    """Try to get platform info from live arrivals data."""
    arrivals = get_arrivals_for_station(naptan_id)
    
    # Parse target time
    target_time = None
    try:
        target_time = datetime.strptime(departure_time, "%H:%M")
        target_time = target_time.replace(
            year=datetime.now().year,
            month=datetime.now().month,
            day=datetime.now().day
        )
    except:
        return None
    
    # Look for matching train
    for arrival in arrivals:
        # Check if line matches
        if line_name.lower() in arrival.get('lineName', '').lower():
            # Check time (within 2 minutes)
            expected = arrival.get('expectedArrival')
            if expected:
                try:
                    arrival_dt = datetime.fromisoformat(expected.replace('Z', '+00:00'))
                    time_diff = abs((arrival_dt - target_time).total_seconds() / 60)
                    
                    if time_diff <= 2:
                        platform = arrival.get('platformName', '')
                        if platform:
                            return platform.replace('Platform ', '').strip()
                except:
                    continue
    
    return None


def get_journey_plan(origin, destination):
    """Fetch journey plans from TFL Journey Planner API."""
    url = f"{TFL_BASE_URL}/Journey/JourneyResults/{origin}/to/{destination}"
    
    params = {
        "mode": "overground,national-rail",
        "timeIs": "Departing",
        "journeyPreference": "LeastTime",
        "maxTransferMinutes": "25",
        "walkingSpeed": "Average"
    }
    params.update(get_auth_params())
    
    try:
        print(f"Fetching journeys from {origin} to {destination}...")
        response = requests.get(url, params=params, timeout=15)
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


def extract_platform_from_instruction(instruction_summary):
    """Try to extract platform from instruction text."""
    if 'platform' in instruction_summary.lower():
        parts = instruction_summary.lower().split('platform')
        if len(parts) > 1:
            # Get text after "platform"
            after = parts[1].strip()
            # Extract number/letter
            platform = ''
            for char in after:
                if char.isdigit() or char.isalpha():
                    platform += char
                elif platform:
                    break
            if platform:
                return platform
    return None


def process_journey(journey, journey_id):
    """Process a TFL journey that goes via Clapham Junction."""
    start_time = parse_datetime(journey.get('startDateTime'))
    arrival_time = parse_datetime(journey.get('arrivalDateTime'))
    duration_mins = journey.get('duration', 0)
    
    legs = journey.get('legs', [])
    
    # Find rail legs only
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
    leg1_instruction = leg1.get('instruction', {}).get('summary', '')
    
    # Try to get platform info
    leg1_platform = extract_platform_from_instruction(leg1_instruction)
    if not leg1_platform:
        leg1_platform = get_platform_from_arrivals(
            CLAPHAM_JUNCTION,
            leg1.get('routeOptions', [{}])[0].get('name', 'Southern'),
            format_time(leg1_arrive)
        )
    
    # Second leg: Clapham Junction to Imperial Wharf
    leg2 = rail_legs[1]
    leg2_depart = parse_datetime(leg2.get('departureTime'))
    leg2_arrive = parse_datetime(leg2.get('arrivalTime'))
    leg2_instruction = leg2.get('instruction', {}).get('summary', '')
    
    leg2_platform = extract_platform_from_instruction(leg2_instruction)
    if not leg2_platform:
        leg2_platform = get_platform_from_arrivals(
            CLAPHAM_JUNCTION,
            leg2.get('routeOptions', [{}])[0].get('name', 'London Overground'),
            format_time(leg2_depart)
        )
    
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
    
    # Try multiple times with different time offsets to get more results
    all_processed = []
    
    for offset_mins in [0, 15, 30]:
        journey_data = get_journey_plan(STREATHAM_COMMON, IMPERIAL_WHARF)
        
        if not journey_data or 'journeys' not in journey_data:
            continue
        
        journeys = journey_data.get('journeys', [])
        print(f"Found {len(journeys)} total journeys (offset: {offset_mins} mins)")
        
        for journey in journeys:
            try:
                if check_journey_via_clapham(journey):
                    # Check if we already have this departure time
                    depart = format_time(parse_datetime(journey.get('startDateTime')))
                    if any(j['departureTime'] == depart for j in all_processed):
                        continue
                    
                    processed_journey = process_journey(journey, len(all_processed) + 1)
                    if processed_journey:
                        all_processed.append(processed_journey)
                        print(f"✓ Journey {len(all_processed)}: {processed_journey['departureTime']} → {processed_journey['arrivalTime']}")
                        
                        if len(all_processed) >= num_journeys:
                            break
            except Exception as e:
                print(f"ERROR processing journey: {e}")
                continue
        
        if len(all_processed) >= num_journeys:
            break
    
    # Re-number journeys
    for idx, journey in enumerate(all_processed, 1):
        journey['id'] = idx
    
    print(f"Successfully processed {len(all_processed)} journeys via Clapham Junction")
    return all_processed


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
