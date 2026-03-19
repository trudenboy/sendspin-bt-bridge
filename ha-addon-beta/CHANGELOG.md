# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.40.5-beta.1] - 2026-03-18

### Changed
- Home Assistant add-on channel variants can now run side by side on the same HAOS host because stable, RC, and beta installs use distinct default ingress ports and Sendspin listener port ranges while still honoring explicit port overrides
- RC and beta Home Assistant add-on variants now default to manual startup and use channel-specific branding in the store/sidebar so prerelease tracks are easier to distinguish from the stable add-on

### Fixed
- Music Assistant album artwork now loads correctly through Home Assistant ingress because artwork proxy URLs stay relative to the current add-on origin instead of escaping to the Home Assistant root
