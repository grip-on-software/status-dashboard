"""
Tests for status dashboard Web application.

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

from argparse import Namespace
from configparser import RawConfigParser
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from unittest.mock import patch, MagicMock
import cherrypy
from cherrypy.test import helper
import dateutil.tz
import Pyro4
import requests_mock
from status import Status

class StatusTest(helper.CPWebCase):
    """
    Tests for status dashboard.
    """

    @staticmethod
    def setup_server() -> None:
        """"
        Set up the application server.
        """

        args = Namespace()
        args.auth = 'open'
        args.debug = True
        args.agent_path = 'test/sample/agent'
        args.controller_path = 'test/sample/controller'
        args.cutoff_days = 2
        args.schedule_threshold = 60 * 60

        jenkins_host = 'http+mock://jenkins.test/'
        config = RawConfigParser()
        config['jenkins'] = {}
        config['jenkins']['host'] = jenkins_host
        config['jenkins']['username'] = '-'
        config['jenkins']['password'] = '-'
        config['jenkins']['verify'] = '0'
        config['jenkins']['scrape'] = 'scrape-projects'

        server = Status(args, config)

        # Set up Jenkins API adapter with crumb issuer and job route
        adapter = requests_mock.Adapter()
        adapter.register_uri('GET', '/crumbIssuer/api/json', status_code=404)
        adapter.register_uri('GET', '/job/scrape-projects/api/json', json={
            'builds': [
                {
                    'number':  1,
                    'result': 'FAILURE',
                    'timestamp': 1694464260366,
                    'actions': [
                        {},
                        {
                            'parameters': [
                                {
                                    'name': 'logLevel',
                                    'value': 'INFO'
                                },
                                {
                                    'name': 'listOfProjects',
                                    'value': 'TEST'
                                }
                            ]
                        }
                    ]
                },
                {
                    'number': 2,
                    'result': 'SUCCESS',
                    'timestamp': 1694464260367,
                    'actions': [
                        {
                            'parameters': [
                                {
                                    'name': 'listOfProjects',
                                    'value': 'CUSTOM,BUILD,FOR,MANY,PROJECTS'
                                }
                            ]
                        }
                    ]
                },
                {
                    'number': 3,
                    'result': 'SUCCESS',
                    'timestamp': 1694464260368,
                    'actions': []
                },
                {}
            ]
        })
        server.jenkins.mount(adapter, prefix=jenkins_host)

        cherrypy.tree.mount(server, '/status', {
            '/': {
                'tools.sessions.on': True,
                'tools.sessions.httponly': True,
            }
        })

    def test_index(self) -> None:
        """
        Test the index page.
        """

        self.getPage("/status/index")
        self.assertStatus('200 OK')
        self.assertInBody('action="login?page=list&amp;params=')

        self.getPage("/status/index?page=log&params=name=TEST%26log=agent-log")
        self.assertStatus('200 OK')
        self.assertInBody('action="login?page=log&amp;params=name%3DTEST%26log%3Dagent-log')

    def test_css(self) -> None:
        """
        Test serving CSS.
        """

        self.getPage("/status/css")
        self.assertStatus('200 OK')
        content_type = self.assertHeader('Content-Type')
        self.assertIn('text/css', content_type)
        etag = self.assertHeader('ETag')

        self.getPage("/status/css", headers=[('If-None-Match', etag)])
        self.assertStatus('304 Not Modified')

        self.getPage("/status/css", headers=[('If-None-Match', 'other')])
        self.assertStatus('200 OK')

    def test_list(self) -> None:
        """
        Test the list page.
        """

        self.getPage("/status/list")
        self.assertIn('/status/index?page=list', self.assertHeader('Location'))

        self.getPage("/status/login", method="POST",
                     body='username=foo&password=bar')
        header = self.assertHeader('Set-Cookie')
        cookie = SimpleCookie()
        cookie.load(header)

        session_id = cookie["session_id"].value
        self.getPage("/status/list",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertStatus('200 OK')
        modified = self.assertHeader('Last-Modified')
        self.assertInBody('<table>')
        url = 'http://www.gros-data-gathering-agent.test:8080/'
        self.assertInBody(f'<td><a href="{url}" target="_blank">Proj8</a></td>')
        self.assertInBody(f'<td><a href="{url}" target="_blank">TEST</a></td>')
        self.assertInBody('<td>EX</td>')

        self.getPage("/status/list",
                     headers=[('Cookie', f'session_id={session_id}'),
                              ('If-Modified-Since', modified)])
        self.assertStatus('304 Not Modified')

    @patch('Pyro4.Proxy')
    def test_schedule(self, proxy: MagicMock) -> None:
        """
        Test rescheduling a project.
        """

        self.getPage("/status/schedule?project=TEST")
        self.assertIn('/status/index?page=schedule',
                      self.assertHeader('Location'))
        proxy.assert_not_called()

        self.getPage("/status/login", method="POST",
                     body='username=foo&password=bar')
        header = self.assertHeader('Set-Cookie')
        cookie = SimpleCookie()
        cookie.load(header)

        session_id = cookie["session_id"].value
        self.getPage("/status/schedule",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertIn('/status/list', self.assertHeader('Location'))
        proxy.assert_not_called()

        self.getPage("/status/schedule?page=log&project=TEST",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertIn('/status/log', self.assertHeader('Location'))
        proxy.assert_called_once()
        proxy.return_value.update_tracker_schedule.assert_called_with('TEST')

        # Errors are handled silently
        proxy.configure_mock(side_effect=Pyro4.errors.NamingError)
        self.getPage("/status/schedule?project=TEST",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertIn('/status/list', self.assertHeader('Location'))

    def test_refresh(self) -> None:
        """
        Test clearing all caches.
        """

        self.getPage("/status/refresh")
        self.assertIn('/status/index?page=refresh',
                      self.assertHeader('Location'))

        self.getPage("/status/login", method="POST",
                     body='username=foo&password=bar')
        header = self.assertHeader('Set-Cookie')
        cookie = SimpleCookie()
        cookie.load(header)

        session_id = cookie["session_id"].value
        self.getPage("/status/refresh",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertIn('/status/list', self.assertHeader('Location'))

        self.getPage("/status/refresh?page=log&params=name=TEST%26log=agent-log",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertIn('/status/log?name=TEST&log=agent-log',
                      self.assertHeader('Location'))

    def test_log(self) -> None:
        """
        Test the log page.
        """

        self.getPage("/status/log?name=TEST&log=agent-log")
        self.assertIn('/status/index?page=log&params=name%3DTEST%26log%3Dagent-log',
                      self.assertHeader('Location'))

        self.getPage("/status/login", method="POST",
                     body='username=foo&password=bar')
        header = self.assertHeader('Set-Cookie')
        cookie = SimpleCookie()
        cookie.load(header)

        session_id = cookie["session_id"].value
        self.getPage("/status/log?log=agent-log",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertStatus('404 Not Found')
        self.assertInBody('No agent name selected')

        self.getPage("/status/log?name=TEST",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertStatus('404 Not Found')
        self.assertInBody('No log selected')

        # Test retrieving an unknown log.
        self.getPage("/status/log?name=TEST&log=unknown-log",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertStatus('404 Not Found')
        self.assertInBody('No log data available')

        # Test retrieving a known agent and log.
        self.getPage("/status/log?name=TEST&log=agent-log",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertStatus('200 OK')
        modified = self.assertHeader('Last-Modified')
        path = 'test/sample/controller/TEST/log.json'
        self.assertInBody(f'<a href="log?name=TEST&amp;log=agent-log&amp;plain=true">{path}</a>')
        self.assertInBody('<table>')
        # Log date is converted to local date.
        log_date = datetime(2018, 1, 24, 1, 0, 29, tzinfo=timezone.utc)
        local_date = log_date.astimezone(dateutil.tz.tzlocal())
        formatted_date = local_date.strftime('%Y-%m-%d %H:%M:%S')
        self.assertInBody(f'<td class="log-date">{formatted_date}</td>')
        self.assertInBody('<td class="level-warning">WARNING</td>')
        message = 'Could not load sprint data, no sprint matching possible.'
        self.assertInBody(f'<td class="log-message">{message}</td>')

        # Test retrieving the log again with a modified date check.
        self.getPage("/status/log?name=TEST&log=agent-log",
                     headers=[('Cookie', f'session_id={session_id}'),
                              ('If-Modified-Since', modified)])
        self.assertStatus('304 Not Modified')

        # Test the plain output.
        self.getPage("/status/log?name=TEST&log=agent-log&plain=true",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertStatus('200 OK')
        self.assertIn('text/plain', self.assertHeader('Content-Type'))
        self.assertHeader('Content-Disposition', 'inline; filename="log.json"')
        with open(path, 'r', encoding='utf-8') as log_file:
            self.assertBody(log_file.read())

        # Test redirecting to Jenkins scrape build log.
        self.getPage("/status/log?name=TEST&log=jenkins-log",
                     headers=[('Cookie', f'session_id={session_id}')])
        self.assertHeader('Location',
                          'http+mock://jenkins.test/job/scrape-projects/1/console')
