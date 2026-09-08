# Intent: list meetings in a date range
Author: C. Wheeler. Status: draft.
## Problem
Meeting agendas and minutes are stored on the Town of Ajax website. A newsletter is only published if one of the three meetings listed in the Scope section have occured in a specified date range.
## Proposed outcome
Given a date range, links to the agendas and minutes are returned.
## Examples
2026-05-01 to 2026-05-31 -> at least one of each type of meeting
2026-08-01 to 2026-08-31 -> there is exactly one special council
2024-02-01 to 2024-02-28 -> at least one of each type of meeting
## Scope
Council meetings, special and regular
General Government Committee meetings, regular and special
Community Affairs and Planning meetings, regular and special
## Inputs
Agendas & minutes: https://ajax-publicmeetings.powerappsportals.com/
## Command
python meetings.py --start <start-date> --end <end-date>
## Output
JSON formatted, with the date of the meeting, the type of meeting, and one link per document for that meeting.
## Constraints
- Standard library only. If you think you need a package, stop and say which and why.
- You're an unannounced client of a small town's portal. One request at a time, no parallel fetching, roughly a second between requests, and a User-Agent that says what this is rather than pretending to be Chrome.
- One file. No package layout, no config system, no CLI framework, no logging setup.
- Print to stdout. Don't write files, create directories, or cache to disk.
- This is public data. If it appears to need a login, cookie, or key, stop and tell me rather than working around it.