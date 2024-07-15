"""
Module for parsing different log formats.

Copyright 2017-2020 ICTU
Copyright 2017-2022 Leiden University
Copyright 2017-2024 Leon Helwerda

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import collections
from datetime import datetime
import json
import logging
from pathlib import Path
import re
from typing import Deque, Dict, List, MutableSequence, Optional, TextIO, \
    Tuple, Union
from gatherer.log import Log_Setup

Log_Line = Dict[str, Optional[Union[str, int, datetime]]]
Log_Columns = List[str]
Log_Result = Dict[str, Optional[Union[
    str, Path, MutableSequence[Log_Line], Log_Columns, datetime
]]]

class Log_Parser:
    """
    Generic log parser interface.
    """

    # List of parsed columns. Each log row has the given fields in its result.
    COLUMNS: List[str] = []

    def __init__(self, open_file: TextIO,
                 date_cutoff: Optional[datetime] = None):
        self._open_file = open_file
        self._date_cutoff = date_cutoff

    def parse(self) -> Tuple[int, MutableSequence[Log_Line]]:
        """
        Parse the open file to find log rows and levels.

        The returned values are the highest log level encountered within the
        date cutoff and all parsed row fields (iterable of dictionaries).
        """

        raise NotImplementedError('Must be implemented by subclasses')

    def is_recent(self, date: Optional[datetime]) -> bool:
        """
        Check whether the given date is within the configured cutoff.
        """

        if self._date_cutoff is None or date is None:
            return True

        return self._date_cutoff < date

class NDJSON_Parser(Log_Parser):
    """
    Log parser for newline-delimited streams of JSON logging objects as
    provided by the HTTP logger.
    """

    COLUMNS = [
        'date', 'level', 'filename', 'line', 'module', 'function', 'message',
        'traceback'
    ]

    def parse(self) -> Tuple[int, MutableSequence[Log_Line]]:
        rows: Deque[Log_Line] = collections.deque()
        level = 0
        for line in self._open_file:
            log: Dict[str, Union[str, int]] = json.loads(line)
            if 'created' in log:
                date = datetime.fromtimestamp(float(log['created']))
            else:
                date = None

            message = log.get('message')
            if 'levelno' in log and message is not None and \
                not Log_Setup.is_ignored(str(message)) and self.is_recent(date):
                level = max(level, int(log['levelno']))

            traceback = log.get('exc_text')
            if traceback == 'None':
                traceback = None

            row = {
                'level': log.get('levelname'),
                'filename': log.get('pathname'),
                'line': log.get('lineno'),
                'module': log.get('module'),
                'function': log.get('funcName'),
                'message': message,
                'date': date,
                'traceback': traceback
            }
            rows.appendleft(row)

        return level, rows

class Export_Parser(Log_Parser):
    """
    Log parser for scraper and exporter runs.
    """

    COLUMNS = ['date', 'level', 'message']

    LINE_REGEX = re.compile(
        r'''^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2}) \s
            (?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})
            (?:,(?P<microsecond>\d{3}))?:(?P<level>[A-Z]+):(?P<message>.+)''',
        re.X
    )

    # Java log levels that are not found in Python
    LEVELS = {
        'SEVERE': 40,
        'CONFIG': 10,
        'FINE': 5,
        'FINER': 4,
        'FINEST': 3
    }

    @staticmethod
    def _safe_int(bit: Optional[str]) -> int:
        return int(bit) if bit is not None else 0

    def parse(self) -> Tuple[int, MutableSequence[Log_Line]]:
        rows: MutableSequence[Log_Line] = []
        level = 0
        for line in self._open_file:
            match = self.LINE_REGEX.match(line)
            if match:
                parts = match.groupdict()
                date = datetime(self._safe_int(parts.get('year')),
                                self._safe_int(parts.get('hour')),
                                self._safe_int(parts.get('day')),
                                self._safe_int(parts.get('hour')),
                                self._safe_int(parts.get('minute')),
                                self._safe_int(parts.get('second')),
                                self._safe_int(parts.get('microsecond')))
                level_name = str(parts['level'])
                if level_name in self.LEVELS:
                    level_number = self.LEVELS[level_name]
                else:
                    try:
                        level_number = int(logging.getLevelName(level_name))
                    except ValueError:
                        level_number = 0

                level = max(level, level_number)
                row: Log_Line = {
                    'level': level_name,
                    'message': str(parts['message']),
                    'date': date,
                }
                rows.append(row)

        return level, rows
