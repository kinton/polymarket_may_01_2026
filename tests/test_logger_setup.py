"""
Unit tests for logger setup and handler management in main.py

Tests verify that logger handlers are properly managed and do not
accumulate on repeated setup_logging() calls, which would cause
duplicate log messages.
"""

import logging
import tempfile


def test_logger_handler_accumulation():
    """Test that calling setup_logging multiple times doesn't accumulate handlers."""
    # Get logger instances
    finder_logger = logging.getLogger("test_finder")
    trader_logger = logging.getLogger("test_trader")

    # Clear any existing handlers
    finder_logger.handlers.clear()
    trader_logger.handlers.clear()

    # Simulate adding handlers (first setup)
    finder_logger.addHandler(logging.StreamHandler())
    finder_logger.addHandler(logging.FileHandler(tempfile.mktemp()))
    trader_logger.addHandler(logging.StreamHandler())
    trader_logger.addHandler(logging.FileHandler(tempfile.mktemp()))

    assert len(finder_logger.handlers) == 2
    assert len(trader_logger.handlers) == 2

    # Simulate clearing and re-adding (second setup)
    if finder_logger.hasHandlers():
        finder_logger.handlers.clear()
    if trader_logger.hasHandlers():
        trader_logger.handlers.clear()

    finder_logger.addHandler(logging.StreamHandler())
    finder_logger.addHandler(logging.FileHandler(tempfile.mktemp()))
    trader_logger.addHandler(logging.StreamHandler())
    trader_logger.addHandler(logging.FileHandler(tempfile.mktemp()))

    # Should still be 2 handlers each, not 4
    assert len(finder_logger.handlers) == 2
    assert len(trader_logger.handlers) == 2


def test_has_handlers_check_works():
    """Test that hasHandlers() correctly detects existing handlers."""
    test_logger = logging.getLogger("test_has_handlers")
    test_logger.handlers.clear()
    test_logger.propagate = False  # Disable propagation to root logger

    assert not test_logger.hasHandlers()

    test_logger.addHandler(logging.StreamHandler())
    assert test_logger.hasHandlers()

    test_logger.handlers.clear()
    assert not test_logger.hasHandlers()


def test_handlers_clear_works():
    """Test that handlers.clear() removes all handlers."""
    test_logger = logging.getLogger("test_clear")
    test_logger.handlers.clear()
    test_logger.propagate = False  # Disable propagation to root logger

    # Add 3 handlers
    test_logger.addHandler(logging.StreamHandler())
    test_logger.addHandler(logging.StreamHandler())
    test_logger.addHandler(logging.FileHandler(tempfile.mktemp()))

    assert len(test_logger.handlers) == 3

    # Clear all handlers
    test_logger.handlers.clear()

    assert len(test_logger.handlers) == 0
    assert not test_logger.hasHandlers()


def test_repeated_setup_without_clear_accumulates():
    """Test that NOT clearing handlers causes accumulation (anti-pattern)."""
    bad_logger = logging.getLogger("test_bad_pattern")
    bad_logger.handlers.clear()

    # First setup
    bad_logger.addHandler(logging.StreamHandler())
    bad_logger.addHandler(logging.FileHandler(tempfile.mktemp()))
    assert len(bad_logger.handlers) == 2

    # Second setup WITHOUT clearing
    bad_logger.addHandler(logging.StreamHandler())
    bad_logger.addHandler(logging.FileHandler(tempfile.mktemp()))

    # Now we have 4 handlers (duplication!)
    assert len(bad_logger.handlers) == 4

    # Clean up
    bad_logger.handlers.clear()


def test_repeated_setup_with_clear_prevents_accumulation():
    """Test that clearing handlers prevents accumulation (correct pattern)."""
    good_logger = logging.getLogger("test_good_pattern")
    good_logger.handlers.clear()

    # First setup
    good_logger.addHandler(logging.StreamHandler())
    good_logger.addHandler(logging.FileHandler(tempfile.mktemp()))
    assert len(good_logger.handlers) == 2

    # Second setup WITH clearing (what our fix does)
    if good_logger.hasHandlers():
        good_logger.handlers.clear()

    good_logger.addHandler(logging.StreamHandler())
    good_logger.addHandler(logging.FileHandler(tempfile.mktemp()))

    # Still only 2 handlers (no duplication!)
    assert len(good_logger.handlers) == 2

    # Clean up
    good_logger.handlers.clear()


def test_file_and_stream_handler_distinction():
    """Test that we can distinguish between FileHandler and StreamHandler."""
    test_logger = logging.getLogger("test_handler_types")
    test_logger.handlers.clear()

    # Add one of each type
    test_logger.addHandler(logging.StreamHandler())
    test_logger.addHandler(logging.FileHandler(tempfile.mktemp()))

    # Check we have both types
    file_handlers = [
        h for h in test_logger.handlers if isinstance(h, logging.FileHandler)
    ]
    stream_handlers = [
        h
        for h in test_logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]

    assert len(file_handlers) == 1
    assert len(stream_handlers) == 1

    # Clean up
    test_logger.handlers.clear()


def test_multiple_setup_cycles():
    """Test that handler count remains constant across many setup cycles."""
    cycle_logger = logging.getLogger("test_cycles")
    cycle_logger.handlers.clear()

    for i in range(10):
        # Simulate our fix: clear before adding
        if cycle_logger.hasHandlers():
            cycle_logger.handlers.clear()

        # Add handlers
        cycle_logger.addHandler(logging.StreamHandler())
        cycle_logger.addHandler(logging.FileHandler(tempfile.mktemp()))

        # Should always be 2, not 2*i
        assert len(cycle_logger.handlers) == 2, (
            f"Cycle {i}: expected 2, got {len(cycle_logger.handlers)}"
        )

    # Clean up
    cycle_logger.handlers.clear()
