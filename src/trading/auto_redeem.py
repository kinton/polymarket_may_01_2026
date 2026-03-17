"""Auto-redeem winning positions on Polymarket after market resolution.

Polymarket uses Gnosis Conditional Tokens Framework (CTF). When a market resolves:
- Winning tokens can be redeemed for $1.00 USDC each
- Redemption goes through ProxyWalletFactory.proxy() which routes calls
  through the user's proxy wallet to the CTF contract

For neg_risk markets, redemption goes through NegRiskAdapter instead of CTF directly.

This module:
1. Detects resolved winning positions in the database
2. Calls ProxyWalletFactory.proxy() to redeem on-chain
3. Logs the redemption result back to the database
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)

# ── Contract Addresses (Polygon mainnet, chain_id=137) ──────────────────

PROXY_WALLET_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_ADDRESS = "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"

# ── ABIs ─────────────────────────────────────────────────────────────────

# CTF.redeemPositions(address collateralToken, bytes32 parentCollectionId,
#                     bytes32 conditionId, uint256[] indexSets)
CTF_REDEEM_ABI = [{
    "inputs": [
        {"name": "collateralToken", "type": "address"},
        {"name": "parentCollectionId", "type": "bytes32"},
        {"name": "conditionId", "type": "bytes32"},
        {"name": "indexSets", "type": "uint256[]"},
    ],
    "name": "redeemPositions",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function",
}]

# NegRiskAdapter.redeemPositions(bytes32 conditionId, uint256[] amounts)
NEG_RISK_REDEEM_ABI = [{
    "inputs": [
        {"name": "conditionId", "type": "bytes32"},
        {"name": "amounts", "type": "uint256[]"},
    ],
    "name": "redeemPositions",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function",
}]

# ProxyWalletFactory.proxy(tuple[] calls) → bytes[]
# tuple = (uint8 typeCode, address to, uint256 value, bytes data)
PROXY_FACTORY_ABI = [{
    "inputs": [{
        "components": [
            {"name": "typeCode", "type": "uint8"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ],
        "name": "calls",
        "type": "tuple[]",
    }],
    "name": "proxy",
    "outputs": [{"name": "returnValues", "type": "bytes[]"}],
    "stateMutability": "payable",
    "type": "function",
}]

# ERC20 balanceOf for checking USDC
USDC_BALANCE_ABI = [{
    "inputs": [{"name": "account", "type": "address"}],
    "name": "balanceOf",
    "outputs": [{"name": "", "type": "uint256"}],
    "stateMutability": "view",
    "type": "function",
}]

# ── Free Polygon RPCs (fallback chain) ──────────────────────────────────

POLYGON_RPCS = [
    "https://polygon.drpc.org",
    "https://polygon-bor-rpc.publicnode.com",
    "https://1rpc.io/matic",
]

DEFAULT_RPC = "https://polygon.drpc.org"


class AutoRedeemer:
    """Handles on-chain redemption of winning Polymarket positions
    via ProxyWalletFactory.proxy()."""

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

        # Contracts
        self.factory = self.w3.eth.contract(
            address=Web3.to_checksum_address(PROXY_WALLET_FACTORY),
            abi=PROXY_FACTORY_ABI,
        )
        self.ctf = self.w3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS),
            abi=CTF_REDEEM_ABI,
        )
        self.neg_risk = self.w3.eth.contract(
            address=Web3.to_checksum_address(NEG_RISK_ADAPTER),
            abi=NEG_RISK_REDEEM_ABI,
        )

        self.log.info(
            "AutoRedeemer initialized (dry_run=%s, address=%s, proxy=%s)",
            dry_run, self.account.address, proxy_address,
        )

    def _get_usdc_balance(self, address: str) -> float:
        """Get USDC balance of an address in human-readable format."""
        usdc = self.w3.eth.contract(
            address=Web3.to_checksum_address(USDC_ADDRESS),
            abi=USDC_BALANCE_ABI,
        )
        raw = usdc.functions.balanceOf(Web3.to_checksum_address(address)).call()
        return raw / 1e6

    async def redeem_position(
        self,
        condition_id: str,
        is_neg_risk: bool = False,
        amounts: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Redeem winning tokens for a resolved market.

        Uses ProxyWalletFactory.proxy() to route the call through the user's
        proxy wallet to the CTF (or NegRiskAdapter) contract.

        Args:
            condition_id: Market condition ID (hex with 0x prefix)
            is_neg_risk: Whether this is a neg_risk market
            amounts: Token amounts for neg_risk redeem (default ["1","1"])

        Returns:
            Dict with tx details if successful, None on failure
        """
        self.log.info(
            "Attempting redeem condition_id=%s (neg_risk=%s, dry_run=%s)",
            condition_id, is_neg_risk, self.dry_run,
        )

        if self.dry_run:
            self.log.info("DRY RUN: Would redeem condition_id=%s", condition_id)
            return {"status": "dry_run", "condition_id": condition_id}

        try:
            cond_bytes = bytes.fromhex(condition_id.replace("0x", ""))

            # Encode the redeem calldata
            if is_neg_risk:
                redeem_amounts = amounts or ["1", "1"]
                redeem_data = self.neg_risk.encode_abi(
                    "redeemPositions",
                    [cond_bytes, [int(a) for a in redeem_amounts]],
                )
                target = Web3.to_checksum_address(NEG_RISK_ADAPTER)
            else:
                redeem_data = self.ctf.encode_abi(
                    "redeemPositions",
                    [
                        Web3.to_checksum_address(USDC_ADDRESS),
                        b"\x00" * 32,  # parentCollectionId = 0
                        cond_bytes,
                        [1, 2],  # both outcomes
                    ],
                )
                target = Web3.to_checksum_address(CTF_ADDRESS)

            # Build ProxyWalletFactory.proxy() call
            # typeCode=1 means CALL
            call_tuple = (1, target, 0, bytes.fromhex(redeem_data[2:]))

            # Check USDC balance before
            usdc_before = 0.0
            if self.proxy_address:
                usdc_before = await asyncio.to_thread(
                    self._get_usdc_balance, self.proxy_address
                )

            nonce = await asyncio.to_thread(
                self.w3.eth.get_transaction_count, self.account.address
            )
            gas_price = await asyncio.to_thread(
                lambda: self.w3.eth.gas_price
            )

            tx = self.factory.functions.proxy([call_tuple]).build_transaction({
                "from": self.account.address,
                "nonce": nonce,
                "gas": 500000,
                "gasPrice": gas_price,
                "chainId": 137,
                "value": 0,
            })

            # Estimate gas — if this fails the tx would revert (wallet holds no tokens)
            try:
                gas_est = await asyncio.to_thread(self.w3.eth.estimate_gas, tx)
                tx["gas"] = gas_est + 50000
                self.log.info("Gas estimate: %d", gas_est)
            except Exception as e:
                self.log.info(
                    "Gas estimation failed for condition_id=%s — "
                    "likely no tokens to redeem, skipping tx. Reason: %s",
                    condition_id, e,
                )
                return None

            # Sign and send
            signed = self.account.sign_transaction(tx)
            tx_hash = await asyncio.to_thread(
                self.w3.eth.send_raw_transaction, signed.raw_transaction
            )
            self.log.info("Redeem tx sent: 0x%s", tx_hash.hex())

            # Wait for receipt
            receipt = await asyncio.to_thread(
                self.w3.eth.wait_for_transaction_receipt, tx_hash, timeout=120
            )

            if receipt["status"] == 1:
                # Check USDC balance after
                usdc_after = 0.0
                if self.proxy_address:
                    usdc_after = await asyncio.to_thread(
                        self._get_usdc_balance, self.proxy_address
                    )
                redeemed_amount = usdc_after - usdc_before

                self.log.info(
                    "✅ Redemption successful! tx=0x%s gas=%d redeemed=$%.2f",
                    tx_hash.hex(), receipt["gasUsed"], redeemed_amount,
                )
                return {
                    "status": "success",
                    "tx_hash": tx_hash.hex(),
                    "gas_used": receipt["gasUsed"],
                    "condition_id": condition_id,
                    "redeemed_amount": redeemed_amount,
                    "usdc_balance": usdc_after,
                }
            else:
                self.log.warning(
                    "❌ Redemption tx reverted: 0x%s", tx_hash.hex()
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

        # Check if neg_risk market via CLOB API
        is_neg_risk = False
        if clob_client:
            try:
                import requests
                r = requests.get(
                    f"https://clob.polymarket.com/markets/{condition_id}",
                    timeout=5,
                )
                if r.ok:
                    market_data = r.json()
                    is_neg_risk = bool(market_data.get("neg_risk", False))
            except Exception:
                pass

        result = await redeemer.redeem_position(
            condition_id, is_neg_risk=is_neg_risk
        )

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
