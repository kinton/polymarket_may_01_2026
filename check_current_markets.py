#!/usr/bin/env python3
"""Quick script to check what markets are currently available."""

import asyncio
from datetime import datetime

import aiohttp
import pytz


async def check_markets():
    ET_TZ = pytz.timezone("US/Eastern")
    now = datetime.now(ET_TZ)
    print(f"Current time ET: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(
        f"Looking for markets ending between {now.strftime('%H:%M')} and {(now.replace(minute=now.minute + 20)).strftime('%H:%M')} ET\n"
    )

    url = "https://gamma-api.polymarket.com/public-search"
    queries = [
        "Up or Down - February 02, 4:",
        "Up or Down - February 02, 5:",
        "Up or Down",
    ]

    async with aiohttp.ClientSession() as session:
        for q in queries:
            async with session.get(
                url, params={"q": q}, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
                events = data.get("events", [])
                print(f'Query "{q}":')
                print(f"  Found {len(events)} events")

                for event in events[:5]:  # Show first 5
                    title = event.get("title", "N/A")
                    end_time = event.get("endDate") or event.get("end_date_iso")

                    if end_time:
                        end_utc = datetime.fromisoformat(
                            end_time.replace("Z", "+00:00")
                        )
                        end_et = end_utc.astimezone(ET_TZ)
                        mins = (end_et - now).total_seconds() / 60

                        if abs(mins) < 30:  # Only show markets within +/- 30 minutes
                            print(f"  - {title[:60]}")
                            print(
                                f"    Ends: {end_et.strftime('%H:%M ET')} ({mins:.1f} min)"
                            )

                print()


if __name__ == "__main__":
    asyncio.run(check_markets())
