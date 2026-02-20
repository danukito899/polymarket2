"""
Winnings recovery service - periodically redeems resolved winning positions.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))); import src.lib_core
import asyncio
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from eth_account import Account
from web3 import Web3
from web3.middleware import geth_poa_middleware

from ..config.env import ENV
from ..utils.fetch_data import fetch_data_async
from ..utils.logger import info, success, warning, error

ZERO_BYTES32 = '0x' + ('0' * 64)
DEFAULT_CTF_CONTRACT_ADDRESS = '0x4D97DCd97eC945f40cF65F87097ACe5EA0476045'
DEFAULT_CHAIN_ID = 137
DEFAULT_INTERVAL_SECONDS = 60

CTF_EXCHANGE_ABI = [
    {
        'inputs': [
            {'internalType': 'address', 'name': 'collateralToken', 'type': 'address'},
            {'internalType': 'bytes32', 'name': 'parentCollectionId', 'type': 'bytes32'},
            {'internalType': 'bytes32', 'name': 'conditionId', 'type': 'bytes32'},
            {'internalType': 'uint256[]', 'name': 'indexSets', 'type': 'uint256[]'},
        ],
        'name': 'redeemPositions',
        'outputs': [],
        'stateMutability': 'nonpayable',
        'type': 'function',
    }
]

is_running = True


def _parse_condition_id(value: str) -> bytes:
    raw = value[2:] if value.startswith('0x') else value
    if len(raw) != 64:
        raise ValueError(f'Unexpected conditionId length: {len(raw)}')
    return bytes.fromhex(raw)


def _get_index_set(position: Dict[str, Any]) -> int:
    outcome_index = int(position.get('outcomeIndex', 0) or 0)
    return 1 << outcome_index


def _has_min_native_balance(web3: Web3, tx_wallet: str) -> bool:
    balance_wei = web3.eth.get_balance(tx_wallet)
    gas_price_wei = web3.eth.gas_price
    min_required_wei = gas_price_wei * 21000

    if balance_wei < min_required_wei:
        warning(
            'Winnings recovery skipped: insufficient native gas balance for signer wallet '
            f'{tx_wallet} (balance={balance_wei} wei, required~{min_required_wei} wei)'
        )
        return False

    return True


def _redeem_position(
    web3: Web3,
    contract: Any,
    tx_wallet: str,
    private_key: str,
    chain_id: int,
    condition_id: str,
    index_set: int,
) -> str:
    nonce = web3.eth.get_transaction_count(tx_wallet)

    tx = contract.functions.redeemPositions(
        Web3.to_checksum_address(ENV.USDC_CONTRACT_ADDRESS),
        _parse_condition_id(ZERO_BYTES32),
        _parse_condition_id(condition_id),
        [index_set],
    ).build_transaction({
        'from': tx_wallet,
        'chainId': chain_id,
        'nonce': nonce,
    })

    try:
        estimated_gas = web3.eth.estimate_gas(tx)
        tx['gas'] = int(estimated_gas * 1.2)
    except Exception:
        tx['gas'] = 500000

    if 'maxFeePerGas' not in tx and 'gasPrice' not in tx:
        tx['gasPrice'] = web3.eth.gas_price

    signed_tx = web3.eth.account.sign_transaction(tx, private_key)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
    tx_hash_hex = web3.to_hex(tx_hash)

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f'Redeem transaction failed: {tx_hash_hex}')

    return tx_hash_hex



async def _recover_winnings_once(
    web3: Web3,
    contract: Any,
    positions_wallet: str,
    tx_wallet: str,
    private_key: str,
    chain_id: int,
    attempted_in_runtime: Set[Tuple[str, int]],
) -> None:
    positions_url = f'https://data-api.polymarket.com/positions?user={positions_wallet}'
    positions_data = await fetch_data_async(positions_url)

    if not isinstance(positions_data, list):
        warning('Winnings recovery skipped: positions response was not a list')
        return

    redeemable_positions = [
        p for p in positions_data
        if p.get('redeemable')
        and p.get('conditionId')
        and (p.get('size', 0) or 0) > 0
    ]

    if not redeemable_positions:
        return

    info(f'Winnings recovery: found {len(redeemable_positions)} redeemable position(s)')

    for position in redeemable_positions:
        condition_id = str(position.get('conditionId'))
        index_set = _get_index_set(position)
        key = (condition_id, index_set)

        if key in attempted_in_runtime:
            continue

        try:
            tx_hash = await asyncio.to_thread(
                _redeem_position,
                web3,
                contract,
                tx_wallet,
                private_key,
                chain_id,
                condition_id,
                index_set,
            )
            success(
                'Recovered winnings for '
                f"{position.get('title', position.get('slug', 'unknown market'))} "
                f'(tx: {tx_hash})'
            )
            attempted_in_runtime.add(key)
        except Exception as exc:
            warning(
                'Winnings recovery failed for '
                f"{position.get('title', position.get('slug', 'unknown market'))}: {exc}"
            )


async def winnings_recovery_loop() -> None:
    """Continuously try to redeem resolved winnings every configured interval."""
    global is_running

    if not ENV.PRIVATE_KEY:
        warning('Winnings recovery disabled: PRIVATE_KEY not set')
        return

    interval_seconds = int(os.getenv('WINNINGS_RECOVERY_INTERVAL_SECONDS', str(DEFAULT_INTERVAL_SECONDS)))
    ctf_contract_address = os.getenv('CTF_EXCHANGE_CONTRACT_ADDRESS', DEFAULT_CTF_CONTRACT_ADDRESS)
    chain_id = int(os.getenv('CHAIN_ID', str(DEFAULT_CHAIN_ID)))

    web3 = Web3(Web3.HTTPProvider(ENV.RPC_URL))
    web3.middleware_onion.inject(geth_poa_middleware, layer=0)
    if not web3.is_connected():
        warning('Winnings recovery disabled: unable to connect to RPC_URL')
        return

    tx_wallet = Account.from_key(ENV.PRIVATE_KEY).address
    positions_wallet = os.getenv('WINNINGS_RECOVERY_WALLET', ENV.PROXY_WALLET or tx_wallet)

    tx_wallet = Web3.to_checksum_address(tx_wallet)
    positions_wallet = Web3.to_checksum_address(positions_wallet)

    if positions_wallet != tx_wallet:
        warning(
            'Winnings recovery wallet mismatch detected: '
            f'positions wallet {positions_wallet} differs from signer wallet {tx_wallet}. '
            'Transactions will be sent from signer wallet.'
        )

    contract = web3.eth.contract(
        address=Web3.to_checksum_address(ctf_contract_address),
        abi=CTF_EXCHANGE_ABI,
    )

    attempted_in_runtime: Set[Tuple[str, int]] = set()

    info(
        f'Winnings recovery started (every {interval_seconds}s) '
        f'for positions wallet {positions_wallet}'
    )

    while is_running:
        cycle_start = asyncio.get_running_loop().time()

        try:
            await _recover_winnings_once(
                web3,
                contract,
                positions_wallet,
                tx_wallet,
                ENV.PRIVATE_KEY,
                chain_id,
                attempted_in_runtime,
            )
        except Exception as exc:
            error(f'Winnings recovery loop error: {exc}')

        elapsed = asyncio.get_running_loop().time() - cycle_start
        await asyncio.sleep(max(0, interval_seconds - elapsed))

    info('Winnings recovery stopped')


def stop_winnings_recovery() -> None:
    """Stop winnings recovery service."""
    global is_running
    is_running = False
