import asyncio
from datetime import datetime, timezone

from hft_trader import LastSecondTrader


def make_trader():
    # Dummy IDs from logs
    yes_id = (
        "33796097032576277738394436537780066685724776835023250317520626139605311276216"
    )
    no_id = (
        "75430070505844207706189940767257960081045332315388688955849375420014109695012"
    )
    end_time = datetime.now(timezone.utc)
    return LastSecondTrader(
        condition_id="cond",
        token_id_yes=yes_id,
        token_id_no=no_id,
        end_time=end_time,
        dry_run=True,
    )


async def run_book_test():
    trader = make_trader()

    # YES book with descending asks, ascending bids
    yes_book = {
        "event_type": "book",
        "asset_id": trader.token_id_yes,
        "asks": [
            {"price": "0.99", "size": "100"},
            {"price": "0.98", "size": "50"},
            {"price": "0.95", "size": "20"},
        ],
        "bids": [
            {"price": "0.10", "size": "10"},
            {"price": "0.93", "size": "5"},
        ],
    }
    await trader.process_market_update(yes_book, is_yes_token=True)

    # NO book with descending asks, ascending bids
    no_book = {
        "event_type": "book",
        "asset_id": trader.token_id_no,
        "asks": [
            {"price": "0.99", "size": "100"},
            {"price": "0.98", "size": "50"},
            {"price": "0.06", "size": "20"},
        ],
        "bids": [
            {"price": "0.01", "size": "10"},
            {"price": "0.05", "size": "5"},
        ],
    }
    await trader.process_market_update(no_book, is_yes_token=False)

    assert abs(trader.best_ask_yes - 0.95) < 1e-9
    assert abs(trader.best_ask_no - 0.06) < 1e-9
    assert abs(trader.best_bid_yes - 0.93) < 1e-9
    assert abs(trader.best_bid_no - 0.05) < 1e-9


async def run_price_change_test():
    trader = make_trader()

    price_change_yes = {
        "event_type": "price_change",
        "price_changes": [
            {
                "asset_id": trader.token_id_yes,
                "best_bid": "0.93",
                "best_ask": "0.95",
            }
        ],
    }
    price_change_no = {
        "event_type": "price_change",
        "price_changes": [
            {
                "asset_id": trader.token_id_no,
                "best_bid": "0.05",
                "best_ask": "0.07",
            }
        ],
    }

    await trader.process_market_update(price_change_yes, is_yes_token=True)
    await trader.process_market_update(price_change_no, is_yes_token=False)

    assert abs(trader.best_ask_yes - 0.95) < 1e-9
    assert abs(trader.best_ask_no - 0.07) < 1e-9
    assert abs(trader.best_bid_yes - 0.93) < 1e-9
    assert abs(trader.best_bid_no - 0.05) < 1e-9


def main():
    asyncio.run(run_book_test())
    asyncio.run(run_price_change_test())
    print("✅ best bid/ask extraction tests passed")


if __name__ == "__main__":
    main()
