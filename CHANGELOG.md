# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- ASGI 3 support
- CI tests for python 3.8-dev

<<<<<<< Updated upstream
### Deprecated
- ASGI 2

# v1.3.0
=======
## [v1.3.1]- 2019-04-28
### Added
- Route params Converters
- Add search for documentation pages
>>>>>>> Stashed changes

### Changed
- Bump dependencies

## [v1.3.0]  - 2019-02-22
### Fixed
- Versioning issue
- Multiple cookies.
- Whitenoise returns not found.
- Other bugfixes.

### Added
- Stream support via `resp.stream`.
- Cookie directives via `resp.set_cookie`.
- Add `resp.html` to send HTML.
- Other improvements.

## [v1.1.3] - 2019-01-12
### Changed
- Refactor `_route_for`

### Fixed
- Resolve startup/shutdwown events

## [v1.2.0] - 2018-12-29
### Added
- Documentations

### Changed
- Use Starlette's LifeSpan middleware
- Update denpendencies

### Fixed
- Fix route.is_class_based
- Fix test_500
- Typos

## [v1.1.2] - 2018-11-11
### Fixed
- Minor fixes for Open API
- Typos

## [v1.1.1] - 2018-10-29
### Changed
- Run sync views in a threadpoolexecutor.

## [v1.1.0] - 2018-10-27
### Added
- Support for `before_request`.

## [v1.0.5]- 2018-10-27
### Fixed
- Fix sessions.

## [v1.0.4] - 2018-10-27
### Fixed
- Potential bufix for cookies.

## [v1.0.3] - 2018-10-27
### Fixed
- Bugfix for redirects.

## [v1.0.2] - 2018-10-27
### Changed
- Improvement for static file hosting.

## [v1.0.1] - 2018-10-26
### Changed
- Improve cors configuration settings.

## [v1.0.0] - 2018-10-26
### Changed
- Move GraphQL support into a built-in plugin.

## [v0.3.3] - 2018-10-25
### Added
- CORS support

### Changed
- Improved exceptions.

## [v0.3.2] - 2018-10-25
### Changed
- Subtle improvements.

## [v0.3.1] - 2018-10-24
### Fixed
- Packaging fix.

## [v0.3.0] - 2018-10-24
### Changed
- Interactive Documentation endpoint.
- Minor improvements.

## [v0.2.3] - 2018-10-24
### Changed
- Overall improvements.

## [v0.2.2] - 2018-10-23
### Added
- Show traceback info when background tasks raise exceptions.

## [v0.2.1] - 2018-10-23
### Added
- api.requests.

## [v0.2.0] - 2018-10-22
### Added
- WebSocket support.

## [v0.1.6] - 2018-10-20
### Added
- 500 support.

## [v0.1.5] - 2018-10-20
### Added
- File upload support

### Changed
- Improvements to sequential media reading.

## [v0.1.4] - 2018-10-19
### Fixed
- Stability.

## [v0.1.3] - 2018-10-18
### Added
- Sessions support.

## [v0.1.2] - 2018-10-18
### Added
- Cookies support.

## [v0.1.1] - 2018-10-17
### Changed
- Default routes.

## [v0.1.0] - 2018-10-17
### Added
- Prototype of static application support.

## [v0.0.10] - 2018-10-17
### Fixed
- Bugfix for async class-based views.

## [v0.0.9] - 2018-10-17
### Fixed
- Bugfix for async class-based views.

## [v0.0.8] - 2018-10-17
### Added
- GraphiQL Support.

### Changed
- Improvement to route selection.

## [v0.0.7] - 2018-10-16
### Changed
- Immutable Request object.

## [v0.0.6] - 2018-10-16
### Added
- Ability to mount WSGI apps.
- Supply content-type when serving up the schema.

## [v0.0.5] - 2018-10-15
### Added
- OpenAPI Schema support.
- Safe load/dump yaml.

## [v0.0.4] - 2018-10-15
### Added
- Asynchronous support for data uploads.

### Fixed
- Bug fixes.

## [v0.0.3] - 2018-10-13
### Fixed
- Bug fixes.

