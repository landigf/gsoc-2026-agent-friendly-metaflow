#!/bin/bash
set -e
cd /Users/landigf/Desktop/Code/GSoC/demo
export METAFLOW_SERVICE_URL=http://localhost:8080
export METAFLOW_DEFAULT_METADATA=service
export METAFLOW_DEFAULT_DATASTORE=local
export PATH="/Users/landigf/Desktop/Code/GSoC/venv/bin:$PATH"

echo "=== Starting BenchmarkFlow ==="
python3 benchmark_flow.py run 2>&1
echo "=== Done ==="
