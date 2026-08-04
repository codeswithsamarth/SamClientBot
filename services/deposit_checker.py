"""
services/deposit_checker.py

DEPOSIT CHECKER - MULTI-COIN + PROGRESSIVE CONFIRMATIONS

Supports: USDT, BUSD, USDC on BEP20 & Polygon
Max wait: ~60 seconds for any legitimate deposit
Never permanently fails a deposit due to slow confirmations
"""

from __future__ import annotations

import asyncio
import email
import hashlib
import hmac
import imaplib
import json
import logging
import re
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.header import decode_header
from typing import Optional, Union

import requests

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

from database import SessionLocal
from models.deposit import Deposit
from models.user import User

import config

logger = logging.getLogger(__name__)

# ╔══════════════════════════════════════════════════════════════╗
# ║                    CONFIGURATION                            ║
# ╚══════════════════════════════════════════════════════════════╝

BINANCE_API_KEY = getattr(config, "BINANCE_API_KEY", "")
BINANCE_API_SECRET = getattr(config, "BINANCE_API_SECRET", "")
NETWORK_MAP = getattr(config, "BINANCE_NETWORK_MAP", {})

DEPOSIT_COINS = getattr(config, "BINANCE_DEPOSIT_COINS", ["USDT", "BUSD", "USDC"])

try:
    BINANCE_LOOKBACK_DAYS = int(getattr(config, "BINANCE_DEPOSIT_LOOKBACK_DAYS", 7))
except (TypeError, ValueError):
    BINANCE_LOOKBACK_DAYS = 7

BINANCE_CONFIRMED_STATUS = 1

BEP20_ADDRESS = getattr(config, "BEP20_ADDRESS", "").lower()
POLYGON_ADDRESS = getattr(config, "POLYGON_ADDRESS", "").lower()
UPI_NETWORK = "UPI"

# Official Contracts
OFFICIAL_CONTRACTS = {
    "BEP20": {
        "USDT": "0x55d398326f99059ff775485246999027b3197955",
        "BUSD": "0xe9e7cea3dedca5984780bafc599bd69add087d56",
        "USDC": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    },
    "POLYGON": {
        "USDT": "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
        "USDC": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
    },
}

# Progressive confirmation tiers per network
NETWORK_CONFIRMATION_TIERS = {
    "BEP20": {"verified_contract": 1, "missing_contract": 3},      # 3s / 9s
    "POLYGON": {"verified_contract": 1, "missing_contract": 5},    # 2s / 10s
}
MAX_WAIT_SECONDS = 60   # After 60 seconds, accept at 1 confirmation (flash USDT impossible)
CHECK_INTERVAL = 10      # Check every 10 seconds

BSCSCAN_API_KEY = getattr(config, "BSCSCAN_API_KEY", "")
POLYGONSCAN_API_KEY = getattr(config, "POLYGONSCAN_API_KEY", "")
ETHERSCAN_V2_API_KEY = getattr(config, "ETHERSCAN_V2_API_KEY", "")

# Layer Toggles
ENABLE_CONTRACT_CHECK = getattr(config, "ENABLE_CONTRACT_CHECK", True)
ENABLE_BLOCKLIST_CHECK = getattr(config, "ENABLE_BLOCKLIST_CHECK", True)
ENABLE_SENDER_BLOCKLIST = getattr(config, "ENABLE_SENDER_BLOCKLIST", True)
ENABLE_CONFIRMATION_CHECK = getattr(config, "ENABLE_CONFIRMATION_CHECK", True)
ENABLE_FINALITY_CHECK = getattr(config, "ENABLE_FINALITY_CHECK", False)
ENABLE_SWAP_LIQUIDITY = getattr(config, "ENABLE_SWAP_LIQUIDITY", False)
ENABLE_EXPLORER_CHECK = getattr(config, "ENABLE_EXPLORER_CHECK", True)
ENABLE_TIMESTAMP_CHECK = getattr(config, "ENABLE_TIMESTAMP_CHECK", True)
ENABLE_VALUE_CHECK = getattr(config, "ENABLE_VALUE_CHECK", True)
ENABLE_DUST_CHECK = getattr(config, "ENABLE_DUST_CHECK", True)

KNOWN_FAKE_CONTRACTS = getattr(config, "KNOWN_FAKE_CONTRACTS", [])
KNOWN_SCAM_SENDERS = getattr(config, "KNOWN_SCAM_SENDERS", [])
MIN_DEPOSIT_USD = getattr(config, "MIN_DEPOSIT_USD", 0.01)

# Binance Pay
BINANCE_PAY_NETWORK = "BINANCE"
PAY_TRANSACTIONS_URL = "https://api.binance.com/sapi/v1/pay/transactions"
PAY_TRANSACTION_MATCH_FIELDS = ("transactionId", "orderId", "id", "referenceId", "merchantTradeNo")

try:
    BINANCE_PAY_LOOKBACK_DAYS = int(getattr(config, "BINANCE_PAY_LOOKBACK_DAYS", 7))
except (TypeError, ValueError):
    BINANCE_PAY_LOOKBACK_DAYS = 7

BINANCE_PAY_ACCEPTED_CURRENCIES = getattr(config, "BINANCE_PAY_ACCEPTED_CURRENCIES", ["USDT", "BUSD", "USDC"])
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9]{8,32}$")

try:
    AMOUNT_TOLERANCE = Decimal(str(getattr(config, "DEPOSIT_AMOUNT_TOLERANCE", "0.01")))
