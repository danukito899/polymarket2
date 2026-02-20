"""
Post order to Polymarket
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))); import src.lib_core
from typing import Optional, Dict, Any, List, Tuple
import math
from ..config.env import ENV
from ..models.user_history import get_user_activity_collection
from ..utils.logger import info, warning, order_result
from ..config.copy_strategy import calculate_order_size, get_trade_multiplier

RETRY_LIMIT = ENV.RETRY_LIMIT
COPY_STRATEGY_CONFIG = ENV.COPY_STRATEGY_CONFIG

# Polymarket minimum order sizes
MIN_ORDER_SIZE_USD = 1.0  # Minimum order size in USD for BUY orders
MIN_ORDER_SIZE_TOKENS = 1.0  # Minimum order size in tokens for SELL/MERGE orders
MAX_MARKET_FALLBACK_DIFF = ENV.MARKET_FALLBACK_MAX_DIFF
MAX_ORDER_PRICE_DECIMALS = 2
MAX_ORDER_SIZE_DECIMALS = 1




def _round_to_decimals(value: float, decimals: int) -> float:
    """Truncate numeric value to a maximum number of decimal places."""
    factor = 10 ** decimals
    return math.floor(float(value) * factor) / factor


def normalize_order_args(order_args: Dict[str, Any]) -> Dict[str, Any]:
    """Clamp order price/amount precision for exchange compatibility."""
    normalized = dict(order_args)
    normalized['price'] = _round_to_decimals(order_args.get('price', 0), MAX_ORDER_PRICE_DECIMALS)
    normalized['amount'] = _round_to_decimals(order_args.get('amount', 0), MAX_ORDER_SIZE_DECIMALS)
    return normalized

def extract_order_error(response: Any) -> Optional[str]:
    """Extract error message from order response"""
    if not response:
        return None
    
    if isinstance(response, str):
        return response
    
    if isinstance(response, dict):
        # Check direct error
        if 'error' in response:
            error_val = response['error']
            if isinstance(error_val, str):
                return error_val
            if isinstance(error_val, dict):
                if 'error' in error_val:
                    return error_val['error']
                if 'message' in error_val:
                    return error_val['message']
        
        # Check other error fields
        if 'errorMsg' in response:
            return response['errorMsg']
        if 'message' in response:
            return response['message']
    
    return None


def is_insufficient_balance_or_allowance_error(message: Optional[str]) -> bool:
    """Check if error is related to insufficient balance or allowance"""
    if not message:
        return False
    lower = message.lower()
    return 'not enough balance' in lower or 'allowance' in lower


def is_not_found_error(err: Exception) -> bool:
    """Check if exception indicates HTTP 404 from order book endpoint."""
    return '404' in str(err) and 'book?token_id=' in str(err)


def is_price_within_tolerance(reference_price: float, market_price: float, side: str) -> bool:
    """Only fallback to market if slippage is within configured tolerance."""
    if reference_price <= 0 or market_price <= 0:
        return False

    normalized_side = side.upper()
    if normalized_side == 'BUY':
        # For buys, higher price is worse. Better prices are always acceptable.
        return market_price <= reference_price * (1 + MAX_MARKET_FALLBACK_DIFF)

    # For sells, lower price is worse. Better prices are always acceptable.
    return market_price >= reference_price * (1 - MAX_MARKET_FALLBACK_DIFF)


async def submit_with_fok_then_market(
    clob_client: Any,
    execution_asset: str,
    order_args: Dict[str, Any],
    side: str,
) -> Dict[str, Any]:
    """Submit as FOK first, then fallback to market-style GTC if within 2%."""
    normalized_order_args = normalize_order_args(order_args)
    info(f'[ORDER TRACE] Creating signed order payload: {normalized_order_args}')
    signed_order = await clob_client.create_market_order(normalized_order_args)
    info('[ORDER TRACE] Submitting order to CLOB with type=FOK')
    resp = await clob_client.post_order(signed_order, 'FOK')
    info(f'[ORDER TRACE] CLOB response: {resp}')

    if resp.get('success') is True:
        return resp

    error_message = extract_order_error(resp)
    warning(f'FOK order failed{f": {error_message}" if error_message else ""}. Checking market fallback...')

    order_book = await clob_client.get_order_book(execution_asset)
    book_side = 'asks' if side.upper() == 'BUY' else 'bids'
    levels = order_book.get(book_side) or []
    if not levels:
        warning('No liquidity available for market fallback')
        return resp

    best_level = min(levels, key=lambda x: float(x['price'])) if side.upper() == 'BUY' else max(levels, key=lambda x: float(x['price']))
    market_price = float(best_level['price'])
    reference_price = float(normalized_order_args.get('price', 0))

    if not is_price_within_tolerance(reference_price, market_price, side):
        warning(
            f'Market fallback skipped: price deviation exceeds {MAX_MARKET_FALLBACK_DIFF * 100:.0f}% '
            f'(target={reference_price:.4f}, market={market_price:.4f})'
        )
        return resp

    fallback_order_args = normalize_order_args({**normalized_order_args, 'price': market_price})
    info(f'[ORDER TRACE] Market fallback order payload: {fallback_order_args}')
    fallback_signed_order = await clob_client.create_market_order(fallback_order_args)
    info('[ORDER TRACE] Submitting fallback order to CLOB with type=GTC')
    fallback_resp = await clob_client.post_order(fallback_signed_order, 'GTC')
    info(f'[ORDER TRACE] Fallback CLOB response: {fallback_resp}')
    return fallback_resp


async def post_order(
    clob_client: Any,
    condition: str,
    my_position: Optional[Dict[str, Any]],
    user_position: Optional[Dict[str, Any]],
    trade: Dict[str, Any],
    my_balance: float,
    user_balance: float,
    user_address: str
):
    """Post order to Polymarket"""
    collection = get_user_activity_collection(user_address)

    info(
        '[ORDER TRACE] '
        f'condition={condition}, user={user_address[:6]}...{user_address[-4:]}, '
        f'trade_side={trade.get("side")}, trade_asset={trade.get("asset")}, '
        f'trade_condition_id={trade.get("conditionId")}, usdc_size={trade.get("usdcSize")}, '
        f'price={trade.get("price")}, my_balance={my_balance:.4f}, user_balance={user_balance:.4f}'
    )

    def resolve_execution_asset() -> Optional[str]:
        """Resolve the most reliable token id for book/order operations."""
        trade_asset = trade.get('asset')
        if isinstance(trade_asset, str) and trade_asset.strip():
            return trade_asset

        if my_position and my_position.get('asset'):
            return my_position.get('asset')

        if user_position and user_position.get('asset'):
            return user_position.get('asset')

        return None
    
    if condition == 'merge':
        info('Executing MERGE strategy...')
        if not my_position:
            warning('No position to merge')
            collection.update_one({'_id': trade['_id']}, {'$set': {'bot': True}})
            return
        
        remaining = my_position.get('size', 0)
        execution_asset = resolve_execution_asset()

        if not execution_asset:
            warning('Missing token id for merge order - skipping')
            collection.update_one({'_id': trade['_id']}, {'$set': {'bot': True}})
            return
        
        # Check minimum order size
        if remaining < MIN_ORDER_SIZE_TOKENS:
            warning(f'Position size ({remaining:.2f} tokens) too small to merge - skipping')
            collection.update_one({'_id': trade['_id']}, {'$set': {'bot': True}})
            return
        
        retry = 0
        abort_due_to_funds = False
        
        while remaining > 0 and retry < RETRY_LIMIT:
            try:
                order_book = await clob_client.get_order_book(execution_asset)
                if not order_book.get('bids') or len(order_book['bids']) == 0:
                    warning('No bids available in order book')
                    collection.update_one({'_id': trade['_id']}, {'$set': {'bot': True}})
                    break
                
                max_price_bid = max(order_book['bids'], key=lambda x: float(x['price']))
                
                info(f'Best bid: {max_price_bid["size"]} @ ${max_price_bid["price"]}')
                
                if remaining <= float(max_price_bid['size']):
                    order_args = {
                        'side': 'SELL',
                        'tokenID': execution_asset,
                        'amount': _round_to_decimals(remaining, MAX_ORDER_SIZE_DECIMALS),
                        'price': _round_to_decimals(float(max_price_bid['price']), MAX_ORDER_PRICE_DECIMALS),
                    }
                else:
                    order_args = {
                        'side': 'SELL',
                        'tokenID': execution_asset,
                        'amount': _round_to_decimals(float(max_price_bid['size']), MAX_ORDER_SIZE_DECIMALS),
                        'price': _round_to_decimals(float(max_price_bid['price']), MAX_ORDER_PRICE_DECIMALS),
                    }

                if order_args['amount'] <= 0:
                    warning('Order amount rounded to 0.0 after precision clamp - skipping')
                    collection.update_one({'_id': trade['_id']}, {'$set': {'bot': True}})
                    break
                
                resp = await submit_with_fok_then_market(
                    clob_client=clob_client,
                    execution_asset=execution_asset,
                    order_args=order_args,
                    side='SELL',
                )
                
                if resp.get('success') is True:
                    retry = 0
                    order_result(True, f'Sold {order_args["amount"]} tokens at ${order_args["price"]}')
                    remaining -= order_args['amount']
                else:
                    error_message = extract_order_error(resp)
                    if is_insufficient_balance_or_allowance_error(error_message):
                        abort_due_to_funds = True
                        warning(f'Order rejected: {error_message or "Insufficient balance or allowance"}')
                        warning('Skipping remaining attempts. Top up funds or check allowance before retrying.')
                        break
                    retry += 1
                    warning(f'Order failed (attempt {retry}/{RETRY_LIMIT}){f" - {error_message}" if error_message else ""}')
            except Exception as e:
                retry += 1
                warning(f'Order error (attempt {retry}/{RETRY_LIMIT}): {e}')
        
        if abort_due_to_funds:
            collection.update_one(
                {'_id': trade['_id']},
                {'$set': {'bot': True, 'botExcutedTime': RETRY_LIMIT}}
            )
            return
        
        if retry >= RETRY_LIMIT:
            collection.update_one(
                {'_id': trade['_id']},
                {'$set': {'bot': True, 'botExcutedTime': retry}}
            )
        else:
            collection.update_one({'_id': trade['_id']}, {'$set': {'bot': True}})
    
    elif condition == 'buy':
        info('Executing BUY strategy...')
        execution_asset = resolve_execution_asset()

        if not execution_asset:
            warning('Missing token id for buy order - skipping')
            collection.update_one({'_id': trade['_id']}, {'$set': {'bot': True}})
            return
        
        info(f'[ORDER TRACE] Resolved execution_asset={execution_asset}')
        info(f'Your balance: ${my_balance:.2f}')
        info(f'Trader bought: ${trade.get("usdcSize", 0):.2f}')
        
        # Get current position size for position limit checks
        current_position_value = (my_position.get('size', 0) * my_position.get('avgPrice', 0)) if my_position else 0
        
        # Use new copy strategy system
        order_calc = calculate_order_size(
            COPY_STRATEGY_CONFIG,
            trade.get('usdcSize', 0),
            my_balance,
            current_position_value
        )
        
        # Log the calculation reasoning
        info(f'{order_calc.reasoning}')
        
        # Check if order should be executed
        if order_calc.final_amount == 0:
            warning(f'Cannot execute: {order_calc.reasoning}')
            if order_calc.below_minimum:
                warning('Increase COPY_SIZE or wait for larger trades')
            collection.update_one({'_id': trade['_id']}, {'$set': {'bot': True}})
            return
        
        remaining = order_calc.final_amount
        available_balance = my_balance  # Track remaining balance after orders
        
        retry = 0
        abort_due_to_funds = False
        total_bought_tokens = 0  # Track total tokens bought for this trade
        
        while remaining > 0 and retry < RETRY_LIMIT:
            try:
                order_book = await clob_client.get_order_book(execution_asset)
                if not order_book.get('asks') or len(order_book['asks']) == 0:
                    warning('No asks available in order book')
                    collection.update_one({'_id': trade['_id']}, {'$set': {'bot': True}})
                    break
                
                min_price_ask = min(order_book['asks'], key=lambda x: float(x['price']))
                
                info(f'Best ask: {min_price_ask["size"]} @ ${min_price_ask["price"]}')
                
                # Check if remaining amount is below minimum before creating order
                if remaining < MIN_ORDER_SIZE_USD:
                    info(f'Remaining amount (${remaining:.2f}) below minimum - completing trade')
                    collection.update_one(
                        {'_id': trade['_id']},
                        {'$set': {'bot': True, 'myBoughtSize': total_bought_tokens}}
                    )
                    break
                
                max_order_size = float(min_price_ask['size']) * float(min_price_ask['price'])
                order_size = min(remaining, max_order_size)
                
                # Ensure minimum order size is 1 USDC
                if order_size < MIN_ORDER_SIZE_USD:
                    info(f'Order size (${order_size:.2f}) below minimum (${MIN_ORDER_SIZE_USD}) - completing trade')
                    collection.update_one(
                        {'_id': trade['_id']},
                        {'$set': {'bot': True, 'myBoughtSize': total_bought_tokens}}
                    )
                    break
                
                order_args = {
                    'side': 'BUY',
                    'tokenID': execution_asset,
                    'amount': _round_to_decimals(order_size, MAX_ORDER_SIZE_DECIMALS),
                    'price': _round_to_decimals(float(min_price_ask['price']), MAX_ORDER_PRICE_DECIMALS),
                }

                if order_args['amount'] < MIN_ORDER_SIZE_USD:
                    info(
                        f'Rounded order size (${order_args["amount"]:.2f}) below minimum (${MIN_ORDER_SIZE_USD}) - completing trade'
                    )
                    collection.update_one(
                        {'_id': trade['_id']},
                        {'$set': {'bot': True, 'myBoughtSize': total_bought_tokens}}
                    )
                    break

                # Check if balance is sufficient for the rounded order
                if available_balance < order_args['amount']:
                    warning(
                        f'Insufficient balance: Need ${order_args["amount"]:.2f} but only have ${available_balance:.2f}'
                    )
                    abort_due_to_funds = True
                    break
                
                info(f'Creating order: ${order_args["amount"]:.2f} @ ${order_args["price"]} (Balance: ${available_balance:.2f})')
                
                resp = await submit_with_fok_then_market(
                    clob_client=clob_client,
                    execution_asset=execution_asset,
                    order_args=order_args,
                    side='BUY',
                )
                
                if resp.get('success') is True:
                    retry = 0
                    tokens_bought = order_args['amount'] / order_args['price']
                    total_bought_tokens += tokens_bought
                    order_result(
                        True,
                        f'Bought ${order_args["amount"]:.2f} at ${order_args["price"]} ({tokens_bought:.2f} tokens)'
                    )
                    remaining -= order_args['amount']
                    # Update balance after successful order
                    available_balance -= order_args['amount']
                else:
                    error_message = extract_order_error(resp)
                    if is_insufficient_balance_or_allowance_error(error_message):
                        abort_due_to_funds = True
                        warning(f'Order rejected: {error_message or "Insufficient balance or allowance"}')
                        warning('Skipping remaining attempts. Top up funds or check allowance before retrying.')
                        break
                    retry += 1
                    warning(f'Order failed (attempt {retry}/{RETRY_LIMIT}){f" - {error_message}" if error_message else ""}')
            except Exception as e:
                retry += 1
                warning(f'Order error (attempt {retry}/{RETRY_LIMIT}): {e}')
        
        if abort_due_to_funds:
            collection.update_one(
                {'_id': trade['_id']},
                {'$set': {'bot': True, 'botExcutedTime': RETRY_LIMIT}}
            )
            return
        
        if retry >= RETRY_LIMIT:
            collection.update_one(
                {'_id': trade['_id']},
                {'$set': {'bot': True, 'botExcutedTime': retry}}
            )
        else:
            collection.update_one(
                {'_id': trade['_id']},
                {'$set': {'bot': True, 'myBoughtSize': total_bought_tokens}}
            )
    
    elif condition == 'sell':
        # SELL strategy - similar to merge but different logic
        info('Executing SELL strategy...')
        # Implementation similar to merge but for selling positions
        # This would be implemented based on the full TypeScript version
        collection.update_one({'_id': trade['_id']}, {'$set': {'bot': True}})
