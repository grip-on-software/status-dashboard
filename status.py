"""
Entry point for the status dashboard Web service.
"""

import collections
from datetime import datetime, timedelta
import glob
from hashlib import md5
import json
import logging
import os
import re
import sys
import time
import cherrypy
from gatherer.jenkins import Jenkins
from gatherer.utils import format_date
from server.application import Authenticated_Application
from server.bootstrap import Bootstrap
from server.template import Template

class Log_Parser(object):
    """
    Generic log parser interface.
    """

    # List of parsed columns. Each log row has the given fields in its result.
    COLUMNS = None

    def __init__(self, open_file, date_cutoff=None):
        self._open_file = open_file
        self._date_cutoff = date_cutoff

    def parse(self):
        """
        Parse the open file to find log rows and levels.

        The returned values are the highest log level encountered within the
        date cutoff and all parsed row fields (iterable of dictionaries).
        """

        raise NotImplementedError('Must be implemented by subclasses')

    def is_recent(self, date):
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

    IGNORE_MESSAGES = [
        re.compile(r'^HTTP error 503 for controller status$'),
        re.compile(r'^Controller status: Some parts are not OK: ' + \
            r'Status \'tracker\': Next scheduled gather moment is in'),
        re.compile(r'Cannot retrieve repository source for dummy repository on')
    ]

    def is_ignored(self, message):
        """
        Check whether the given log message is ignored with regard to the
        overall status of the action log.
        """

        return any(ignore.match(message) for ignore in self.IGNORE_MESSAGES)

    def parse(self):
        rows = collections.deque()
        level = 0
        for line in self._open_file:
            log = json.loads(line)
            if 'created' in log:
                date = datetime.fromtimestamp(float(log.get('created')))
            else:
                date = None

            message = log.get('message')
            if 'levelno' in log and not self.is_ignored(message) and \
                self.is_recent(date):
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

    def parse(self):
        rows = []
        level = 0
        for line in self._open_file:
            match = self.LINE_REGEX.match(line)
            if match:
                parts = match.groups()
                date = datetime(*[int(bit) if bit is not None else 0 for bit in parts[:7]])
                level_name = parts[7]
                if level_name in self.LEVELS:
                    level_number = self.LEVELS[level_name]
                else:
                    try:
                        level_number = int(logging.getLevelName(level_name))
                    except ValueError:
                        level_number = 0

                level = max(level, level_number)
                row = {
                    'level': level_name,
                    'message': parts[8],
                    'date': date,
                }
                rows.append(row)

        return level, rows

