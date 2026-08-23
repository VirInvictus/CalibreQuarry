#!/bin/bash
DB_PATH="/home/bdkl/docs/Calibre Library/metadata.db"
export PYTHONPATH=src

commands=(
  "python -m cquarry_cli --catalog --db \"$DB_PATH\" --output /tmp/test_catalog.txt"
  "python -m cquarry_cli --all-wings --db \"$DB_PATH\" --outdir /tmp/test_all_wings"
  "python -m cquarry_cli --stats --db \"$DB_PATH\" > /tmp/test_stats.txt"
  "python -m cquarry_cli --audit --db \"$DB_PATH\" --output /tmp/test_audit.csv"
  "python -m cquarry_cli --recent 5 --db \"$DB_PATH\" > /tmp/test_recent.txt"
  "python -m cquarry_cli --series --db \"$DB_PATH\" > /tmp/test_series.txt"
  "python -m cquarry_cli --wings --db \"$DB_PATH\" > /tmp/test_wings.txt"
  "python -m cquarry_cli --tags --db \"$DB_PATH\" > /tmp/test_tags.txt"
  "python -m cquarry_cli --export --format json --db \"$DB_PATH\" --output /tmp/test_export.json"
  "python -m cquarry_cli --export --format csv --db \"$DB_PATH\" --output /tmp/test_export.csv"
  "python -m cquarry_cli --export --format ai --db \"$DB_PATH\" --output /tmp/test_export.ai"
  "python -m cquarry_cli --search \"tags:Fic\" --db \"$DB_PATH\" --output /tmp/test_search.txt"
  "python -m cquarry_cli --search \"rating:>=4 and tags:Fic.SciFi\" --db \"$DB_PATH\" > /tmp/test_search_stdout.txt"
  "python -m cquarry_cli --search \"tags:Fic\" --format json --db \"$DB_PATH\" --output /tmp/test_search.json"
  "python -m cquarry_cli --search \"\" --format csv --db \"$DB_PATH\" --output /tmp/test_search_all.csv"
  "python -m cquarry_cli --analytics author --db \"$DB_PATH\" > /tmp/test_analytics_author.txt"
  "python -m cquarry_cli --analytics pace --db \"$DB_PATH\" > /tmp/test_analytics_pace.txt"
  "python -m cquarry_cli --analytics tags --db \"$DB_PATH\" > /tmp/test_analytics_tags.txt"
  "python -m cquarry_cli --analytics overlap --db \"$DB_PATH\" > /tmp/test_analytics_overlap.txt"
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
