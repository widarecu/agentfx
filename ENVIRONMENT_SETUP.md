# Environment Configuration System

AgentFX now uses a proper environment-driven configuration system that automatically loads the appropriate configuration based on the `ENVIRONMENT` variable.

## 🚀 Quick Start

### Start Server in Different Environments

```bash
# Development (default)
./start-server.sh development
# or simply
./start-server.sh

# Test environment
./start-server.sh test

# Staging environment  
./start-server.sh staging

# Production environment
./start-server.sh production
```

### Docker-based Startup

```bash
# Start with Docker database
./start-server-docker.sh development
./start-server-docker.sh test
./start-server-docker.sh staging
./start-server-docker.sh production
```

## 📁 Environment Files Structure

```
.env.development    → PostgreSQL port 5432 (dev database)
.env.test          → PostgreSQL port 5433 (test database)
.env.staging       → PostgreSQL port 5434 (staging database)
.env.production    → PostgreSQL port 5435 (production database)
```

## 🔧 Configuration Loading Logic

The system automatically determines which environment file to load based on:

1. **ENVIRONMENT environment variable** (primary)
2. **ENV_FILE environment variable** (fallback for backward compatibility)
3. **Default to `.env.development`** (if neither is set)

### Environment Variable Mapping

| ENVIRONMENT Value | Config File Loaded |
|------------------|-------------------|
| development, dev | `.env.development` |
| test, testing    | `.env.test` |
| staging, stage   | `.env.staging` |
| production, prod | `.env.production` |

## 🐳 Docker Integration

Each environment has its own Docker Compose file:

- `docker-compose.development.yml` → Development database
- `docker-compose.test.yml` → Test database  
- `docker-compose.staging.yml` → Staging database
- `docker-compose.production.yml` → Production database

## 🧪 Testing

### Integration Tests
```bash
# Old way (still works for backward compatibility)
ENV_FILE=".env.test" pytest tests/integration/

# New way (recommended)
export ENVIRONMENT=test
pytest tests/integration/

# Or use the test runner script
./run_integration_tests.sh
```

### Environment-Specific Testing
```bash
# Test with development config
ENVIRONMENT=development pytest tests/

# Test with staging config  
ENVIRONMENT=staging pytest tests/
```

## 📊 Database Architecture

All environments use PostgreSQL Docker instances:

| Environment | Database | Port | User | Database Name |
|-------------|----------|------|------|---------------|
| Development | PostgreSQL | 5432 | agentfx | agentfx_dev |
| Test | PostgreSQL | 5433 | test_user | agentfx_test |
| Staging | PostgreSQL | 5434 | agentfx_staging | agentfx_staging |
| Production | PostgreSQL | 5435 | agentfx_prod | agentfx_production |

## 🔄 Migration from Old System

### Before (Manual)
```bash
# Manual environment variable setting
export TESTING=true
export ENV_FILE=".env.test"
python -m uvicorn src.agentfx.api.main:app --host 0.0.0.0 --port 8000
```

### After (Automatic)
```bash
# Simple environment specification
export ENVIRONMENT=test
python -m uvicorn src.agentfx.api.main:app --host 0.0.0.0 --port 8000

# Or use the startup script
./start-server.sh test
```

## 🛠️ Development Workflow

### 1. Development
```bash
# Start development server
./start-server.sh development

# Run development tests
export ENVIRONMENT=development
pytest tests/unit/
```

### 2. Testing
```bash
# Run integration tests
./run_integration_tests.sh

# Run specific test with test environment
ENVIRONMENT=test pytest tests/integration/market_data/
```

### 3. Staging
```bash
# Deploy to staging
./start-server-docker.sh staging
```

### 4. Production
```bash
# Deploy to production
./start-server-docker.sh production
```

## 🔍 Configuration Verification

You can verify which configuration is being loaded:

```bash
# Check current environment configuration
python -c "
from src.agentfx.api.core.config import settings
print(f'Environment: {settings.ENVIRONMENT}')
print(f'Database: {settings.DATABASE_URL}')
print(f'Testing Mode: {settings.TESTING}')
"
```

## 🚨 Important Notes

1. **No More SQLite**: All environments use PostgreSQL Docker instances
2. **Consistent Architecture**: Same database type across all environments
3. **Automatic Detection**: No manual file specification needed
4. **Backward Compatibility**: Old `ENV_FILE` method still works
5. **Environment Isolation**: Each environment has separate database instance

## 📝 Environment Variables

Key environment variables that control the system:

- `ENVIRONMENT` - Specifies which environment to run (development/test/staging/production)
- `ENV_FILE` - Fallback for backward compatibility (specifies exact env file)
- `TESTING` - Automatically set to `true` when `ENVIRONMENT=test`

## 🔧 Makefile Updates

The Makefile has been updated to use the new environment system:

```bash
# Development commands now use ENVIRONMENT=development
make run-backfill PAIRS="EURUSD" DAYS=2
make backtest PAIRS="EURUSD" DAYS=2

# Test runner automatically sets ENVIRONMENT=test  
./run_integration_tests.sh
```
