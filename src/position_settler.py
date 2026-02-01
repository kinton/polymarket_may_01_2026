"""
Position Settler for Polymarket Trading Bot.

This module handles:
- Fetching open positions from CLOB API
- Checking market resolution status
- Redeeming winning tokens for USDC
- Logging P&L to CSV

Usage:
    # Run once and exit
    uv run python -m src.position_settler --once

    # Continuous mode (every 5 minutes)
    uv run python -m src.position_settler --daemon

    # Custom check interval (seconds)
    uv run python -m src.position_settler --daemon --interval 300
"""

import argparse
import asyncio
import csv
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from py_clob_client.client import ClobClient

# Load environment variables
load_dotenv()


class PositionSettler:
    """
    Handles position settlement and P&L tracking.

    Features:
    - Fetch open positions via CLOB API
    - Check market resolution status
    - Redeem winning tokens
    - Log P&L to CSV
    """

    def __init__(self, dry_run: bool = True):
        """
        Initialize position settler.

        Args:
            dry_run: If True, don't execute redeem operations (default: True)
        """
        self.dry_run = dry_run
        self.setup_logging()
        self.setup_clob_client()

        self.logger.info("=" * 80)
        self.logger.info("Position Settler Initialized")
        self.logger.info("=" * 80)
        self.logger.info(f"Mode: {'DRY RUN' if self.dry_run else '🔴 LIVE 🔴'}")
        self.logger.info("=" * 80)

    def setup_logging(self):
        """Setup logger for position settler."""
        log_dir = Path("log")
        log_dir.mkdir(exist_ok=True)

        self.logger = logging.getLogger("settler")
        self.logger.setLevel(logging.INFO)

        # Clear existing handlers to avoid duplicates
        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            "%(asctime)s - [SETTLER] - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # File handler
        file_handler = logging.FileHandler(log_dir / "settler.log")
        file_handler.setLevel(logging.INFO)
        file_formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)

    def setup_clob_client(self):
        """Initialize CLOB API client using private key (same as hft_trader)."""
        if self.dry_run:
            self.logger.info("Dry run mode: Skipping CLOB client initialization")
            self.client = None
            return

        try:
            private_key = os.getenv("PRIVATE_KEY")
            chain_id = int(os.getenv("POLYGON_CHAIN_ID", "137"))
            host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
            funder = os.getenv("POLYMARKET_PROXY_ADDRESS")

            if not private_key:
                self.logger.error("Missing PRIVATE_KEY in .env")
                sys.exit(1)

            # Initialize client with private key (same as hft_trader)
            self.client = ClobClient(
                host=host,
                key=private_key,
                chain_id=chain_id,
                signature_type=2,  # POLY_PROXY
                funder=funder,
            )

            # Derive API credentials from private key
            api_creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(api_creds)

            self.logger.info(f"CLOB client initialized ({host})")
            if funder:
                self.logger.info(f"  Proxy wallet: {funder}")

        except Exception as e:
            self.logger.error(f"Failed to initialize CLOB client: {e}", exc_info=True)
            sys.exit(1)

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        """
        Fetch all open positions from CLOB API.

        Returns:
            List of position dicts with asset_id, size, and other metadata
        """
        try:
            self.logger.info("Fetching open positions...")

            # Get positions from API (synchronous call, wrapped in executor)
            loop = asyncio.get_event_loop()
            positions = await loop.run_in_executor(None, self.client.get_positions)

            if not positions:
                self.logger.info("No open positions found")
                return []

            # Filter for CONDITIONAL tokens only (not USDC)
            conditional_positions = [
                p for p in positions if p.get("asset_type") == "CONDITIONAL"
            ]

            self.logger.info(
                f"Found {len(conditional_positions)} conditional position(s)"
            )

            return conditional_positions

        except Exception as e:
            self.logger.error(f"Error fetching positions: {e}", exc_info=True)
            return []

    async def check_market_resolution(self, condition_id: str) -> Optional[str]:
        """
        Check if market is resolved and get winning outcome.

        Args:
            condition_id: Market condition ID

        Returns:
            Winning outcome index ("0" or "1") if resolved, None if pending
        """
        try:
            # Get market info from CLOB API
            loop = asyncio.get_event_loop()
            market_info = await loop.run_in_executor(
                None, self.client.get_market, condition_id
            )

            if not market_info:
                self.logger.warning(f"Market {condition_id} not found")
                return None

            # Check if market is closed and resolved
            closed = market_info.get("closed", False)
            outcome = market_info.get("outcome")

            if closed and outcome is not None:
                self.logger.info(
                    f"Market {condition_id} resolved with outcome: {outcome}"
                )
                return str(outcome)
            else:
                self.logger.debug(f"Market {condition_id} not yet resolved")
                return None

        except Exception as e:
            self.logger.error(
                f"Error checking market resolution for {condition_id}: {e}",
                exc_info=True,
            )
            return None

    async def redeem_position(
        self, token_id: str, condition_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Redeem winning tokens for USDC.

        Args:
            token_id: Token ID to redeem
            condition_id: Market condition ID

        Returns:
            Redemption transaction info if successful, None otherwise
        """
        if self.dry_run:
            self.logger.info(f"DRY RUN: Would redeem token {token_id}")
            return {"status": "dry_run", "token_id": token_id}

        try:
            self.logger.info(f"Redeeming token {token_id} for condition {condition_id}")

            # Call CLOB API to redeem position
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self.client.redeem_position, token_id
            )

            if result:
                self.logger.info(f"Successfully redeemed token {token_id}")
                return result
            else:
                self.logger.warning(f"Redemption failed for token {token_id}")
                return None

        except Exception as e:
            self.logger.error(f"Error redeeming token {token_id}: {e}", exc_info=True)
            return None

    def calculate_pnl(
        self, position: Dict[str, Any], entry_price: float = 0.99
    ) -> Dict[str, float]:
        """
        Calculate P&L for a position.

        Args:
            position: Position dict from API
            entry_price: Assumed entry price (default: 0.99)

        Returns:
            Dict with cost, exit_value, profit_loss, roi_percent
        """
        size = float(position.get("size", 0))
        cost = size * entry_price
        exit_value = size * 1.0  # Winning tokens worth $1.00 each

        profit_loss = exit_value - cost
        roi_percent = (profit_loss / cost * 100) if cost > 0 else 0.0

        return {
            "tokens": size,
            "cost": round(cost, 2),
            "exit_value": round(exit_value, 2),
            "profit_loss": round(profit_loss, 2),
            "roi_percent": round(roi_percent, 2),
        }

    async def log_pnl_to_csv(
        self,
        position: Dict[str, Any],
        pnl: Dict[str, float],
        condition_id: str,
        market_title: str = "N/A",
    ):
        """
        Log P&L data to CSV file.

        Args:
            position: Position dict
            pnl: P&L calculation dict
            condition_id: Market condition ID
            market_title: Market title
        """
        log_dir = Path("log")
        log_dir.mkdir(exist_ok=True)
        csv_path = log_dir / "pnl.csv"

        # Check if file exists to determine if we need to write headers
        file_exists = csv_path.exists()

        try:
            with open(csv_path, "a", newline="") as csvfile:
                fieldnames = [
                    "timestamp",
                    "market_title",
                    "condition_id",
                    "token_id",
                    "side",
                    "tokens_bought",
                    "entry_price",
                    "cost",
                    "exit_value",
                    "profit_loss",
                    "roi_percent",
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                if not file_exists:
                    writer.writeheader()

                writer.writerow(
                    {
                        "timestamp": datetime.now(timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S UTC"
                        ),
                        "market_title": market_title,
                        "condition_id": condition_id,
                        "token_id": position.get("asset_id", "N/A"),
                        "side": "YES/NO",  # Determined by token_id
                        "tokens_bought": pnl["tokens"],
                        "entry_price": 0.99,
                        "cost": pnl["cost"],
                        "exit_value": pnl["exit_value"],
                        "profit_loss": f"{'+' if pnl['profit_loss'] >= 0 else ''}{pnl['profit_loss']}",
                        "roi_percent": f"{'+' if pnl['roi_percent'] >= 0 else ''}{pnl['roi_percent']}%",
                    }
                )

            self.logger.info(f"P&L logged to {csv_path}")

        except Exception as e:
            self.logger.error(f"Error logging P&L to CSV: {e}", exc_info=True)

    async def process_positions(self):
        """
        Main processing loop: check positions, redeem if needed, log P&L.
        """
        self.logger.info("Starting position processing...")

        # Get all open positions
        positions = await self.get_open_positions()

        if not positions:
            self.logger.info("No positions to process")
            return

        processed = 0
        redeemed = 0

        for position in positions:
            try:
                token_id = position.get("asset_id")
                condition_id = position.get("condition_id")

                if not token_id or not condition_id:
                    self.logger.warning(f"Invalid position data: {position}")
                    continue

                self.logger.info(f"Processing position: {token_id}")

                # Check if market is resolved
                winning_outcome = await self.check_market_resolution(condition_id)

                if winning_outcome is not None:
                    # Market resolved - calculate P&L
                    pnl = self.calculate_pnl(position)
                    self.logger.info(
                        f"P&L for {token_id}: ${pnl['profit_loss']} ({pnl['roi_percent']}%)"
                    )

                    # Log to CSV
                    await self.log_pnl_to_csv(
                        position,
                        pnl,
                        condition_id,
                        market_title=position.get("market", "N/A"),
                    )

                    # Redeem winning tokens
                    result = await self.redeem_position(token_id, condition_id)

                    if result:
                        redeemed += 1

                processed += 1

            except Exception as e:
                self.logger.error(f"Error processing position {token_id}: {e}")
                continue

        self.logger.info(f"Processed {processed} position(s), redeemed {redeemed}")

    async def run(self, interval: int = 300):
        """
        Main entry point for position settler.

        Args:
            interval: Check interval in seconds (default: 300 = 5 minutes)
        """
        self.logger.info("Position settler started")

        try:
            while True:
                await self.process_positions()

                self.logger.info(f"Sleeping {interval}s until next check...")
                await asyncio.sleep(interval)

        except KeyboardInterrupt:
            self.logger.info("Received shutdown signal")
        except Exception as e:
            self.logger.error(f"Fatal error in settler: {e}", exc_info=True)
        finally:
            self.logger.info("Position settler shut down")

    async def run_once(self):
        """Run position processing once and exit."""
        self.logger.info("Running position settler once...")
        await self.process_positions()
        self.logger.info("Position settler finished (run-once mode)")


async def main():
    """Main entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        description="Polymarket Position Settler - Redeem winnings and track P&L"
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Live mode - execute actual redemptions (default: dry run)",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (default: continuous daemon mode)",
    )

    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run in daemon mode (continuous loop)",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Check interval in seconds (default: 300 = 5 minutes)",
    )

    args = parser.parse_args()

    # Safety warning for live mode
    if args.live:
        print("\n" + "=" * 80)
        print("🔴 WARNING: LIVE MODE ENABLED 🔴")
        print("=" * 80)
        print("This will execute REAL redemptions!")
        print("Press Ctrl+C within 5 seconds to cancel...")
        print("=" * 80 + "\n")
        await asyncio.sleep(5)

    # Create settler instance
    settler = PositionSettler(dry_run=not args.live)

    # Run mode
    if args.once:
        await settler.run_once()
    else:
        await settler.run(interval=args.interval)


if __name__ == "__main__":
    asyncio.run(main())
