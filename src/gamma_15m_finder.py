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
import time
from datetime import datetime, timezone
import logging
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp


class GammaAPI15mFinder:
    """Find active binary markets on Polymarket."""

    BASE_URL = "https://gamma-api.polymarket.com/public-search"
    ET_TZ = ZoneInfo("America/New_York")  # Handles DST correctly
    CACHE_FILE = "/tmp/gamma_cache.json"
    CACHE_TTL_SECONDS = 60  # Cache results for 60 seconds

    def __init__(
        self,
        max_minutes_ahead: int = 20,
        use_wide_search: bool = False,
        logger: logging.Logger | None = None,
    ):
        """Initialize finder.

        Args:
            max_minutes_ahead: Maximum minutes ahead to search for markets (default: 20)
            use_wide_search: If True, also use wide search with single letters (default: False)
            logger: Optional logger (avoids raw print output)
        """
        self.current_time_et = None
        self.current_window = None
        self.max_minutes_ahead = max_minutes_ahead
        self.use_wide_search = use_wide_search
        self.logger = logger
        # Always load base queries (Bitcoin/Ethereum + custom from env)
        self.base_queries = self._load_base_queries()
        # Rate limiting to avoid Cloudflare 403
        self.min_request_interval = float(
            os.getenv("GAMMA_MIN_REQUEST_INTERVAL", "0.35")
        )
        self._last_request_ts = 0.0
        # Retry/backoff settings for transient errors
        self.max_retries = int(os.getenv("GAMMA_MAX_RETRIES", "3"))
        self.backoff_base = float(os.getenv("GAMMA_BACKOFF_BASE", "0.5"))
        self.backoff_max = float(os.getenv("GAMMA_BACKOFF_MAX", "4.0"))
        # Cache stats
        self.cache_hits = 0
        self.cache_misses = 0

    def _out(self, message: str) -> None:
        if not message:
            return
        if self.logger is not None:
            self.logger.info(message)
            return
        print(message)

    def _load_cache(self) -> dict[str, Any] | None:
        """Load cached market data if still valid."""
        try:
            if not os.path.exists(self.CACHE_FILE):
                return None

            with open(self.CACHE_FILE, "r") as f:
                cache_data = json.load(f)

            # Check if cache is still fresh
            cache_age = time.time() - cache_data.get("timestamp", 0)
            if cache_age > self.CACHE_TTL_SECONDS:
                self._out(f"Cache expired (age: {cache_age:.1f}s, TTL: {self.CACHE_TTL_SECONDS}s)")
                return None

            self._out(f"Cache HIT! Using cached data (age: {cache_age:.1f}s)")
            self.cache_hits += 1
            return cache_data

        except Exception as e:
            self._out(f"Failed to load cache: {e}")
            return None

    def _save_cache(self, markets: list[dict[str, Any]], all_events: list[dict[str, Any]]) -> None:
        """Save market data to cache."""
        try:
            cache_data = {
                "timestamp": time.time(),
                "markets": markets,
                "all_events": all_events,
            }

            with open(self.CACHE_FILE, "w") as f:
                json.dump(cache_data, f)

            self._out(f"Cache SAVED: {len(all_events)} events, {len(markets) or 'no'} matching markets")
        except Exception as e:
            self._out(f"Failed to save cache: {e}")

    async def _rate_limit(self) -> None:
        """Throttle Gamma API requests to avoid rate limits."""
        if self.min_request_interval <= 0:
            return
        now = time.monotonic()
        wait_for = self.min_request_interval - (now - self._last_request_ts)
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        self._last_request_ts = time.monotonic()

    async def _backoff_sleep(self, attempt: int) -> None:
        """Exponential backoff with a max cap."""
        delay = min(self.backoff_base * (2**attempt), self.backoff_max)
        await asyncio.sleep(delay)

    def _load_base_queries(self) -> list[str]:
        """Load base queries from env or use defaults.

        Env format: MARKET_QUERIES="Query1;Query2;Query3"

        Default queries target Bitcoin/Ethereum/Solana 5m/15m markets.
        You can add more via env variable to trade on other markets.

        Examples:
            MARKET_QUERIES="Trump;Election;Will"  # Add political markets
            MARKET_QUERIES="AAPL;TSLA;Stock"      # Add stock markets
        """
        env_val = os.getenv("MARKET_QUERIES")

        # Start with crypto "Up or Down" defaults (5m/15m markets)
        # Note: Recent format uses time ranges: "2:45PM-3:00PM ET"
        default_queries = [
            "Bitcoin Up or Down",
            "Ethereum Up or Down",
            "Solana Up or Down",
            "BTC Up or Down",
            "ETH Up or Down",
            "SOL Up or Down",
            "BTC 15 Minute Up or Down",
            "ETH 15 Minute Up or Down",
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
        self,
        query: str = "Up or Down",
        limit: int = 100,
        offset: int = 0,
        session: aiohttp.ClientSession | None = None,
    ) -> dict[str, Any]:
        """
        Query Gamma API public search endpoint.
        Note: The API expects 'q' parameter, not 'query'
        """
        session_to_close: aiohttp.ClientSession | None = None
        if session is None:
            session_to_close = aiohttp.ClientSession()
            session = session_to_close

        try:
            # API expects 'q' parameter
            params = {"q": query}

            for attempt in range(self.max_retries):
                await self._rate_limit()
                try:
                    async with session.get(
                        self.BASE_URL,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        if response.status == 200:
                            try:
                                return await response.json()
                            except Exception as e:
                                self._out(f"Failed to parse JSON response: {e}")
                                return {"markets": []}
                        elif response.status == 422:
                            # API returns 422 for validation issues - try to get error details
                            try:
                                error_data = await response.json()
                                self._out(f"API validation error: {error_data}")
                            except Exception as e:
                                self._out(
                                    f"API Error {response.status}: Could not parse error details - {e}"
                                )
                            return {"markets": []}
                        elif response.status in {429, 500, 502, 503, 504}:
                            if attempt < (self.max_retries - 1):
                                await self._backoff_sleep(attempt)
                                continue
                            self._out(f"API Error: {response.status}")
                            return {"markets": []}
                        else:
                            self._out(f"API Error: {response.status}")
                            return {"markets": []}
                except asyncio.TimeoutError:
                    if attempt < (self.max_retries - 1):
                        await self._backoff_sleep(attempt)
                        continue
                    self._out("API request timed out")
                    return {"markets": []}

            # If we exhausted retries, return an empty result
            return {"markets": []}

        except asyncio.TimeoutError:
            self._out("API request timed out")
            return {"markets": []}
        except Exception as e:
            self._out(f"Error querying API: {e}")
            return {"markets": []}
        finally:
            if session_to_close is not None:
                await session_to_close.close()

    def filter_markets(
        self, events: list[dict[str, Any]], max_minutes_ahead: int = 20
    ) -> list[dict[str, Any]]:
        """
        Filter markets to find those ending within max_minutes_ahead minutes.
        Works with Polymarket 'events' objects from Gamma API.
        Only returns strictly binary markets (exactly 2 outcomes: YES/NO).
        """
        now = self.get_current_time_et()
        filtered_markets = []

        self._out(f"Filtering {len(events)} events...")
        self._out(f"Searching for markets ending within {max_minutes_ahead} minutes")
        self._out(f"Current time: {now.strftime('%H:%M:%S %Z')}")

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
                        except Exception as e:
                            self._out(f"Error parsing token IDs: {e}")
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
                self._out(f"Error processing market: {e}")
                continue

        self._out("Filter statistics:")
        self._out(f"  Events checked: {len(events)}")
        self._out(f"  Events skipped (inactive/closed): {events_skipped_inactive}")
        self._out(f"  Markets checked: {markets_checked}")
        self._out(f"  Skipped (inactive/closed): {markets_skipped_inactive}")
        self._out(f"  Skipped (no end time): {markets_skipped_no_endtime}")
        self._out(f"  Skipped (outside time window): {markets_skipped_time_window}")
        self._out(f"  Skipped (non-binary): {markets_skipped_non_binary}")
        self._out(f"  Found: {len(filtered_markets)}")

        return filtered_markets

    async def find_active_market(self) -> list[dict[str, Any]] | None:
        """
        Main function to find active binary markets.
        Searches for markets ending in the next max_minutes_ahead minutes (default 20).

        If use_wide_search=True (default), fetches all markets without query restrictions
        and relies on filter_markets() to select binary markets with correct timing.
        """
        now = self.get_current_time_et()
        self._out(f"Current time (ET): {now.strftime('%H:%M:%S')}")
        self._out(
            f"Searching for markets ending in the next {self.max_minutes_ahead} minutes..."
        )

        # Step 1: Check cache first
        cached_data = self._load_cache()
        if cached_data:
            all_events = cached_data.get("all_events", [])
        else:
            # Step 2: Query API for markets
            self._out("Querying Polymarket Gamma API...")
            self.cache_misses += 1

            all_events = []
            seen_ids = set()

            # Step 2a: Run targeted searches with specific queries
            self._out(f"Using targeted search with {len(self.base_queries)} base queries...")

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

            async with aiohttp.ClientSession() as session:
                for query in queries:
                    markets_data = await self.search_markets(query=query, session=session)
                    events = markets_data.get("events", [])

                    if events:
                        self._out(f"Query '{query[:50]}...' returned {len(events)} events")

                        for event in events:
                            event_id = event.get("id")
                            if event_id and event_id not in seen_ids:
                                seen_ids.add(event_id)
                                all_events.append(event)

            # Step 2b: Optionally run wide search if enabled
            if self.use_wide_search:
                self._out("Additionally running wide search (a-e)...")
                wide_queries = ["a", "b", "c", "d", "e"]

                async with aiohttp.ClientSession() as session:
                    for query in wide_queries:
                        markets_data = await self.search_markets(
                            query=query, session=session
                        )
                        events = markets_data.get("events", [])

                        if events:
                            self._out(f"Query '{query}' returned {len(events)} events")

                            for event in events:
                                event_id = event.get("id")
                                if event_id and event_id not in seen_ids:
                                    seen_ids.add(event_id)
                                    all_events.append(event)

            if not all_events:
                self._out("No markets found")
                return None

            self._out(f"Found {len(all_events)} unique events total")

        # Step 3: Filter for active markets ending in less than max_minutes_ahead
        active_markets = self.filter_markets(
            all_events, max_minutes_ahead=self.max_minutes_ahead
        )

        # Save cache only on miss (fresh data)
        if cached_data is None:
            self._save_cache(active_markets, all_events)

        if not active_markets:
            self._out(
                f"No matching markets found ending in the next {self.max_minutes_ahead} minutes"
            )
            return None

        self._out(f"Found {len(active_markets)} matching market(s):")
        self._out("-" * 80)
        for market in active_markets:
            self._out(f"Title: {market['title']}")
            self._out(f"Condition ID: {market['condition_id']}")
            self._out(f"Token ID (YES): {market['token_id_yes']}")
            self._out(f"Token ID (NO): {market['token_id_no']}")
            self._out(f"End Time (ET): {market['end_time']}")
            self._out(f"End Time (UTC): {market['end_time_utc']}")
            self._out(f"Minutes until end: {market['minutes_until_end']}")
            self._out("-" * 80)

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
