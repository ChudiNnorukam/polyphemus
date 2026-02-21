"""Auto-redeemer — redeems winning CTF positions after accumulator settlement.

Supports three redemption modes:
  1. PROXY relay (gasless) — for MagicLink/Proxy wallets (sig_type=1), GSN-style signing
  2. Safe relayer (gasless) — for Gnosis Safe wallets (sig_type=2), via py-builder-relayer-client
  3. On-chain (direct Web3 tx) — for EOA wallets (sig_type=0), costs POL gas

Mode is selected automatically based on signature_type.
"""

import asyncio
import json as _json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests
from eth_abi import encode
from eth_keys import keys
from eth_utils import keccak
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from .config import setup_logger
from .types import RedemptionEvent

CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Exchange contracts that need CTF approval for SELL orders
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

RELAYER_URL = "https://relayer-v2.polymarket.com/"

# PROXY relay constants (GSN-style for MagicLink/ProxyWallet)
PROXY_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
RELAY_HUB = "0xD216153c06E857cD7f72665E0aF1d7D82172F494"
_PROXY_SELECTOR = keccak(text="proxy((uint8,address,uint256,bytes)[])")[:4]

CTF_ABI = [
    {"name": "redeemPositions", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "collateralToken", "type": "address"},
         {"name": "parentCollectionId", "type": "bytes32"},
         {"name": "conditionId", "type": "bytes32"},
         {"name": "indexSets", "type": "uint256[]"},
     ], "outputs": []},
    {"name": "payoutDenominator", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "conditionId", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "isApprovedForAll", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "setApprovalForAll", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
     "outputs": []},
]

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

# Retry backoff schedule (seconds)
RETRY_DELAYS = [30, 60, 120, 300, 600]  # 30s, 1m, 2m, 5m, 10m

# redeemPositions(address,bytes32,bytes32,uint256[]) selector
_REDEEM_SELECTOR = keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]


def _encode_redeem_calldata(condition_id_bytes: bytes) -> str:
    """Encode redeemPositions calldata for the CTF contract."""
    args = encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [
            Web3.to_checksum_address(USDC_E_ADDRESS),
            bytes(32),              # parentCollectionId = 0
            condition_id_bytes,
            [1, 2],                 # indexSets: both outcomes
        ]
    )
    return "0x" + (_REDEEM_SELECTOR + args).hex()


