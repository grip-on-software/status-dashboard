"""
Tests for parsing different log formats.

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

from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from typing import Dict, Optional, Union
import unittest
import dateutil.tz
from status.parser import NDJSON_Parser, Export_Parser, Log_Line

def _local_date(row: Dict[str, Optional[Union[str, int, float]]]) -> Log_Line:
    output_row: Log_Line = {
        key: value for key, value in row.items() if not isinstance(value, float)
    }
    date = row.get('date')
    if isinstance(date, float):
        output_row['date'] = datetime.fromtimestamp(date)

    return output_row

def _utc_date(row: Dict[str, Optional[Union[str, int, float]]]) -> Log_Line:
    output_row: Log_Line = {
        key: value for key, value in row.items() if not isinstance(value, float)
    }
    date = row.get('date')
    if isinstance(date, float):
        # The expected result is for the UTC date, but the log is in the local
        # timezone, so convert "back" to be able to compare the dates.
        local_date = datetime.fromtimestamp(date, tz=dateutil.tz.tzlocal())
        utc_date = local_date.astimezone(timezone.utc)
        # Make timezone-naive for comparison
        output_row['date'] = utc_date.replace(tzinfo=None)

    return output_row

class NDJSONParserTest(unittest.TestCase):
    """
    Tests for log parser of new-line delimited JSON logging data.
    """

    def test_parse(self) -> None:
        """
        Test parsing an open file.
        """

        controller_path = Path('test/sample/controller')
        result_path = Path('test/sample/result')
        for project, expected_level in (('Proj8', 0), ('TEST', 30)):
            with self.subTest(project=project):
                input_path = Path(controller_path, project, 'log.json')
                output_path = Path(result_path, project,  'ndjson.json')
                with input_path.open('r', encoding='utf-8') as open_file:
                    parser = NDJSON_Parser(open_file)
                    level, rows = parser.parse()
                    self.assertEqual(level, expected_level,
                                     msg=f'{input_path} {level} != {expected_level}')
                    with output_path.open('r', encoding='utf-8') as output_file:
                        expected_rows = json.load(output_file,
                                                  object_hook=_local_date)
                        self.assertEqual(list(rows), expected_rows)

    def test_is_recent(self) -> None:
        """
        Test checking whether a date is within the configured cutoff.
        """

        open_file = StringIO()
        optional = NDJSON_Parser(open_file)
        self.assertTrue(optional.is_recent(None))
        self.assertTrue(optional.is_recent(datetime(2024, 6, 25, 12, 34, 56)))

        cutoff = NDJSON_Parser(open_file,
                               date_cutoff=datetime(2024, 6, 25, 12, 0, 0))
        self.assertTrue(cutoff.is_recent(datetime(2024, 6, 25, 13, 57, 9)))
        self.assertFalse(cutoff.is_recent(datetime(2024, 6, 25, 11, 11, 11)))

class ExportParserTest(unittest.TestCase):
    """
    Tests for log parser of scraper and exporter runs.
    """

    def test_parse(self) -> None:
        """
        Test parsing an open file.
        """

        controller_path = Path('test/sample/controller')
        result_path = Path('test/sample/result')
        for project, log, expected_level in (('Proj8', 'export.log', 30),
                                             ('TEST', 'export.log-1', 50)):
            with self.subTest(project=project):
                input_path = Path(controller_path, project, log)
                output_path = Path(result_path, project,  'export.json')
                with input_path.open('r', encoding='utf-8') as open_file:
                    parser = Export_Parser(open_file)
                    level, rows = parser.parse()
                    self.assertEqual(level, expected_level,
                                     msg=f'{input_path} {level} != {expected_level}')
                    with output_path.open('r', encoding='utf-8') as output_file:
                        expected_rows = json.load(output_file,
                                                  object_hook=_utc_date)
                        self.assertEqual(list(rows), expected_rows)

        with self.subTest(project='EX'):
            parser = Export_Parser(StringIO('Invalid line'))
            self.assertEqual(parser.parse(), (0, []))