except (InvalidOperation, ValueError):
    AMOUNT_TOLERANCE = Decimal("0.01")

ALLOW_OVERPAY = getattr(config, "DEPOSIT_ALLOW_OVERPAY", True)
try:
    MAX_CHECK_ATTEMPTS = int(getattr(config, "DEPOSIT_MAX_CHECK_ATTEMPTS", 60))
except (TypeError, ValueError):
    MAX_CHECK_ATTEMPTS = 60

_check_attempts: dict[int, int] = {}
DELETE_FAILED_DEPOSITS = getattr(config, "DEPOSIT_DELETE_FAILED", False)
DEPOSIT_BEFORE_TX_GRACE_DAYS = getattr(config, "DEPOSIT_BEFORE_TX_GRACE_DAYS", 5)

IMAP_HOST = getattr(config, "IMAP_HOST", "imap.gmail.com")
IMAP_EMAIL = getattr(config, "IMAP_EMAIL", "")
IMAP_APP_PASSWORD = getattr(config, "IMAP_APP_PASSWORD", "")
FAMAPP_SENDER_EMAIL = getattr(config, "FAMAPP_SENDER_EMAIL", "")
UPI_ID = getattr(config, "UPI_ID", "")
IMAP_LOOKBACK_DAYS = getattr(config, "IMAP_LOOKBACK_DAYS", 1)
MAX_EMAILS_TO_SCAN = getattr(config, "UPI_MAX_EMAILS_TO_SCAN", 40)

UTR_RE = re.compile(r"^\d{12}$")
TXN_ID_RE = re.compile(r"^[A-Za-z]{3,10}\d{6,15}$")
UTR_SPECIFIC_RE = re.compile(
    r"(?:UTR(?:\s*No\.?)?|UPI\s*Ref(?:erence)?(?:\s*No\.?)?|RRN)[\s:\-]{0,10}(\d{12})", re.IGNORECASE)
TXN_ID_SPECIFIC_RE = re.compile(
    r"(?:Txn\s*(?:ID|Ref(?:erence)?)|Transaction\s*ID|Reference\s*ID)[\s:\-]{0,10}([A-Za-z]{3,10}\d{6,15})", re.IGNORECASE)
