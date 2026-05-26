#!/bin/bash
DB_PATH="/home/bdkl/docs/Calibre Library/metadata.db"
export PYTHONPATH=src

# Representative queries exercising the search engine. Each is streamed to
# stdout and its own "Matches:" line is read back, so a query that legitimately
# returns nothing can't show a previous query's stale count.
queries=(
  "tags:Fic"
  "tags:\"=Fic.Fantasy\""
  "tags:Fic AND tags:Fantasy"
  "tags:Fic tags:Fantasy"
  "tags:Fic OR tags:NonFic"
  "NOT tags:Fic"
  "(tags:Fic OR tags:NonFic) AND NOT tags:Gaming"
  "vl:\"The Tabletop\""
  "tags:Fic and (not tags:Horror) and tags:\"=Fic.SciFi.Cyberpunk\""
  "author:Sanderson"
  "series:Mistborn"
  "rating:>=4"
  "rating:5 and tags:Fic"
  "pubdate:>2015"
  "formats:EPUB and not formats:PDF"
  "languages:eng"
  "identifiers:isbn:true"
  "cover:false"
)

for q in "${queries[@]}"; do
  out=$(python -m cquarry --search "$q" --db "$DB_PATH" --quiet 2>/dev/null)
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "FAILED: $q (exit $rc)"
    exit 1
  fi
  matches=$(printf '%s\n' "$out" | grep -m1 "^Matches:")
  printf '%-58s %s\n' "$q" "${matches:-(no results)}"
done
echo "All syntax tests passed."
