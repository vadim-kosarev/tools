#!/bin/sh
set -e

# cron jobs do not inherit the container environment, so whitelist the
# variables face-indexer.py needs into a file the crontab entry sources.
{
  env | grep -E '^(IMMICH_URL|IMMICH_API_KEY|LOG_LEVEL)=' | sed 's/^/export /'
} > /etc/environment

exec cron -f
