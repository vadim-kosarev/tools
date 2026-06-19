#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Organize files by date extracted from filename into YYYY-MM folders."""

import re
import logging
from pathlib import Path
from datetime import datetime
import sys

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

current_dir = Path('.').resolve()
logger.info(f"Working directory: {current_dir}")

if not Path('.organize').exists():
    logger.warning("Marker file '.organize' not found in current directory. Skipping.")
    sys.exit(0)

logger.info("Marker file '.organize' found. Starting organization...")

# Pattern to match date in filename: YYYY-MM-DD or YYYY.MM.DD (with optional time)
# Formats supported:
# - CallRec: _YYYY-MM-DD_HH-MM-SS (date and time)
# - SmartRecorder: _YYYY.MM.DD-NN (date only, no time)
# Uses backreference \2 to ensure same separator throughout
# This avoids matching dates inside phone numbers like [+7 495 809-31-78]
date_pattern = re.compile(r'_(\d{4})([-._])(\d{2})\2(\d{2})(?:_\d{2}\2\d{2}\2\d{2})?')


for file in Path('.').rglob('*'):
    if not file.is_file():
        continue

    if m := date_pattern.search(file.name):
        year, _, month, day = m.groups()  # _ is the separator (dash/dot/underscore)
        year_int = int(year)
        month_int = int(month)
        day_int = int(day)
        current_year = datetime.now().year

        # Validation checks with detailed logging
        if year_int < 1981:
            logger.info(f"Skipped {file.name} (year {year_int} < 1981)")
            continue
        if year_int > current_year:
            logger.info(f"Skipped {file.name} (year {year_int} > {current_year})")
            continue
        if month_int < 1 or month_int > 12:
            logger.info(f"Skipped {file.name} (month {month_int} invalid)")
            continue

        folder = Path(f"{year}-{month}")  # month is already zero-padded from regex
        if file.parent == folder:
            logger.debug(f"Already organized {file.name}")
            continue
        folder.mkdir(exist_ok=True)
        file.rename(folder / file.name)
        logger.info(f"Moved {file.name} to {folder}/")
    else:
        logger.debug(f"No date found in {file.name}")
