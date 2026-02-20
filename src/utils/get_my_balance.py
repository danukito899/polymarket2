"""
Get USDC balance helpers for trading wallet diagnostics.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))); import src.lib_core
from typing import Dict
from web3 import Web3
from ..config.env import ENV


USDC_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    }
]

POLYGON_USDC_E = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
POLYGON_NATIVE_USDC = '0x3c499c542cef5e3811e1192ce70d8cc03d5c3359'


def _read_erc20_balance(w3: Web3, wallet: str, token: str) -> float:
    """Read ERC20 balance and return human-readable value using 6 decimals."""
    checksum_wallet = Web3.to_checksum_address(wallet)
    checksum_token = Web3.to_checksum_address(token)
    token_contract = w3.eth.contract(address=checksum_token, abi=USDC_ABI)
    return float(token_contract.functions.balanceOf(checksum_wallet).call() / 10**6)


async def get_usdc_balance_snapshot_async(address: str) -> Dict[str, float]:
    """Get balances for configured USDC, USDC.e, and native USDC contracts."""
    w3 = Web3(Web3.HTTPProvider(ENV.RPC_URL))
    configured_token = ENV.USDC_CONTRACT_ADDRESS

    return {
        'configured': _read_erc20_balance(w3, address, configured_token),
        'usdc_e': _read_erc20_balance(w3, address, POLYGON_USDC_E),
        'native_usdc': _read_erc20_balance(w3, address, POLYGON_NATIVE_USDC),
    }


async def get_my_balance_async(address: str) -> float:
    """Get balance for configured USDC contract (used by runtime trading checks)."""
    snapshot = await get_usdc_balance_snapshot_async(address)
    return snapshot['configured']


def get_my_balance(address: str) -> float:
    """Get balance for configured USDC contract (sync wrapper)."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(get_my_balance_async(address))
        return loop.run_until_complete(get_my_balance_async(address))
    except RuntimeError:
        return asyncio.run(get_my_balance_async(address))


def get_usdc_balance_snapshot(address: str) -> Dict[str, float]:
    """Sync wrapper for USDC snapshot helper."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(get_usdc_balance_snapshot_async(address))
        return loop.run_until_complete(get_usdc_balance_snapshot_async(address))
    except RuntimeError:
        return asyncio.run(get_usdc_balance_snapshot_async(address))
