# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

OpenAlgo is a production-ready algorithmic trading platform built with Flask (backend) and React 19 (frontend). It provides a unified API layer across 29 Indian brokers, enabling seamless integration with TradingView, Amibroker, Excel, Python, and AI agents.

**Repository**: https://github.com/marketcalls/openalgo
**Documentation**: https://docs.openalgo.in

## Development Environment Setup

### Prerequisites
- Python 3.12+ (required per pyproject.toml)
- Node.js 20/22/24 for root-level CSS compilation and React frontend
- **uv package manager (required)** - Never use global Python

### Initial Setup

```bash
# Install uv package manager (required)
pip install uv

# Configure environment
cp .sample.env .env

# Generate new APP_KEY and API_KEY_PEPPER:
uv run python -c "import secrets; print(secrets.token_hex(32))"

# Build React frontend (required - not tracked in git)
cd frontend && npm install && npm run build && cd ..

# Run application (uv automatically handles virtual env and dependencies)
uv run app.py
```

### Important: Always Use UV

**Never use global Python or manually manage virtual environments.** Always prefix Python commands with `uv run`:

```bash
# Running the app
uv run app.py

# Running any Python script
uv run python script.py

# Installing a new package (adds to pyproject.toml)
uv add package_name

# Syncing dependencies after pulling changes
uv sync
```

### CSS Development (Root Level - Jinja2 Templates)

```bash
# Development mode (auto-compile on changes)
npm run dev

# Production build (before committing)
npm run build

# NEVER edit static/css/main.css directly!
# Only edit src/css/styles.css
```

### React Frontend Development

```bash
cd frontend

# Install dependencies
npm install

# Development server (hot reload)
npm run dev

# Production build
npm run build

# Run tests
npm test

# Run end-to-end tests
npm run e2e

# Linting and formatting (Biome)
npm run lint
npm run format
```

## Application Architecture

### Dual Frontend System

OpenAlgo has TWO frontend systems that coexist:

1. **Jinja2 Templates** (`/templates/`, `/static/`): Traditional Flask templates with Tailwind CSS 4 + DaisyUI — being phased out
2. **React 19 Frontend** (`/frontend/`): Modern SPA with TypeScript, Vite, shadcn/ui, TanStack Query — **primary frontend, migration 100% complete**

Both frontends are served by the same Flask application. The React frontend is built and served from `/frontend/dist/`.

### Backend Structure

- `app.py` - Main Flask application entry point
- `blueprints/` - Flask route handlers (35 files: UI, webhooks, and platform integrations)
- `restx_api/` - REST API endpoints (`/api/v1/`, 44 files with Swagger docs)
- `services/` - Business logic layer (52 files)
- `broker/` - Broker integrations (29 brokers), each with `api/`, `database/`, `mapping/`, `streaming/`, `plugin.json`
- `database/` - SQLAlchemy models and database utilities (29 files)
- `utils/` - Shared utilities and helpers (22 files)
- `websocket_proxy/` - Unified WebSocket server (port 8765, 7 files)
- `mcp/` - Model Context Protocol server for AI assistant integration

### Blueprint Catalog

Key blueprints in `blueprints/`:

| Blueprint | Purpose |
|-----------|---------|
| `auth.py` | Authentication and login flows |
| `dashboard.py` | Main dashboard |
| `orders.py` | Order management UI |
| `analyzer.py` | Paper trading / sandbox mode |
| `flow.py` | Visual workflow / automation engine |
| `historify.py` | Historical data retrieval and caching |
| `strategy.py` | Python strategy execution |
| `python_strategy.py` | Strategy runner (largest file ~105KB) |
| `telegram.py` | Telegram bot integration |
| `playground.py` | Interactive API test playground |
| `health.py` | Health monitoring and stats |
| `latency.py` | Latency monitoring |
| `pnltracker.py` | P&L tracking |
| `chartink.py` | ChartIQ charting integration |
| `ivchart.py` | Implied Volatility charting |
| `react_app.py` | React frontend serving |
| `security.py` | Security settings and CSRF |
| `system_permissions.py` | Role-based access control |
| `admin.py` | Admin panel and user management |
| `apikey.py` | API key management |
| `brlogin.py` | Broker-specific TOTP/login |

