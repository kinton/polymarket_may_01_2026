"""
Risk manager for handling balance checks, risk limits, and daily PnL tracking.

Ensures trades respect capital allocation limits and daily loss/trade count limits.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

import asyncio

from src.clob_types import (
    EXCHANGE_CONTRACT,
    MAX_CAPITAL_PCT_PER_TRADE,
    MAX_DAILY_LOSS_PCT,
    MAX_TOTAL_TRADES_PER_DAY,
    MIN_TRADE_USDC,
)


class RiskManager:
    """
    Manages risk checks and limits.

    Responsibilities:
    - Balance and allowance checks
    - Capital allocation limits (max % per trade)
    - Daily loss limit enforcement
    - Daily trade count limit
    - PnL tracking
    """

    def __init__(
        self,
        client: Any | None,
        market_name: str,
        trade_size: float = 1.0,
        logger: Any | None = None,
    ):
        """
        Initialize risk manager.

        Args:
            client: CLOB client (None in dry-run mode)
            market_name: Market name for logging
            trade_size: Default trade size in USDC
            logger: Optional logger for logging events
        """
        self.client = client
        self.market_name = market_name
        self.trade_size = trade_size
        self.logger = logger

        # Dynamic sizing thresholds
        self.min_trade_usdc = max(MIN_TRADE_USDC, round(float(trade_size), 2))
        self.balance_risk_pct = 0.05
        self.balance_risk_switch_usdc = 30.0

        # Planned trade amount (from balance check)
        self._planned_trade_amount: float | None = None

        # Daily limits file path
        self._daily_limits_path = self._get_daily_limits_path()

    @property
    def planned_trade_amount(self) -> float | None:
        """Get the planned trade amount from balance check."""
        return self._planned_trade_amount

    @planned_trade_amount.setter
    def planned_trade_amount(self, value: float | None) -> None:
        """Set the planned trade amount (for test overrides)."""
        self._planned_trade_amount = value

    def _get_daily_limits_path(self) -> str:
        """Get the path to the daily limits JSON file."""
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "log", "daily_limits.json"
        )

    def _log(self, message: str) -> None:
        """Log message."""
        if self.logger:
            self.logger.info(message)
        else:
            # When logger is None (e.g., in tests), print to stdout
            print(message)

    async def check_balance(self) -> bool:
        """
        Check if we have sufficient USDC balance and allowance for the trade.

        Returns:
            True if balance and allowance are sufficient, False otherwise
        """
        if not self.client:
            self._log(f"❌ [{self.market_name}] CLOB client not initialized")
            return False

        try:
            # Get balance and allowance for USDC
            from py_clob_client.clob_types import BalanceAllowanceParams

            self._log(
                f"DEBUG: client type={type(self.client)}, has_method={hasattr(self.client, 'get_balance_allowance')}"
            )
            params = BalanceAllowanceParams(asset_type="COLLATERAL")  # type: ignore[arg-type]
            balance_data_raw = await asyncio.to_thread(
                self.client.get_balance_allowance, params
            )
            balance_data: dict[str, Any] = balance_data_raw

            self._log(f"DEBUG: balance_data_raw={balance_data_raw}")

            # Extract USDC balance (API returns in 6-decimal units, divide by 1e6 to get dollars)
            usdc_balance = float(balance_data.get("balance", 0)) / 1e6

            self._log(
                f"DEBUG: usdc_balance={usdc_balance:.2f}, required_amount_will_be_calculated_below"
            )

            # API returns 'allowances' (dict of contract -> allowance), not 'allowance'
            allowances_dict = balance_data.get("allowances", {})

            # Allowance is also in 6-decimal units (micro-USDC), convert to dollars
            usdc_allowance = float(allowances_dict.get(EXCHANGE_CONTRACT, 0)) / 1e6

            self._log(
                f"DEBUG: usdc_allowance={usdc_allowance:.2f}, exchange_contract={EXCHANGE_CONTRACT}"
            )

            # Calculate trade size: MAXIMUM of three values
            # 1. trade_size parameter (e.g., $1.1 from --size flag)
            # 2. 5% of balance (MAX_CAPITAL_PCT_PER_TRADE)
            # 3. MIN_TRADE_USDC ($0.10) - minimum Polymarket trade size
            balance_5_pct = usdc_balance * MAX_CAPITAL_PCT_PER_TRADE
            trade_size_param = round(self.trade_size, 2)

            # Use MAXIMUM of all three values (as requested by Konstantin)
            required_amount = max(trade_size_param, MIN_TRADE_USDC, balance_5_pct)

            # Ensure not exceeding MAX_TRADE_USDC
            from src.clob_types import MAX_TRADE_USDC
            required_amount = min(required_amount, MAX_TRADE_USDC)

            self._planned_trade_amount = required_amount
            self._log(
                f"DEBUG: trade_size=${trade_size_param:.2f}, 5%_balance=${balance_5_pct:.2f}, MIN=${MIN_TRADE_USDC:.2f} → required=${required_amount:.2f}"
            )

            self._log(
                f"DEBUG: required_amount={required_amount:.2f}, usdc_balance={usdc_balance:.2f}, usdc_allowance={usdc_allowance:.2f}"
            )

            # Check both balance and allowance
            if usdc_balance < required_amount:
                self._log(
                    f"❌ [{self.market_name}] Insufficient balance: "
                    + f"${usdc_balance:.2f} < ${required_amount:.2f}"
                )
                return False

            if usdc_allowance < required_amount:
                self._log(
                    f"❌ [{self.market_name}] Insufficient allowance: "
                    + f"${usdc_allowance:.2f} < ${required_amount:.2f}"
                )
                self._log("   → Run: uv run python approve.py to approve USDC spending")
                return False

            self._log(
                f"✓ [{self.market_name}] Balance check passed: "
                + f"${usdc_balance:.2f} available (need ${required_amount:.2f})"
            )
            return True

        except Exception as e:
            self._log(f"⚠️  [{self.market_name}] Balance check failed: {e}")
            return False

    def check_daily_limits(self) -> bool:
        """
        Check if daily limits are within acceptable bounds.

        Returns:
            True if daily limits are OK, False if limits exceeded
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self._daily_limits_path

        try:
            # If no tracking file exists, limits are OK
            if not os.path.exists(path):
                return True

            with open(path, "r") as f:
                data = json.load(f)

            # If data is from a different day, reset and OK
            if data.get("date") != today:
                return True

            # Check daily loss limit
            initial_balance = data.get("initial_balance")
            current_pnl = data.get("current_pnl", 0.0)

            if initial_balance is not None:
                max_daily_loss = initial_balance * -MAX_DAILY_LOSS_PCT
                if current_pnl < max_daily_loss:
                    self._log(
                        f"🛑 FATAL [{self.market_name}] DAILY LOSS LIMIT EXCEEDED: "
                        + f"PnL=${current_pnl:+.2f} < Max Loss=${max_daily_loss:+.2f} "
                        + f"({MAX_DAILY_LOSS_PCT * 100:.0f}% of ${initial_balance:.2f})",
                    )
                    return False

            # Check daily trade count limit
            total_trades = data.get("total_trades", 0)
            if total_trades >= MAX_TOTAL_TRADES_PER_DAY:
                self._log(
                    f"🛑 [{self.market_name}] DAILY TRADE LIMIT EXCEEDED: "
                    + f"{total_trades} trades >= {MAX_TOTAL_TRADES_PER_DAY} max",
                )
                return False

            return True

        except Exception as e:
            self._log(f"⚠️  [{self.market_name}] Daily limits check failed: {e}")
            # Fail closed on error - block trading if we can't verify limits
            return False

    def track_daily_pnl(self, trade_amount: float, pnl: float = 0.0) -> None:
        """
        Track daily PnL and trade count in daily_limits.json.

        Args:
            trade_amount: Amount of the trade in USDC
            pnl: Profit or loss from the trade (positive = profit, negative = loss)
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self._daily_limits_path

        try:
            # Ensure log directory exists
            os.makedirs(os.path.dirname(path), exist_ok=True)

            # Read existing data or create new
            if os.path.exists(path):
                with open(path, "r") as f:
                    data = json.load(f)
            else:
                data = {}

            # Check if we need to reset for new day
            if data.get("date") != today:
                # New day - reset tracking
                data = {
                    "date": today,
                    "initial_balance": None,
                    "current_pnl": 0.0,
                    "total_trades": 0,
                }

            # Update trade count and PnL
            data["total_trades"] = data.get("total_trades", 0) + 1
            data["current_pnl"] = data.get("current_pnl", 0.0) + pnl

            # Set initial balance on first trade of the day
            if data["total_trades"] == 1 and data["initial_balance"] is None:
                if self.client:
                    try:
                        from py_clob_client.clob_types import BalanceAllowanceParams

                        balance_data_raw = self.client.get_balance_allowance(
                            BalanceAllowanceParams(asset_type="COLLATERAL")  # type: ignore[arg-type]
                        )
                        balance_data: dict[str, Any] = balance_data_raw
                        fetched_balance = float(balance_data.get("balance", 0)) / 1e6
                        # Sanity check: initial balance must be reasonable (> $1)
                        if fetched_balance > 1.0:
                            data["initial_balance"] = fetched_balance
                        else:
                            self._log(
                                f"⚠️  [{self.market_name}] Fetched balance too low "
                                + f"(${fetched_balance:.2f}), skipping initial_balance set"
                            )
                    except Exception as e:
                        self._log(
                            f"⚠️  [{self.market_name}] Failed to fetch initial balance: {e}"
                        )
                        # Don't set initial_balance to trade_amount — it's meaningless for limits

            # Write updated data
            with open(path, "w") as f:
                json.dump(data, f, indent=2)

            self._log(
                f"📊 [{self.market_name}] Daily stats updated: "
                + f"PnL=${data['current_pnl']:+.2f}, Trades={data['total_trades']}",
            )

        except Exception as e:
            self._log(f"⚠️  [{self.market_name}] Failed to track daily PnL: {e}")
