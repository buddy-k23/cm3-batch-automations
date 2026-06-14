#!/bin/bash

# Test runner script for Valdo
# Usage: ./run_tests.sh [options]

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Valdo - Test Runner${NC}"
echo "=========================================="
echo ""

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo -e "${RED}Error: pytest is not installed${NC}"
    echo "Install with: pip install -r requirements.txt"
    exit 1
fi

# Parse arguments
TEST_TYPE="all"
COVERAGE=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --unit)
            TEST_TYPE="unit"
            shift
            ;;
        --integration)
            TEST_TYPE="integration"
            shift
            ;;
        --coverage)
            COVERAGE=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            echo "Usage: ./run_tests.sh [options]"
            echo ""
            echo "Options:"
            echo "  --unit          Run only unit tests"
            echo "  --integration   Run only integration tests"
            echo "  --coverage      Generate coverage report"
            echo "  --verbose, -v   Verbose output"
            echo "  --help, -h      Show this help message"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Build pytest command
PYTEST_CMD="pytest"

if [ "$TEST_TYPE" = "unit" ]; then
    PYTEST_CMD="$PYTEST_CMD tests/unit"
    echo -e "${YELLOW}Running unit tests only${NC}"
elif [ "$TEST_TYPE" = "integration" ]; then
    PYTEST_CMD="$PYTEST_CMD tests/integration"
    echo -e "${YELLOW}Running integration tests only${NC}"
else
    echo -e "${YELLOW}Running all tests${NC}"
fi

if [ "$VERBOSE" = true ]; then
    PYTEST_CMD="$PYTEST_CMD -v"
fi

if [ "$COVERAGE" = true ]; then
    PYTEST_CMD="$PYTEST_CMD --cov=src --cov-report=html --cov-report=term-missing"
    echo -e "${YELLOW}Coverage reporting enabled${NC}"
fi

echo ""
echo "Command: $PYTEST_CMD"
echo "=========================================="
echo ""

# Run tests
if $PYTEST_CMD; then
    echo ""
    echo -e "${GREEN}==========================================${NC}"
    echo -e "${GREEN}✓ All tests passed!${NC}"
    echo -e "${GREEN}==========================================${NC}"
    
    if [ "$COVERAGE" = true ]; then
        echo ""
        echo -e "${GREEN}Coverage report generated: htmlcov/index.html${NC}"
    fi
    
    exit 0
else
    echo ""
    echo -e "${RED}==========================================${NC}"
    echo -e "${RED}✗ Tests failed!${NC}"
    echo -e "${RED}==========================================${NC}"
    exit 1
fi
