"""
Module for bootstrapping the server dashboard Web service.

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

from argparse import ArgumentParser
from typing import Any, Dict
import cherrypy
from server.bootstrap import Bootstrap
from .application import Status

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
