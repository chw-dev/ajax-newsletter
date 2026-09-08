"""List Ajax Council / GGC / Community Affairs and Planning meetings in a date range.

Usage:
    python meetings.py --start 2026-05-01 --end 2026-05-31

Talks to the Town of Ajax public meetings portal (a Power Apps portal). The
meeting list is rendered by a JS grid, not present in the raw page HTML, so
this makes the same request sequence the browser makes: fetch the anonymous
session cookie and CSRF token the portal issues to every visitor, fetch the
grid's view configuration, then POST for the full record set and filter
client-side (the API has no server-side date filter). No account, login, or
user-specific secret is involved; see the note in intent.md's Constraints.
"""

import argparse
import collections
import datetime
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE_URL = "https://ajax-publicmeetings.powerappsportals.com"
LIST_VIEW_NAME = "City Connections Meetings Lookup View"
USER_AGENT = "ajax-newsletter-meetings-fetcher/1.0 (non-commercial community newsletter tool)"
REQUEST_DELAY_SECONDS = 1
PAGE_SIZE = 5000

MEETING_TZ_NAME = "America/Toronto"
try:
    MEETING_TZ = ZoneInfo(MEETING_TZ_NAME)
except ZoneInfoNotFoundError:
    raise SystemExit(
        f"error: time-zone data for {MEETING_TZ_NAME!r} is unavailable. "
        "Run: pip install -r requirements.txt"
    )

# Meeting-type names that should map into IN_SCOPE_MEETING_TYPES. Used only to
# decide whether an unmatched name is worth warning about: a renamed in-scope
# meeting ("Special General Government Committee") gets flagged, an out-of-scope
# body ("Committee of Adjustment") stays quiet.
IN_SCOPE_HINTS = ("council", "government committee", "ggc", "affairs and planning")

# The "special" General Government Committee meeting is abbreviated "Special
# GGC" rather than following the "Special <name>" pattern the other two
# meeting families use. No "special" Community Affairs and Planning variant
# has ever appeared in the data; if the town adds one, it needs to be added
# here.
IN_SCOPE_MEETING_TYPES = {
    "Council",
    "Special Council",
    "General Government Committee",
    "Special GGC",
    "Community Affairs and Planning Committee",
}

WIRE_DATE_RE = re.compile(r"/Date\((\d+)\)/")


def parse_wire_date(value):
    # The wire value is a plain UTC instant; the request's timezoneOffset field
    # is inert (posting 240, 0 or -480 returns identical epochs). Some records
    # carry a real afternoon meeting time, others are date-only stored at local
    # midnight. Converting to America/Toronto local before taking .date() is
    # correct for both, and the exact offset only matters for values landing in
    # the 04:00-05:00 UTC window, where EST vs EDT changes the calendar day.
    match = WIRE_DATE_RE.search(value)
    if not match:
        raise ValueError(f"unrecognized date format: {value!r}")
    epoch_ms = int(match.group(1))
    utc_dt = datetime.datetime.fromtimestamp(epoch_ms / 1000, tz=datetime.timezone.utc)
    return utc_dt.astimezone(MEETING_TZ).date()


def build_opener():
    cookie_jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))


def request(opener, url, data=None, extra_headers=None):
    headers = {"User-Agent": USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers)
    with opener.open(req, timeout=30) as resp:
        return resp.read()


def fetch_home(opener):
    request(opener, f"{BASE_URL}/")


def fetch_csrf_token(opener):
    body = request(opener, f"{BASE_URL}/_layout/tokenhtml?_=1").decode("utf-8")
    match = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', body)
    if not match:
        raise RuntimeError("could not find __RequestVerificationToken on tokenhtml page")
    return match.group(1)


def fetch_view_config(opener):
    url = f"{BASE_URL}/_services/portal/GetListViewConfiguration/{urllib.parse.quote(LIST_VIEW_NAME)}"
    body = request(opener, url).decode("utf-8")
    return json.loads(body)


