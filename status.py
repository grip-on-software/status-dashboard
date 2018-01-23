"""
Entry point for the status dashboard Web service.
"""

from datetime import datetime
from hashlib import md5
import logging
import os
import cherrypy
from gatherer.jenkins import Jenkins
from gatherer.utils import format_date
from server.application import Authenticated_Application
from server.bootstrap import Bootstrap
from server.template import Template

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
        'failure': (0, 'Problems'),
        'unknown': (1, 'Missing'),
        'success': (2, 'OK')
    }

    def __init__(self, args, config):
        super(Status, self).__init__(args, config)
        self.args = args
        self.config = config

        self._template = Template()

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

    def _collect_jenkins(self, agents):
        jenkins = Jenkins.from_config(self.config)

        fields = [
            'actions[parameters[name,value]]', 'number', 'result', 'timestamp'
        ]
        job = jenkins.get_job(self.config.get('jenkins', 'scrape'),
                              url={'tree': 'builds[{}]'.format(','.join(fields))})

        if 'builds' not in job.data:
            return {}

        jobs = {}
        for build in job.data['builds']:
            agent = self._get_build_project(build, agents)
            if agent is not None and agent not in jobs:
                jobs[agent] = {
                    'number': build['number'],
                    'result': build['result'].lower(),
                    'date': datetime.fromtimestamp(build['timestamp'] / 1000.0)
                }

        return jobs

    def _find_log(self, agent, filename):
        path = os.path.join(self.args.controller_path, agent, filename)
        if os.path.exists(path):
            return {
                'path': path,
                'filename': filename,
                'result': 'unknown',
                'date': datetime.fromtimestamp(os.path.getmtime(path))
            }

        return None

    def _collect_fields(self, agent, jobs):
        return {
            'name': agent,
            'agent-log': self._find_log(agent, 'log.json'),
            'export-log': self._find_log(agent, 'export.log'),
            'jenkins-log': jobs.get(agent, None)
        }

    def _collect_agent(self, agent):
        jobs = self._collect_jenkins([agent])
        return self._collect_fields(agent, jobs)

    def _collect(self):
        agents = os.listdir(self.args.agent_path)
        jobs = self._collect_jenkins(agents)
        data = []
        for agent in agents:
            data.append(self._collect_fields(agent, jobs))

        return data

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
a {
    text-decoration: none;
}
a:hover, a:active {
    text-decoration: underline;
}
.status-unknown {
    color: #888;
}
.status-failure {
    color: #b00;
}
.status-success {
    color: #090;
}
"""

        cherrypy.response.headers['Content-Type'] = 'text/css'
        cherrypy.response.headers['ETag'] = md5(content).hexdigest()

        cherrypy.lib.cptools.validate_etags()

        return content

    def _aggregate_status(self, fields):
        worst = None
        for log in fields:
            if 'result' in log and \
                (worst is None or self.STATUSES[log['result']][0] < self.STATUSES[worst][0]):
                worst = log['result']

        if worst is None:
            worst = 'unknown'

        return worst, self.STATUSES[worst][1]

    def _format_log(self, fields, log):
        if fields[log] is None:
            return 'Unknown'

        text = '<a href="log?name={name!u}&amp;log={log!u}" class="status-{status!h}">{date!h}</a>'
        return self._template.format(text, name=fields['name'], log=log,
                                     status=fields[log].get('result', 'unknown'),
                                     date=format_date(fields[log].get('date')))

    @cherrypy.expose
    def list(self):
        """
        List agents and status overview.
        """

        data = self._collect()
        rows = []
        row_format = """
    <tr>
        <td>{name!h}</td>
        <td><span class="status-{status!h}">{status_text!h}</span></td>
        <td>{agent_log}</td>
        <td>{export_log}</td>
        <td>{jenkins_log}</td>
    </tr>"""
        for fields in data:
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

        content = self._template.format("""
<table>
    <tr>
        <th>Agent name</th>
        <th>Status</th>
        <th>Agent log</th>
        <th>Export log</th>
        <th>Scrape log</th>
    </tr>
    {rows}
</table>""", rows='\n'.join(rows))

        return self._template.format(self.COMMON_HTML, title='Dashboard',
                                     content=content)

    @cherrypy.expose
    def log(self, name, log):
        """
        Display log file contents.
        """

        fields = self._collect_agent(name)

        if fields[log] is None:
            raise cherrypy.NotFound('No log data available')

        if log == 'jenkins-log':
            jenkins = Jenkins.from_config(self.config)
            job = jenkins.get_job(self.config.get('jenkins', 'scrape'))
            build = job.get_build(fields[log].get('number'))
            raise cherrypy.HTTPRedirect(build.base_url + 'console')

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