UTR_FALLBACK_RE = re.compile(r"\b(\d{12})\b")
TXN_ID_FALLBACK_RE = re.compile(r"\b([A-Za-z]{3,10}\d{6,15})\b")
AMOUNT_RE = re.compile(r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
RECEIVED_AMOUNT_RE = re.compile(
    r"(?:Received|Credited|Payment\s+of|Amount\s+Received|Paid)[:\s]*"
    r"(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)

TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

_binance_client: Optional[Client] = None


def valid_hash(tx_hash: str) -> bool:
    return bool(TX_HASH_RE.match(tx_hash)) if isinstance(tx_hash, str) else False


def valid_order_id(order_id: str) -> bool:
    return bool(ORDER_ID_RE.match(order_id)) if isinstance(order_id, str) else False


# ╔══════════════════════════════════════════════════════════════╗
# ║                    DATA CLASSES                             ║
# ╚══════════════════════════════════════════════════════════════╝

@dataclass
class Chain:
    name: str
    address: str
    binance_network: str
    contracts: dict = field(default_factory=dict)
    explorer_url: str = ""
    explorer_api_key: str = ""
    chain_id: int = 0


CHAINS = {
    "BEP20": Chain(
        name="BEP20",
        address=BEP20_ADDRESS,
        binance_network=NETWORK_MAP.get("BEP20", "BSC"),
        contracts=OFFICIAL_CONTRACTS.get("BEP20", {}),
        explorer_url="https://api.etherscan.io/v2/api",
        explorer_api_key=ETHERSCAN_V2_API_KEY or BSCSCAN_API_KEY,
        chain_id=56,
    ),
    "POLYGON": Chain(
        name="POLYGON",
        address=POLYGON_ADDRESS,
        binance_network=NETWORK_MAP.get("POLYGON", "MATIC"),
        contracts=OFFICIAL_CONTRACTS.get("POLYGON", {}),
        explorer_url="https://api.etherscan.io/v2/api",
        explorer_api_key=ETHERSCAN_V2_API_KEY or POLYGONSCAN_API_KEY,
        chain_id=137,
    ),
}

@dataclass
class VerificationResult:
    passed: bool
    layer: str
    reason: str = ""
    details: dict = field(default_factory=dict)


# ╔══════════════════════════════════════════════════════════════╗
# ║                    HELPERS                                  ║
# ╚══════════════════════════════════════════════════════════════╝

def _get_binance_client() -> Optional[Client]:
    global _binance_client
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        return None
    try:
        _binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
        server_time = _binance_client.get_server_time()["serverTime"]
        _binance_client.timestamp_offset = server_time - int(time_module.time() * 1000)
        logger.info("Binance synced | offset=%sms", _binance_client.timestamp_offset)
    except Exception as e:
        logger.error("Binance init failed: %s", e)
        _binance_client = None
    return _binance_client


def _record_pending_attempt(deposit_id: int) -> int:
    count = _check_attempts.get(deposit_id, 0) + 1
    _check_attempts[deposit_id] = count
    return count


def _clear_pending_attempts(deposit_id: int) -> None:
    _check_attempts.pop(deposit_id, None)


def _finalize_failed(db, deposit: Deposit, reason: str) -> None:
    if DELETE_FAILED_DEPOSITS:
        db.delete(deposit)
    else:
        deposit.status = "failed"
    db.commit()
    logger.warning("Deposit %s FAILED: %s", deposit.id, reason)
    _clear_pending_attempts(deposit.id)


def _parse_confirmations(raw_value) -> int:
    if raw_value is None:
        return 0
    try:
        if isinstance(raw_value, str):
            raw_value = raw_value.strip()
            if not raw_value:
                return 0
            if "/" in raw_value:
                parts = raw_value.split("/")
                return int(parts[0].strip()) if parts[0].strip().isdigit() else 0
            if raw_value.isdigit():
                return int(raw_value)
            return 0
        elif isinstance(raw_value, (int, float)):
            return int(raw_value)
        elif isinstance(raw_value, bool):
            return 1 if raw_value else 0
        return 0
    except (ValueError, TypeError):
        return 0


def _get_coin_from_row(row: dict) -> str:
    coin = (row.get("coin") or row.get("asset") or "").upper()
    return coin if coin in ["USDT", "BUSD", "USDC"] else "USDT"


# ╔══════════════════════════════════════════════════════════════╗
# ║  LAYER 1-7: QUICK CHECKS (instant rejection or pass)       ║
# ╚══════════════════════════════════════════════════════════════╝

def _layer_1_contract_check(chain: Chain, row: dict, tx_hash: str, coin: str) -> VerificationResult:
    if not ENABLE_CONTRACT_CHECK:
        return VerificationResult(True, "L1_CONTRACT", "disabled")
    official = chain.contracts.get(coin, "")
    received = (row.get("contractAddress") or row.get("tokenContractAddress") or
                row.get("tokenAddress") or "").lower()
    if not official:
        return VerificationResult(True, "L1_CONTRACT", "no_official_contract")
    if not received:
        return VerificationResult(True, "L1_CONTRACT", "no_contract_data")
    if received != official:
        return VerificationResult(False, "L1_CONTRACT", "contract_mismatch",
                                  {"official": official[:16], "received": received[:16]})
    return VerificationResult(True, "L1_CONTRACT", "verified")


def _layer_2_blocklist_check(chain: Chain, row: dict, tx_hash: str, coin: str) -> VerificationResult:
    if not ENABLE_BLOCKLIST_CHECK:
        return VerificationResult(True, "L2_BLOCKLIST", "disabled")
    received = (row.get("contractAddress") or row.get("tokenContractAddress") or "").lower()
    if not received:
        return VerificationResult(True, "L2_BLOCKLIST", "no_contract")
    for fake in KNOWN_FAKE_CONTRACTS:
        if received == fake.lower():
            return VerificationResult(False, "L2_BLOCKLIST", "known_fake")
    return VerificationResult(True, "L2_BLOCKLIST", "clean")


def _layer_3_sender_check(chain: Chain, row: dict, tx_hash: str, coin: str) -> VerificationResult:
    if not ENABLE_SENDER_BLOCKLIST or not KNOWN_SCAM_SENDERS:
        return VerificationResult(True, "L3_SENDER", "disabled_or_empty")
    sender = (row.get("fromAddress") or row.get("sender") or "").lower()
    if not sender:
        return VerificationResult(True, "L3_SENDER", "no_data")
    for scammer in KNOWN_SCAM_SENDERS:
        if sender == scammer.lower():
            return VerificationResult(False, "L3_SENDER", "known_scammer")
    return VerificationResult(True, "L3_SENDER", "clean")


def _layer_5_explorer_check(chain: Chain, row: dict, tx_hash: str, coin: str) -> VerificationResult:
    if not ENABLE_EXPLORER_CHECK or not chain.explorer_api_key:
        return VerificationResult(True, "L5_EXPLORER", "disabled")
    try:
        resp = requests.get(chain.explorer_url, params={
            "chainid": chain.chain_id,
            "module": "account",
            "action": "tokentx",
            "txhash": tx_hash,
            "apikey": chain.explorer_api_key,
        }, timeout=10)
        data = resp.json()
        if data.get("status") != "1" or not data.get("result"):
            return VerificationResult(True, "L5_EXPLORER", "not_found_or_api_error")
        return VerificationResult(True, "L5_EXPLORER", "verified")
    except Exception:
        return VerificationResult(True, "L5_EXPLORER", "timeout")


def _layer_6_timestamp_check(chain: Chain, row: dict, tx_hash: str, coin: str,
                              deposit_created_at: Optional[datetime] = None) -> VerificationResult:
    if not ENABLE_TIMESTAMP_CHECK:
        return VerificationResult(True, "L6_TIME", "disabled")
    ts = row.get("insertTime") or row.get("timestamp") or 0
    try:
        tx_time = datetime.fromtimestamp(int(ts) / 1000.0)
    except:
        return VerificationResult(True, "L6_TIME", "no_timestamp")
    if tx_time > datetime.now() + timedelta(minutes=5):
        return VerificationResult(False, "L6_TIME", "future")
    if deposit_created_at and tx_time < deposit_created_at - timedelta(days=DEPOSIT_BEFORE_TX_GRACE_DAYS):
        return VerificationResult(False, "L6_TIME", "too_old")
    return VerificationResult(True, "L6_TIME", "valid")


def _layer_7_value_check(chain: Chain, row: dict, tx_hash: str, coin: str,
                          requested: Optional[Decimal] = None) -> VerificationResult:
    if not ENABLE_VALUE_CHECK:
        return VerificationResult(True, "L7_VALUE", "disabled")
    try:
        amount = Decimal(str(row.get("amount", "0")))
    except:
        return VerificationResult(False, "L7_VALUE", "invalid")
    if amount <= 0:
        return VerificationResult(False, "L7_VALUE", "zero_or_negative")
    if ENABLE_DUST_CHECK and float(amount) < MIN_DEPOSIT_USD:
        return VerificationResult(False, "L7_VALUE", "dust")
    if requested and amount < requested - AMOUNT_TOLERANCE:
        return VerificationResult(False, "L7_VALUE", "underpaid")
    return VerificationResult(True, "L7_VALUE", "valid")


# ╔══════════════════════════════════════════════════════════════╗
# ║  LAYER 8: PROGRESSIVE CONFIRMATIONS (wait, never fail)     ║
# ╚══════════════════════════════════════════════════════════════╝

def _layer_8_confirmation_check(chain: Chain, row: dict, tx_hash: str, coin: str,
                                 contract_passed: bool = True,
                                 deposit_created_at: Optional[datetime] = None) -> VerificationResult:
    """
    PROGRESSIVE CONFIRMATION REQUIREMENTS — Never permanently fails.

    RISK-BASED TIERS:
      • Contract VERIFIED → 1 confirmation (~3 sec BSC, ~2 sec Polygon)
      • Contract MISSING (Trust Wallet) → 3-5 confirmations (~9-10 sec)

    60-SECOND FALLBACK:
      • If deposit is older than 60 seconds → ACCEPT at 1 confirmation
      • Flash USDT CANNOT survive 60+ seconds on any chain
      • This ensures NO legitimate deposit ever fails

    RETURNS:
      • "retry" in details → Check again next poll (NEVER permanent fail)
    """
    if not ENABLE_CONFIRMATION_CHECK:
        return VerificationResult(True, "L8_CONF", "disabled")

    raw = row.get("confirmTimes") or row.get("confirmations") or row.get("confirmNo") or 0
    confs = _parse_confirmations(raw)

    # Determine required confirmations based on risk
    tiers = NETWORK_CONFIRMATION_TIERS.get(chain.name,
                                           {"verified_contract": 1, "missing_contract": 3})
    required = tiers["verified_contract"] if contract_passed else tiers["missing_contract"]

    # 60-second fallback: if deposit is old enough, reduce to 1 confirmation
    if deposit_created_at:
        tx_age = (datetime.now() - deposit_created_at).total_seconds()
        if tx_age > MAX_WAIT_SECONDS:
            required = 1

    if confs < required:
        return VerificationResult(
            False, "L8_CONF", "waiting_for_blocks",
            {
                "current": confs,
                "required": required,
                "retry": True,       # ← ALWAYS retry, never fail
                "retry_in_seconds": CHECK_INTERVAL
            }
        )

    return VerificationResult(True, "L8_CONF", "sufficient",
                             {"confirmations": confs, "required": required})


# ╔══════════════════════════════════════════════════════════════╗
# ║  LAYER 9: ADVISORY ONLY (never rejects)                    ║
# ╚══════════════════════════════════════════════════════════════╝

def _layer_9_liquidity_check(chain: Chain, row: dict, tx_hash: str, coin: str) -> VerificationResult:
    if not ENABLE_SWAP_LIQUIDITY:
        return VerificationResult(True, "L9_LIQ", "disabled")
    # Always returns True — advisory only
    return VerificationResult(True, "L9_LIQ", "advisory")


# ╔══════════════════════════════════════════════════════════════╗
# ║  RUN ALL LAYERS                                            ║
# ╚══════════════════════════════════════════════════════════════╝

def _verify_all_layers(chain: Chain, row: dict, tx_hash: str, coin: str,
                       deposit_created_at=None, requested_amount=None):
    results = []

    # L1: Contract (hard fail)
    r1 = _layer_1_contract_check(chain, row, tx_hash, coin)
    results.append(r1)
    if not r1.passed: return results

    # L2: Blocklist (hard fail)
    r2 = _layer_2_blocklist_check(chain, row, tx_hash, coin)
    results.append(r2)
    if not r2.passed: return results

    # L3: Sender blocklist (hard fail)
    r3 = _layer_3_sender_check(chain, row, tx_hash, coin)
    results.append(r3)
    if not r3.passed: return results

    # L4: Dedup (always pass)
    results.append(VerificationResult(True, "L4_DEDUP", "passed"))

    # L5: Explorer (advisory — never stops)
    results.append(_layer_5_explorer_check(chain, row, tx_hash, coin))

    # L6: Timestamp (hard fail for future/too-old only)
    r6 = _layer_6_timestamp_check(chain, row, tx_hash, coin, deposit_created_at)
    results.append(r6)
    if not r6.passed: return results

    # L7: Value (hard fail)
    r7 = _layer_7_value_check(chain, row, tx_hash, coin, requested_amount)
    results.append(r7)
    if not r7.passed: return results

    # L8: Confirmations (ALWAYS RETRY — never permanent fail)
    contract_verified = r1.passed and r1.reason == "verified"
    r8 = _layer_8_confirmation_check(chain, row, tx_hash, coin,
                                      contract_verified, deposit_created_at)
    results.append(r8)
    if not r8.passed: return results

    # L9: Advisory
    results.append(_layer_9_liquidity_check(chain, row, tx_hash, coin))

    return results


# ╔══════════════════════════════════════════════════════════════╗
# ║  FETCH + MATCH                                             ║
# ╚══════════════════════════════════════════════════════════════╝

def _fetch_binance_deposits_sync(binance_network: str) -> list[dict]:
    client = _get_binance_client()
    if client is None:
        return []

    end_time = int(time_module.time() * 1000)
    start_time = end_time - BINANCE_LOOKBACK_DAYS * 24 * 60 * 60 * 1000
    all_deposits = []

    for coin in DEPOSIT_COINS:
        try:
            deposits = client.get_deposit_history(
                coin=coin, network=binance_network,
                startTime=start_time, endTime=end_time,
            ) or []
            for d in deposits:
                d["_coin"] = coin
            all_deposits.extend(deposits)
        except BinanceAPIException as e:
            if "Timestamp" in str(e):
                global _binance_client
                _binance_client = None
            logger.warning("API error %s/%s: %s", coin, binance_network, e)
        except Exception as e:
            logger.warning("Error %s/%s: %s", coin, binance_network, e)

    return all_deposits


async def _fetch_binance_matches(chain: Chain) -> dict[str, dict]:
    if not chain.binance_network:
        return {}
    rows = await asyncio.to_thread(_fetch_binance_deposits_sync, chain.binance_network)
    return {(row.get("txId") or "").lower(): row for row in rows if row.get("txId")}


def _match_binance_row(chain: Chain, tx_hash: str, matches: dict[str, dict],
                       deposit_created_at=None, requested_amount=None):
    row = matches.get(tx_hash.lower())
    if row is None:
        return None

    coin = row.get("_coin", _get_coin_from_row(row))

    try:
        status = int(row.get("status", -1))
    except:
        status = -1
    if status != BINANCE_CONFIRMED_STATUS:
        return None

    address = (row.get("address") or "").lower()
    if chain.address and address and address != chain.address:
        logger.warning("[%s] ADDR MISMATCH: got=%s", chain.name, address[:16])
        return False

    results = _verify_all_layers(chain, row, tx_hash, coin, deposit_created_at, requested_amount)

    statuses = []
    for r in results:
        icon = "✓" if r.passed else "✗"
        statuses.append(f"{icon}{r.layer}")
    logger.info("[%s] %s | tx=%s coin=%s", chain.name, " ".join(statuses), tx_hash[:16], coin)

    for r in results:
        if not r.passed:
            if r.details.get("retry"):
                return None
            return False

    try:
        amount = Decimal(str(row.get("amount", "0")))
    except:
        return False

    return {
        "sender": "BINANCE", "receiver": address, "amount": amount,
        "coin": coin, "network": row.get("network", chain.binance_network),
        "verified_by": [r.layer for r in results],
    }


async def verify_transaction(chain: Chain, tx_hash: str,
                             deposit_created_at=None, requested_amount=None):
    if not chain.binance_network:
        return None
    matches = await _fetch_binance_matches(chain)
    return _match_binance_row(chain, tx_hash, matches, deposit_created_at, requested_amount)


# ╔══════════════════════════════════════════════════════════════╗
# ║  BINANCE PAY                                               ║
# ╚══════════════════════════════════════════════════════════════╝

def _sapi_sign(params: dict, secret: str) -> str:
    return hmac.new(secret.encode(),
        "&".join(f"{k}={v}" for k, v in params.items()).encode(),
        hashlib.sha256).hexdigest()


def _query_pay_trade_history_sync(lookback_days: int) -> list[dict]:
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        return []
    client = _get_binance_client()
    if client is None:
        return []
    try:
        server_time = client.get_server_time()["serverTime"]
    except:
        return []
    end_time = server_time
    start_time = end_time - lookback_days * 24 * 60 * 60 * 1000
    params = {"startTime": start_time, "endTime": end_time,
              "recvWindow": 5000, "timestamp": server_time}
    params["signature"] = _sapi_sign(params, BINANCE_API_SECRET)
    try:
        resp = requests.get(PAY_TRANSACTIONS_URL,
                            headers={"X-MBX-APIKEY": BINANCE_API_KEY},
                            params=params, timeout=15)
        if resp.status_code != 200: return []
        payload = resp.json()
        rows = payload.get("data", [])
        return rows if isinstance(rows, list) else []
    except:
        return []


def _match_pay_transaction(order_id: str, rows: list[dict]) -> Optional[dict]:
    for row in rows:
        matched = any(str(row.get(f)) == str(order_id)
                     for f in PAY_TRANSACTION_MATCH_FIELDS if row.get(f) is not None)
        if not matched: continue
        try:
            if Decimal(str(row.get("amount", "0"))) > 0:
                return row
        except: pass
    return None


async def verify_binance_pay_order(order_id: str) -> Optional[Union[dict, bool]]:
    rows = await asyncio.to_thread(_query_pay_trade_history_sync, BINANCE_PAY_LOOKBACK_DAYS)
    match = _match_pay_transaction(order_id, rows)
    if match:
        currency = str(match.get("currency", "")).upper()
        if currency not in [c.upper() for c in BINANCE_PAY_ACCEPTED_CURRENCIES]:
            return False
        return {"sender": "BINANCE_PAY", "receiver": "",
                "amount": Decimal(str(match.get("amount", "0"))).copy_abs(),
                "currency": currency, "order_id": order_id}
    return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  UPI                                                       ║
# ╚══════════════════════════════════════════════════════════════╝

def valid_utr(utr: str) -> bool:
    return bool(UTR_RE.match(utr) or TXN_ID_RE.match(utr)) if isinstance(utr, str) else False


def _decode_text(text) -> str:
    if text is None: return ""
    result = ""
    for value, encoding in decode_header(text):
        result += value.decode(encoding or "utf-8", errors="ignore") if isinstance(value, bytes) else value
    return result


def _get_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                payload = part.get_payload(decode=True)
                if payload: return payload.decode(errors="ignore")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(errors="ignore") if payload else ""


def _extract_upi_identifiers(text: str) -> dict:
    result = {"utr": None, "txn_id": None, "amount": None}
    if not text: return result
    utr_match = UTR_SPECIFIC_RE.search(text)
    if utr_match: result["utr"] = utr_match.group(1)
    txn_match = TXN_ID_SPECIFIC_RE.search(text)
    if txn_match: result["txn_id"] = txn_match.group(1)
    if not result["utr"]:
        fallback = UTR_FALLBACK_RE.search(text)
        if fallback and fallback.group(1) != result["txn_id"]:
            result["utr"] = fallback.group(1)
    if not result["txn_id"]:
        fallback = TXN_ID_FALLBACK_RE.search(text)
        if fallback and fallback.group(1) != result["utr"]:
            result["txn_id"] = fallback.group(1)
    pos = utr_match.start() if utr_match else (txn_match.start() if txn_match else None)
    amt = _extract_amount(text, pos)
    if amt is not None: result["amount"] = amt
    return result


def _extract_amount(text: str, near_pos: Optional[int] = None) -> Optional[Decimal]:
    if not text: return None
    def td(raw):
        try: return Decimal(raw.replace(",", ""))
        except InvalidOperation: return None
    rm = list(RECEIVED_AMOUNT_RE.finditer(text))
    if rm:
        if near_pos is not None:
            best = min(rm, key=lambda m: abs(m.start(1) - near_pos))
            v = td(best.group(1))
            if v is not None: return v
        v = td(rm[0].group(1))
        if v is not None: return v
    am = list(AMOUNT_RE.finditer(text))
    if am:
        if near_pos is not None:
            best = min(am, key=lambda m: abs(m.start() - near_pos))
            v = td(best.group(1))
            if v is not None: return v
        return td(am[0].group(1))
    return None


def _fetch_famapp_matches() -> dict:
    if not IMAP_EMAIL or not IMAP_APP_PASSWORD or not FAMAPP_SENDER_EMAIL:
        return {}
    matches, mail = {}, None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(IMAP_EMAIL, IMAP_APP_PASSWORD)
        mail.select("INBOX")
        since = (datetime.now() - timedelta(days=IMAP_LOOKBACK_DAYS)).strftime("%d-%b-%Y")
        status, data = mail.search(None, f'(FROM "{FAMAPP_SENDER_EMAIL}" SINCE {since})')
        if status != "OK": return {}
        ids = data[0].split()[-MAX_EMAILS_TO_SCAN:]
        if not ids: return {}
        status, msg_data = mail.fetch(b",".join(ids), "(RFC822)")
        if status != "OK" or not msg_data: return {}
        for item in msg_data:
            if not isinstance(item, tuple): continue
            msg = email.message_from_bytes(item[1])
            full = f"{_decode_text(msg.get('Subject'))}\n{_get_body(msg)}"
            ids_dict = _extract_upi_identifiers(full)
            if ids_dict["amount"] is None: continue
            utr, txn_id = ids_dict["utr"], ids_dict["txn_id"]
            if not utr and not txn_id: continue
            payment_info = {"amount": ids_dict["amount"], "utr": utr, "txn_id": txn_id}
            if utr: matches[utr.upper()] = payment_info
            if txn_id: matches[txn_id.upper()] = payment_info
        return matches
    except Exception:
        logger.exception("UPI IMAP failed")
        return {}
    finally:
        if mail:
            try: mail.logout()
            except: pass


def _match_upi(deposit: Deposit, matches: dict) -> Optional[Union[dict, bool]]:
    user_input = (deposit.tx_hash or "").strip()
    if not valid_utr(user_input): return False
    payment_info = matches.get(user_input.upper())
    if payment_info is None: return None
    return {"sender": "UPI", "receiver": UPI_ID,
            "inr_amount": payment_info["amount"],
            "amount": payment_info["amount"],
            "utr": payment_info.get("utr") or user_input,
            "txn_id": payment_info.get("txn_id")}


async def verify_upi(deposit: Deposit) -> Optional[Union[dict, bool]]:
    try:
        return _match_upi(deposit, await asyncio.to_thread(_fetch_famapp_matches))
    except:
        return None


# ╔══════════════════════════════════════════════════════════════╗
# ║  INR CONVERSION                                            ║
# ╚══════════════════════════════════════════════════════════════╝

_usdt_inr_rate_cache: dict = {"rate": None, "last_updated": None, "ttl_seconds": 300}

def _get_usdt_inr_rate() -> Decimal:
    now = time_module.time()
    if (_usdt_inr_rate_cache["rate"] is not None
            and _usdt_inr_rate_cache["last_updated"] is not None
            and (now - _usdt_inr_rate_cache["last_updated"]) < _usdt_inr_rate_cache["ttl_seconds"]):
        return _usdt_inr_rate_cache["rate"]
    client = _get_binance_client()
    if client is not None:
        try:
            rate = Decimal(str(client.get_symbol_ticker(symbol="USDTINR")["price"]))
            _usdt_inr_rate_cache["rate"] = rate
            _usdt_inr_rate_cache["last_updated"] = now
            return rate
        except: pass
        try:
            btc_inr = Decimal(str(client.get_symbol_ticker(symbol="BTCINR")["price"]))
            btc_usdt = Decimal(str(client.get_symbol_ticker(symbol="BTCUSDT")["price"]))
            if btc_inr > 0 and btc_usdt > 0:
                rate = (btc_inr / btc_usdt).quantize(Decimal("0.01"))
                _usdt_inr_rate_cache["rate"] = rate
                _usdt_inr_rate_cache["last_updated"] = now
                return rate
        except: pass
    return Decimal(str(getattr(config, "UPI_USDT_INR_RATE", 95.0)))

def convert_inr_to_usdt(inr_amount: Decimal) -> Decimal:
    rate = _get_usdt_inr_rate()
    return (inr_amount / rate).quantize(Decimal("0.000001")) if rate > 0 else Decimal("0")


# ╔══════════════════════════════════════════════════════════════╗
# ║  CREDIT USER                                               ║
# ╚══════════════════════════════════════════════════════════════╝

def credit_user(db, deposit: Deposit, credited_amount: Decimal,
                requested_amount: Decimal, inr_amount=None, usdt_inr_rate=None) -> bool:
    user = db.query(User).filter(User.telegram_id == deposit.telegram_id).first()
    if user is None: return False
    user.balance += Decimal(str(credited_amount))
    if hasattr(user, "total_deposit"):
        user.total_deposit += float(credited_amount)
    deposit.amount = float(credited_amount)
    deposit.status = "completed"
    if hasattr(deposit, 'received_amount'):
        deposit.received_amount = float(credited_amount)
    if inr_amount is not None and hasattr(deposit, 'inr_amount'):
        deposit.inr_amount = float(inr_amount)
    if usdt_inr_rate is not None and hasattr(deposit, 'conversion_rate'):
        deposit.conversion_rate = float(usdt_inr_rate)
    db.commit()
    logger.info("CREDITED | User=%s Amount=%s | Balance=%s",
                user.telegram_id, credited_amount, user.balance)
    return True


# ╔══════════════════════════════════════════════════════════════╗
# ║  VERIFY ONE DEPOSIT                                        ║
# ╚══════════════════════════════════════════════════════════════╝

async def verify_deposit(deposit_or_id, upi_matches=None,
                         binance_matches=None, result_info=None):
    db = SessionLocal()
    try:
        deposit_id = deposit_or_id if isinstance(deposit_or_id, int) else deposit_or_id.id
        deposit = db.get(Deposit, deposit_id)
        if deposit is None:
            _clear_pending_attempts(deposit_id)
            return False
        if deposit.status in ("completed", "failed"):
            _clear_pending_attempts(deposit_id)
            return deposit.status == "completed"

        try:
            requested_amount = Decimal(str(deposit.amount))
        except (InvalidOperation, TypeError):
            requested_amount = None

        network = deposit.network.upper() if deposit.network else ""
        deposit_created_at = getattr(deposit, 'created_at', None) or getattr(deposit, 'timestamp', None)
        upi_inr_amount, upi_rate = None, None

        if network == UPI_NETWORK:
            if not valid_utr(deposit.tx_hash or ""):
                _fail_deposit(db, deposit.id, "invalid UTR")
                return False
            verification = _match_upi(deposit, upi_matches) if upi_matches else await verify_upi(deposit)
            if verification and isinstance(verification, dict) and verification.get("sender") == "UPI":
                upi_inr_amount = verification["inr_amount"]
                upi_rate = _get_usdt_inr_rate()
                verification["amount"] = convert_inr_to_usdt(upi_inr_amount)
        elif network == BINANCE_PAY_NETWORK:
            if not valid_order_id(deposit.tx_hash or ""):
                _fail_deposit(db, deposit.id, "invalid order ID")
                return False
            verification = await verify_binance_pay_order(deposit.tx_hash)
        else:
            if not isinstance(deposit.tx_hash, str) or not valid_hash(deposit.tx_hash):
                _fail_deposit(db, deposit.id, "invalid tx hash")
                return False
            if network not in CHAINS:
                _fail_deposit(db, deposit.id, f"unknown network {network}")
                return False
            chain = CHAINS[network]
            if binance_matches is not None:
                verification = _match_binance_row(chain, deposit.tx_hash,
                                                  binance_matches, deposit_created_at,
                                                  requested_amount)
            else:
                verification = await verify_transaction(chain, deposit.tx_hash,
                                                        deposit_created_at, requested_amount)

        if verification is None:
            attempts = _record_pending_attempt(deposit.id)
            if attempts >= MAX_CHECK_ATTEMPTS:
                # After max attempts, check if deposit is > 60 seconds old
                # If so, force-accept by rechecking without confirmation requirement
                if deposit_created_at:
                    age = (datetime.now() - deposit_created_at).total_seconds()
                    if age > MAX_WAIT_SECONDS and network in CHAINS:
                        logger.warning("Deposit %s expired after %s attempts but is %ss old — "
                                     "forcing final check without confirmation requirement",
                                     deposit.id, attempts, age)
                        # Temporarily disable confirmation check for this final attempt
                        global ENABLE_CONFIRMATION_CHECK
                        old_setting = ENABLE_CONFIRMATION_CHECK
                        ENABLE_CONFIRMATION_CHECK = False
                        try:
                            if binance_matches is not None:
                                verification = _match_binance_row(
                                    CHAINS[network], deposit.tx_hash, binance_matches,
                                    deposit_created_at, requested_amount)
                            else:
                                verification = await verify_transaction(
                                    CHAINS[network], deposit.tx_hash,
                                    deposit_created_at, requested_amount)
                        finally:
                            ENABLE_CONFIRMATION_CHECK = old_setting

                        if verification and isinstance(verification, dict):
                            _clear_pending_attempts(deposit.id)
                            received_amount = verification["amount"]
                            return credit_user(db, deposit, received_amount,
                                             requested_amount if requested_amount is not None else received_amount,
                                             upi_inr_amount, upi_rate)

                if result_info is not None: result_info["reason"] = "expired"
                _finalize_failed(db, deposit, f"expired after {attempts} attempts")
                return False
            return None

        _clear_pending_attempts(deposit.id)
        if verification is False:
            if result_info is not None: result_info["reason"] = "verification_failed"
            _finalize_failed(db, deposit, "verification returned false")
            return False

        dup = db.query(Deposit).filter(
            Deposit.tx_hash == deposit.tx_hash,
            Deposit.status == "completed",
            Deposit.id != deposit.id
        ).first()
        if dup:
            if result_info is not None: result_info["reason"] = "duplicate"
            _finalize_failed(db, deposit, "duplicate")
            return False

        received_amount = verification["amount"]
        if requested_amount is not None:
            if network == UPI_NETWORK:
                requested_usdt = convert_inr_to_usdt(requested_amount)
                if received_amount < requested_usdt - AMOUNT_TOLERANCE:
                    _finalize_failed(db, deposit, "underpaid")
                    return False
                requested_amount = requested_usdt
            elif received_amount < requested_amount - AMOUNT_TOLERANCE:
                _finalize_failed(db, deposit, "underpaid")
                return False

        return credit_user(db, deposit, received_amount,
                          requested_amount if requested_amount is not None else received_amount,
                          upi_inr_amount, upi_rate)
    except Exception:
        db.rollback()
        logger.exception("Error verifying deposit")
        return False
    finally:
        db.close()


def _fail_deposit(db, deposit_id: int, reason: str) -> None:
    try:
        dep = db.get(Deposit, deposit_id)
        if dep and dep.status not in ("completed", "failed"):
            _finalize_failed(db, dep, reason)
    except Exception:
        db.rollback()
    finally:
        _clear_pending_attempts(deposit_id)


# ╔══════════════════════════════════════════════════════════════╗
# ║  CHECK PENDING DEPOSITS                                    ║
# ╚══════════════════════════════════════════════════════════════╝

async def check_pending_deposits():
    db = SessionLocal()
    try:
        rows = db.query(Deposit.id, Deposit.network).filter(Deposit.status == "pending").all()
    finally:
        db.close()

    crypto_by_net, upi_ids, pay_ids = {}, [], []
    for dep_id, network in rows:
        net = (network or "").upper()
        if net == UPI_NETWORK: upi_ids.append(dep_id)
        elif net == BINANCE_PAY_NETWORK: pay_ids.append(dep_id)
        elif net in CHAINS: crypto_by_net.setdefault(net, []).append(dep_id)

    logger.info("Checking %s pending (%s crypto, %s Pay, %s UPI)",
                len(rows), sum(len(v) for v in crypto_by_net.values()),
                len(pay_ids), len(upi_ids))

    for net, dep_ids in crypto_by_net.items():
        try:
            matches = await _fetch_binance_matches(CHAINS[net])
        except:
            matches = {}
        for did in dep_ids:
            try:
                await verify_deposit(did, binance_matches=matches)
            except:
                logger.exception("Failed deposit %s", did)

    for did in pay_ids:
        try:
            await verify_deposit(did)
        except:
            logger.exception("Failed Pay %s", did)

    if upi_ids:
        try:
            um = await asyncio.to_thread(_fetch_famapp_matches)
        except:
            um = {}
        for did in upi_ids:
            try:
                await verify_deposit(did, upi_matches=um)
            except:
                logger.exception("Failed UPI %s", did)


# ╔══════════════════════════════════════════════════════════════╗
# ║  BACKGROUND LOOP                                           ║
# ╚══════════════════════════════════════════════════════════════╝

async def deposit_checker_loop():
    logger.info("=" * 55)
    logger.info("DEPOSIT CHECKER — PROGRESSIVE CONFIRMATIONS")
    logger.info("Coins: %s", ", ".join(DEPOSIT_COINS))
    logger.info("Networks: %s", ", ".join(CHAINS.keys()))
    logger.info("Max wait: %ss | Check: every %ss | Max attempts: %s",
                MAX_WAIT_SECONDS, CHECK_INTERVAL, MAX_CHECK_ATTEMPTS)
    logger.info("Tiers: BEP20=%s | POLYGON=%s",
                NETWORK_CONFIRMATION_TIERS.get("BEP20"),
                NETWORK_CONFIRMATION_TIERS.get("POLYGON"))
    logger.info("=" * 55)

    while True:
        try:
            await check_pending_deposits()
        except Exception:
            logger.exception("Checker crashed")
        await asyncio.sleep(CHECK_INTERVAL)


def start_checker():
    return asyncio.create_task(deposit_checker_loop())