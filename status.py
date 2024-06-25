"""
Entry point for the status dashboard Web service.

Copyright 2017-2020 ICTU
Copyright 2017-2022 Leiden University
Copyright 2017-2023 Leon Helwerda

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

from argparse import ArgumentParser, Namespace
import collections
from configparser import RawConfigParser
from datetime import datetime, timedelta
import glob
from hashlib import md5
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Deque, Dict, List, Mapping, MutableSequence, Optional, \
    Sequence, TextIO, Tuple, Type, Union
import cherrypy
import Pyro4
from gatherer.domain import Source
from gatherer.jenkins import Jenkins
from gatherer.log import Log_Setup
from gatherer.utils import format_date
from gatherer.version_control.review import Review_System
from server.application import Authenticated_Application
from server.bootstrap import Bootstrap
from server.template import Template

Log_Line = Dict[str, Optional[Union[str, int, datetime]]]
Log_Columns = List[str]
Log_Result = Dict[str, Optional[Union[
    str, Path, MutableSequence[Log_Line], Log_Columns, datetime
]]]

Date_Field = Dict[str, Union[str, datetime]]
Agent_Status_Fields = Dict[str, Optional[str]]
Agent_Log_Fields = Dict[str, Optional[Union[str, Log_Result, Date_Field]]]
Data_Sources = Dict[str, Any]

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
    Log parser for newline JSON-delimited streams of logging objects as
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
        r'^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})(?:,(\d{3}))?:([A-Z]+):(.+)'
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
                parts = match.groups()
                date = datetime(self._safe_int(parts[0]),
                                self._safe_int(parts[1]),
                                self._safe_int(parts[2]),
                                self._safe_int(parts[3]),
                                self._safe_int(parts[4]),
                                self._safe_int(parts[5]),
                                self._safe_int(parts[6]))
                level_name = str(parts[7])
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
                    'message': str(parts[8]),
                    'date': date,
                }
                rows.append(row)

        return level, rows

class Status(Authenticated_Application):
    """
    Status dashboard.
    """

    # Common HTML template
    COMMON_HTML = """<!doctype html>
<html>
    <head>
        <meta charset="utf-8">
        <title>{title} - Status</title>
        <link rel="stylesheet" href="css">
    </head>
    <body>
        <h1>Status: {title!h}</h1>
        <div class="content">
            {content}
        </div>
    </body>