def _to_bytes_padded(hex_like, size=None) -> bytes:
    """Convert hex string, int, or bytes to bytes with optional left-padding."""
    if isinstance(hex_like, int):
        length = size if size else max(1, (hex_like.bit_length() + 7) // 8)
        return hex_like.to_bytes(length, "big")
    if isinstance(hex_like, bytes):
        return hex_like.rjust(size, b"\x00") if size else hex_like
    if isinstance(hex_like, str):
        if hex_like.startswith("0x"):
            raw = bytes.fromhex(hex_like[2:])
            return raw.rjust(size, b"\x00") if size else raw
        if hex_like.isdigit():
            num = int(hex_like)
            length = size if size else max(1, (num.bit_length() + 7) // 8)
            return num.to_bytes(length, "big")
        return hex_like.encode().rjust(size, b"\x00") if size else hex_like.encode()
    raise TypeError(f"Unsupported type: {type(hex_like)}")


def _create_struct_hash(from_addr, to, data, tx_fee, gas_price, gas_limit,
                        nonce, relay_hub, relay_addr) -> str:
    """Create GSN relay struct hash for PROXY transactions."""
    data_to_hash = b"".join([
        b"rlx:",
        _to_bytes_padded(from_addr),
        _to_bytes_padded(to),
        _to_bytes_padded(data),
        _to_bytes_padded(tx_fee, size=32),
        _to_bytes_padded(gas_price, size=32),
        _to_bytes_padded(gas_limit, size=32),
        _to_bytes_padded(nonce, size=32),
        _to_bytes_padded(relay_hub),
        _to_bytes_padded(relay_addr),
    ])
    return "0x" + keccak(data_to_hash).hex()


def _sign_gsn(hash_hex: str, private_key: str) -> str:
    """EIP-191 prefix + secp256k1 sign, return serialized 65-byte signature."""
    msg_bytes = bytes.fromhex(hash_hex[2:])
    prefix = f"\x19Ethereum Signed Message:\n{len(msg_bytes)}".encode()
    prefixed_hash = keccak(prefix + msg_bytes)

    pk_hex = private_key[2:] if private_key.startswith("0x") else private_key
    pk_obj = keys.PrivateKey(bytes.fromhex(pk_hex))
    sig = pk_obj.sign_msg_hash(prefixed_hash)

    r, s, v = sig.r, sig.s, sig.v
    SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    if s > SECP256K1_N // 2:
        s = SECP256K1_N - s
        v = 1 - v

    r_hex = hex(r)[2:].zfill(64)
    s_hex = hex(s)[2:].zfill(64)
    v_hex = "1c" if v else "1b"
    return "0x" + r_hex + s_hex + v_hex


def _encode_proxy_calldata(redeem_calldatas: list[str]) -> str:
    """Encode proxy(ProxyCall[]) for the ProxyWalletFactory.
    Each ProxyCall: (typeCode=1, to=CTF, value=0, data=redeem_calldata)."""
    ctf_cs = Web3.to_checksum_address(CTF_ADDRESS)
    calls = [(1, ctf_cs, 0, bytes.fromhex(rd[2:] if rd.startswith("0x") else rd))
             for rd in redeem_calldatas]
    args = encode(["(uint8,address,uint256,bytes)[]"], [calls])
    return "0x" + (_PROXY_SELECTOR + args).hex()


DATA_API = "https://data-api.polymarket.com"
SWEEP_INTERVAL = 300  # 5 minutes


class Redeemer:
    """Background redeemer that processes settlement events from a queue."""

    def __init__(self, private_key: str, wallet_address: str, rpc_url: str,
                 builder_api_key: str = "", builder_secret: str = "",
                 builder_passphrase: str = "", signature_type: int = 1):
        self._logger = setup_logger("polyphemus.redeemer")
        self._queue: asyncio.Queue[RedemptionEvent] = asyncio.Queue()
        self._private_key = private_key
        self._wallet = Web3.to_checksum_address(wallet_address)
        self._rpc_url = rpc_url
        self._signature_type = signature_type
        self._store = None  # set via set_position_store()
        self._swept_conditions: set = set()  # already-swept condition IDs
        # Persist swept conditions to disk so restarts don't re-redeem
        self._swept_file = Path(__file__).parent.parent / "data" / "swept_conditions.json"
        self._load_swept_conditions()

        # Derive EOA from private key (needed for PROXY relay where wallet != EOA)
        pk_hex = private_key[2:] if private_key.startswith("0x") else private_key
        self._eoa = keys.PrivateKey(bytes.fromhex(pk_hex)).public_key.to_checksum_address()

        # Web3 setup (synchronous — runs in executor)
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self._ctf = self._w3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI
        )
        self._usdc_e = self._w3.eth.contract(
            address=Web3.to_checksum_address(USDC_E_ADDRESS), abi=ERC20_ABI
        )

        self._total_redeemed = 0.0
        self._total_gas_pol = 0.0
        self._gasless_count = 0
        self._onchain_count = 0
        self.sweep_count = 0  # F4 fix: track sweeps for health invariant check

        # Initialize relayer based on signature type
        self._relayer = None
        self._builder_config = None
        if builder_api_key:
            self._init_relayer(private_key, builder_api_key, builder_secret,
                               builder_passphrase, signature_type)

    def set_position_store(self, store):
        self._store = store

    def _load_swept_conditions(self):
        """Load swept condition IDs from disk."""
        try:
            if self._swept_file.exists():
                data = _json.loads(self._swept_file.read_text())
                # Prune entries older than 7 days
                cutoff = time.time() - 7 * 86400
                self._swept_conditions = {
                    cid for cid, ts in data.items() if ts > cutoff
                }
                self._logger.info(
                    f"Loaded {len(self._swept_conditions)} swept conditions from disk"
                )
        except Exception as e:
            self._logger.warning(f"Could not load swept conditions: {e}")

    def _save_swept_conditions(self):
        """Persist swept condition IDs to disk."""
        try:
            self._swept_file.parent.mkdir(parents=True, exist_ok=True)
            # Store as {condition_id: timestamp} for age-based pruning
            existing = {}
            if self._swept_file.exists():
                try:
                    existing = _json.loads(self._swept_file.read_text())
                except Exception:
                    pass
            now = time.time()
            for cid in self._swept_conditions:
                if cid not in existing:
                    existing[cid] = now
            self._swept_file.write_text(_json.dumps(existing))
        except Exception as e:
            self._logger.warning(f"Could not save swept conditions: {e}")

    def _init_relayer(self, pk: str, key: str, secret: str, passphrase: str,
                      sig_type: int):
        """Initialize relayer — Safe relayer for type 2, builder config for type 1."""
        try:
            from py_builder_signing_sdk.config import BuilderConfig, BuilderApiKeyCreds
            self._builder_config = BuilderConfig(
                local_builder_creds=BuilderApiKeyCreds(
                    key=key, secret=secret, passphrase=passphrase,
                )
            )

            if sig_type == 2:
                from py_builder_relayer_client.client import RelayClient
                self._relayer = RelayClient(RELAYER_URL, 137, pk, self._builder_config)
                self._logger.info("Safe relayer initialized (type 2)")
            elif sig_type == 1:
                self._logger.info("PROXY relay initialized (type 1)")
            else:
                self._logger.info("Builder config stored (type 0, on-chain)")
        except ImportError:
            self._logger.warning("py-builder-signing-sdk not installed, using on-chain only")
        except Exception as e:
            self._logger.warning(f"Relayer init failed, using on-chain: {e}")

    def enqueue(self, event: RedemptionEvent):
        """Non-blocking push — called by accumulator after settlement."""
        self._queue.put_nowait(event)

    async def start(self):
        """Background loop processing redemption events + periodic orphan sweep."""
        modes = {0: "on-chain (EOA)", 1: "PROXY relay (gasless)", 2: "Safe relayer (gasless)"}
        mode = modes.get(self._signature_type, f"type {self._signature_type}")
        self._logger.info(f"Redeemer started ({mode})")

        # Ensure CTF approvals are set for SELL orders (one-time)
        await self._ensure_ctf_approvals()

        await asyncio.gather(
            self._queue_loop(),
            self._sweep_loop(),
        )

    async def _queue_loop(self):
        """Process enqueued redemption events with pacing to avoid rate limits."""
        while True:
            event = await self._queue.get()
            try:
                await self._process_with_retry(event)
            except Exception as e:
                self._logger.error(f"Redemption failed permanently for {event.slug}: {e}")
                # Remove from swept so next sweep cycle will retry
                self._swept_conditions.discard(event.condition_id)
                self._save_swept_conditions()
            await asyncio.sleep(2)  # pace to avoid relayer 429s

    async def _ensure_ctf_approvals(self):
        """Check and set CTF approvals for all exchange contracts."""
        loop = asyncio.get_event_loop()

        def _check_and_approve():
            exchanges = [
                ("NegRiskCtfExchange", NEG_RISK_CTF_EXCHANGE),
                ("NegRiskAdapter", NEG_RISK_ADAPTER),
                ("CTFExchange", CTF_EXCHANGE),
            ]
            for name, addr in exchanges:
                operator = Web3.to_checksum_address(addr)
                approved = self._ctf.functions.isApprovedForAll(self._wallet, operator).call()
                if approved:
                    self._logger.info(f"CTF approval OK: {name}")
                    continue

                self._logger.info(f"Setting CTF approval for {name}...")
                gas_price = self._w3.eth.gas_price
                nonce = self._w3.eth.get_transaction_count(self._wallet)
                tx = self._ctf.functions.setApprovalForAll(
                    operator, True
                ).build_transaction({
                    "from": self._wallet,
                    "nonce": nonce,
                    "gas": 100000,
                    "gasPrice": int(gas_price * 1.3),
                    "chainId": 137,
                })
                signed = self._w3.eth.account.sign_transaction(tx, self._private_key)
                tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
                if receipt.get("status") == 1:
                    self._logger.info(f"CTF approval SET: {name} | tx={tx_hash.hex()[:16]}...")
                else:
                    self._logger.error(f"CTF approval FAILED: {name} | tx={tx_hash.hex()}")

        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _check_and_approve),
                timeout=180,
            )
        except Exception as e:
            self._logger.error(f"CTF approval check failed: {e}")

    async def _process_with_retry(self, event: RedemptionEvent):
        """Process with retry — markets take time to resolve on-chain."""
        for attempt, delay in enumerate(RETRY_DELAYS):
            result = await self._process(event)
            if result == "redeemed" or result == "no_tokens":
                return
            if result == "not_resolved":
                self._logger.info(
                    f"Retry {attempt + 1}/{len(RETRY_DELAYS)}: {event.slug} "
                    f"not resolved yet, waiting {delay}s"
                )
                await asyncio.sleep(delay)
                continue
            # Other failure — don't retry
            return

        self._logger.warning(f"Gave up redeeming {event.slug} after {len(RETRY_DELAYS)} retries")

    async def _process(self, event: RedemptionEvent) -> str:
        """Verify on-chain resolution, then redeem via appropriate method."""
        loop = asyncio.get_event_loop()
        cid_bytes = bytes.fromhex(event.condition_id[2:]) if event.condition_id.startswith("0x") else bytes.fromhex(event.condition_id)

        # 1. Check if market is resolved (read-only RPC call)
        def _check_resolved():
            return self._ctf.functions.payoutDenominator(cid_bytes).call()

        try:
            pd = await asyncio.wait_for(
                loop.run_in_executor(None, _check_resolved), timeout=30
            )
        except Exception as e:
            self._logger.error(f"Resolution check error {event.slug}: {e}")
            return "error"

        if pd == 0:
            return "not_resolved"

        # 2. Route based on signature type
        if self._signature_type == 1 and self._builder_config:
            result = await self._redeem_proxy_relay(event, cid_bytes)
            if result == "redeemed":
                return result
            # On-chain fallback doesn't work for type 1 (Proxy wallet)
            self._logger.warning(f"PROXY relay failed for {event.slug}: {result}")
            return result
        elif self._signature_type == 2 and self._relayer:
            result = await self._redeem_gasless(event, cid_bytes)
            if result == "redeemed":
                return result
            self._logger.warning(f"Safe relayer failed for {event.slug}, trying on-chain")

        # 3. Fallback: on-chain transaction
        return await self._redeem_onchain(event, cid_bytes)

    async def _redeem_proxy_relay(self, event: RedemptionEvent, cid_bytes: bytes) -> str:
        """Redeem via PROXY relay (gasless, for MagicLink/ProxyWallet type 1)."""
        loop = asyncio.get_event_loop()

        def _do():
            redeem_cd = _encode_redeem_calldata(cid_bytes)
            proxy_data = _encode_proxy_calldata([redeem_cd])

            # Get relay payload
            rp_resp = requests.get(
                f"{RELAYER_URL}relay-payload",
                params={"address": self._eoa, "type": "PROXY"},
                timeout=15,
            )
            rp_resp.raise_for_status()
            rp = rp_resp.json()

            gas_limit = str(500000)

            # GSN struct hash + EIP-191 sign
            struct_hash = _create_struct_hash(
                self._eoa, PROXY_FACTORY, proxy_data,
                "0", "0", gas_limit, rp["nonce"],
                RELAY_HUB, rp["address"],
            )
            signature = _sign_gsn(struct_hash, self._private_key)

            # Submit
            req_body = {
                "from": self._eoa,
                "to": PROXY_FACTORY,
                "proxyWallet": self._wallet,
                "data": proxy_data,
                "nonce": rp["nonce"],
                "signature": signature,
                "signatureParams": {
                    "gasPrice": "0",
                    "gasLimit": gas_limit,
                    "relayerFee": "0",
                    "relayHub": RELAY_HUB,
                    "relay": rp["address"],
                },
                "type": "PROXY",
                "metadata": f"Redeem {event.slug}",
            }
            headers = self._builder_config.generate_builder_headers(
                "POST", "/submit", str(req_body)
            )
            resp = requests.post(
                f"{RELAYER_URL}submit",
                json=req_body,
                headers=headers.to_dict(),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _do), timeout=120
            )
        except Exception as e:
            self._logger.error(f"PROXY relay error {event.slug}: {e}")
            return "proxy_failed"

        tx_id = result.get("transactionId", "unknown")
        self._total_redeemed += event.shares
        self._gasless_count += 1
        self._logger.info(
            f"Redeemed (PROXY relay): {event.slug} | {event.shares:.0f} shares | "
            f"txn_id={str(tx_id)[:16]}..."
        )
        return "redeemed"

    async def _redeem_gasless(self, event: RedemptionEvent, cid_bytes: bytes) -> str:
        """Redeem via Safe Builder Relayer (zero gas cost, type 2 only)."""
        loop = asyncio.get_event_loop()

        def _do():
            from py_builder_relayer_client.models import OperationType, SafeTransaction

            calldata = _encode_redeem_calldata(cid_bytes)

            txn = SafeTransaction(
                to=Web3.to_checksum_address(CTF_ADDRESS),
                operation=OperationType.Call,
                data=calldata,
                value="0",
            )

            resp = self._relayer.execute([txn], f"Redeem {event.slug}")
            awaited = resp.wait()
            return resp, awaited

        try:
            resp, awaited = await asyncio.wait_for(
                loop.run_in_executor(None, _do), timeout=120
            )
        except Exception as e:
            self._logger.error(f"Gasless redeem error {event.slug}: {e}")
            return "gasless_failed"

        if awaited:
            self._total_redeemed += event.shares
            self._gasless_count += 1
            tx_id = getattr(resp, 'transaction_id', 'unknown')
            tx_hash = getattr(resp, 'transaction_hash', '')
            self._logger.info(
                f"Redeemed (gasless): {event.slug} | {event.shares:.0f} shares | "
                f"id={tx_id} | tx={str(tx_hash)[:16]}..."
            )
            return "redeemed"

        self._logger.warning(f"Gasless redeem timed out for {event.slug}")
        return "gasless_failed"

    async def _redeem_onchain(self, event: RedemptionEvent, cid_bytes: bytes) -> str:
        """Redeem via direct on-chain transaction (costs POL gas)."""
        loop = asyncio.get_event_loop()

        def _do():
            # Check POL balance for gas
            pol = self._w3.eth.get_balance(self._wallet)
            pol_eth = self._w3.from_wei(pol, 'ether')
            if pol_eth < 0.5:
                self._logger.warning(f"Low POL: {pol_eth:.4f} — redemption may fail")

            gas_price = self._w3.eth.gas_price
            nonce = self._w3.eth.get_transaction_count(self._wallet)

            tx = self._ctf.functions.redeemPositions(
                Web3.to_checksum_address(USDC_E_ADDRESS),
                bytes(32),  # parentCollectionId = 0
                cid_bytes,
                [1, 2],     # indexSets: both UP and DOWN
            ).build_transaction({
                "from": self._wallet,
                "nonce": nonce,
                "gas": 200000,
                "gasPrice": int(gas_price * 1.3),
                "chainId": 137,
            })

            signed = self._w3.eth.account.sign_transaction(tx, self._private_key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)

            status = receipt.get("status", 0)
            gas_used = receipt.get("gasUsed", 0)
            gas_cost = float(self._w3.from_wei(gas_used * int(gas_price * 1.3), "ether"))

            return {
                "tx_hash": tx_hash.hex(),
                "status": status,
                "gas_used": gas_used,
                "gas_cost_pol": gas_cost,
            }

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _do),
                timeout=120,
            )
        except Exception as e:
            self._logger.error(f"On-chain redeem error for {event.slug}: {e}")
            return "error"

        if isinstance(result, dict):
            if result["status"] == 1:
                self._total_redeemed += event.shares
                self._total_gas_pol += result["gas_cost_pol"]
                self._onchain_count += 1
                self._logger.info(
                    f"Redeemed (on-chain): {event.slug} | {event.shares:.0f} shares | "
                    f"tx={result['tx_hash'][:16]}... | gas={result['gas_cost_pol']:.6f} POL"
                )
                return "redeemed"
            else:
                self._logger.error(
                    f"Redemption TX FAILED: {event.slug} | tx={result['tx_hash']}"
                )
                return "tx_failed"

        return "error"

    # -- Orphan sweep --

    async def _sweep_loop(self):
        """Periodic sweep: startup + every SWEEP_INTERVAL seconds."""
        await asyncio.sleep(10)  # let other subsystems start first
        while True:
            try:
                await self._sweep_orphans()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._logger.error(f"Orphan sweep error: {e}")
            await asyncio.sleep(SWEEP_INTERVAL)

    async def _sweep_orphans(self):
        """Query Data API for wallet positions, redeem any resolved orphans."""
        loop = asyncio.get_event_loop()

        def _fetch_positions():
            resp = requests.get(
                f"{DATA_API}/positions",
                params={"user": self._wallet.lower(), "sizeThreshold": "0", "limit": "500"},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

        try:
            positions = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_positions), timeout=60
            )
        except Exception as e:
            self._logger.warning(f"Orphan sweep: Data API error: {e}")
            return

        # Collect tracked token_ids from PositionStore (skip active positions)
        tracked_tokens = set()
        if self._store:
            for pos in self._store.get_open():
                tracked_tokens.add(pos.token_id)

        orphans = []
        for p in positions:
            size = float(p.get("size", 0) or 0)
            if size < 0.1:
                continue
            redeemable = p.get("redeemable", False)
            if not redeemable:
                continue
            condition_id = p.get("conditionId", "")
            if not condition_id:
                continue
            token_id = p.get("asset", "")
            # Skip if actively tracked by the bot
            if token_id in tracked_tokens:
                continue
            # Skip if already swept this session
            if condition_id in self._swept_conditions:
                continue
            orphans.append({
                "condition_id": condition_id,
                "size": size,
                "title": (p.get("title") or "")[:50],
                "token_id": token_id,
            })

        if not orphans:
            self._logger.debug("Orphan sweep: 0 redeemable orphans found")
            return

        self._logger.info(f"Orphan sweep: {len(orphans)} redeemable orphans found")

        # Dedupe by condition_id (multiple tokens can share one condition)
        seen_conditions = {}
        for o in orphans:
            cid = o["condition_id"]
            if cid not in seen_conditions:
                seen_conditions[cid] = o
            else:
                seen_conditions[cid]["size"] += o["size"]

        for cid, info in seen_conditions.items():
            event = RedemptionEvent(
                condition_id=cid,
                slug=f"orphan:{info['title']}",
                winning_side="",
                shares=info["size"],
                settled_at=time.time(),
            )
            self._logger.info(
                f"Orphan sweep: queuing {info['size']:.0f} shares | {info['title']}"
            )
            self._swept_conditions.add(cid)
            self.enqueue(event)
            self.sweep_count += 1

        # Persist to disk so restarts don't re-redeem
        if seen_conditions:
            self._save_swept_conditions()