class Status(Authenticated_Application):
    # pylint: disable=no-self-use
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

    def __init__(self, args, config):
        super(Status, self).__init__(args, config)
        self.args = args
        self.config = config

        self._jenkins = Jenkins.from_config(self.config)
        self._template = Template()
        self._cache = cherrypy.lib.caching.MemoryCache()

    def _get_session_html(self):
        return self._template.format("""
            <div class="logout">
                {user!h} - <a href="logout">Logout</a>
            </div>""", user=cherrypy.session['authenticated'])

    def _get_build_project(self, build, agents):
        for action in build['actions']:
            if 'parameters' in action:
                for parameter in action['parameters']:
                    if parameter['name'] == 'listOfProjects':
                        if parameter['value'] in agents:
                            return parameter['value']

                        # Project is not a valid agent name
                        return None

        logging.info('Could not find project parameter in build')
        return None

    @staticmethod
    def _get_build_date(build):
        return datetime.fromtimestamp(build['timestamp'] / 1000.0)

    def _collect_jenkins(self, agents, date_cutoff=None):
        fields = [
            'actions[parameters[name,value]]', 'number', 'result', 'timestamp'
        ]
        query = {'tree': 'builds[{}]'.format(','.join(fields))}
        job = self._jenkins.get_job(self.config.get('jenkins', 'scrape'),
                                    url=query)

        if 'builds' not in job.data:
            return {}

        jobs = {}
        for build in job.data['builds']:
            agent = self._get_build_project(build, agents)
            if agent is not None and agent not in jobs:
                job_date = self._get_build_date(build)
                job_result = self._handle_date_cutoff(job_date, date_cutoff,
                                                      build['result'].lower())
                jobs[agent] = {
                    'number': build['number'],
                    'result': job_result,
                    'date': job_date
                }

        return jobs

    @staticmethod
    def _handle_date_cutoff(log_date, date_cutoff, result):
        if date_cutoff is not None and result in ('unknown', 'success') and \
            log_date < date_cutoff:
            return 'warning'

        return result

    @classmethod
    def _read_log(cls, path, filename, log_parser, date_cutoff):
        result = 'unknown'
        rows = []
        columns = None
        if log_parser is not None:
            columns = log_parser.COLUMNS
            with open(path) as open_file:
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

    def _find_log(self, agent, filename, log_parser=None, date_cutoff=None):
        path = os.path.join(self.args.controller_path, agent, filename)
        if os.path.exists(path):
            return self._read_log(path, filename, log_parser, date_cutoff)
        else:
            # Read rotated stale log
            rotated_paths = sorted(glob.glob(path + '-*'), reverse=True)
            if rotated_paths:
                return self._read_log(rotated_paths[0], filename, log_parser,
                                      date_cutoff)

        return None

    def _collect_fields(self, agent, jobs, expensive=True, date_cutoff=None):
        return {
            'name': agent,
            'agent-log': self._find_log(agent, 'log.json',
                                        log_parser=NDJSON_Parser if expensive else None,
                                        date_cutoff=date_cutoff),
            'export-log': self._find_log(agent, 'export.log',
                                         log_parser=Export_Parser if expensive else None,
                                         date_cutoff=date_cutoff),
            'jenkins-log': jobs.get(agent, None)
        }

    def _collect_agent(self, agent):
        jobs = self._collect_jenkins([agent])
        return self._collect_fields(agent, jobs)

    def _collect(self, data=None, expensive=True, date_cutoff=None):
        if data is None:
            data = collections.OrderedDict()

        if data:
            agents = data.keys()
        else:
            agents = sorted(os.listdir(self.args.agent_path))

        if expensive:
            jobs = self._collect_jenkins(agents, date_cutoff=date_cutoff)
        else:
            jobs = {}

        for agent in agents:
            data.setdefault(agent, {})
            data[agent].update(self._collect_fields(agent, jobs,
                                                    expensive=expensive,
                                                    date_cutoff=date_cutoff))

        return data

    def _get_jenkins_modified_date(self):
        job = self._jenkins.get_job(self.config.get('jenkins', 'scrape'))
        build = job.last_build
        build.query = {'tree': 'timestamp'}
        return self._get_build_date(build.data)

    def _set_modified_date(self, data, date=None):
        if date is not None:
            max_date = date
        else:
            max_date = datetime.min

        for fields in data.values():
            dates = [
                log['date'] for log in fields.values()
                if log is not None and 'date' in log
            ]
            if dates:
                max_date = max(max_date, max(dates))

        if max_date > datetime.min:
            max_time = time.mktime(max_date.timetuple())
            http_date = cherrypy.lib.httputil.HTTPDate(max_time)
            cherrypy.response.headers['Cache-Control'] = 'max-age=0, must-revalidate'
            cherrypy.response.headers['Last-Modified'] = http_date

    @cherrypy.expose
    def index(self, page='list', params=''):
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
    def css(self):
        """
        Serve CSS.
        """

        content = """
body {
  font-family: -apple-system, "Segoe UI", "Roboto", "Ubuntu", "Droid Sans", "Helvetica Neue", "Helvetica", "Arial", sans-serif;
}
.content {
    margin: auto 20rem auto 20rem;
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
        cherrypy.response.headers['ETag'] = md5(content).hexdigest()

        cherrypy.lib.cptools.validate_etags()

        return content

    def _aggregate_status(self, fields):
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

    def _format_log(self, fields, log):
        if fields[log] is None:
            return '<span class="status-unknown">Missing</span>'

        text = '<a href="log?name={name!u}&amp;log={log!u}" class="status-{status!h}">{date!h}</a>'
        return self._template.format(text, name=fields['name'], log=log,
                                     status=fields[log].get('result', 'unknown'),
                                     date=format_date(fields[log].get('date')))

    @cherrypy.expose
    def list(self):
        """
        List agents and status overview.
        """

        self.validate_login()

        data = self._cache.get()
        cache_miss = data is None
        has_modified_since = cherrypy.request.headers.get('If-Modified-Since')
        if has_modified_since:
            if cache_miss:
                data = self._collect(expensive=False)
                jenkins_date = self._get_jenkins_modified_date()
            else:
                jenkins_date = None

            self._set_modified_date(data, date=jenkins_date)
            cherrypy.lib.cptools.validate_since()

        if cache_miss:
            logging.info('cache miss for %s',
                         cherrypy.url(qs=cherrypy.serving.request.query_string))
            date_cutoff = datetime.now() - timedelta(days=self.args.cutoff_days)
            data = self._collect(data=data, date_cutoff=date_cutoff)
            self._cache.put(data, sys.getsizeof(data))
        if not has_modified_since:
            self._set_modified_date(data)

        rows = []
        row_format = """
    <tr>
        <td>{name!h}</td>
        <td><span class="status-{status!h}">{status_text!h}</span></td>
        <td>{agent_log}</td>
        <td>{export_log}</td>
        <td>{jenkins_log}</td>
    </tr>"""
        for fields in data.values():
            status, status_text = self._aggregate_status(fields)
            row = self._template.format(row_format,
                                        status=status,
                                        status_text=status_text,
                                        agent_log=self._format_log(fields,
                                                                   'agent-log'),
                                        export_log=self._format_log(fields,
                                                                    'export-log'),
                                        jenkins_log=self._format_log(fields,
                                                                     'jenkins-log'),
                                        **fields)

            rows.append(row)

        template = """
{session}
<table>
    <tr>
        <th>Agent name</th>
        <th>Status</th>
        <th>Agent log</th>
        <th>Export log</th>
        <th>Scrape log</th>
    </tr>
    {rows}
