"""
Position manager for tracking trading position state.

Manages position state including entry price, side, open status, and trailing stop.
"""

from typing import Any


class PositionManager:
    """
    Manages the state of an open trading position.

    Tracks:
    - Entry price
    - Position side (YES/NO)
    - Position open/closed status
    - Trailing stop price level
    """

    def __init__(self, logger: Any | None = None):
        """
        Initialize position manager.

        Args:
            logger: Optional logger for logging position events
        """
        self.logger = logger
        self._reset_state()

    def _reset_state(self) -> None:
        """Reset position state to initial (no position)."""
        self.entry_price: float | None = None
        self.position_side: str | None = None  # "YES" or "NO"
        self.position_open = False
        self.trailing_stop_price: float | None = None

    def open_position(
        self, entry_price: float, side: str, trailing_stop_price: float
    ) -> None:
        """
        Open a new position.

        Args:
            entry_price: Price at which position was opened
            side: Position side ("YES" or "NO")
            trailing_stop_price: Initial trailing stop price level
        """
        self.entry_price = entry_price
        self.position_side = side
        self.position_open = True
        self.trailing_stop_price = trailing_stop_price

        if self.logger:
            self.logger.info(
                f"Position opened: {side} @ ${entry_price:.4f} | "
                + f"Stop-loss: ${trailing_stop_price:.4f}"
            )

    def close_position(self) -> None:
        """Close the current position."""
        if self.logger and self.position_open:
            self.logger.info(f"Position closed: {self.position_side}")
        self._reset_state()

    def update_trailing_stop(self, new_stop_price: float) -> None:
        """
        Update trailing stop price.

        Args:
            new_stop_price: New trailing stop price level
        """
        old_stop = self.trailing_stop_price
        self.trailing_stop_price = new_stop_price

        if self.logger and old_stop is not None and new_stop_price > old_stop:
            self.logger.info(
                f"Trailing-stop moved: ${old_stop:.4f} → ${new_stop_price:.4f}"
            )

    @property
    def is_open(self) -> bool:
        """Check if a position is currently open."""
        return self.position_open

    @property
    def has_entry(self) -> bool:
        """Check if entry price is set."""
        return self.entry_price is not None
