#!/bin/bash
DB_PATH="/home/bdkl/docs/Calibre Library/metadata.db"
export PYTHONPATH=src

commands=(
  "python -m cquarry --catalog --db \"$DB_PATH\" --output /tmp/test_catalog.txt"
  "python -m cquarry --all-wings --db \"$DB_PATH\" --outdir /tmp/test_all_wings"
  "python -m cquarry --stats --db \"$DB_PATH\" > /tmp/test_stats.txt"
  "python -m cquarry --audit --db \"$DB_PATH\" --output /tmp/test_audit.csv"
  "python -m cquarry --recent 5 --db \"$DB_PATH\" > /tmp/test_recent.txt"
  "python -m cquarry --series --db \"$DB_PATH\" > /tmp/test_series.txt"
  "python -m cquarry --wings --db \"$DB_PATH\" > /tmp/test_wings.txt"
  "python -m cquarry --export --format json --db \"$DB_PATH\" --output /tmp/test_export.json"
  "python -m cquarry --export --format csv --db \"$DB_PATH\" --output /tmp/test_export.csv"
  "python -m cquarry --export --format ai --db \"$DB_PATH\" --output /tmp/test_export.ai"
  "python -m cquarry --search \"tags:Fic\" --db \"$DB_PATH\" --output /tmp/test_search.txt"
  "python -m cquarry --analytics author --db \"$DB_PATH\" > /tmp/test_analytics_author.txt"
  "python -m cquarry --analytics pace --db \"$DB_PATH\" > /tmp/test_analytics_pace.txt"
  "python -m cquarry --analytics tags --db \"$DB_PATH\" > /tmp/test_analytics_tags.txt"
  "python -m cquarry --analytics overlap --db \"$DB_PATH\" > /tmp/test_analytics_overlap.txt"
)

for cmd in "${commands[@]}"; do
  echo "Running: $cmd"
  eval $cmd
  if [ $? -ne 0 ]; then
    echo "FAILED: $cmd"
    exit 1
  fi
done
echo "All tests passed without exceptions."