### REST API Endpoints (`/api/v1/`)

Defined in `restx_api/`, auto-documented at `/api/docs`:

**Orders**: `place_order`, `place_smart_order`, `basket_order`, `split_order`, `modify_order`, `cancel_order`, `cancel_all_order`, `options_order`, `options_multiorder`

**Positions & Holdings**: `openposition`, `close_position`, `holdings`, `orderbook`, `tradebook`, `orderstatus`

**Market Data**: `quotes`, `multiquotes`, `depth`, `history`, `chart_api`, `ticker`

**Options**: `option_chain`, `option_greeks`, `multi_option_greeks`, `option_symbol`

**Instruments**: `instruments`, `search`, `symbol`

**Utilities**: `funds`, `margin`, `expiry`, `intervals`, `market_timings`, `market_holidays`, `ping`, `synthetic_future`, `pnl_symbols`, `analyzer`

### Database Architecture

OpenAlgo uses **5 separate databases** for isolation:

| Database | File | Purpose |
|----------|------|---------|
| Main | `db/openalgo.db` | Users, orders, positions, settings, strategies |
| Logs | `db/logs.db` | Traffic and API request/response logs |
| Latency | `db/latency.db` | Latency monitoring metrics |
| Sandbox | `db/sandbox.db` | Analyzer/paper trading (isolated) |
| Historical | `db/historify.duckdb` | Historical market data (DuckDB) |

Each database has its own initialization and model file in `/database/`.

Notable database files:
- `database/auth_db.py` (25KB) - User authentication, roles, API keys
- `database/historify_db.py` (123KB) - Historical price data storage
- `database/market_calendar_db.py` (43KB) - Trading holidays and timings
- `database/telegram_db.py` (27KB) - Telegram bot config and chat logs
- `database/flow_db.py` (15KB) - Flow/workflow definitions
- `database/traffic_db.py` (18KB) - API traffic logging

### Broker Integration Pattern

All 29 brokers follow a standardized structure in `broker/{broker_name}/`:

1. `api/auth_api.py` - OAuth2 or API key based authentication
2. `api/order_api.py` - Place, modify, cancel orders
3. `api/data.py` - Quotes, depth, historical data
4. `api/funds.py` - Account balance and margins
5. `mapping/` - Transform OpenAlgo format ↔ broker format
6. `streaming/` - WebSocket adapter for real-time data
7. `database/master_contract_db.py` - Symbol mapping
8. `plugin.json` - Broker metadata

**Supported Brokers (29):**
aliceblue, angel, compositedge, definedge, dhan, dhan_sandbox, firstock, fivepaisa, fivepaisaxts, flattrade, fyers, groww, ibulls, iifl, indmoney, jainamxts, kotak, motilal, mstock, nubra, paytm, pocketful, samco, shoonya, tradejini, upstox, wisdom, zebu, zerodha

Reference implementations: `/broker/zerodha/`, `/broker/dhan/`, `/broker/angel/`

### WebSocket Architecture

- **Unified Proxy Server**: `websocket_proxy/server.py` (port 8765, 71KB)
- **ZeroMQ Message Bus**: High-performance data distribution (port 5555)
- **Broker Adapters**: `websocket_proxy/base_adapter.py` normalizes broker-specific WebSocket data
- **Connection Manager**: `websocket_proxy/connection_manager.py` handles pooling and routing
- **Connection Pooling**: `MAX_SYMBOLS_PER_WEBSOCKET` (default: 1000) × `MAX_WEBSOCKET_CONNECTIONS` (default: 3) = 3000 symbols max

### Flow Editor (Visual Automation Engine)

The Flow system (`blueprints/flow.py`, `services/flow_*.py`, `database/flow_db.py`) provides a visual node-based automation engine:

- 60+ node types including: `PlaceOrderNode`, `OptionsOrderNode`, `OptionChainNode`, `PriceConditionNode`, `TimeWindowNode`, `TelegramAlertNode`
- Built with `@xyflow/react` (React Flow) in the frontend
- Services: `flow_executor_service.py` (90KB), `flow_scheduler_service.py`, `flow_price_monitor_service.py`
- Supports scheduled execution, price-based triggers, and multi-leg options strategies