def fetch_records(opener, view_config, csrf_token):
    layout = view_config["layouts"][0]
    payload = json.dumps(
        {
            "base64SecureConfiguration": layout["Base64SecureConfiguration"],
            "sortExpression": layout.get("SortExpression") or "crf6e_meetingdate DESC",
            "search": "",
            "page": 1,
            "pageSize": PAGE_SIZE,
            "filter": None,
            "metaFilter": "",
            "timezoneOffset": 240,
            "customParameters": [],
            "odataFilterQuery": "",
            "nlSearchFilter": "",
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "__RequestVerificationToken": csrf_token,
    }
    url = BASE_URL + view_config["getDataUrl"]
    body = request(opener, url, data=payload, extra_headers=headers).decode("utf-8")
    data = json.loads(body)
    if data.get("MoreRecords"):
        # Sort is crf6e_meetingdate DESC, so a truncated set silently drops the
        # oldest meetings. Fail loudly instead; add real pagination if the table
        # ever approaches PAGE_SIZE rows (582 as of 2026-09).
        raise RuntimeError(
            f"portal returned a truncated result set (pageSize={PAGE_SIZE}, "
            "MoreRecords=true); pagination is not implemented"
        )
    return data["Records"]


def flatten_attributes(record):
    return {attr["Name"]: attr["Value"] for attr in record["Attributes"]}


def collect_meetings(opener):
    fetch_home(opener)
    time.sleep(REQUEST_DELAY_SECONDS)
    csrf_token = fetch_csrf_token(opener)
    time.sleep(REQUEST_DELAY_SECONDS)
    view_config = fetch_view_config(opener)
    time.sleep(REQUEST_DELAY_SECONDS)
    return fetch_records(opener, view_config, csrf_token)


def build_meeting_entry(fields, meeting_date):
    documents = []
    agenda_url = fields.get("crf6e_agendalink")
    if agenda_url:
        documents.append({"type": "agenda", "url": agenda_url})
    minutes_url = fields.get("crf6e_minuteslink")
    if minutes_url:
        documents.append({"type": "minutes", "url": minutes_url})
    return {
        "date": meeting_date.isoformat(),
        "meeting_type": fields["crf6e_name"],
        "status": fields.get("crf6e_meetingstatus"),
        "documents": documents,
    }


def filter_meetings(records, start_date, end_date):
    """Return (meetings, notes): entries in range, plus human-readable strings
    about records that were dropped in a way that could hide a real meeting."""
    meetings = []
    missing_fields = 0
    unmatched_in_scope = collections.Counter()
    for record in records:
        fields = flatten_attributes(record)
        meeting_type = fields.get("crf6e_name")
        wire_date = fields.get("crf6e_meetingdate")
        if not meeting_type or not wire_date:
            missing_fields += 1
            continue
        if meeting_type not in IN_SCOPE_MEETING_TYPES:
            if any(hint in meeting_type.lower() for hint in IN_SCOPE_HINTS):
                unmatched_in_scope[meeting_type] += 1
            continue
        # crf6e_meetingstatus (Completed / Cancelled / Scheduled) is surfaced in
        # each entry, not filtered on: the caller decides whether a given status
        # counts as "occurred" for publish purposes.
        meeting_date = parse_wire_date(wire_date)
        if not (start_date <= meeting_date <= end_date):
            continue
        meetings.append(build_meeting_entry(fields, meeting_date))
    meetings.sort(key=lambda m: m["date"])

    notes = []
    if missing_fields:
        notes.append(f"{missing_fields} record(s) skipped: missing name or date")
    if unmatched_in_scope:
        listed = ", ".join(
            f"{name!r} ({count})" for name, count in sorted(unmatched_in_scope.items())
        )
        notes.append(
            f"{sum(unmatched_in_scope.values())} record(s) skipped: name resembles an "
            f"in-scope meeting but is not in IN_SCOPE_MEETING_TYPES: {listed}"
        )
    return meetings, notes


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD (inclusive)")
    args = parser.parse_args(argv)
    try:
        start_date = datetime.date.fromisoformat(args.start)
        end_date = datetime.date.fromisoformat(args.end)
    except ValueError as exc:
        parser.error(str(exc))
    if start_date > end_date:
        parser.error("--start must not be after --end")
    return start_date, end_date


def main(argv):
    start_date, end_date = parse_args(argv)
    opener = build_opener()
    try:
        records = collect_meetings(opener)
        meetings, notes = filter_meetings(records, start_date, end_date)
    except (
        urllib.error.URLError,
        TimeoutError,
        RuntimeError,
        KeyError,
        IndexError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: failed to fetch meetings: {exc}", file=sys.stderr)
        return 1
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    print(json.dumps(meetings, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
