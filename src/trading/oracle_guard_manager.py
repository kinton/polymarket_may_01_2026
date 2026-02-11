"""
Oracle Guard Manager - Handles oracle tracking and guard checks.

Manages Chainlink oracle price tracking from RTDS and provides
guard functions to block trades when oracle data is unreliable.
"""

import time
from typing import Any

from src.oracle_tracker import OracleSnapshot, OracleTracker
from src.updown_prices import (
    EventPageClient,
    RtdsClient,
    guess_chainlink_symbol,
    parse_market_window,
)


class OracleGuardManager:
    """
    Manages oracle tracking and guard checks for trading decisions.

    Tracks Chainlink oracle prices via RTDS WebSocket and provides
    quality checks to block trades when data is unreliable.
    """

    def __init__(
        self,
        title: str,
        market_name: str,
        end_time: str,
        enabled: bool = True,
        guard_enabled: bool = True,
        min_points: int = 4,
        window_s: float = 60.0,
    ):
        """
        Initialize the oracle guard manager.

        Args:
            title: Market title (e.g., "Bitcoin Up or Down")
            market_name: Short market name for logging
            end_time: Market end time in ISO format
            enabled: Whether oracle tracking is enabled
            guard_enabled: Whether oracle guard is enabled
            min_points: Minimum data points required
            window_s: Statistics window in seconds
        """
        self.enabled = bool(enabled)
        self.guard_enabled = bool(guard_enabled)
        self.market_name = market_name
        self.min_points = int(min_points)
        self.stats_window_s = float(window_s)

        # Oracle configuration
        self.decide_side = False
        self.require_side = False
        self.symbol = guess_chainlink_symbol(title or market_name)

        # Oracle tracker instance
        self.tracker: OracleTracker | None = (
            OracleTracker(window_seconds=self.stats_window_s)
            if self.enabled
            else None
        )

        # Current snapshot
        self.snapshot: OracleSnapshot | None = None
        self.last_update_ts = 0.0
        self._last_log_ts = 0.0

        # Oracle guard configuration
        self.max_stale_s = 20.0
        self.log_every_s = 5.0
        self.max_vol_pct = 0.002
        self.min_abs_z = 0.75
        self.require_agreement = True
        self.require_beat = False
        self.max_reversal_slope = 0.0
        self.beat_max_lag_ms = 10_000

        # Outcome mapping (YES/NO for Up/Down)
        self.up_side: str | None = None
        self.down_side: str | None = None

        # Market window parsing
        try:
            self.window = parse_market_window(title or "", end_time)
        except Exception:
            self.window = None

        # Metrics/log throttling
        self.block_count = 0
        self.reason_counts: dict[str, int] = {}
        self.last_reason: str | None = None
        self.last_log_ts = 0.0
        self.html_beat_attempted = False

        # Client for fetching price-to-beat from event page
        self.event_page_client = EventPageClient()

    def recommended_side(self) -> str | None:
        """
        Determine which outcome is winning based on oracle price.

        Returns:
            "YES" or "NO" if available, None otherwise
        """
        if self.snapshot is None or self.snapshot.price_to_beat is None or self.snapshot.delta is None:
            return None
        if self.up_side is None or self.down_side is None:
            return None
        return self.up_side if self.snapshot.delta >= 0 else self.down_side

    def quality_ok(self, *, trade_side: str, time_remaining: float) -> tuple[bool, str, str]:
        """
        Check if oracle data quality is acceptable for trading.

        Args:
            trade_side: The side we want to trade ("YES" or "NO")
            time_remaining: Time remaining in seconds

        Returns:
            (ok, reason_code, detail) tuple. When ok=False, reason_code is stable for counters.
        """
        if not self.enabled or not self.guard_enabled:
            return True, "", ""

        snap = self.snapshot
        if snap is None:
            return False, "oracle_snapshot_missing", ""

        staleness_s = time.time() - float(self.last_update_ts)
        if staleness_s > self.max_stale_s:
            return False, "oracle_stale", f"{staleness_s:.2f}s"

        if self.require_beat and snap.price_to_beat is None:
            return False, "price_to_beat_missing", ""

        if snap.n_points < self.min_points:
            return (
                False,
                "oracle_points_insufficient",
                f"{snap.n_points}<{self.min_points}",
            )

        if snap.vol_pct is None:
            return False, "oracle_vol_missing", ""

        if snap.vol_pct > self.max_vol_pct:
            return (
                False,
                "oracle_vol_high",
                f"{snap.vol_pct:.6f}>{self.max_vol_pct:.6f}",
            )

        if snap.zscore is None:
            if snap.price_to_beat is None and not self.require_beat:
                return True, "", ""
            return False, "oracle_z_missing", ""

        if abs(snap.zscore) < self.min_abs_z:
            return (
                False,
                "oracle_z_low",
                f"{abs(snap.zscore):.2f}<{self.min_abs_z:.2f}",
            )

        oracle_side = self.recommended_side()
        if (
            self.require_agreement
            and oracle_side is not None
            and oracle_side != trade_side
        ):
            return (
                False,
                "oracle_disagrees",
                f"oracle={oracle_side}, trade={trade_side}",
            )

        max_rev = self.max_reversal_slope
        if max_rev > 0 and snap.slope_usd_per_s is not None:
            expected_sign = None
            if self.up_side is not None and trade_side == self.up_side:
                expected_sign = 1
            elif (
                self.down_side is not None
                and trade_side == self.down_side
            ):
                expected_sign = -1

            if expected_sign == 1 and snap.slope_usd_per_s < -max_rev:
                return (
                    False,
                    "oracle_reversal_slope",
                    f"{snap.slope_usd_per_s:.2f}<-{max_rev:.2f}",
                )
            if expected_sign == -1 and snap.slope_usd_per_s > max_rev:
                return (
                    False,
                    "oracle_reversal_slope",
                    f"{snap.slope_usd_per_s:.2f}>{max_rev:.2f}",
                )

        return True, "", ""

    async def price_loop(self, logger: Any, slug: str | None) -> None:
        """
        Stream Chainlink oracle prices from RTDS and update tracking.

        Args:
            logger: Logger instance for messages
            slug: Market slug for fetching outcome mapping
        """
        if self.symbol is None:
            logger.info(f"[{self.market_name}] Oracle symbol not determined, skipping oracle tracking")
            return

        if self.tracker is None:
            return

        # Check window timing
        now_ms = int(time.time() * 1000)
        start_ms = getattr(self.window, "start_ms", None)
        end_ms = getattr(self.window, "end_ms", None) if self.window else None

        # Log warning if we missed the window start
        if start_ms and now_ms > start_ms:
            lag_ms = now_ms - start_ms
            if lag_ms > self.beat_max_lag_ms:
                logger.warning(
                    f"⚠️  [{self.market_name}] Oracle start missed by {lag_ms / 1000:.1f}s "
                    f"(max_lag={self.beat_max_lag_ms / 1000:.1f}s); price_to_beat will be unavailable"
                )

        # Try to fetch price-to-beat from event page if needed
        if (
            not self.html_beat_attempted
            and self.require_beat
            and self.window is not None
            and self.window.start_iso_z is not None
            and self.tracker.price_to_beat is None
        ):
            self.html_beat_attempted = True
            try:
                open_price = await self.event_page_client.fetch_open_price(
                    slug=slug,
                    condition_id=None,
                    start_time_iso_z=self.window.start_iso_z,
                )
                if open_price:
                    self.tracker.price_to_beat = float(open_price)
                    logger.info(
                        f"[{self.market_name}] Fetched price_to_beat from event page: {open_price}"
                    )
            except Exception as e:
                logger.warning(f"[{self.market_name}] Failed to fetch price_to_beat from event page: {e}")

        # Determine YES/NO mapping for Up/Down outcomes
        if slug and (self.up_side is None or self.down_side is None):
            try:
                from src.updown_prices import (
                    parse_question_tokens,
                    fetch_market_data,
                )

                data = await fetch_market_data(slug)
                if data and data.question:
                    yes_token, no_token = parse_question_tokens(
                        data.question, data.description
                    )
                    if yes_token and no_token:
                        # Up = YES, Down = NO (for "price will go UP" markets)
                        # This may need adjustment based on actual market wording
                        if "up" in (data.question or "").lower():
                            self.up_side = "YES"
                            self.down_side = "NO"
                        else:
                            self.up_side = "NO"
                            self.down_side = "YES"

                        if self.up_side and self.down_side:
                            logger.info(
                                f"✓ [{self.market_name}] Oracle outcome mapping: "
                                f"Up→{self.up_side}, Down→{self.down_side}"
                            )
            except Exception as e:
                logger.warning(f"[{self.market_name}] Failed to determine outcome mapping: {e}")

        logger.info(
            f"✓ [{self.market_name}] Oracle tracking enabled (RTDS Chainlink) symbol={self.symbol}"
        )

        # Start RTDS stream
        topics = ["chainlink", self.symbol]
        async with RtdsClient() as rtds:
            async for price, timestamp_iso in rtds.stream_prices(
                symbol=self.symbol,
                topics=topics,
                seconds=15.0,
            ):
                self.last_update_ts = time.time()

                # Update tracker with new price
                self.tracker.maybe_set_price_to_beat(
                    window=self.window,
                    max_lag_ms=self.beat_max_lag_ms,
                    price=price,
                )

                self.snapshot = self.tracker.update(price)

                # Periodic logging
                if (time.time() - self._last_log_ts) >= 1.0:
                    snap = self.snapshot
                    if snap and snap.price is not None:
                        parts = [
                            f"{self.symbol}={snap.price:,.2f}",
                        ]
                        if snap.price_to_beat is not None:
                            parts.append(f"beat={snap.price_to_beat:,.2f}")
                            if snap.delta is not None:
                                delta_sign = "+" if snap.delta >= 0 else ""
                                parts.append(f"delta={delta_sign}{snap.delta:,.2f}")
                        if snap.vol_pct is not None:
                            parts.append(f"vol={snap.vol_pct:.4f}%")
                        if snap.zscore is not None:
                            parts.append(f"z={snap.zscore:.2f}")
                        logger.info(f"[{self.market_name}] ORACLE " + " | ".join(parts))
                    self._last_log_ts = time.time()

    def log_block_summary(self, logger: Any) -> None:
        """Log oracle guard block summary."""
        if not self.enabled or not self.guard_enabled:
            return

        if self.block_count > 0:
            top_reasons = sorted(
                self.reason_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:3]
            top_s = ", ".join(f"{k}={v}" for k, v in top_reasons)
            logger.info(
                f"📊 [{self.market_name}] Oracle guard summary: "
                f"blocked={self.block_count} (top: {top_s})"
            )
