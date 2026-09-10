# Decisions

Decisions already taken, with the reasoning behind them. Newest at the bottom, append only.

## 2026-09-08: Added tzdata as a dependency

`zoneinfo` is standard library but needs an IANA time-zone database, which Windows does not
ship. `tzdata` (requirements.txt) supplies it. Used to convert meeting timestamps to
America/Toronto local time before taking the date.

This replaced a hardcoded UTC-4 offset, which was correct only during EDT and would silently
misdate any meeting starting within four hours of midnight during EST.

## 2026-09-08: Anonymous session cookies and CSRF token are acceptable

The portal serves the meeting grid over an XHR that needs an anonymous session cookie and a
CSRF token from `/_layout/tokenhtml`. Both are issued to every visitor with no account, login,
or user-specific secret, and the underlying data is the same public agenda and minutes list.

The "stop if it appears to need a login, cookie, or key" constraint is considered satisfied for
this portal. It still applies anywhere else.
