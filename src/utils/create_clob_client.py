"""
Create Polymarket CLOB client
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))); import src.lib_core
import inspect
import math
from typing import Optional, Dict, Any, Callable
from web3 import Web3
from eth_account import Account
from ..config.env import ENV
from ..utils.logger import info, error
import httpx


def _to_bool(value: Any, default: bool = True) -> bool:
    """Convert common environment-style truthy/falsey values to bool."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


TRACE_LOGS = _to_bool(os.getenv('CLOB_TRACE_LOGS', 'true'), default=True)


MAX_ORDER_PRICE_DECIMALS = 2
MAX_ORDER_SIZE_DECIMALS = 1


def _truncate_to_decimals(value: Any, decimals: int) -> float:
    """Truncate a numeric value to a maximum decimal precision."""
    factor = 10 ** decimals
    return math.floor(float(value) * factor) / factor


def trace(message: str) -> None:
    """Emit verbose CLOB traces to console/log file when enabled."""
    if TRACE_LOGS:
        info(f'[CLOB TRACE] {message}')


async def _call_maybe_async(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call function/method that may be sync or async."""
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _to_plain_dict(value: Any) -> Dict[str, Any]:
    """Best-effort conversion from SDK response models to plain dictionaries."""
    if isinstance(value, dict):
        return value

    if hasattr(value, 'model_dump') and callable(getattr(value, 'model_dump')):
        dumped = value.model_dump()
        if isinstance(dumped, dict):
            return dumped

    if hasattr(value, 'dict') and callable(getattr(value, 'dict')):
        dumped = value.dict()
        if isinstance(dumped, dict):
            return dumped

    attrs = getattr(value, '__dict__', None)
    if isinstance(attrs, dict):
        return {k: v for k, v in attrs.items() if not k.startswith('_')}

    return {}


def _normalize_level(level: Any) -> Optional[Dict[str, Any]]:
    """Normalize an order book level to {'price': float, 'size': float}."""
    payload = _to_plain_dict(level)
    if not payload and not isinstance(level, dict):
        payload = {
            'price': getattr(level, 'price', None),
            'size': getattr(level, 'size', None),
        }

    price = payload.get('price')
    size = payload.get('size')
    if price is None or size is None:
        return None

    try:
        return {'price': float(price), 'size': float(size)}
    except (TypeError, ValueError):
        return None


def _normalize_order_book(order_book: Any) -> Dict[str, Any]:
    """Normalize SDK/HTTP order book response into dict with bids/asks arrays."""
    payload = _to_plain_dict(order_book)
    if not payload:
        payload = {
            'bids': getattr(order_book, 'bids', []),
            'asks': getattr(order_book, 'asks', []),
            'token_id': getattr(order_book, 'token_id', None),
        }

    bids = payload.get('bids') or []
    asks = payload.get('asks') or []

    normalized_bids = [level for item in bids if (level := _normalize_level(item))]
    normalized_asks = [level for item in asks if (level := _normalize_level(item))]

    return {
        'token_id': payload.get('token_id'),
        'bids': normalized_bids,
        'asks': normalized_asks,
    }


async def is_gnosis_safe(address: str) -> bool:
    """Determines if a wallet is a Gnosis Safe by checking if it has contract code"""
    try:
        w3 = Web3(Web3.HTTPProvider(ENV.RPC_URL))
        # Convert address to checksum format for web3.py
        checksum_address = Web3.to_checksum_address(address)
        code = w3.eth.get_code(checksum_address)
        # If code is not "0x", then it's a contract (likely Gnosis Safe)
        return code != b'0x'
    except Exception as e:
        error(f'Error checking wallet type: {e}')
        return False


class ClobClient:
    """Polymarket CLOB client wrapper with SDK + HTTP fallback support."""
    
    def __init__(
        self,
        host: str,
        chain_id: int,
        wallet: Any,
        api_creds: Optional[Dict[str, Any]] = None,
        signature_type: str = 'EOA',
        proxy_wallet: Optional[str] = None
    ):
        self.host = host.rstrip('/')
        self.chain_id = chain_id
        self.wallet = wallet
        self.api_creds = api_creds or {}
        self.signature_type = signature_type
        self.proxy_wallet = proxy_wallet
        self.api_key = api_creds.get('key') if api_creds else None
        self.api_secret = api_creds.get('secret') if api_creds else None
        self.api_passphrase = api_creds.get('passphrase') if api_creds else None

        self._sdk_error: Optional[str] = None
        self._sdk_client: Optional[Any] = None

        try:
            from py_clob_client.client import ClobClient as PyClobClient  # type: ignore

            sig_type_obj: Any = signature_type
            try:
                from py_clob_client.constants import POLY_GNOSIS_SAFE, EOA  # type: ignore
                if signature_type == 'POLY_GNOSIS_SAFE':
                    sig_type_obj = POLY_GNOSIS_SAFE
                elif signature_type == 'EOA':
                    sig_type_obj = EOA
            except Exception:
                pass

            kwargs: Dict[str, Any] = {
                'host': self.host,
                'chain_id': self.chain_id,
                'key': self.wallet.key.hex() if hasattr(self.wallet, 'key') else self.wallet,
                'signature_type': sig_type_obj,
            }
            if self.proxy_wallet:
                kwargs['funder'] = self.proxy_wallet

            self._sdk_client = PyClobClient(**kwargs)

            if self.api_creds and hasattr(self._sdk_client, 'set_api_creds'):
                self._sdk_client.set_api_creds(self.api_creds)

            trace(
                f'SDK initialized (host={self.host}, chain_id={self.chain_id}, '
                f'signature_type={self.signature_type}, proxy_wallet={self.proxy_wallet})'
            )
        except Exception as sdk_error:
            self._sdk_error = str(sdk_error)
            trace(f'SDK unavailable, using HTTP fallback where possible: {self._sdk_error}')
    
    async def create_api_key(self) -> Dict[str, Any]:
        """Create API key using SDK when available."""
        trace('create_api_key() called')
        if not self._sdk_client:
            trace('create_api_key() skipped - SDK not available')
            return {}

        try:
            creds = await _call_maybe_async(self._sdk_client.create_api_key)
            trace(f'create_api_key() response keys: {list(creds.keys()) if isinstance(creds, dict) else type(creds)}')
            return creds if isinstance(creds, dict) else {}
        except Exception as e:
            error(f'create_api_key failed: {e}')
            return {}

    async def set_api_creds(self, api_creds: Dict[str, Any]) -> None:
        """Attach API credentials to SDK client if supported."""
        self.api_creds = api_creds or {}
        self.api_key = self.api_creds.get('key') if self.api_creds else None
        self.api_secret = self.api_creds.get('secret') if self.api_creds else None
        self.api_passphrase = self.api_creds.get('passphrase') if self.api_creds else None

        if self._sdk_client and hasattr(self._sdk_client, 'set_api_creds'):
            trace('set_api_creds() applying credentials to SDK client')
            self._sdk_client.set_api_creds(self.api_creds)
    
    async def derive_api_key(self) -> Dict[str, Any]:
        """Derive API key using SDK when available."""
        trace('derive_api_key() called')
        if not self._sdk_client:
            trace('derive_api_key() skipped - SDK not available')
            return {}

        try:
            creds = await _call_maybe_async(self._sdk_client.derive_api_key)
            trace(f'derive_api_key() response keys: {list(creds.keys()) if isinstance(creds, dict) else type(creds)}')
            return creds if isinstance(creds, dict) else {}
        except Exception as e:
            error(f'derive_api_key failed: {e}')
            return {}
    
    async def get_order_book(self, token_id: str) -> Dict[str, Any]:
        """Get order book for a token"""
        trace(f'get_order_book(token_id={token_id}) called')

        if self._sdk_client and hasattr(self._sdk_client, 'get_order_book'):
            try:
                order_book = await _call_maybe_async(self._sdk_client.get_order_book, token_id)
                normalized = _normalize_order_book(order_book)
                trace(
                    f'get_order_book() SDK response: bids={len(normalized.get("bids", []) or [])}, '
                    f'asks={len(normalized.get("asks", []) or [])}'
                )
                return normalized
            except Exception as e:
                error(f'SDK get_order_book failed for token {token_id}: {e}')

        url = f'{self.host}/book?token_id={token_id}'
        trace(f'HTTP GET {url}')
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
            normalized_payload = _normalize_order_book(payload)
            trace(
                f'HTTP order book response: status={response.status_code}, '
                f'bids={len(normalized_payload.get("bids", []) or [])}, asks={len(normalized_payload.get("asks", []) or [])}'
            )
            return normalized_payload
    
    async def create_market_order(self, order_args: Dict[str, Any]) -> Dict[str, Any]:
        """Create/sign an order using SDK."""
        side = str(order_args.get('side', 'BUY')).upper()
        token_id = str(order_args.get('tokenID') or order_args.get('token_id') or '')
        amount = _truncate_to_decimals(order_args.get('amount', 0), MAX_ORDER_SIZE_DECIMALS)
        price = _truncate_to_decimals(order_args.get('price', 0), MAX_ORDER_PRICE_DECIMALS)

        trace(
            f'create_market_order(side={side}, token_id={token_id}, amount={amount}, price={price}) called'
        )

        if not self._sdk_client:
            raise RuntimeError(
                'Cannot create market order: py_clob_client is not available. '
                'Install it and configure credentials.'
            )

        # Try SDK create_market_order path first.
        create_market_order_errors: Optional[str] = None

        if hasattr(self._sdk_client, 'create_market_order'):
            dict_error: Optional[str] = None

            # Some SDK versions require typed args and reject dict payloads.
            try:
                signed = await _call_maybe_async(self._sdk_client.create_market_order, order_args)
                trace('create_market_order() signed order generated via SDK method create_market_order(dict args)')
                signed_dict = _to_plain_dict(signed)
                return signed if isinstance(signed, dict) else signed_dict
            except Exception as e:
                dict_error = str(e)
                trace(f'create_market_order(dict args) failed: {dict_error}. Retrying with MarketOrderArgs...')

            try:
                from py_clob_client.clob_types import MarketOrderArgs  # type: ignore

                sdk_market_args = MarketOrderArgs(
                    token_id=token_id,
                    amount=amount,
                    side=side,
                    price=price,
                )
                signed = await _call_maybe_async(self._sdk_client.create_market_order, sdk_market_args)
                trace('create_market_order() signed order generated via SDK method create_market_order(MarketOrderArgs)')
                signed_dict = _to_plain_dict(signed)
                return signed if isinstance(signed, dict) else signed_dict
            except Exception as typed_error:
                raise RuntimeError(
                    f'Failed to create market order with SDK create_market_order: '
                    f'dict_error={dict_error!r}; typed_error={typed_error}'
                ) from typed_error

        # Fallback to create_order using typed order args from py_clob_client.
        try:
            from py_clob_client.clob_types import OrderArgs  # type: ignore

            size = amount / price if price > 0 else 0.0
            typed_args = OrderArgs(price=price, size=size, side=side, token_id=token_id)
            signed = await _call_maybe_async(self._sdk_client.create_order, typed_args)
            trace('create_market_order() signed order generated via SDK method create_order')
            signed_dict = _to_plain_dict(signed)
            return signed if isinstance(signed, dict) else signed_dict
        except Exception as e:
            if create_market_order_errors:
                raise RuntimeError(
                    'Failed to create market order with SDK: '
                    f'create_market_order_errors={create_market_order_errors}; '
                    f'create_order_error={e}'
                ) from e
            raise RuntimeError(f'Failed to create market order with SDK: {e}')
    
    async def post_order(self, signed_order: Dict[str, Any], order_type: str) -> Dict[str, Any]:
        """Submit signed order through SDK."""
        trace(f'post_order(order_type={order_type}) called')

        if not self._sdk_client:
            raise RuntimeError(
                'Cannot post order: py_clob_client is not available. '
                'Install it and configure credentials.'
            )

        final_order_type: Any = order_type
        try:
            from py_clob_client.clob_types import OrderType  # type: ignore
            if hasattr(OrderType, str(order_type).upper()):
                final_order_type = getattr(OrderType, str(order_type).upper())
        except Exception:
            pass

        response = await _call_maybe_async(self._sdk_client.post_order, signed_order, final_order_type)
        response_dict = response if isinstance(response, dict) else _to_plain_dict(response)
        trace(f'post_order() response keys: {list(response_dict.keys())}')
        return response_dict


async def create_clob_client() -> ClobClient:
    """Create and initialize CLOB client"""
    chain_id = 137  # Polygon
    host = ENV.CLOB_HTTP_URL
    
    # Create wallet from private key
    account = Account.from_key(ENV.PRIVATE_KEY)
    
    # Signature type: 0 = EOA MetaMask/funder mode, non-zero = legacy proxy/safe mode.
    use_eoa_mode = ENV.CLOB_SIGNATURE_TYPE == 0
    is_proxy_safe = False
    if not use_eoa_mode and ENV.PROXY_WALLET:
        is_proxy_safe = await is_gnosis_safe(ENV.PROXY_WALLET)

    signature_type = 'EOA' if use_eoa_mode or not is_proxy_safe else 'POLY_GNOSIS_SAFE'
    active_proxy_wallet = ENV.PROXY_WALLET if signature_type == 'POLY_GNOSIS_SAFE' else None

    info(
        f'Wallet type detected: {"Gnosis Safe" if signature_type == "POLY_GNOSIS_SAFE" else "EOA (Externally Owned Account)"}'
    )
    trace(
        f'Using CLOB host={host}, chain_id={chain_id}, trading_wallet={ENV.TRADING_WALLET_ADDRESS}, '
        f'proxy_wallet={active_proxy_wallet}, signature_type={ENV.CLOB_SIGNATURE_TYPE}'
    )

    # Create initial client
    clob_client = ClobClient(
        host=host,
        chain_id=chain_id,
        wallet=account,
        signature_type=signature_type,
        proxy_wallet=active_proxy_wallet
    )
    
    # Try to create or derive API key
    try:
        creds = await clob_client.create_api_key()
        if not creds.get('key'):
            creds = await clob_client.derive_api_key()
    except Exception as e:
        error(f'Failed to create/derive API key: {e}')
        creds = {}
    
    # Create client with credentials
    clob_client = ClobClient(
        host=host,
        chain_id=chain_id,
        wallet=account,
        api_creds=creds,
        signature_type=signature_type,
        proxy_wallet=active_proxy_wallet
    )

    if creds:
        await clob_client.set_api_creds(creds)
        trace(f'API credentials loaded (key={"present" if creds.get("key") else "missing"})')
    else:
        trace('No API credentials were generated/derived')

    return clob_client
