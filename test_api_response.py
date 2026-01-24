"""Quick test to see what the API actually returns."""
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

async def test_api():
    # Get current ET time
    et_tz = timezone(timedelta(hours=-5))
    now_et = datetime.now(timezone.utc).astimezone(et_tz)
    print(f"Current ET time: {now_et.strftime('%H:%M:%S')}")
    
    # Calculate current 15-minute window
    minute = (now_et.minute // 15) * 15
    window_start = now_et.replace(minute=minute, second=0, microsecond=0)
    window_end = window_start + timedelta(minutes=15)
    print(f"Current 15-min window: {window_start.strftime('%H:%M')}–{window_end.strftime('%H:%M')} ET\n")
    
    # Try more specific searches matching the actual market titles
    queries = [
        f"Bitcoin Up or Down - January {now_et.day}",
        "Bitcoin Up or Down January 24",
        "Bitcoin Up or Down PM ET",
        "Bitcoin Up or Down AM ET",
    ]
    
    for query in queries:
        print(f"{'='*80}")
        print(f"Query: '{query}'")
        print('='*80)
        
        url = "https://gamma-api.polymarket.com/public-search"
        params = {"q": query}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    events = data.get("events", [])
                    print(f"Found {len(events)} events\n")
                    
                    for event in events[:3]:
                        title = event.get('title', 'N/A')
                        active = event.get('active', False)
                        closed = event.get('closed', False)
                        end_date = event.get('endDate', 'N/A')
                        
                        # Parse end time
                        try:
                            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00')).astimezone(et_tz)
                            time_remaining = (end_dt - now_et).total_seconds() / 60
                            print(f"{title}")
                            print(f"  Status: Active={active}, Closed={closed}")
                            print(f"  Ends: {end_dt.strftime('%Y-%m-%d %H:%M %Z')}")
                            print(f"  Time until end: {time_remaining:.1f} minutes")
                        except Exception:
                            print(f"{title}")
                            print(f"  Status: Active={active}, Closed={closed}")
                            print(f"  End: {end_date}")
                        print()
                else:
                    print(f"Error: {response.status}\n")

asyncio.run(test_api())



