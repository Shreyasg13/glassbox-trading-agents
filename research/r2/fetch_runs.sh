#!/usr/bin/env bash
# R2: export clean-run inputs from production (read-only) into research/r2/data/export.jsonl. Owner runs this.
set -euo pipefail
VM=instance-20260902-033025 ZONE=us-central1-a
cd "$(dirname "$0")"
mkdir -p data
gcloud compute scp export_runs.py "$VM":/tmp/export_runs.py --zone "$ZONE" --quiet
gcloud compute ssh "$VM" --zone "$ZONE" --quiet --command 'cd ~/glassbox && C=$(docker compose ps -q backend) && docker cp /tmp/export_runs.py $C:/tmp/export_runs.py && docker exec -w /app $C python /tmp/export_runs.py /tmp/r2_runs.jsonl && docker cp $C:/tmp/r2_runs.jsonl /tmp/r2_runs.jsonl && docker exec $C rm -f /tmp/r2_runs.jsonl /tmp/export_runs.py && rm -f /tmp/export_runs.py'
gcloud compute scp "$VM":/tmp/r2_runs.jsonl data/export.jsonl --zone "$ZONE" --quiet
gcloud compute ssh "$VM" --zone "$ZONE" --quiet --command 'rm -f /tmp/r2_runs.jsonl'
wc -l data/export.jsonl
