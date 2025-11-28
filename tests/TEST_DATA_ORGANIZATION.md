# Test Results & Data Organization

## ✅ Complete Organization Implemented

### 📁 Directory Structure

```
project-root/
├── tests/                     # Test source code (version controlled)
│   ├── unit/                 # Unit tests
│   ├── integration/          # Integration tests
│   ├── e2e/                  # E2E tests
│   ├── fixtures/             # Static test data (versioned)
│   └── utils/                # Test utilities
├── test-results/             # Test execution artifacts (git ignored)
│   ├── screenshots/         # Failed test screenshots
│   ├── traces/              # Playwright traces
│   └── reports/             # HTML/JSON reports
├── test-data/               # Generated test data (git ignored)
│   ├── market_data/         # Market data for tests
│   ├── backtest_results/    # Backtest outputs
│   ├── temp/                # Temporary files
│   ├── performance/         # Performance test data
│   └── benchmarks/          # Benchmark results
├── .coverage                # Python coverage data
├── .pytest_cache/           # Pytest cache
├── htmlcov/                 # HTML coverage reports
└── test_agentfx.db          # Test database
```

### 🎯 What Goes Where

#### Version Controlled (in tests/)
- **Test source code**: All `.py` and `.ts` test files
- **Static fixtures**: Sample configurations, test scenarios
- **Test utilities**: Helper functions, mock servers
- **Documentation**: Test plans and strategies

#### Generated During Tests (git ignored)
- **Test results**: Screenshots, traces, reports
- **Coverage data**: `.coverage`, `htmlcov/`
- **Generated data**: Market data downloads, backtest results
- **Temporary files**: Files created during test execution
- **Cache data**: `.pytest_cache/`

### 🔧 Configuration Updates

#### Playwright Config
```typescript
// Updated to centralized locations
testDir: '../tests/e2e'
outputDir: '../test-results'
reporter: [
  ['html', { outputFolder: '../test-results/reports' }],
  ['json', { outputFile: '../test-results/test-results.json' }],
]
```

#### Git Ignore
```gitignore
# Test results and coverage
.coverage
.pytest_cache/
htmlcov/
test-results/
test-data/
test_*.db
*_test.log
```

### 📊 Test Results by Type

#### Python Tests (pytest)
- **Coverage**: `.coverage` → `htmlcov/` (via `coverage html`)
- **Cache**: `.pytest_cache/` (automatic)
- **Reports**: Console output + optional HTML reports

#### E2E Tests (Playwright)
- **Screenshots**: `test-results/screenshots/` (on failure)
- **Traces**: `test-results/traces/` (on failure)
- **Reports**: `test-results/reports/` (HTML + JSON)

#### Performance Tests
- **Results**: `test-data/performance/`
- **Benchmarks**: `test-data/benchmarks/`
- **Historical data**: Kept for trend analysis

### 🧹 Cleanup Strategies

#### Automatic Cleanup
- **Temporary files**: Cleaned after each test run
- **Test data**: Cleaned on next test run
- **Cache**: Managed by pytest automatically

#### Manual Cleanup
```bash
# Clean everything
rm -rf test-results/ test-data/ htmlcov/ .coverage .pytest_cache/

# Clean specific types
pytest --cache-clear              # Pytest cache
rm -rf test-results/screenshots/  # Screenshots only
rm -rf test-data/temp/            # Temp files only
```

### 📈 Performance Data Management

#### Load Test Data
- **Location**: `test-data/performance/`
- **Format**: JSON/CSV for analysis
- **Retention**: Keep recent runs, archive old results

#### Benchmark Tracking
- **Location**: `test-data/benchmarks/`
- **Purpose**: Performance regression detection
- **Format**: Structured for trend analysis

### 🔄 CI/CD Integration

#### Artifacts to Save
- **Test results**: `test-results/`
- **Coverage reports**: `htmlcov/`
- **Performance data**: `test-data/performance/`

#### Artifacts to Clean
- **Cache files**: `.pytest_cache/`
- **Temporary data**: `test-data/temp/`
- **Local databases**: `test_*.db`

### 📝 Best Practices

1. **Version control fixtures**, not generated data
2. **Clean up temporary files** automatically
3. **Use meaningful names** for test data files
4. **Document data formats** for complex test data
5. **Archive old performance results** for trend analysis
6. **Separate test data** from production data
7. **Use environment-specific** test databases

This organization ensures clean separation between version-controlled test code and generated test artifacts, making the repository clean while preserving important test results.
