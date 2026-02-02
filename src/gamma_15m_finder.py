"""
Query Polymarket Gamma API to find currently active binary markets.

Features:
- Get current time in ET timezone
- Search for markets ending within max_minutes_ahead (default: 20 minutes)
- Wide search mode (default): fetch all markets without query restrictions
- Filter for markets ending within the specified time window
- Return only strictly binary markets (exactly 2 outcomes: YES/NO)
- Return condition_id, token_id for YES/NO, end_time, title, slug

Usage:
    python gamma_15m_finder.py

Or with uv:
    uv run gamma_15m_finder.py

The script will output:
- Current time in ET
- Any matching markets with their condition_id, token IDs, and end times
- Returns None if no active markets are found
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp


class GammaAPI15mFinder:
    """Find active binary markets on Polymarket."""

    BASE_URL = "https://gamma-api.polymarket.com/public-search"
    ET_TZ = timezone(timedelta(hours=-5))  # EST (adjust to -4 for EDT if needed)

    def __init__(self, max_minutes_ahead: int = 20, use_wide_search: bool = False):
        """Initialize finder.

        Args:
            max_minutes_ahead: Maximum minutes ahead to search for markets (default: 20)
            use_wide_search: If True, also use wide search with single letters (default: False)
        """
        self.current_time_et = None
        self.current_window = None
        self.max_minutes_ahead = max_minutes_ahead
        self.use_wide_search = use_wide_search
        # Always load base queries (Bitcoin/Ethereum + custom from env)
        self.base_queries = self._load_base_queries()

    def _load_base_queries(self) -> List[str]:
        """Load base queries from env or use defaults.

        Env format: MARKET_QUERIES="Query1;Query2;Query3"

        Default: "Up or Down" - finds ALL 5/15-min binary markets
        (crypto, stocks, commodities, indices, etc.)

        Custom queries via MARKET_QUERIES for specific events:
            MARKET_QUERIES="Trump;Election;President"  # Political
            MARKET_QUERIES="Fed;rates;FOMC"            # Economic
        """
        env_val = os.getenv("MARKET_QUERIES")

        # Universal "Up or Down" query finds ALL 5m/15m binary markets
        # (Bitcoin, Ethereum, Solana, Stocks, Gold, Oil, etc.)
        default_queries = [
            "Up or Down",
        ]

        if env_val:
            # Add custom queries from env
            custom_queries = [q.strip() for q in env_val.split(";") if q.strip()]
            return default_queries + custom_queries

        return default_queries

    def get_current_time_et(self) -> datetime:
        """Get current time in ET timezone."""
        # Get UTC time first, then convert to ET
        utc_now = datetime.now(timezone.utc)
        self.current_time_et = utc_now.astimezone(self.ET_TZ)
        return self.current_time_et

    async def search_markets(
        self, query: str = "Up or Down", limit: int = 100, offset: int = 0
    ) -> Dict[str, Any]:
        """
        Query Gamma API public search endpoint.
        Note: The API expects 'q' parameter, not 'query'
        """
        try:
            async with aiohttp.ClientSession() as session:
                # API expects 'q' parameter
                params = {"q": query}

                async with session.get(
                    self.BASE_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
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
        self, events: List[Dict[str, Any]], max_minutes_ahead: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Filter markets to find those ending within max_minutes_ahead minutes.
        Works with Polymarket 'events' objects from Gamma API.
        Only returns strictly binary markets (exactly 2 outcomes: YES/NO).
        """
        now = self.get_current_time_et()
        filtered_markets = []

        print(f"\nFiltering {len(events)} events...")
        print(f"Searching for markets ending within {max_minutes_ahead} minutes")
        print(f"Current time: {now.strftime('%H:%M:%S %Z')}")
        print()

        markets_checked = 0
        markets_skipped_inactive = 0
        markets_skipped_no_endtime = 0
        markets_skipped_time_window = 0
        markets_skipped_non_binary = 0
        events_skipped_inactive = 0

        for event in events:
            try:
                # Skip only INACTIVE events (not closed - we filter by time instead)
                # Closed events will be filtered out by end_time check
                if not event.get("active", False):
                    events_skipped_inactive += 1
                    continue

                # Events can have nested markets array
                markets_in_event = event.get("markets", [])

                # If no nested markets, treat event itself as a market
                if not markets_in_event:
                    markets_in_event = [event]

                for market in markets_in_event:
                    markets_checked += 1

                    # Skip only INACTIVE markets (not closed - we filter by time instead)
                    if not market.get("active", False):
                        markets_skipped_inactive += 1
                        continue

                    # Get end time from the market
                    end_time_str = (
                        market.get("endDate")
                        or market.get("endTime")
                        or market.get("end_time")
                    )
                    if not end_time_str:
                        markets_skipped_no_endtime += 1
                        continue

                    # Parse end_time (usually ISO format)
                    if isinstance(end_time_str, str):
                        # Handle ISO format with 'Z' or timezone info
                        end_time_str = end_time_str.replace("Z", "+00:00")
                        try:
                            end_time_utc = datetime.fromisoformat(end_time_str)
                            # Ensure it's in UTC
                            if end_time_utc.tzinfo is None:
                                end_time_utc = end_time_utc.replace(tzinfo=timezone.utc)
                            elif end_time_utc.tzinfo != timezone.utc:
                                end_time_utc = end_time_utc.astimezone(timezone.utc)

                            # Convert to ET for time calculations
                            end_time_et = end_time_utc.astimezone(self.ET_TZ)
                        except ValueError:
                            continue
                    else:
                        continue

                    # Get market title early for debug logging
                    title = market.get("question") or market.get("title", "N/A")

                    # Check if market ends within max_minutes_ahead minutes (using ET)
                    time_until_end = (end_time_et - now).total_seconds() / 60

                    if time_until_end < 0 or time_until_end > max_minutes_ahead:
                        markets_skipped_time_window += 1
                        continue

                    # Market is ending within the time window - add it
                    # Get condition_id and token_ids
                    condition_id = (
                        market.get("conditionId")
                        or market.get("condition_id")
                        or market.get("id")
                    )

                    # Extract token IDs from clobTokenIds if available
                    # Only accept strictly binary markets (exactly 2 outcomes: YES/NO)
                    token_ids_raw = market.get("clobTokenIds")
                    token_id_yes = None
                    token_id_no = None

                    if token_ids_raw:
                        try:
                            if isinstance(token_ids_raw, str):
                                token_ids = json.loads(token_ids_raw)
                                # Require exactly 2 outcomes (YES/NO)
                                if len(token_ids) != 2:
                                    markets_skipped_non_binary += 1
                                    continue
                                token_id_yes = token_ids[0]
                                token_id_no = token_ids[1]
                            elif isinstance(token_ids_raw, list):
                                # Require exactly 2 outcomes (YES/NO)
                                if len(token_ids_raw) != 2:
                                    markets_skipped_non_binary += 1
                                    continue
                                token_id_yes = token_ids_raw[0]
                                token_id_no = token_ids_raw[1]
                            else:
                                markets_skipped_non_binary += 1
                                continue
                        except Exception:
                            markets_skipped_non_binary += 1
                            continue
                    else:
                        # No token IDs = not binary
                        markets_skipped_non_binary += 1
                        continue

                    # Extract slug for UI link if available
                    slug = market.get("slug") or event.get("slug") or None

                    filtered_markets.append(
                        {
                            "condition_id": condition_id,
                            "token_id_yes": token_id_yes or "N/A",
                            "token_id_no": token_id_no or "N/A",
                            "end_time": end_time_et.strftime("%H:%M:%S %Z"),
                            "end_time_utc": end_time_utc.strftime(
                                "%Y-%m-%d %H:%M:%S UTC"
                            ),
                            "minutes_until_end": round(time_until_end, 1),
                            "title": title,
                            "ticker": event.get("ticker", "N/A"),
                            "slug": slug,
                        }
                    )
            except Exception as e:
                print(f"Error processing market: {e}")
                continue

        print("\nFilter statistics:")
        print(f"  Events checked: {len(events)}")
        print(f"  Events skipped (inactive/closed): {events_skipped_inactive}")
        print(f"  Markets checked: {markets_checked}")
        print(f"  Skipped (inactive/closed): {markets_skipped_inactive}")
        print(f"  Skipped (no end time): {markets_skipped_no_endtime}")
        print(f"  Skipped (outside time window): {markets_skipped_time_window}")
        print(f"  Skipped (non-binary): {markets_skipped_non_binary}")
        print(f"  Found: {len(filtered_markets)}")
        print()

        return filtered_markets

    async def find_active_market(self) -> Optional[List[Dict[str, Any]]]:
        """
        Main function to find active binary markets.
        Searches for markets ending in the next max_minutes_ahead minutes (default 20).

        If use_wide_search=True (default), fetches all markets without query restrictions
        and relies on filter_markets() to select binary markets with correct timing.
        """
        now = self.get_current_time_et()
        print(f"Current time (ET): {now.strftime('%H:%M:%S')}")
        print(
            f"Searching for markets ending in the next {self.max_minutes_ahead} minutes..."
        )
        print()

        # Step 2: Query API for markets
        print("Querying Polymarket Gamma API...")

        all_events = []
        seen_ids = set()

        # Step 1: Run targeted searches with specific queries
        print(f"Using targeted search with {len(self.base_queries)} base queries...")
        
        # Generate date strings for today
        # Polymarket inconsistently uses "February 2" vs "February 02"
        # So we generate both formats to be safe
        current_date_no_zero = now.strftime("%B %-d")  # "February 2"
        current_date_with_zero = now.strftime("%B %d")  # "February 02"
        
        # Get current hour in 12h format
        current_hour_24 = now.hour
        hour_12 = current_hour_24 % 12 or 12
        
        queries = []
        for base in self.base_queries:
            if "Up or Down" in base:
                # Add date+hour queries (both date formats for reliability)
                # This helps API find recent markets instead of old popular ones
                queries.append(f"{base} - {current_date_no_zero}, {hour_12}:")
                queries.append(f"{base} - {current_date_with_zero}, {hour_12}:")
            queries.append(base)

        for query in queries:
            markets_data = await self.search_markets(query=query)
            events = markets_data.get("events", [])

            if events:
                print(f"Query '{query[:50]}...' returned {len(events)} events")

                for event in events:
                    event_id = event.get("id")
                    if event_id and event_id not in seen_ids:
                        seen_ids.add(event_id)
                        all_events.append(event)

        # Step 2: Optionally run wide search if enabled
        if self.use_wide_search:
            print("\nAdditionally running wide search (a-e)...")
            wide_queries = ["a", "b", "c", "d", "e"]

            for query in wide_queries:
                markets_data = await self.search_markets(query=query)
                events = markets_data.get("events", [])

                if events:
                    print(f"Query '{query}' returned {len(events)} events")

                    for event in events:
                        event_id = event.get("id")
                        if event_id and event_id not in seen_ids:
                            seen_ids.add(event_id)
                            all_events.append(event)

        if not all_events:
            print("No markets found")
            return None

        print(f"\nFound {len(all_events)} unique events total")

        # Step 3: Filter for active markets ending in less than max_minutes_ahead
        active_markets = self.filter_markets(
            all_events, max_minutes_ahead=self.max_minutes_ahead
        )

        if not active_markets:
            print(
                f"\nNo matching markets found ending in the next {self.max_minutes_ahead} minutes"
            )
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
