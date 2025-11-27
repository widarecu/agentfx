#!/bin/bash

# Navigate to the mock API directory
cd "$(dirname "$0")/src/agentfx/portal_mock"

# Activate the virtual environment
source venv/bin/activate

# Install required packages if not already installed
pip install fastapi uvicorn pydantic

# Run the mock API server
PYTHONPATH=../.. uvicorn app:app --reload --port 8000

echo "Mock API server started at http://localhost:8000"
