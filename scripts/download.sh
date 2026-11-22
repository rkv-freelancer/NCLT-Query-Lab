#!/usr/bin/env bash
# Manual download + extract NCLT sensor data 
# Usage: download.sh [DATE ...]  (no args = all 27)
set -euo pipefail

BASE="https://s3.us-east-2.amazonaws.com/nclt.perl.engin.umich.edu/sensor_data"
ALL_DATES=(2012-01-08 2012-01-15 2012-01-22 2012-02-02 2012-02-04 2012-02-05
           2012-02-12 2012-02-18 2012-02-19 2012-03-17 2012-03-25 2012-03-31
           2012-04-29 2012-05-11 2012-05-26 2012-06-15 2012-08-04 2012-08-20
           2012-09-28 2012-10-28 2012-11-04 2012-11-16 2012-11-17 2012-12-01
           2013-01-10 2013-02-23 2013-04-05)

RAW=data/raw
OUT=data/sensor_data
mkdir -p "$RAW" "$OUT"

# Check if the user selects single or multiple sessions
if [[ $# -gt 0 ]]; then dates=("$@"); else dates=("${ALL_DATES[@]}"); fi

for d in "${dates[@]}"; do
  archive="$RAW/${d}_sen.tar.gz"
  if [[ -f "$archive.done" ]]; then echo "skip $d (done)"; continue; fi
  echo "==> $d"
  
  # -C - resumes a partial file
  curl -fL --retry 3 -C - -o "$archive" "$BASE/${d}_sen.tar.gz"  
  
  # fail fast on a truncated download
  gzip -t "$archive"                    
  tar -xzf "$archive" -C "$OUT"
  
  # marker makes re-runs idempotent
  touch "$archive.done"                 
done