</html>"""

    STATUSES = {
        'failure': (0, 'Errors'),
        'warning': (1, 'Problems'),
        'unknown': (2, 'Missing'),
        'success': (3, 'OK')
    }

    PORTS = {
        'agent': 7070,
        'www': 8080
    }

    VERSION_PARSER = re.compile(
        r'(?P<version>[\d.]+)-(?P<branch>.*)-(?P<sha>[0-9a-f]+)'
    )

    GATHERER_SOURCE = 'gatherer'

    def __init__(self, args: Namespace, config: RawConfigParser):
        super().__init__(args, config)
        self.args = args
        self._controller_path = Path(self.args.controller_path)
        self.config = config

        self._jenkins = Jenkins.from_config(self.config)
        self._template = Template()
        self._cache = cherrypy.lib.caching.MemoryCache()

        gatherer_url = f"{self.config.get('gitlab', 'url')}{self.config.get('gitlab', 'repo')}"
        self._source = Source.from_type('gitlab', name=self.GATHERER_SOURCE,
                                        url=gatherer_url)

    def _get_session_html(self) -> str:
        return self._template.format("""
            <div class="logout">
                {user!h} - <a href="logout">Logout</a>
            </div>""", user=cherrypy.session['authenticated'])

    def _get_build_project(self, build: Mapping[str, Any],
                           agents: Sequence[str]) -> Optional[str]:
        for action in build['actions']:
            if 'parameters' in action:
                for parameter in action['parameters']:
                    if parameter['name'] == 'listOfProjects':
                        project = str(parameter['value'])
                        if project in agents:
                            return parameter['value']

                        # Project is not a valid agent name
                        return None

        logging.info('Could not find project parameter in build')
        return None

    @staticmethod
    def _get_build_date(build: Mapping[str, Any]) -> datetime:
        return datetime.fromtimestamp(build['timestamp'] / 1000.0)

    def _collect_jenkins(self, agents: Sequence[str],
                         date_cutoff: Optional[datetime] = None) -> \
            Dict[str, Any]:
        fields = [
            'actions[parameters[name,value]]', 'number', 'result', 'timestamp'
        ]
        query = {'tree': f'builds[{",".join(fields)}]'}
        jobs: Dict[str, Dict[str, Union[str, int, datetime]]] = {}
        job = self._jenkins.get_job(self.config.get('jenkins', 'scrape'),
                                    url=query)

        if 'builds' not in job.data:
            return jobs

        for build in job.data['builds']:
            if build.get('result') is None:
                continue

            agent = self._get_build_project(build, agents)
            if agent is not None and agent not in jobs:
                job_date = self._get_build_date(build)
                result = build['result'].lower()
                job_result = self._handle_date_cutoff(job_date, date_cutoff,
                                                      result)
                jobs[agent] = {
                    'number': build['number'],
                    'result': job_result,
                    'date': job_date
                }

        return jobs

    @staticmethod
    def _handle_date_cutoff(log_date: datetime, date_cutoff: Optional[datetime],
                            result: str = 'success') -> str:
        if date_cutoff is not None and result in ('unknown', 'success') and \
            log_date < date_cutoff:
            return 'warning'

        return result

    @classmethod
    def _read_log(cls, path: Path, filename: str,
                  log_parser: Optional[Type[Log_Parser]],
                  date_cutoff: Optional[datetime]) -> Log_Result:
        result = 'unknown'
        rows: MutableSequence[Log_Line] = []
        columns = None
        if log_parser is not None:
            columns = log_parser.COLUMNS
            with path.open('r', encoding='utf-8') as open_file:
                parser = log_parser(open_file, date_cutoff=date_cutoff)
                level, rows = parser.parse()
                if level > 40:
                    result = 'failure'
                elif level > 30:
                    result = 'warning'
                else:
                    result = 'success'

        log_date = datetime.fromtimestamp(os.path.getmtime(path))
        return {
            'path': path,
            'filename': filename,
            'result': cls._handle_date_cutoff(log_date, date_cutoff, result),
            'rows': rows,
            'columns': columns,
            'date': log_date
        }

    def _find_log(self, agent: str, filename: str,
                  log_parser: Optional[Type[Log_Parser]] = None,
                  date_cutoff: Optional[datetime] = None) -> \
            Optional[Log_Result]:
        path = self._controller_path / agent / filename
        if path.exists():
            return self._read_log(path, filename, log_parser, date_cutoff)

        # Read rotated stale log
        rotated_paths = sorted(glob.glob(f'{path}-*'), reverse=True)
        if rotated_paths:
            return self._read_log(Path(rotated_paths[0]), filename, log_parser,
                                  date_cutoff)

        return None

    def _find_date(self, date: datetime,
                   interval: Optional[Union[timedelta, int]] = None,
                   threshold: Union[timedelta, int] = 0) -> \
            Optional[Date_Field]:
        if interval is None:
            return None
        if not isinstance(interval, timedelta):
            interval = timedelta(seconds=interval)
        if not isinstance(threshold, timedelta):
            threshold = timedelta(seconds=threshold)

        status = self._handle_date_cutoff(date + interval, date - threshold)
        return {
            'result': status,
            'date': date + interval
        }

    def _get_version_url(self, fields: Agent_Status_Fields) -> Optional[str]:
        if self._source.repository_class is None or \
            not issubclass(self._source.repository_class, Review_System):
            return None

        return self._source.repository_class.get_tree_url(self._source,
                                                          fields['sha'])

    def _collect_agent_version(self, fields: Agent_Status_Fields,
                               expensive: bool = True) -> None:
        version = fields.get('version')
        if version is None:
            return

        for tags in version.split(' '):
            component, tag = tags.split('/', 1)
            if component == self.GATHERER_SOURCE:
                match = self.VERSION_PARSER.match(tag)
                if not match:
                    return

                fields.update(match.groupdict())

                if expensive:
                    fields['version_url'] = self._get_version_url(fields)

                return

    def _collect_agent_status(self, agent: str, expensive: bool = True) -> \
            Agent_Status_Fields:
        path = self._controller_path / f"agent-{agent}.json"
        fields: Agent_Status_Fields = {
            'hostname': None,
            'version': None,
            'branch': None,
            'sha': None,
            'version_url': None
        }
        if not path.exists():
            return fields

        with path.open('r', encoding='utf-8') as status_file:
            fields.update(json.load(status_file))

        # Convert agent instance to www and add protocol and port
        if fields['hostname'] is not None:
            instance, domain = fields['hostname'].split('.', 1)
            if instance == 'agent':
                instance = 'www'

            if instance in self.PORTS:
                fields['hostname'] = f'http://{instance}.{domain}:{self.PORTS[instance]}/'

        # Parse version strings
        self._collect_agent_version(fields, expensive=expensive)

        return fields

    def _collect_fields(self, agent: str, sources: Data_Sources,
                        expensive: bool = True,
                        date_cutoff: Optional[datetime] = None) -> \
            Agent_Log_Fields:
        fields: Agent_Log_Fields = {
            'name': agent,
            'agent-log': self._find_log(agent, 'log.json',
                                        log_parser=NDJSON_Parser if expensive else None,
                                        date_cutoff=date_cutoff),
            'export-log': self._find_log(agent, 'export.log',
                                         log_parser=Export_Parser if expensive else None,
                                         date_cutoff=date_cutoff),
            'jenkins-log': sources.get("jobs", {}).get(agent, None),
            'schedule': self._find_date(datetime.now(),
                                        interval=sources.get("schedule", {}).get(agent, None),
                                        threshold=self.args.schedule_threshold)
        }
        fields.update(self._collect_agent_status(agent, expensive=expensive))
        return fields

    def _collect_schedule(self, agents: Sequence[str]) -> Data_Sources:
        schedule = {}
        try:
            gatherer = Pyro4.Proxy("PYRONAME:gros.gatherer")
            for agent in agents:
                try:
                    schedule[agent] = gatherer.get_tracker_schedule(agent)
                except ValueError:
                    schedule[agent] = None
        except Pyro4.errors.NamingError:
            pass

        return schedule

    def _collect_agent(self, agent: str) -> Data_Sources:
        return self._collect_fields(agent, {
            "jobs": self._collect_jenkins([agent]),
            "schedule": self._collect_schedule([agent])
        })

    def _collect(self, data: Optional[Dict[str, Data_Sources]] = None,
                 expensive: bool = True,
                 date_cutoff: Optional[datetime] = None) -> \
            Dict[str, Data_Sources]:
        if data is None:
            data = collections.OrderedDict()

        if data:
            agents = list(data.keys())
        else:
            agents = sorted(
                child.name for child in Path(self.args.agent_path).iterdir()
                if child.is_dir()
            )

        sources = {}
        if expensive:
            sources.update({
                "schedule": self._collect_schedule(agents),
                "jobs": self._collect_jenkins(agents, date_cutoff=date_cutoff)
            })

        for agent in agents:
            data.setdefault(agent, {})
            data[agent].update(self._collect_fields(agent, sources,
                                                    expensive=expensive,
                                                    date_cutoff=date_cutoff))

        return data

    def _set_modified_date(self, data: Dict[str, Data_Sources]) -> None:
        max_date = datetime.min
        for fields in data.values():
            dates = [
                log['date'] for log in fields.values()
                if isinstance(log, dict) and log.get('date') is not None
            ]
            if dates:
                max_date = max(max_date, *dates)

        if max_date > datetime.min:
            max_time = time.mktime(max_date.timetuple())
            http_date = cherrypy.lib.httputil.HTTPDate(max_time)
            cherrypy.response.headers['Cache-Control'] = 'max-age=0, must-revalidate'
            cherrypy.response.headers['Last-Modified'] = http_date

    @cherrypy.expose
    def index(self, page: str = 'list', params: str = '') -> str:
        form = self._template.format("""
            <form class="login" method="post" action="login?page={page!u}&amp;params={params!u}">
                <div><label>
                    Username: <input type="text" name="username" autofocus>
                </label></div>
                <div><label>
                    Password: <input type="password" name="password">
                </label></div>
                <div><button type="submit">Login</button></div>
            </form>""", page=page, params=params)

        return self._template.format(self.COMMON_HTML, title='Login',
                                     content=form)

    @cherrypy.expose
    def css(self) -> str:
        """
        Serve CSS.
        """

        content = """