### Model Context Protocol (MCP) Integration

Located in `mcp/`:

- `mcp/mcpserver.py` (35KB) - Full MCP server implementation
- `mcp/README.md` - Setup for Claude/Cursor/Windsurf

Exposes 30+ trading tools to AI assistants:
- Place, modify, cancel orders
- Check positions, margins, funds
- Get quotes and historical data
- Manage strategies and settings
- Send Telegram alerts
- Access analyzer/sandbox mode

### Real-Time Communication

1. **Flask-SocketIO**: Real-time updates for orders, trades, positions, logs
2. **WebSocket Proxy**: Unified market data streaming (port 8765)
3. **ZeroMQ**: High-performance message bus for internal communication (port 5555)

## Common Development Tasks

### Running the Application

```bash
# Development mode (auto-reloads on code changes)
uv run app.py

# Production mode with Gunicorn (Linux only)
uv run gunicorn --worker-class eventlet -w 1 app:app

# IMPORTANT: Use -w 1 (one worker) for WebSocket compatibility

# Docker
docker-compose up -d
```

Access points:
- Main app: http://127.0.0.1:5000
- API docs: http://127.0.0.1:5000/api/docs
- React frontend: http://127.0.0.1:5000/react
- API Analyzer: http://127.0.0.1:5000/analyzer
- API Playground: http://127.0.0.1:5000/playground

### Testing

```bash
# Run all tests
uv run pytest test/ -v

# Run specific test file
uv run pytest test/test_broker.py -v

# Run single test function
uv run pytest test/test_broker.py::test_function_name -v

# Run tests with coverage
uv run pytest test/ --cov

# React frontend tests
cd frontend
npm test                    # Run all tests (Vitest)
npm run test:coverage      # With coverage
npm run e2e                # End-to-end tests (Playwright)
```

Most testing is currently manual via:
- Web UI: http://127.0.0.1:5000
- Swagger API: http://127.0.0.1:5000/api/docs
- API Analyzer: http://127.0.0.1:5000/analyzer

### Python Linting

Ruff is configured in `pyproject.toml` (line-length: 100):

```bash
uv run ruff check .         # Check for issues
uv run ruff check . --fix   # Auto-fix issues
uv run ruff format .        # Format code
```

### Building for Production

```bash
# Build Jinja2 frontend CSS
npm run build

# Build React frontend
cd frontend
npm run build

# The React build artifacts go to frontend/dist/
# These are served by Flask via blueprints/react_app.py
```

### Important: Frontend Build (CI/CD)

**`frontend/dist/` is NOT tracked in git.** The CI/CD pipeline builds it automatically on each push.

For local development after cloning:
```bash
cd frontend
npm install
npm run build
```

This is required before running the application locally. The build artifacts are gitignored to:
- Prevent merge conflicts on hash-named files
- Keep the repository size smaller
- Ensure fresh builds via CI/CD

## React Frontend Tech Stack

Located in `/frontend/`:

| Tool | Version | Purpose |
|------|---------|---------|
| React | 19.x | UI framework with React Compiler |
| TypeScript | 5.9+ | Type safety |
| Vite | 7.x | Build tool and dev server |
| React Router | 7.x | Client-side routing |
| TanStack Query | 5.x | Server state management |
| Zustand | 5.x | Client state management |
| Tailwind CSS | 4.x | Utility-first styling |
| shadcn/ui | latest | Component library (Radix UI primitives) |
| Biome | latest | Linting and formatting |
| @xyflow/react | 12.x | Flow editor / node graph |
| Socket.io-client | 4.x | Real-time WebSocket updates |
| Lightweight Charts | 5.x | TradingView candlestick charts |
| Vitest | latest | Unit testing |
| Playwright | latest | End-to-end testing |

**Frontend Structure:**
```
frontend/src/
├── app/           # App initialization and routes
├── api/           # API client modules
├── components/
│   ├── ui/        # Shadcn/ui base components
│   ├── auth/      # Login, TOTP, registration
│   ├── layout/    # Headers, sidebars, footers
│   ├── trading/   # Order forms, position displays
│   ├── flow/      # Flow editor (60+ node types)
│   ├── option-chain/ # Options chain viewer
│   └── socket/    # WebSocket components
├── pages/         # Route-level page components
├── stores/        # Zustand state (auth, alerts, theme, flow)
├── types/         # TypeScript interfaces
├── hooks/         # Custom React hooks
├── contexts/      # React contexts
└── utils/         # Utility functions
```