</table>
<form>
    <button formaction="refresh" name="page" value="list">Refresh</button>
</form>"""
        content = self._template.format(template,
                                        session=self._get_session_html(),
                                        rows='\n'.join(rows))

        return self._template.format(self.COMMON_HTML, title='Dashboard',
                                     content=content)

    def _format_log_row(self, log_row, columns):
        field_format = """<td class="{column_class!h}">{text!h}</td>"""
        row = []
        for column in columns:
            column_class = 'log-{}'.format(column)
            text = log_row[column]
            if text is None:
                text = ''
            elif column == 'date':
                text = format_date(text)
            elif column == 'level':
                column_class = 'level-{}'.format(text.lower())

            row.append(self._template.format(field_format,
                                             column_class=column_class,
                                             text=text))

        return "<tr>{row}</tr>".format(row='\n'.join(row))

    def _format_log_table(self, name, log, fields):
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
    def refresh(self, page='list', params=''):
        """
        Clear all caches.
        """

        self.validate_login()

        self._cache.clear()

        # Return back to a valid page after clearing its cache.
        self.validate_page(page)

        if params != '':
            page += '?' + params
        raise cherrypy.HTTPRedirect(page)

    @cherrypy.expose
    def log(self, name, log, plain=False):
        """
        Display log file contents.
        """

        self.validate_login()

        fields = self._cache.get()
        if fields is None:
            logging.info('cache miss for %s',
                         cherrypy.url(qs=cherrypy.serving.request.query_string))
            fields = self._collect_agent(name)
            self._cache.put(fields, sys.getsizeof(fields))

        self._set_modified_date({name: fields})
        cherrypy.lib.cptools.validate_since()

        if fields[log] is None:
            raise cherrypy.NotFound('No log data available')

        if log == 'jenkins-log':
            jenkins = Jenkins.from_config(self.config)
            job = jenkins.get_job(self.config.get('jenkins', 'scrape'))
            build = job.get_build(fields[log].get('number'))
            console = 'consoleText' if plain else 'console'
            raise cherrypy.HTTPRedirect(build.base_url + console)

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
    def description(self):
        return 'Run deployment WSGI server'

    def add_args(self, parser):
        parser.add_argument('--agent-path', dest='agent_path',
                            default='/agent',
                            help='Path to agent data')
        parser.add_argument('--controller-path', dest='controller_path',
                            default='/controller',
                            help='Path to controller data')
        parser.add_argument('--cutoff-days', dest='cutoff_days', type=int,
                            default=int(self.config.get('schedule', 'days'))+1,
                            help='Days during which logs are fresh')

    def mount(self, conf):
        cherrypy.tree.mount(Status(self.args, self.config), '/status', conf)

def main():
    """
    Main entry point.
    """

    bootstrap = Bootstrap_Status()
    bootstrap.bootstrap()

if __name__ == '__main__':
    main()
