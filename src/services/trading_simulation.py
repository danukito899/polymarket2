"""Trading simulation support for dry-run execution."""
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..config.copy_strategy import calculate_order_size
from ..config.env import ENV
from ..utils.logger import info, warning


@dataclass
class SimPosition:
    quantity: float = 0.0
    cost_basis: float = 0.0


@dataclass
class SimAccount:
    cash: float
    positions: Dict[str, SimPosition] = field(default_factory=dict)
    last_mid_prices: Dict[str, float] = field(default_factory=dict)
    win_carry: float = 0.0
    loss_carry: float = 0.0


class TradingSimulation:
    """Tracks per-trader virtual accounts and writes each simulated trade to trader-specific CSV."""

    CSV_HEADERS = [
        'timestamp',
        'mode',
        'trader_address',
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
    ]

    def __init__(self) -> None:
        self.enabled = ENV.TRADING_SIMULATION
        self.starting_balance = ENV.SIMULATION_TOTAL_BALANCE
        self.base_filepath = Path(ENV.SIMULATION_RESULTS_FILE)
        self._accounts: Dict[str, SimAccount] = {}
        self._initialized_files: set[Path] = set()

    def _file_for_trader(self, user_address: str) -> Path:
        """Use one CSV per copied trader, suffixed by trader wallet address."""
        suffix = user_address.lower().replace('0x', '') if user_address else 'unknown'
        suffix = ''.join(ch for ch in suffix if ch.isalnum()) or 'unknown'
        if len(suffix) > 16:
            suffix = suffix[:16]

        stem = self.base_filepath.stem
        ext = self.base_filepath.suffix or '.csv'
        filename = f'{stem}_{suffix}{ext}'
        return self.base_filepath.with_name(filename)

    def _initialize_file(self, filepath: Path) -> None:
        if filepath in self._initialized_files:
            return

        filepath.parent.mkdir(parents=True, exist_ok=True)
        if not filepath.exists():
            with filepath.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle)
                writer.writerow(self.CSV_HEADERS)

        self._initialized_files.add(filepath)

    def _account(self, user_address: str) -> SimAccount:
        if user_address not in self._accounts:
            self._accounts[user_address] = SimAccount(cash=self.starting_balance)
        return self._accounts[user_address]

    async def simulate_trade(
        self,
        clob_client: Any,
        trade: Dict[str, Any],
        my_position: Optional[Dict[str, Any]],
        live_balance: float,
        user_address: str,
    ) -> Dict[str, Any]:
        filepath = self._file_for_trader(user_address)
        self._initialize_file(filepath)
        account = self._account(user_address)

        market = trade.get('slug') or trade.get('eventSlug') or 'unknown'
        asset = str(trade.get('asset') or '')
        side = str(trade.get('side') or 'BUY').upper()
        trade_size_usdc = float(trade.get('usdcSize') or 0.0)
        timestamp = datetime.now(timezone.utc).isoformat()

        if not asset:
            result = self._snapshot_result(account, timestamp, user_address, market, asset, side, 'skipped', 'missing asset', 0, 0, 0, 0, 0)
            self._write_result(filepath, result)
            return result

        order_book = await clob_client.get_order_book(asset)
        bids = order_book.get('bids') or []
        asks = order_book.get('asks') or []
        best_bid = float(max(bids, key=lambda x: float(x['price']))['price']) if bids else 0.0
        best_ask = float(min(asks, key=lambda x: float(x['price']))['price']) if asks else 0.0
        if best_bid > 0 and best_ask > 0:
            account.last_mid_prices[asset] = (best_bid + best_ask) / 2

        if side == 'BUY':
            if best_ask <= 0:
                result = self._snapshot_result(account, timestamp, user_address, market, asset, side, 'skipped', 'no asks', trade_size_usdc, 0, 0, best_bid, best_ask)
                self._write_result(filepath, result)
                return result

            order_calc = calculate_order_size(
                ENV.COPY_STRATEGY_CONFIG,
                trade_size_usdc,
                live_balance,
                (my_position.get('size', 0) * my_position.get('avgPrice', 0)) if my_position else 0,
            )
            order_usdc = min(order_calc.final_amount, account.cash)
            if order_usdc <= 0:
                result = self._snapshot_result(account, timestamp, user_address, market, asset, side, 'skipped', order_calc.reasoning, trade_size_usdc, 0, best_ask, best_bid, best_ask)
                self._write_result(filepath, result)
                return result

            quantity = order_usdc / best_ask
            position = account.positions.setdefault(asset, SimPosition())
            position.quantity += quantity
            position.cost_basis += order_usdc
            account.cash -= order_usdc

            result = self._snapshot_result(account, timestamp, user_address, market, asset, side, 'simulated', 'buy simulated', trade_size_usdc, order_usdc, best_ask, best_bid, best_ask, quantity)
            self._write_result(filepath, result)
            return result

        if best_bid <= 0:
            result = self._snapshot_result(account, timestamp, user_address, market, asset, side, 'skipped', 'no bids', trade_size_usdc, 0, 0, best_bid, best_ask)
            self._write_result(filepath, result)
            return result

        position = account.positions.get(asset, SimPosition())
        if position.quantity <= 0:
            result = self._snapshot_result(account, timestamp, user_address, market, asset, side, 'skipped', 'no open position', trade_size_usdc, 0, best_bid, best_bid, best_ask)
            self._write_result(filepath, result)
            return result

        requested_qty = trade_size_usdc / best_bid if trade_size_usdc > 0 else position.quantity
        quantity = min(position.quantity, requested_qty)
        order_usdc = quantity * best_bid
        avg_cost = (position.cost_basis / position.quantity) if position.quantity > 0 else 0
        cost_removed = avg_cost * quantity
        pnl = order_usdc - cost_removed
        if pnl >= 0:
            account.win_carry += pnl
        else:
            account.loss_carry += abs(pnl)

        position.quantity -= quantity
        position.cost_basis -= cost_removed
        if position.quantity <= 1e-10:
            account.positions.pop(asset, None)

        account.cash += order_usdc

        result = self._snapshot_result(account, timestamp, user_address, market, asset, side, 'simulated', 'sell simulated', trade_size_usdc, order_usdc, best_bid, best_bid, best_ask, quantity)
        self._write_result(filepath, result)
        return result

    def _balances(self, account: SimAccount) -> Tuple[float, float, float]:
        invested = 0.0
        for asset, position in account.positions.items():
            if position.quantity <= 0:
                continue
            mark_price = account.last_mid_prices.get(asset)
            invested += position.quantity * mark_price if mark_price and mark_price > 0 else position.cost_basis
        total = account.cash + invested
        return total, account.cash, invested

    def _snapshot_result(
        self,
        account: SimAccount,
        timestamp: str,
        user_address: str,
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
        total, cash, invested = self._balances(account)
        return {
            'timestamp': timestamp,
            'mode': 'simulation',
            'trader_address': user_address,
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
            'win_carry': round(account.win_carry, 8),
            'loss_carry': round(account.loss_carry, 8),
        }

    def _write_result(self, filepath: Path, result: Dict[str, Any]) -> None:
        with filepath.open('a', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow([result[column] for column in self.CSV_HEADERS])

        info(
            f"[SIMULATION] {result['status']} {result['side']} {result['market']} | "
            f"trader={result['trader_address'][:8]}..., file={filepath.name}, "
            f"order=${result['simulated_order_usdc']:.2f}, qty={result['quantity']:.4f}, "
            f"total=${result['total_balance']:.2f}, invested=${result['invested_balance']:.2f}"
        )


SIMULATION = TradingSimulation()
if SIMULATION.enabled:
    warning(
        f'TRADING_SIMULATION enabled. Live orders are disabled. '
        f'Starting virtual balance per trader=${SIMULATION.starting_balance:.2f}, '
        f'base output={SIMULATION.base_filepath}'
    )
