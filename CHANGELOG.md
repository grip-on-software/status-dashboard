# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 
and we adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2024-07-15

### Added

- Unit tests for `application`, `bootstrap` and `parser` modules added.

### Changed

- The `log` endpoint now gives custom 404 messages if the agent `name` and 
  `log` field name parameters are not provided.
- Service is now built as a package with executable script entry point.

### Fixed

- Export log parser could use a log level as a microsecond datetime field.
- Logs containing unknown status levels no longer cause errors.
- Agent versions link to the GitHub 
  [data-gathering](https://github.com/grip-on-software/data-gathering) 
  repository instead of a GitLab repository if commit information is available.
- Jenkins job build project lookups handle connection errors by falling back to 
  not having Jenkins details in the overview until refreshed.

### Security

- The `log` endpoint no longer tries to serve agent fields that are not logs.

## [0.0.3] - 2024-06-25

### Added

- Initial release of version as used during the GROS research project. 
  Previously, versions were rolling releases based on Git commits.

### Fixed

- Correct help output description.

### Removed

- Support for Python 2.7 dropped.

[Unreleased]: 
https://github.com/grip-on-software/status-dashboard/compare/v1.0.0...HEAD
[1.0.0]: 
https://github.com/grip-on-software/status-dashboard/compare/v0.0.3...v1.0.0
[0.0.3]: 
https://github.com/grip-on-software/status-dashboard/releases/tag/v0.0.3