body {
  font-family: -apple-system, "Segoe UI", "Roboto", "Ubuntu", "Droid Sans", "Helvetica Neue", "Helvetica", "Arial", sans-serif;
}
.content {
    margin: 0 10vw 0 10vw;
    padding: 2rem 2rem 2rem 10rem;
    border: 0.01rem solid #aaa;
    border-radius: 1rem;
    -webkit-box-shadow: 0 2px 3px rgba(10, 10, 10, 0.1), 0 0 0 1px rgba(10, 10, 10, 0.1);
    box-shadow: 0 2px 3px rgba(10, 10, 10, 0.1), 0 0 0 1px rgba(10, 10, 10, 0.1);
    text-align: left;
}
table {
    border: 1px solid #ccc;
    padding: 0.1rem;
    margin: 0 auto 0 auto;
}
th {
    background: #eee;
    border: 1px solid #aaa;
}
td {
    vertical-align: top;
}
a {
    text-decoration: none;
}
a:hover, a:active {
    text-decoration: underline;
}
.logout {
    text-align: right;
    font-size: 90%;
    color: #777;
}
.logout a {
    color: #5555ff;
}
.logout a:hover {
    color: #ff5555;
}
.status-unknown, .level-debug {
    color: #888;
}
.status-warning, .level-warning {
    color: #b80;
}
.status-failure, .level-error, .level-critical, .level-severe {
    color: #b00;
}
.status-success, .level-info {
    color: #090;
}
.log-date {
    white-space: nowrap;
}
.log-message {
    font-family: monospace;
}
.log-traceback {
    white-space: pre-wrap;
    font-family: monospace;
}
"""

        cherrypy.response.headers['Content-Type'] = 'text/css'
        cherrypy.response.headers['ETag'] = md5(content.encode('ISO-8859-1')).hexdigest()

        cherrypy.lib.cptools.validate_etags()

        return content

    def _aggregate_status(self, fields: Data_Sources) -> Tuple[str, str]:
        worst = None
        for log in fields:
            if fields[log] is None:
                # Missing log
                worst = None
                break

            if 'result' in fields[log]:
                result = fields[log]['result']
                if worst is None or self.STATUSES[result][0] < self.STATUSES[worst][0]:
                    worst = result

        if worst is None:
            worst = 'unknown'

        return worst, self.STATUSES[worst][1]

    def _format_date(self, fields: Data_Sources, field_name: str) -> str:
        field = fields[field_name]
        if field is None:
            return '<span class="status-unknown">Missing</span>'

        text = '<span class="status-{status!h}">{date!h}</span>'
        return self._template.format(text,
                                     status=field.get('result', 'unknown'),
                                     date=format_date(field.get('date')))

    def _format_log(self, fields: Data_Sources, log: str) -> str:
        if fields[log] is None:
            return self._format_date(fields, log)

        text = '<a href="log?name={name!u}&amp;log={log!u}">{date}</a>'
        return self._template.format(text, name=fields['name'], log=log,
                                     status=fields[log].get('result', 'unknown'),
                                     date=self._format_date(fields, log))

    def _format_name(self, fields: Data_Sources) -> str:
        if fields['hostname'] is None:
            template = '{name!h}'
        else:
            template = '<a href="{hostname!h}" target="_blank">{name!h}</a>'

        return self._template.format(template, **fields)

    def _format_version(self, fields: Data_Sources) -> str:
        if fields['version'] is None:
            return '<span class="status-unknown">Missing</span>'

        if fields['sha'] is None:
            template = '<span class="ellipsis">{version!h}</span>'
        elif fields['version_url'] is None:
            template = '<span title="{sha!h}">{version!h}</span>'
        else:
            template = '<a href="{version_url!h}" title="{sha!h}">{version!h}</a>'
        return self._template.format(template, **fields)

    @cherrypy.expose
    def list(self) -> str:
        """
        List agents and status overview.
        """

        self.validate_login()

        data: Optional[Dict[str, Data_Sources]] = self._cache.get()
        has_modified = bool(cherrypy.request.headers.get('If-Modified-Since'))
        if has_modified and data is not None:
            # Cache hit with a modified date check, so we could send a 304
            self._set_modified_date(data)
            cherrypy.lib.cptools.validate_since()

        if data is None:
            logging.info('cache miss for %s',
                         cherrypy.url(qs=cherrypy.serving.request.query_string))
            date_cutoff = datetime.now() - timedelta(days=self.args.cutoff_days)
            data = self._collect(data=data, date_cutoff=date_cutoff)
            self._cache.put(data, sys.getsizeof(data))
        if not has_modified:
            # We did not set the last-modified date before
            self._set_modified_date(data)

        rows = []
        row_format = """
        <tr>
            <td>{name}</td>
            <td>{version}</td>
            <td><span class="status-{status!h}">{status_text!h}</span></td>
            <td>{agent_log}</td>
            <td>{export_log}</td>
            <td>{jenkins_log}</td>
            <td>{schedule}
                <button formaction="schedule" name="project" value="{project!h}">Reschedule</button>
            </td>
        </tr>"""
        for fields in data.values():
            status, status_text = self._aggregate_status(fields)
            row = self._template.format(row_format,
                                        project=fields['name'],
                                        name=self._format_name(fields),
                                        version=self._format_version(fields),
                                        status=status,
                                        status_text=status_text,
                                        agent_log=self._format_log(fields,
                                                                   'agent-log'),
                                        export_log=self._format_log(fields,
                                                                    'export-log'),
                                        jenkins_log=self._format_log(fields,
                                                                     'jenkins-log'),
                                        schedule=self._format_date(fields,
                                                                   'schedule'))

            rows.append(row)

        template = """
{session}
<form>
    <input type="hidden" name="page" value="list">
    <table>
        <tr>
            <th>Agent name</th>
            <th>Version</th>
            <th>Status</th>
            <th>Agent log</th>
            <th>Export log</th>
            <th>Scrape log</th>
            <th>Schedule</th>
        </tr>
        {rows}
    </table>
    <button formaction="refresh">Refresh</button>
