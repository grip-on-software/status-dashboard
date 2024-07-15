GROS data gathering agent status
================================

[![PyPI](https://img.shields.io/pypi/v/gros-status.svg)](https://pypi.python.org/pypi/gros-status)
[![Build 
status](https://github.com/grip-on-software/status-dashboard/actions/workflows/status-tests.yml/badge.svg)](https://github.com/grip-on-software/status-dashboard/actions/workflows/status-tests.yml)
[![Coverage 
Status](https://coveralls.io/repos/github/grip-on-software/status-dashboard/badge.svg?branch=master)](https://coveralls.io/github/grip-on-software/status-dashboard?branch=master)
[![Quality Gate
Status](https://sonarcloud.io/api/project_badges/measure?project=grip-on-software_status-dashboard&metric=alert_status)](https://sonarcloud.io/project/overview?id=grip-on-software_status-dashboard)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.12533335.svg)](https://doi.org/10.5281/zenodo.12533335)

This repository contains a Web application that provides an overview of the 
data gathering agents and importer jobs, based on log parsing.

## Installation

The latest version of GROS status dashboard and its dependencies can be 
installed using `pip install gros-status`.

Another option is to build the module from this repository, which allows using 
the most recent development code. Run `make setup` to install the dependencies. 
The status dashboard itself may then be installed with `make install`, which 
places the package in your current environment. We recommend using a virtual 
environment during development.

## Running

Simply start the application using `gros-status`. Use command-line arguments 
(displayed with `gros-status --help`) and/or a data-gathering `settings.cfg` 
file (specifically the sections `ldap`, `deploy`, `jenkins` and `schedule` 
influence this application's behavior - see the [gros-gatherer documentation on 
configuration](https://gros.liacs.nl/data-gathering/configuration.html) for 
details).

You can also configure the application as a systemd service such that it can 
run headless under a separate user, using a virtualenv setup. See the 
`gros-status.service` file in this repository for a possible setup.

## Development and testing

To run tests, first install the test dependencies with `make setup_test` which 
also installs all dependencies for the server framework. Then `make coverage` 
provides test results in the output and in XML versions compatible with, e.g., 
JUnit and SonarQube available in the `test-reports/` directory. If you do not 
need XML outputs, then run `make test` to just report on test successes and 
failures or `make cover` to also have the terminal report on hits and misses in 
statements and branches.

[GitHub Actions](https://github.com/grip-on-software/status-dashboard/actions) 
is used to run the unit tests and report on coverage on commits and pull 
requests. This includes quality gate scans tracked by 
[SonarCloud](https://sonarcloud.io/project/overview?id=grip-on-software_status-dashboard) 
and [Coveralls](https://coveralls.io/github/grip-on-software/status-dashboard) 
for coverage history.

The Python module conforms to code style and typing standards which can be 
checked using Pylint with `make pylint` and mypy with `make mypy`, after 
installing the pylint and mypy dependencies using `make setup_analysis`; typing 
reports are XML formats compatible with JUnit and SonarQube placed in the 
`mypy-report/` directory. To also receive the HTML report, use `make mypy_html` 
instead.

We publish releases to [PyPI](https://pypi.org/project/gros-status/) using 
`make setup_release` to install dependencies and `make release` which performs 
multiple checks: unit tests, typing, lint and version number consistency. The 
release files are also published on 
[GitHub](https://github.com/grip-on-software/status-dashboard/releases) and 
from there are archived on 
[Zenodo](https://zenodo.org/doi/10.5281/zenodo.12533334). Noteworthy changes to 
the module are added to the [changelog](CHANGELOG.md).

## License

GROS status dashboard is licensed under the Apache 2.0 License.
