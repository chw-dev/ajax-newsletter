"""Tests for meetings.py.

Records here are synthetic and hand-built (metadata only): the shape mirrors
what the portal returns, but no captured payloads are checked in.
"""

import datetime
import io
import unittest
import urllib.request

import meetings


def make_record(**fields):
    """Build a record in the portal's {"Attributes": [{"Name", "Value"}]} shape."""
    return {"Attributes": [{"Name": name, "Value": value} for name, value in fields.items()]}


def wire_date(date):
    """A YYYY-MM-DD string -> the portal's /Date(ms)/ form at 18:00 UTC that day."""
    dt = datetime.datetime(date.year, date.month, date.day, 18, 0, tzinfo=datetime.timezone.utc)
    return f"/Date({int(dt.timestamp() * 1000)})/"


D = datetime.date


class ParseWireDateTests(unittest.TestCase):
    def test_parses_date_epoch(self):
        # 2026-05-11 18:00 UTC, minus the fixed 4h offset, is still 2026-05-11.
        self.assertEqual(
            meetings.parse_wire_date(wire_date(D(2026, 5, 11))),
            D(2026, 5, 11),
        )

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            meetings.parse_wire_date("not a date")


class BuildMeetingEntryTests(unittest.TestCase):
    def entry(self, **fields):
        fields.setdefault("crf6e_name", "Council")
        return meetings.build_meeting_entry(fields, D(2026, 5, 11))

    def test_agenda_and_minutes(self):
        docs = self.entry(
            crf6e_agendalink="http://x/agenda.pdf",
            crf6e_minuteslink="http://x/minutes.pdf",
        )["documents"]
        self.assertEqual(
            docs,
            [
                {"type": "agenda", "url": "http://x/agenda.pdf"},
                {"type": "minutes", "url": "http://x/minutes.pdf"},
            ],
        )

    def test_agenda_only(self):
        docs = self.entry(crf6e_agendalink="http://x/agenda.pdf")["documents"]
        self.assertEqual(docs, [{"type": "agenda", "url": "http://x/agenda.pdf"}])

    def test_no_documents(self):
        self.assertEqual(self.entry()["documents"], [])

    def test_status_always_present(self):
        self.assertIsNone(self.entry()["status"])
        self.assertEqual(self.entry(crf6e_meetingstatus="Completed")["status"], "Completed")


class FilterMeetingsTests(unittest.TestCase):
    def test_surfaces_status_without_filtering(self):
        records = [
            make_record(
                crf6e_name="Council",
                crf6e_meetingdate=wire_date(D(2026, 4, 13)),
                crf6e_meetingstatus="Completed",
            ),
            make_record(
                crf6e_name="Community Affairs and Planning Committee",
                crf6e_meetingdate=wire_date(D(2026, 4, 7)),
                crf6e_meetingstatus="Cancelled",
            ),
            make_record(
                crf6e_name="Council",
                crf6e_meetingdate=wire_date(D(2026, 4, 20)),
                crf6e_meetingstatus="Scheduled",
            ),
        ]
        out = meetings.filter_meetings(records, D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual(
            [(m["date"], m["status"]) for m in out],
            [
                ("2026-04-07", "Cancelled"),
                ("2026-04-13", "Completed"),
                ("2026-04-20", "Scheduled"),
            ],
        )

    def test_out_of_scope_type_dropped(self):
        records = [
            make_record(
                crf6e_name="Committee of Adjustment",
                crf6e_meetingdate=wire_date(D(2026, 4, 10)),
                crf6e_meetingstatus="Completed",
            ),
        ]
        self.assertEqual(meetings.filter_meetings(records, D(2026, 4, 1), D(2026, 4, 30)), [])

    def test_date_range_bounds_inclusive(self):
        records = [
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 1))),
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 30))),
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 3, 31))),
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 5, 1))),
        ]
        out = meetings.filter_meetings(records, D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual([m["date"] for m in out], ["2026-04-01", "2026-04-30"])

    def test_records_missing_name_or_date_skipped(self):
        records = [
            make_record(crf6e_meetingdate=wire_date(D(2026, 4, 10)), crf6e_meetingstatus="Cancelled"),
            make_record(crf6e_name="Council"),
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 12))),
        ]
        out = meetings.filter_meetings(records, D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual([m["date"] for m in out], ["2026-04-12"])

    def test_output_sorted_by_date(self):
        records = [
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 20))),
            make_record(crf6e_name="Special GGC", crf6e_meetingdate=wire_date(D(2026, 4, 5))),
            make_record(crf6e_name="General Government Committee", crf6e_meetingdate=wire_date(D(2026, 4, 13))),
        ]
        out = meetings.filter_meetings(records, D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual([m["date"] for m in out], ["2026-04-05", "2026-04-13", "2026-04-20"])


class FetchCsrfTokenTests(unittest.TestCase):
    class FakeOpener:
        def __init__(self, body):
            self._body = body.encode("utf-8")

        def open(self, req, timeout=None):
            return io.BytesIO(self._body)

    def test_extracts_token(self):
        opener = self.FakeOpener(
            '<input name="__RequestVerificationToken" type="hidden" value="Tok-123" />'
        )
        self.assertEqual(meetings.fetch_csrf_token(opener), "Tok-123")

    def test_raises_when_absent(self):
        with self.assertRaises(RuntimeError):
            meetings.fetch_csrf_token(self.FakeOpener("<html>no token here</html>"))


class BuildOpenerTests(unittest.TestCase):
    def test_opener_carries_cookie_processor(self):
        opener = meetings.build_opener()
        self.assertTrue(
            any(isinstance(h, urllib.request.HTTPCookieProcessor) for h in opener.handlers)
        )


class ParseArgsTests(unittest.TestCase):
    def test_rejects_start_after_end(self):
        with self.assertRaises(SystemExit):
            meetings.parse_args(["--start", "2026-05-31", "--end", "2026-05-01"])

    def test_rejects_malformed_date(self):
        with self.assertRaises(SystemExit):
            meetings.parse_args(["--start", "2026-13-01", "--end", "2026-13-05"])

    def test_accepts_valid_range(self):
        self.assertEqual(
            meetings.parse_args(["--start", "2026-05-01", "--end", "2026-05-31"]),
            (D(2026, 5, 1), D(2026, 5, 31)),
        )


if __name__ == "__main__":
    unittest.main()
