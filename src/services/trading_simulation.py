"""Trading simulation support for dry-run execution."""
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..config.copy_strategy import calculate_order_size
from ..config.env import ENV
from ..utils.logger import info, warning


@dataclass
class SimPosition:
    quantity: float = 0.0
    cost_basis: float = 0.0


class TradingSimulation:
    """Tracks a virtual account and writes each simulated trade to CSV."""

    def __init__(self) -> None:
        self.enabled = ENV.TRADING_SIMULATION
        self.starting_balance = ENV.SIMULATION_TOTAL_BALANCE
        self.cash = ENV.SIMULATION_TOTAL_BALANCE
        self.positions: Dict[str, SimPosition] = {}
        self.last_mid_prices: Dict[str, float] = {}
        self.win_carry = 0.0
        self.loss_carry = 0.0
        self.filepath = Path(ENV.SIMULATION_RESULTS_FILE)
        self._initialized = False

    def _initialize_file(self) -> None:
        if self._initialized:
            return
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        if not self.filepath.exists():
            with self.filepath.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    'timestamp',
                    'mode',
                    'market',
                    'asset',
                    'side',
                    'status',
                    'reason',
                    'trade_size_usdc',
                    'simulated_order_usdc',
                    'price_used',
                    'quantity',
                    'best_bid',
                    'best_ask',
                    'total_balance',
                    'cash_balance',
                    'invested_balance',
                    'win_carry',
                    'loss_carry',
                ])
        self._initialized = True

    async def simulate_trade(
        self,
        clob_client: Any,
        trade: Dict[str, Any],
        my_position: Optional[Dict[str, Any]],
        live_balance: float,
        user_address: str,
    ) -> Dict[str, Any]:
        self._initialize_file()

        market = trade.get('slug') or trade.get('eventSlug') or 'unknown'
        asset = str(trade.get('asset') or '')
        side = str(trade.get('side') or 'BUY').upper()
        trade_size_usdc = float(trade.get('usdcSize') or 0.0)
        timestamp = datetime.now(timezone.utc).isoformat()

        if not asset:
            result = self._snapshot_result(timestamp, market, asset, side, 'skipped', 'missing asset', 0, 0, 0, 0, 0)
            self._write_result(result)
            return result

        order_book = await clob_client.get_order_book(asset)
        bids = order_book.get('bids') or []
        asks = order_book.get('asks') or []
        best_bid = float(max(bids, key=lambda x: float(x['price']))['price']) if bids else 0.0
        best_ask = float(min(asks, key=lambda x: float(x['price']))['price']) if asks else 0.0
        if best_bid > 0 and best_ask > 0:
            self.last_mid_prices[asset] = (best_bid + best_ask) / 2

        if side == 'BUY':
            if best_ask <= 0:
                result = self._snapshot_result(timestamp, market, asset, side, 'skipped', 'no asks', trade_size_usdc, 0, 0, best_bid, best_ask)
                self._write_result(result)
                return result

            order_calc = calculate_order_size(
                ENV.COPY_STRATEGY_CONFIG,
                trade_size_usdc,
                live_balance,
                (my_position.get('size', 0) * my_position.get('avgPrice', 0)) if my_position else 0,
            )
            order_usdc = min(order_calc.final_amount, self.cash)
            if order_usdc <= 0:
                result = self._snapshot_result(timestamp, market, asset, side, 'skipped', order_calc.reasoning, trade_size_usdc, 0, best_ask, best_bid, best_ask)
                self._write_result(result)
                return result

            quantity = order_usdc / best_ask
            position = self.positions.setdefault(asset, SimPosition())
            position.quantity += quantity
            position.cost_basis += order_usdc
            self.cash -= order_usdc

            result = self._snapshot_result(timestamp, market, asset, side, 'simulated', 'buy simulated', trade_size_usdc, order_usdc, best_ask, best_bid, best_ask, quantity)
            self._write_result(result)
            return result

        # SELL simulation
        if best_bid <= 0:
            result = self._snapshot_result(timestamp, market, asset, side, 'skipped', 'no bids', trade_size_usdc, 0, 0, best_bid, best_ask)
            self._write_result(result)
            return result

        position = self.positions.get(asset, SimPosition())
        if position.quantity <= 0:
            result = self._snapshot_result(timestamp, market, asset, side, 'skipped', 'no open position', trade_size_usdc, 0, best_bid, best_bid, best_ask)
            self._write_result(result)
            return result

        requested_qty = trade_size_usdc / best_bid if trade_size_usdc > 0 else position.quantity
        quantity = min(position.quantity, requested_qty)
        order_usdc = quantity * best_bid
        avg_cost = (position.cost_basis / position.quantity) if position.quantity > 0 else 0
        cost_removed = avg_cost * quantity
        pnl = order_usdc - cost_removed
        if pnl >= 0:
            self.win_carry += pnl
        else:
            self.loss_carry += abs(pnl)

        position.quantity -= quantity
        position.cost_basis -= cost_removed
        if position.quantity <= 1e-10:
            self.positions.pop(asset, None)

        self.cash += order_usdc

        result = self._snapshot_result(timestamp, market, asset, side, 'simulated', 'sell simulated', trade_size_usdc, order_usdc, best_bid, best_bid, best_ask, quantity)
        self._write_result(result)
        return result

    def _balances(self) -> tuple[float, float, float]:
        invested = 0.0
        for asset, position in self.positions.items():
            if position.quantity <= 0:
                continue
            mark_price = self.last_mid_prices.get(asset)
            if mark_price and mark_price > 0:
                invested += position.quantity * mark_price
            else:
                invested += position.cost_basis
        total = self.cash + invested
        return total, self.cash, invested

    def _snapshot_result(
        self,
        timestamp: str,
        market: str,
        asset: str,
        side: str,
        status: str,
        reason: str,
        trade_size_usdc: float,
        simulated_order_usdc: float,
        price_used: float,
        best_bid: float,
        best_ask: float,
        quantity: float = 0.0,
    ) -> Dict[str, Any]:
        total, cash, invested = self._balances()
        return {
            'timestamp': timestamp,
            'mode': 'simulation',
            'market': market,
            'asset': asset,
            'side': side,
            'status': status,
            'reason': reason,
            'trade_size_usdc': round(trade_size_usdc, 8),
            'simulated_order_usdc': round(simulated_order_usdc, 8),
            'price_used': round(price_used, 8),
            'quantity': round(quantity, 8),
            'best_bid': round(best_bid, 8),
            'best_ask': round(best_ask, 8),
            'total_balance': round(total, 8),
            'cash_balance': round(cash, 8),
            'invested_balance': round(invested, 8),
            'win_carry': round(self.win_carry, 8),
            'loss_carry': round(self.loss_carry, 8),
        }

    def _write_result(self, result: Dict[str, Any]) -> None:
        with self.filepath.open('a', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow([
                result['timestamp'],
                result['mode'],
                result['market'],
                result['asset'],
                result['side'],
                result['status'],
                result['reason'],
                result['trade_size_usdc'],
                result['simulated_order_usdc'],
                result['price_used'],
                result['quantity'],
                result['best_bid'],
                result['best_ask'],
                result['total_balance'],
                result['cash_balance'],
                result['invested_balance'],
                result['win_carry'],
                result['loss_carry'],
            ])
        info(
            f"[SIMULATION] {result['status']} {result['side']} {result['market']} | "
            f"order=${result['simulated_order_usdc']:.2f}, qty={result['quantity']:.4f}, "
            f"total=${result['total_balance']:.2f}, invested=${result['invested_balance']:.2f}"
        )


SIMULATION = TradingSimulation()
if SIMULATION.enabled:
    warning(
        f'TRADING_SIMULATION enabled. Live orders are disabled. '
        f'Starting virtual balance=${SIMULATION.starting_balance:.2f}, '
        f'output={SIMULATION.filepath}'
    )
