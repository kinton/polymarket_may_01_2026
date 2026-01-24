#!/usr/bin/env python3
"""
Debug script to see what Gamma API actually returns
"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

API_URL = "https://gamma-api.polymarket.com/public-search"
ET_TZ = ZoneInfo("America/New_York")


async def search_markets(query: str):
    """Query Gamma API"""
    params = {"q": query}  # API expects 'q' not 'query'
    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(API_URL, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"API error: {response.status}")
                    return {"events": []}
        except Exception as e:
            print(f"Error: {e}")
            return {"events": []}


async def main():
    now = datetime.now(ET_TZ)
    print(f"Current time (ET): {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print()

    # Search for Bitcoin markets with today's date
    current_date = now.strftime("January %d")
    current_hour = now.hour % 12
    if current_hour == 0:
        current_hour = 12

    queries = [
        f"Bitcoin Up or Down - {current_date}, {current_hour}:",
        f"Bitcoin Up or Down - {current_date}",
        "Bitcoin Up or Down",
    ]

    for query in queries:
        print(f"\n{'=' * 80}")
        print(f"Query: {query}")
        print("=" * 80)
        data = await search_markets(query)
        events = data.get("events", [])

        print(f"Found {len(events)} events")

        # Show active vs closed
        active_count = sum(1 for e in events if e.get("active") and not e.get("closed"))
        closed_count = sum(1 for e in events if e.get("closed"))
        print(f"  Active (not closed): {active_count}")
        print(f"  Closed: {closed_count}")

    print(f"\n\n{'=' * 80}")
    print("DETAILED VIEW OF FIRST QUERY")
    print("=" * 80)

    # Now show details for first query
    query = queries[0]
    data = await search_markets(query)
    events = data.get("events", [])

    print(f"Found {len(events)} events\n")

    for i, event in enumerate(events[:5], 1):  # Show first 5 events
        print(f"\n{'=' * 80}")
        print(f"EVENT #{i}")
        print(f"{'=' * 80}")

        # Basic info
        print(f"Title: {event.get('title', 'N/A')}")
        print(f"Ticker: {event.get('ticker', 'N/A')}")
        print(f"Active: {event.get('active', False)}")
        print(f"Closed: {event.get('closed', False)}")

        # Look for end time fields
        end_time_fields = ["endDate", "endTime", "end_time", "endDateIso"]
        print("\nEnd time fields:")
        for field in end_time_fields:
            value = event.get(field)
            if value:
                print(f"  {field}: {value}")

        # Check nested markets
        markets = event.get("markets", [])
        if markets:
            print(f"\nNested markets: {len(markets)}")
            for j, market in enumerate(markets[:3], 1):  # Show first 3 markets
                print(f"\n  Market #{j}:")
                print(
                    f"    Title: {market.get('question', market.get('title', 'N/A'))}"
                )
                print(f"    Active: {market.get('active', False)}")
                print(f"    Closed: {market.get('closed', False)}")

                # End time for market
                for field in end_time_fields:
                    value = market.get(field)
                    if value:
                        print(f"    {field}: {value}")

                        # Try to parse and show time until end
                        try:
                            if isinstance(value, str):
                                end_time_str = value.replace("Z", "+00:00")
                                end_time = datetime.fromisoformat(end_time_str)
                                if end_time.tzinfo is None:
                                    end_time = end_time.replace(tzinfo=ZoneInfo("UTC"))
                                end_time_et = end_time.astimezone(ET_TZ)

                                minutes_until = (end_time_et - now).total_seconds() / 60
                                print(
                                    f"    → Ends at: {end_time_et.strftime('%H:%M:%S ET')}"
                                )
                                print(f"    → Minutes until end: {minutes_until:.1f}")
                        except Exception as e:
                            print(f"    → Parse error: {e}")

                # Token IDs
                token_ids = market.get("clobTokenIds")
                if token_ids:
                    print(f"    Token IDs: {token_ids}")


if __name__ == "__main__":
    asyncio.run(main())