## Key Architectural Concepts

### Plugin System for Brokers

Brokers are dynamically loaded from `broker/*/plugin.json`. The plugin loader (`utils/plugin_loader.py`) discovers and loads broker modules at runtime. To add a new broker:

1. Create directory: `broker/new_broker/`
2. Implement required modules: `api/`, `mapping/`, `database/`, `streaming/`
3. Add `plugin.json` with metadata
4. Add broker to `VALID_BROKERS` in `.env`

### REST API Layer (Flask-RESTX)

The `/api/v1/` endpoints are defined in `restx_api/`:
- Automatic Swagger documentation at `/api/docs`
- Request/response schemas in `restx_api/schemas.py` and `restx_api/data_schemas.py`
- All endpoints require API key authentication
- Rate limiting configured per endpoint type

### Action Center (Order Approval System)

Orders can flow through two modes:
- **Auto Mode**: Direct execution (personal trading)
- **Semi-Auto Mode**: Manual approval required (managed accounts)

Approval workflow in `database/action_center_db.py` and `services/action_center_service.py`.

### Analyzer Mode (Paper Trading)

Separate database (`sandbox.db`) with ₹1 Crore virtual capital:
- Realistic margin system with leverage
- Auto square-off at exchange timings
- Complete isolation from live trading
- Toggle via `/analyzer` blueprint and `services/analyzer_service.py`

### Historify (Historical Data)

DuckDB-backed historical data system:
- `blueprints/historify.py` - HTTP endpoints
- `services/historify_service.py` (72KB) - Core logic
- `services/historify_scheduler_service.py` (22KB) - Scheduled downloads
- `database/historify_db.py` (123KB) - DuckDB storage layer

### Strategy Execution

Located in `blueprints/python_strategy.py` (105KB) and `strategies/` directory:
- Users place Python strategy files in `strategies/`
- `strategies/` is gitignored to protect user strategies
- Full execution environment with order placement capabilities

### Security Architecture

- `csp.py` - Content Security Policy headers
- `cors.py` - CORS configuration
- `limiter.py` - Rate limiting
- `utils/security_middleware.py` - Security headers
- `utils/auth_utils.py` (14KB) - Authentication utilities
- `database/auth_db.py` - API key hashing with pepper
- `.secrets.baseline` - Secret detection baseline (detect-secrets)
- `.pre-commit-config.yaml` - Pre-commit hooks

## Important Configuration

### Environment Variables (.env)

Critical variables to configure (full template in `.sample.env`, 270 lines):

```bash
# Security (required, generate with secrets.token_hex(32))
APP_KEY=your_flask_secret_key
API_KEY_PEPPER=your_pepper_value

# Broker credentials
BROKER_API_KEY=your_broker_api_key
BROKER_API_SECRET=your_broker_api_secret

# Enabled brokers (comma-separated)
VALID_BROKERS=zerodha,dhan,angel

# Database paths
DATABASE_URL=sqlite:///db/openalgo.db

# WebSocket configuration
WEBSOCKET_HOST=127.0.0.1
WEBSOCKET_PORT=8765
MAX_SYMBOLS_PER_WEBSOCKET=1000
MAX_WEBSOCKET_CONNECTIONS=3

# Development
FLASK_DEBUG=True
```

## Code Style and Conventions

### Python
- Follow PEP 8 style guide
- Use 4 spaces for indentation
- Line length: 100 characters (Ruff configured)
- Use Google-style docstrings
- Imports: Standard library → Third-party → Local
- Linter: Ruff (`uv run ruff check .`)

### React/TypeScript
- Follow Biome.js linting rules (`frontend/biome.json`)
- Use functional components with hooks
- Component files use PascalCase: `MyComponent.tsx`
- Use TanStack Query for all server state
- Use Zustand for client-only state

### Git Commit Messages (Conventional Commits)
- `feat:` New features
- `fix:` Bug fixes
- `docs:` Documentation changes
- `refactor:` Code refactoring
- `test:` Test additions/changes
- `chore:` Build, config, dependency updates

