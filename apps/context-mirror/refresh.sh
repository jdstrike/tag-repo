#!/usr/bin/env bash
# Re-mirror tag.schatt.me into ./content. Run from this directory.
set -euo pipefail
cd "$(dirname "$0")"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Mirror everything reachable from /. We accept query strings (no --reject-regex)
# because the only ones in use are cache-busters like ?v=6.4.0, and we want the
# referenced files. Post-process strips the query from filenames.
wget --quiet --mirror --no-host-directories --adjust-extension \
  --convert-links --page-requisites --no-parent \
  --user-agent='Mozilla/5.0 (mirror)' \
  --directory-prefix="$WORK" \
  https://tag.schatt.me/

# Some assets are referenced only with absolute upstream URLs and a query string
# (e.g. site.css?v=6.4.0); wget saves them as 'site.css?v=6.4.0' on disk, which
# Caddy cannot serve because the URL parser strips the query. Strip the query
# from filenames, then rewrite remaining absolute upstream CSS refs to absolute
# local paths.
find "$WORK" -name '*\?*' -type f | while read -r f; do
  newname="${f%%\?*}"
  [ "$f" != "$newname" ] && mv -n "$f" "$newname"
done

# Catch the one CSS that always slips through: site.css under /assets/css/.
mkdir -p "$WORK/assets/css"
[ -f "$WORK/assets/css/site.css" ] || \
  curl -sk -o "$WORK/assets/css/site.css" 'https://tag.schatt.me/assets/css/site.css'

# Rewrite remaining absolute CSS hrefs to absolute local paths.
find "$WORK" \( -name '*.html' -o -name '*.css' \) -type f -print0 | \
  xargs -0 sed -i \
    -e 's|https://tag\.schatt\.me/assets/css/site\.css?v=[0-9.]*|/assets/css/site.css|g' \
    -e 's|https://tag\.schatt\.me/assets/css/site\.css|/assets/css/site.css|g'

rm -rf content
mv "$WORK" content
trap - EXIT

echo "Mirrored $(find content -type f | wc -l) files, $(du -sh content | cut -f1)."
