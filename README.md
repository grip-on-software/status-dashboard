GROS data gathering agent status
================================

This repository contains a Web application that provides an overview of the 
data gathering agents and importer jobs, based on log parsing.

## Installation

Run `pip install -r requirements.txt` to install the dependencies. Add `--user` 
if you do not have access to the system libraries, or make use of `virtualenv`.
You may need to add additional parameters, such as `--extra-index-url` for 
a private repository.

## Running

Simply start the application using `python status.py`. Use command-line 
arguments (displayed with `python status.py --help`) and/or a data-gathering 
`settings.cfg` file (specifically the sections `ldap`, `deploy`, `jenkins` and 
`schedule` influence this application's behavior - see the gros-gatherer 
documentation for details).

You can also configure the application as a systemd service such that it can 
run headless under a separate user, using a virtualenv setup. See 
`gros-status.service` for details.