## Common Patterns and Utilities

### API Authentication

All `/api/v1/` endpoints require API key:
```python
# In request body (recommended):
{"apikey": "YOUR_API_KEY", "symbol": "SBIN", ...}

# Or in headers:
X-API-KEY: YOUR_API_KEY
```

API keys are generated at `/apikey` and hashed with pepper before storage.

### Symbol Format

OpenAlgo uses standardized symbol format across all brokers:
```
NSE:SBIN-EQ            # Equity
NFO:NIFTY24JAN24000CE  # Options
NSE:NIFTY-INDEX        # Index
MCX:CRUDEOIL25JANFUT   # Commodity futures
```

Broker-specific symbols are mapped via `broker/*/mapping/` modules.

### Database Queries

Always use SQLAlchemy ORM (never raw SQL):
```python
from database.auth_db import User

# Good
user = User.query.filter_by(username='admin').first()
```

For historical data, use DuckDB via `database/historify_db.py`.

### Error Handling

Return consistent JSON responses:
```python
return {
    'status': 'success' | 'error',
    'message': 'Human-readable message',
    'data': {...}  # Optional payload
}
```

### React API Calls

Use TanStack Query for server state:
```typescript
import { useQuery } from '@tanstack/react-query';

const { data, isLoading, error } = useQuery({
  queryKey: ['positions'],
  queryFn: () => api.getPositions()
});
```

### Health Monitoring

Available utilities:
- `utils/health_monitor.py` (22KB) - System health monitoring
- `utils/latency_monitor.py` (11KB) - Request latency tracking
- `utils/traffic_logger.py` - API traffic logging
- `blueprints/health.py` - Health HTTP endpoints
- `blueprints/latency.py` - Latency HTTP endpoints

## Troubleshooting Common Issues

### CSS Not Updating (Root Level)
1. Clear browser cache
2. Run `npm run build` in root directory
3. Check that `node_modules` exists (run `npm install`)
4. Never edit `static/css/main.css` directly

### WebSocket Connection Issues
1. Ensure WebSocket server is running (starts with app.py)
2. Check `WEBSOCKET_HOST` and `WEBSOCKET_PORT` in `.env`
3. For Gunicorn: Use `-w 1` (single worker only)
4. Check firewall settings for port 8765
5. ZeroMQ message bus must be available on port 5555

### Database Locked Errors
1. SQLite doesn't handle high concurrency well
2. Close all connections and restart app
3. For production, consider PostgreSQL
4. DuckDB (`historify.duckdb`) is single-writer by design

### Broker Integration Not Loading
1. Check broker name in `VALID_BROKERS` (.env)
2. Verify `plugin.json` exists in broker directory
3. Check broker module structure matches pattern
4. Restart application to reload plugins

### React Frontend Build Errors
1. Ensure Node.js version matches `frontend/package.json` engines (20, 22, or 24)
2. Delete `frontend/node_modules` and run `npm install`
3. Check for TypeScript errors: `npm run build`
4. Biome lint errors will block builds: `npm run lint`

### MCP Server Issues
1. Check `mcp/README.md` for platform-specific config
2. Ensure API key is valid before connecting MCP client
3. MCP server requires a running OpenAlgo instance

## Claude Code Instructions

### Frontend Build Process
When building the React frontend locally:
- Run `cd frontend && npm run build` (build only, no tests)
- Tests are handled by CI/CD pipeline, not required for local builds
- The `frontend/dist/` directory is gitignored and built by GitHub Actions

### Working with Brokers
- Always reference an existing broker (e.g., `broker/zerodha/`) as a template
- All brokers must implement the full standardized module structure
- Test broker changes against the `/api/docs` Swagger interface

### Adding New API Endpoints
1. Create service file in `services/`
2. Create endpoint file in `restx_api/`
3. Register in `restx_api/__init__.py`
4. Add schema definitions to `restx_api/schemas.py` if needed

### Flow Editor Nodes
When adding new flow node types:
1. Create node component in `frontend/src/components/flow/nodes/`
2. Register in the flow node registry
3. Add corresponding backend execution logic in `services/flow_executor_service.py`
4. Update `database/flow_db.py` if new node requires persistence
