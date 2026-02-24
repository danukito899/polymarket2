"""Independent own trading strategy for BTC minute markets."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))); import src.lib_core
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..config.env import ENV
from ..services.trading_simulation import SIMULATION
from ..utils.fetch_data import fetch_data_async
from ..utils.get_my_balance import get_my_balance_async
from ..utils.logger import info, warning, success, error
from ..utils.post_order import submit_with_fok_then_market

is_running = True
executed_markets: set[str] = set()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_end_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except Exception:
        return None


def _extract_probabilities(market: Dict[str, Any]) -> List[float]:
    raw = market.get('outcomePrices')
    if not isinstance(raw, list):
        return []

    probs: List[float] = []
    for item in raw:
        try:
            probs.append(float(item))
        except Exception:
            probs.append(0.0)
    return probs


def _extract_token_ids(market: Dict[str, Any]) -> List[str]:
    token_ids = market.get('clobTokenIds')
    if isinstance(token_ids, list):
        return [str(token_id) for token_id in token_ids]

    if isinstance(token_ids, str):
        # Gamma frequently returns a JSON-encoded list string.
        cleaned = token_ids.strip()
        if cleaned.startswith('[') and cleaned.endswith(']'):
            try:
                import json
                parsed = json.loads(cleaned)
                if isinstance(parsed, list):
                    return [str(token_id) for token_id in parsed]
            except Exception:
                pass
    return []


def _select_market_candidate(markets: List[Dict[str, Any]]) -> Optional[Tuple[Dict[str, Any], int, float, int]]:
    now = _now_utc()
    threshold = ENV.OWN_STRATEGY_MIN_PROBABILITY

    for market in markets:
        question = str(market.get('question', '')).lower()
        title = str(market.get('title', '')).lower()
        slug = str(market.get('slug', '')).lower()

        if 'bitcoin' not in f'{question} {title} {slug}' or 'minute' not in f'{question} {title} {slug}':
            continue

        market_id = str(market.get('conditionId') or market.get('id') or market.get('slug') or '')
        if not market_id or market_id in executed_markets:
            continue

        end_at = _parse_end_time(market.get('endDate'))
        if not end_at:
            continue

        seconds_left = (end_at - now).total_seconds()
        if seconds_left <= 0 or seconds_left > ENV.OWN_STRATEGY_LAST_SECONDS:
            continue

        probabilities = _extract_probabilities(market)
        token_ids = _extract_token_ids(market)
        if not probabilities or not token_ids:
            continue

        best_index = max(range(len(probabilities)), key=lambda idx: probabilities[idx])
        best_prob = probabilities[best_index]
        if best_index >= len(token_ids):
            continue

        if best_prob >= threshold:
            return market, best_index, best_prob, int(seconds_left)

    return None


async def _fetch_btc_minute_markets() -> List[Dict[str, Any]]:
    # Gamma API provides market metadata including token IDs and probabilities.
    markets_url = (
        'https://gamma-api.polymarket.com/markets?active=true&closed=false&'
        'limit=200&order=volume&ascending=false'
    )
    data = await fetch_data_async(markets_url)
    return data if isinstance(data, list) else []


async def _execute_own_buy(clob_client: Any, token_id: str, market: Dict[str, Any], probability: float) -> None:
    order_book = await clob_client.get_order_book(token_id)
    asks = order_book.get('asks') or []
    if not asks:
        warning(f'No asks available for own strategy token {token_id}, skipping')
        return

    best_ask = min(asks, key=lambda level: float(level['price']))
    ask_price = float(best_ask['price'])
    if ask_price <= 0:
        warning(f'Invalid ask price for own strategy token {token_id}, skipping')
        return

    order_usd = ENV.OWN_STRATEGY_ORDER_SIZE_USD
    amount = order_usd / ask_price
    market_name = market.get('question') or market.get('slug') or 'BTC minute market'

    info(
        f'Own strategy trigger: prob={probability:.4f}, price={ask_price:.4f}, '
        f'order=${order_usd:.2f}, market={market_name}'
    )

    my_balance = await get_my_balance_async(ENV.BALANCE_WALLET_ADDRESS)
    if my_balance < order_usd:
        warning(f'Insufficient balance for own strategy (${my_balance:.2f} < ${order_usd:.2f}), skipping')
        return

    synthetic_trade = {
        'asset': token_id,
        'side': 'BUY',
        'usdcSize': order_usd,
        'price': ask_price,
        'slug': market.get('slug'),
        'eventSlug': market.get('eventSlug') or market.get('slug'),
        'conditionId': market.get('conditionId') or market.get('id'),
    }

    if SIMULATION.enabled:
        await SIMULATION.simulate_trade(
            clob_client=clob_client,
            trade=synthetic_trade,
            my_position=None,
            live_balance=my_balance,
            user_address='own_strategy',
        )
        success('Own strategy simulation completed')
        return

    order_args = {
        'side': 'BUY',
        'tokenID': token_id,
        'amount': amount,
        'price': ask_price,
    }
    response = await submit_with_fok_then_market(
        clob_client=clob_client,
        execution_asset=token_id,
        order_args=order_args,
        side='BUY',
    )

    if response.get('success'):
        success('Own strategy live BUY order executed')
    else:
        warning(f'Own strategy order failed: {response}')


async def own_trading_strategy_loop(clob_client: Any) -> None:
    """Poll BTC minute markets and execute configured strategy when conditions are met."""
    success('Own trading strategy enabled (independent mode)')
    while is_running:
        try:
            markets = await _fetch_btc_minute_markets()
            candidate = _select_market_candidate(markets)

            if candidate:
                market, outcome_index, probability, seconds_left = candidate
                token_ids = _extract_token_ids(market)
                token_id = token_ids[outcome_index]
                market_id = str(market.get('conditionId') or market.get('id') or market.get('slug'))

                info(
                    f'Candidate market found (seconds_left={seconds_left}, outcome_index={outcome_index}, '
                    f'probability={probability:.4f})'
                )
                await _execute_own_buy(clob_client, token_id, market, probability)
                executed_markets.add(market_id)

            await asyncio.sleep(ENV.OWN_STRATEGY_SCAN_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            error(f'Own strategy loop error: {exc}')
            await asyncio.sleep(ENV.OWN_STRATEGY_SCAN_INTERVAL_SECONDS)

    info('Own trading strategy stopped')


def stop_own_trading_strategy() -> None:
    """Gracefully stop own strategy loop."""
    global is_running
    is_running = False
