"""Auto-redeem winning positions on Polymarket after market resolution.

Polymarket uses Gnosis Conditional Tokens Framework (CTF). When a market resolves:
- Winning tokens can be redeemed for $1.00 USDC each
- Redemption is done via CTFExchange.redeemPositions() on-chain

For neg_risk markets (most newer markets including Up/Down), redemption goes through
the NegRiskCtfExchange contract instead.

This module:
1. Detects resolved winning positions in the database
2. Calls the appropriate on-chain redeem function
3. Logs the redemption result back to the database
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)

# Polymarket CTF Exchange on Polygon mainnet
# Minimal ABI for redeemPositions
CTF_EXCHANGE_ABI = [
    {
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# NegRiskCtfExchange for neg_risk markets
NEG_RISK_CTF_EXCHANGE_ABI = [
    {
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# Contract addresses on Polygon mainnet (chain_id=137)
CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
# CTFExchange (standard markets)
CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
# NegRiskCtfExchange (neg_risk markets)
NEG_RISK_CTF_EXCHANGE_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# Default Polygon RPC
DEFAULT_RPC = "https://polygon-rpc.com"


class AutoRedeemer:
    """Handles on-chain redemption of winning Polymarket positions."""

    def __init__(
        self,
        private_key: str,
        rpc_url: str | None = None,
        proxy_address: str | None = None,
        dry_run: bool = True,
        logger_: logging.Logger | None = None,
    ):
        self.dry_run = dry_run
        self.log = logger_ or logger
        self.proxy_address = proxy_address

        rpc = rpc_url or os.getenv("POLYGON_RPC_URL", DEFAULT_RPC)
        self.w3 = Web3(Web3.HTTPProvider(rpc))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        self.account = self.w3.eth.account.from_key(private_key)
        # Use proxy address if set (Polymarket uses proxy wallets)
        self.address = Web3.to_checksum_address(proxy_address) if proxy_address else self.account.address

        self.ctf_exchange = self.w3.eth.contract(
            address=Web3.to_checksum_address(CTF_EXCHANGE_ADDRESS),
            abi=CTF_EXCHANGE_ABI,
        )
        self.neg_risk_exchange = self.w3.eth.contract(
            address=Web3.to_checksum_address(NEG_RISK_CTF_EXCHANGE_ADDRESS),
            abi=NEG_RISK_CTF_EXCHANGE_ABI,
        )

        self.log.info(
            "AutoRedeemer initialized (dry_run=%s, address=%s)",
            dry_run, self.address,
        )

    async def redeem_position(
        self,
        condition_id: str,
        is_neg_risk: bool = False,
    ) -> dict[str, Any] | None:
        """Redeem winning tokens for a resolved market.

        Args:
            condition_id: The market's condition ID (hex string with 0x prefix)
            is_neg_risk: Whether this is a neg_risk market

        Returns:
            Transaction receipt dict if successful, None on failure
        """
        self.log.info(
            "Attempting to redeem condition_id=%s (neg_risk=%s, dry_run=%s)",
            condition_id, is_neg_risk, self.dry_run,
        )

        if self.dry_run:
            self.log.info("DRY RUN: Would redeem condition_id=%s", condition_id)
            return {"status": "dry_run", "condition_id": condition_id}

        try:
            contract = self.neg_risk_exchange if is_neg_risk else self.ctf_exchange

            # Convert condition_id to bytes32
            cond_bytes = bytes.fromhex(condition_id.replace("0x", ""))

            # indexSets: [1, 2] for binary markets (both outcomes)
            # This tells the CTF to check both outcome slots
            index_sets = [1, 2]

            # Build transaction
            tx = contract.functions.redeemPositions(
                cond_bytes, index_sets
            ).build_transaction({
                "from": self.account.address,
                "nonce": await asyncio.to_thread(
                    self.w3.eth.get_transaction_count, self.account.address
                ),
                "gas": 300000,
                "gasPrice": await asyncio.to_thread(self.w3.eth.gas_price.__int__),
                "chainId": 137,
            })

            # Sign and send
            signed = self.account.sign_transaction(tx)
            tx_hash = await asyncio.to_thread(
                self.w3.eth.send_raw_transaction, signed.raw_transaction
            )
            self.log.info("Redeem tx sent: %s", tx_hash.hex())

            # Wait for receipt
            receipt = await asyncio.to_thread(
                self.w3.eth.wait_for_transaction_receipt, tx_hash, timeout=120
            )

            if receipt["status"] == 1:
                self.log.info(
                    "✅ Redemption successful! tx=%s gas_used=%d",
                    tx_hash.hex(), receipt["gasUsed"],
                )
                return {
                    "status": "success",
                    "tx_hash": tx_hash.hex(),
                    "gas_used": receipt["gasUsed"],
                    "condition_id": condition_id,
                }
            else:
                self.log.warning(
                    "❌ Redemption tx reverted: %s", tx_hash.hex()
                )
                return None

        except Exception as e:
            self.log.error(
                "Error redeeming condition_id=%s: %s", condition_id, e,
                exc_info=True,
            )
            return None


async def redeem_resolved_wins(
    db: Any,
    redeemer: AutoRedeemer,
    clob_client: Any | None = None,
) -> list[dict]:
    """Find all resolved_win positions and redeem them on-chain.

    Args:
        db: TradeDatabase instance
        redeemer: AutoRedeemer instance
        clob_client: Optional ClobClient for neg_risk checks

    Returns:
        List of redemption results
    """
    # Find resolved_win positions that haven't been redeemed yet
    async with db._db.execute(
        """SELECT DISTINCT condition_id FROM dry_run_positions
           WHERE status = 'resolved_win'
           AND close_reason NOT LIKE '%redeemed%'"""
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        logger.info("No unredeemed winning positions found")
        return []

    results = []
    for row in rows:
        condition_id = row[0]

        # Check if neg_risk market
        is_neg_risk = False
        if clob_client:
            try:
                # Use any token_id from the market to check
                async with db._db.execute(
                    "SELECT condition_id FROM dry_run_positions WHERE condition_id = ? LIMIT 1",
                    (condition_id,),
                ) as cur2:
                    pass
                # For now, assume neg_risk based on condition_id format
                # Most modern Polymarket markets are neg_risk
                is_neg_risk = True
            except Exception:
                pass

        result = await redeemer.redeem_position(condition_id, is_neg_risk=is_neg_risk)

        if result and result.get("status") in ("success", "dry_run"):
            # Mark as redeemed in close_reason
            await db._db.execute(
                """UPDATE dry_run_positions
                   SET close_reason = close_reason || ' [redeemed]'
                   WHERE condition_id = ? AND status = 'resolved_win'""",
                (condition_id,),
            )
            await db._db.commit()
            results.append(result)
            logger.info("Redeemed condition_id=%s: %s", condition_id, result)

    return results
