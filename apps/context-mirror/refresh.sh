#!/usr/bin/env bash
# Re-mirror tag.schatt.me into ./content. Run from this directory.
set -euo pipefail
cd "$(dirname "$0")"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

wget --quiet --mirror --no-host-directories --adjust-extension \
  --convert-links --page-requisites --no-parent \
  --reject-regex '\?' \
  --user-agent='Mozilla/5.0 (mirror)' \
  --directory-prefix="$WORK" \
  https://tag.schatt.me/

rm -rf content
mv "$WORK" content

echo "Mirrored $(find content -type f | wc -l) files, $(du -sh content | cut -f1)."