## [v0.0.2] - 2018-10-13
### Changed
- Switch to ASGI/Starlette.

## [v0.0.1] - 2018-10-12
### Added
- Conception!

[Unreleased]: https://github.com/taoufik07/responder/compare/v1.3.1..HEAD
[v1.3.1]: https://github.com/taoufik07/responder/compare/v1.3.0..v1.3.1
[v1.3.0]: https://github.com/taoufik07/responder/compare/v1.2.0..v1.3.0
[v1.2.0]: https://github.com/taoufik07/responder/compare/v1.1.3..v1.2.0
[v1.1.3]: https://github.com/taoufik07/responder/compare/v1.1.2..v1.1.3
[v1.1.2]: https://github.com/taoufik07/responder/compare/v1.1.1..v1.1.2
[v1.1.1]: https://github.com/taoufik07/responder/compare/v1.1.0..v1.1.1
[v1.1.0]: https://github.com/taoufik07/responder/compare/v1.0.5..v1.1.0
[v1.0.5]: https://github.com/taoufik07/responder/compare/v1.0.4..v1.0.5
[v1.0.4]: https://github.com/taoufik07/responder/compare/v1.0.3..v1.0.4
[v1.0.3]: https://github.com/taoufik07/responder/compare/v1.0.2..v1.0.3
[v1.0.2]: https://github.com/taoufik07/responder/compare/v1.0.1..v1.0.2
[v1.0.1]: https://github.com/taoufik07/responder/compare/v1.0.0..v1.0.1
[v1.0.0]: https://github.com/taoufik07/responder/compare/v0.3.3..v1.0.0
[v0.3.3]: https://github.com/taoufik07/responder/compare/v0.3.2..v0.3.3
[v0.3.2]: https://github.com/taoufik07/responder/compare/v0.3.1..v0.3.2
[v0.3.1]: https://github.com/taoufik07/responder/compare/v0.3.0..v0.3.1
[v0.3.0]: https://github.com/taoufik07/responder/compare/v0.2.3..v0.3.0
[v0.2.3]: https://github.com/taoufik07/responder/compare/v0.2.2..v0.2.3
[v0.2.2]: https://github.com/taoufik07/responder/compare/v0.2.1..v0.2.2
[v0.2.1]: https://github.com/taoufik07/responder/compare/v0.2.0..v0.2.1
[v0.2.0]: https://github.com/taoufik07/responder/compare/v0.1.6..v0.2.0
[v0.1.6]: https://github.com/taoufik07/responder/compare/v0.1.5..v0.1.6
[v0.1.5]: https://github.com/taoufik07/responder/compare/v0.1.4..v0.1.5
[v0.1.4]: https://github.com/taoufik07/responder/compare/v0.1.3..v0.1.4
[v0.1.3]: https://github.com/taoufik07/responder/compare/v0.1.2..v0.1.3
[v0.1.2]: https://github.com/taoufik07/responder/compare/v0.1.1..v0.1.2
[v0.1.1]: https://github.com/taoufik07/responder/compare/v0.1.0..v0.1.1
[v0.1.0]: https://github.com/taoufik07/responder/compare/v0.0.10..v0.1.0
[v0.0.10]: https://github.com/taoufik07/responder/compare/v0.0.9..v0.0.10
[v0.0.9]: https://github.com/taoufik07/responder/compare/v0.0.8..v0.0.9
[v0.0.8]: https://github.com/taoufik07/responder/compare/v0.0.7..v0.0.8
[v0.0.7]: https://github.com/taoufik07/responder/compare/v0.0.6..v0.0.7
[v0.0.6]: https://github.com/taoufik07/responder/compare/v0.0.5..v0.0.6
[v0.0.5]: https://github.com/taoufik07/responder/compare/v0.0.4..v0.0.5
[v0.0.4]: https://github.com/taoufik07/responder/compare/v0.0.3..v0.0.4
[v0.0.3]: https://github.com/taoufik07/responder/compare/v0.0.2..v0.0.3
[v0.0.2]: https://github.com/taoufik07/responder/compare/v0.0.1..v0.0.2
[v0.0.1]: https://github.com/taoufik07/responder/compare/v0.0.0..v0.0.1