</form>"""
        content = self._template.format(template,
                                        session=self._get_session_html(),
                                        rows='\n'.join(rows))

        return self._template.format(self.COMMON_HTML, title='Dashboard',
                                     content=content)

    def _format_log_row(self, log_row: Log_Line, columns: Log_Columns) -> str:
        field_format = """\n<td class="{column_class!h}">{text!h}</td>"""
        row = []
        for column in columns:
            column_class = f'log-{column}'
            text = log_row[column]
            if text is None:
                text = ''
            elif column == 'date' and isinstance(text, datetime):
                text = format_date(text)
            elif column == 'level' and isinstance(text, str):
                column_class = f'level-{text.lower()}'

            row.append(self._template.format(field_format,
                                             column_class=column_class,
                                             text=text))

        return f"<tr>{''.join(row)}</tr>"

    def _format_log_table(self, name: str, log: str, fields: Data_Sources) -> \
            str:
        columns = fields[log].get('columns')
        column_heads = []
        column_format = """<th>{name!h}</th>"""
        for column in columns:
            column_heads.append(self._template.format(column_format,
                                                      name=column))

        rows = []
        for log_row in fields[log].get('rows'):
            rows.append(self._format_log_row(log_row, columns))

        template = """
{session}
Physical path:
<a href="log?name={name!u}&amp;log={log!u}&amp;plain=true">{path}</a>,
last changed <span class="status-{status}">{date}</span>
<table>
    <tr>
        {column_heads}
    </tr>
    {rows}
