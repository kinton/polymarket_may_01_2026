"""
Query Polymarket Gamma API to find currently active 15-minute Bitcoin/Ethereum markets.

Features:
- Get current time in ET timezone
- Calculate the current 15-minute window (e.g., 10:00–10:15)
- Query https://gamma-api.polymarket.com/public-search for Bitcoin/Ethereum markets
- Filter for markets ending within the next 20 minutes (configurable)
- Return condition_id, token_id for YES/NO, and end_time

Usage:
    python gamma_15m_finder.py
    
Or with uv:
    uv run gamma_15m_finder.py
    
The script will output:
- Current 15-minute trading window
- Any matching markets with their condition_id, token IDs, and end times
- Returns None if no active markets are found
"""

import aiohttp
import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Optional, Dict, List, Any


class GammaAPI15mFinder:
    """Find active 15-minute Bitcoin/Ethereum markets on Polymarket."""
    
    BASE_URL = "https://gamma-api.polymarket.com/public-search"
    ET_TZ = timezone(timedelta(hours=-5))  # EST (adjust to -4 for EDT if needed)
    
    def __init__(self):
        self.current_time_et = None
        self.current_window = None
    
    def get_current_time_et(self) -> datetime:
        """Get current time in ET timezone."""
        # Get UTC time first, then convert to ET
        utc_now = datetime.now(timezone.utc)
        self.current_time_et = utc_now.astimezone(self.ET_TZ)
        return self.current_time_et
    
    def calculate_15m_window(self) -> tuple[str, str]:
        """
        Calculate current 15-minute window.
        Returns: (window_start, window_end) in format "HH:MM"
        Example: ("10:00", "10:15")
        """
        now = self.get_current_time_et()
        
        # Round down to nearest 15-minute interval
        minute = (now.minute // 15) * 15
        window_start = now.replace(minute=minute, second=0, microsecond=0)
        window_end = window_start + timedelta(minutes=15)
        
        start_str = window_start.strftime("%H:%M")
        end_str = window_end.strftime("%H:%M")
        
        self.current_window = (start_str, end_str)
        print(f"Current time (ET): {now.strftime('%H:%M:%S')}")
        print(f"Current 15-min window: {start_str}–{end_str}")
        
        return self.current_window
    
    async def search_markets(
        self,
        query: str = "Bitcoin Up or Down",
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Query Gamma API public search endpoint.
        Note: The API expects 'q' parameter, not 'query'
        """
        try:
            async with aiohttp.ClientSession() as session:
                # API expects 'q' parameter
                params = {"q": query}
                
                async with session.get(self.BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        try:
                            return await response.json()
                        except Exception:
                            return {"markets": []}
                    elif response.status == 422:
                        # API returns 422 for validation issues - try to get error details
                        try:
                            error_data = await response.json()
                            print(f"API validation error: {error_data}")
                        except Exception:
                            print(f"API Error: {response.status}")
                        return {"markets": []}
                    else:
                        print(f"API Error: {response.status}")
                        return {"markets": []}
        except asyncio.TimeoutError:
            print("API request timed out")
            return {"markets": []}
        except Exception as e:
            print(f"Error querying API: {e}")
            return {"markets": []}
    
    def filter_markets(
        self,
        events: List[Dict[str, Any]],
        target_window: tuple[str, str],
        max_minutes_ahead: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Filter markets to find those matching the 15-minute window
        and ending within max_minutes_ahead.
        Works with Polymarket 'events' objects from Gamma API.
        """
        now = self.get_current_time_et()
        filtered_markets = []
        
        print(f"\nFiltering {len(events)} events...")
        print(f"Target window: {target_window[0]}–{target_window[1]} ET")
        print(f"Current time: {now.strftime('%H:%M:%S %Z')}")
        print()
        
        for event in events:
            try:
                # Skip inactive or closed events
                if not event.get("active", False) or event.get("closed", False):
                    continue
                
                # Events can have nested markets array
                markets_in_event = event.get("markets", [])
                
                # If no nested markets, treat event itself as a market
                if not markets_in_event:
                    markets_in_event = [event]
                
                for market in markets_in_event:
                    # Skip inactive or closed markets
                    if not market.get("active", False) or market.get("closed", False):
                        continue
                    
                    # Get end time from the market
                    end_time_str = market.get("endDate") or market.get("endTime") or market.get("end_time")
                    if not end_time_str:
                        continue
                    
                    # Parse end_time (usually ISO format)
                    if isinstance(end_time_str, str):
                        # Handle ISO format with 'Z' or timezone info
                        end_time_str = end_time_str.replace("Z", "+00:00")
                        try:
                            end_time = datetime.fromisoformat(end_time_str)
                            # Convert to ET if in UTC
                            if end_time.tzinfo is None or end_time.tzinfo == timezone.utc:
                                end_time = end_time.replace(tzinfo=timezone.utc).astimezone(self.ET_TZ)
                        except ValueError:
                            continue
                    else:
                        continue
                    
                    # Check if market ends within max_minutes_ahead (requirement: less than 20 minutes)
                    time_until_end = (end_time - now).total_seconds() / 60
                    
                    if time_until_end < 0 or time_until_end > max_minutes_ahead:
                        continue
                    
                    # Market is ending within the time window - add it
                    # Get condition_id and token_ids
                    condition_id = market.get("conditionId") or market.get("condition_id") or market.get("id")
                    title = market.get("question") or market.get("title", "N/A")
                    
                    # Extract token IDs from clobTokenIds if available
                    token_ids_raw = market.get("clobTokenIds")
                    token_id_yes = None
                    token_id_no = None
                    
                    if token_ids_raw:
                        try:
                            if isinstance(token_ids_raw, str):
                                token_ids = json.loads(token_ids_raw)
                                if len(token_ids) >= 2:
                                    token_id_yes = token_ids[0]
                                    token_id_no = token_ids[1]
                            elif isinstance(token_ids_raw, list) and len(token_ids_raw) >= 2:
                                token_id_yes = token_ids_raw[0]
                                token_id_no = token_ids_raw[1]
                        except Exception:
                            pass
                    
                    filtered_markets.append({
                        "condition_id": condition_id,
                        "token_id_yes": token_id_yes or "N/A",
                        "token_id_no": token_id_no or "N/A",
                        "end_time": end_time.strftime("%H:%M:%S %Z"),
                        "end_time_utc": end_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        "minutes_until_end": round(time_until_end, 1),
                        "title": title,
                        "ticker": event.get("ticker", "N/A"),
                    })
            except Exception as e:
                print(f"Error processing market: {e}")
                continue
        
        return filtered_markets
    
    async def find_active_market(self) -> Optional[List[Dict[str, Any]]]:
        """
        Main function to find active 15-minute Bitcoin/Ethereum markets.
        Searches for 'Bitcoin Up or Down' and 'Ethereum Up or Down' matching current window.
        """
        # Step 1: Calculate current 15-minute window
        window = self.calculate_15m_window()
        print()
        
        # Step 2: Query API for Bitcoin/Ethereum markets
        print("Querying Polymarket Gamma API...")
        
        # Build time-specific queries to find 15-minute markets
        # API search works better with specific time patterns
        now = self.get_current_time_et()
        current_hour_24 = now.hour
        current_date = now.strftime("January %d")
        
        # Convert to 12-hour format for matching market titles
        hour_12 = current_hour_24 % 12
        if hour_12 == 0:
            hour_12 = 12
        
        # Try current and next hour in 12-hour format
        # 15-minute markets use format like "7:15PM-7:30PM ET"
        queries = []
        for crypto in ["Bitcoin", "Ethereum"]:
            # Current hour patterns
            queries.append(f"{crypto} Up or Down - {current_date}, {hour_12}:")
            
            # Next hour
            next_hour_24 = (current_hour_24 + 1) % 24
            next_hour_12 = next_hour_24 % 12
            if next_hour_12 == 0:
                next_hour_12 = 12
            queries.append(f"{crypto} Up or Down - {current_date}, {next_hour_12}:")
        
        # Also try generic search as fallback
        queries.extend(["Bitcoin Up or Down", "Ethereum Up or Down"])
        
        all_events = []
        seen_ids = set()
        
        for query in queries:
            markets_data = await self.search_markets(query=query)
            events = markets_data.get("events", [])
            
            if events:
                print(f"Query '{query[:50]}...' returned {len(events)} events")
                
                # Deduplicate events by ID
                for event in events:
                    event_id = event.get("id")
                    if event_id and event_id not in seen_ids:
                        seen_ids.add(event_id)
                        all_events.append(event)
        
        if not all_events:
            print("No markets found with any query")
            return None
        
        print(f"\nFound {len(all_events)} unique events total")
        
        # Step 3: Filter for active markets ending in less than 30 minutes (was 20)
        active_markets = self.filter_markets(all_events, window, max_minutes_ahead=30)
        
        if not active_markets:
            print("\nNo matching markets found ending in less than 20 minutes")
            return None
        
        print(f"\nFound {len(active_markets)} matching market(s):")
        print("-" * 80)
        for market in active_markets:
            print(f"Title: {market['title']}")
            print(f"Condition ID: {market['condition_id']}")
            print(f"Token ID (YES): {market['token_id_yes']}")
            print(f"Token ID (NO): {market['token_id_no']}")
            print(f"End Time (ET): {market['end_time']}")
            print(f"End Time (UTC): {market['end_time_utc']}")
            print(f"Minutes until end: {market['minutes_until_end']}")
            print("-" * 80)
        
        return active_markets


async def main():
    """Main entry point."""
    finder = GammaAPI15mFinder()
    markets = await finder.find_active_market()
    
    if markets:
        return markets
    else:
        print("No active markets found")
        return None


if __name__ == "__main__":
    result = asyncio.run(main())
    if result:
        print("\n" + json.dumps(result, indent=2))
