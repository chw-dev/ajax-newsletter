"""List Ajax Council / GGC / Community Affairs and Planning meetings in a date range.

Usage:
    python meetings.py --start 2026-05-01 --end 2026-05-31

Talks to the Town of Ajax public meetings portal (a Power Apps portal). The
meeting list is rendered by a JS grid, not present in the raw page HTML, so
this replays the same request sequence the browser makes: fetch an anonymous
session + CSRF token, fetch the grid's view configuration, then POST for the
full record set and filter client-side (the API has no server-side date
filter).
"""

import argparse
import datetime
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://ajax-publicmeetings.powerappsportals.com"
LIST_VIEW_NAME = "City Connections Meetings Lookup View"
USER_AGENT = "ajax-newsletter-meetings-fetcher/1.0 (non-commercial community newsletter tool)"
REQUEST_DELAY_SECONDS = 1

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

# The portal bakes a fixed timezoneOffset of 240 (EDT, UTC-4) into the
# request; the server uses it to produce the returned epoch timestamp. During
# EST (winter) this is off by one hour from true local time. Ajax meetings in
# this dataset run in the afternoon/evening, so an hour's shift can't cross a
# date boundary in practice.
FIXED_UTC_OFFSET = datetime.timedelta(hours=4)


def parse_wire_date(value):
    match = WIRE_DATE_RE.search(value)
    if not match:
        raise ValueError(f"unrecognized date format: {value!r}")
    epoch_ms = int(match.group(1))
    utc_dt = datetime.datetime.fromtimestamp(epoch_ms / 1000, tz=datetime.timezone.utc)
    return (utc_dt - FIXED_UTC_OFFSET).date()


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
            "pageSize": 5000,
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
        "documents": documents,
    }


def filter_meetings(records, start_date, end_date):
    meetings = []
    for record in records:
        fields = flatten_attributes(record)
        meeting_type = fields.get("crf6e_name")
        wire_date = fields.get("crf6e_meetingdate")
        if not meeting_type or not wire_date:
            continue
        if meeting_type not in IN_SCOPE_MEETING_TYPES:
            continue
        meeting_date = parse_wire_date(wire_date)
        if not (start_date <= meeting_date <= end_date):
            continue
        meetings.append(build_meeting_entry(fields, meeting_date))
    meetings.sort(key=lambda m: m["date"])
    return meetings


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
    except (urllib.error.URLError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: failed to fetch meetings: {exc}", file=sys.stderr)
        return 1
    meetings = filter_meetings(records, start_date, end_date)
    print(json.dumps(meetings, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