</table>
You can <a href="refresh?page=log&amp;params={params!u}">refresh</a> this data
or return to the <a href="list">list</a>."""

        date_cutoff = datetime.now() - timedelta(days=self.args.cutoff_days)
        status = self._handle_date_cutoff(fields[log].get('date'), date_cutoff,
                                          'success')
        return self._template.format(template,
                                     session=self._get_session_html(),
                                     name=name,
                                     log=log,
                                     path=fields[log].get('path'),
                                     status=status,
                                     date=format_date(fields[log].get('date')),
                                     column_heads='\n'.join(column_heads),
                                     rows='\n'.join(rows),
                                     params=cherrypy.request.query_string)

    @cherrypy.expose
    def schedule(self, page: str = 'list', project: str = '') -> str:
        """
        Reschedule a project.
        """

        self.validate_login()
        self.validate_page(page)

        if project != '':
            try:
                gatherer = Pyro4.Proxy("PYRONAME:gros.gatherer")
                gatherer.update_tracker_schedule(project)
                self._cache.clear()
            except Pyro4.errors.NamingError:
                pass

        raise cherrypy.HTTPRedirect(page)

    @cherrypy.expose
    def refresh(self, page: str = 'list', params: str = '') -> str:
        """
        Clear all caches.
        """

        self.validate_login()

        self._cache.clear()

        # Return back to a valid page after clearing its cache.
        self.validate_page(page)

        if params != '':
            page += f'?{params}'
        raise cherrypy.HTTPRedirect(page)

    @cherrypy.expose
    def log(self, name: str = '', log: str = '', plain: bool = False) -> str:
        """
        Display log file contents.
        """

        self.validate_login()

        if name == '':
            raise cherrypy.NotFound('No agent name selected')
        if log == '':
            raise cherrypy.NotFound('No log selected')

        fields: Optional[Data_Sources] = self._cache.get()
        if fields is None:
            logging.info('cache miss for %s',
                         cherrypy.url(qs=cherrypy.serving.request.query_string))
            fields = self._collect_agent(name)
            self._cache.put(fields, sys.getsizeof(fields))

        self._set_modified_date({name: fields})
        cherrypy.lib.cptools.validate_since()

        if fields.get(log) is None:
            raise cherrypy.NotFound('No log data available')

        if log == 'jenkins-log':
            jenkins = Jenkins.from_config(self.config)
            job = jenkins.get_job(self.config.get('jenkins', 'scrape'))
            build = job.get_build(fields[log].get('number'))
            console = 'consoleText' if plain else 'console'
            raise cherrypy.HTTPRedirect(f'{build.base_url}{console}')

        if fields[log].get('columns') and not plain:
            content = self._format_log_table(name, log, fields)
            return self._template.format(self.COMMON_HTML, title='Log',
                                         content=content)

        path = os.path.abspath(fields[log].get('path'))
        return cherrypy.lib.static.serve_file(path,
                                              content_type='text/plain',
                                              disposition='inline',
                                              name=fields[log].get('filename'))

class Bootstrap_Status(Bootstrap):
    """
    Bootstrapper for the status dashboard.
    """

    @property
    def application_id(self) -> str:
        return 'status_dashboard'

    @property
    def description(self) -> str:
        return 'Run status dashboard WSGI server'

    def add_args(self, parser: ArgumentParser) -> None:
        parser.add_argument('--agent-path', dest='agent_path',
                            default='/agent',
                            help='Path to agent data')
        parser.add_argument('--controller-path', dest='controller_path',
                            default='/controller',
                            help='Path to controller data')
        parser.add_argument('--cutoff-days', dest='cutoff_days', type=int,
                            default=int(self.config.get('schedule', 'days'))+1,
                            help='Days during which logs are fresh')
        parser.add_argument('--schedule-threshold', dest='schedule_threshold',
                            type=int, default=60 * 60,
                            help='Seconds allowed to be overdue on schedule')

    def mount(self, conf: Dict[str, Dict[str, Any]]) -> None:
        cherrypy.tree.mount(Status(self.args, self.config), '/status', conf)

def main() -> None:
    """
    Main entry point.
    """

    bootstrap = Bootstrap_Status()
    bootstrap.bootstrap()

if __name__ == '__main__':
    main()
