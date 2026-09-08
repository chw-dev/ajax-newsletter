"""Tests for meetings.py.

Records here are synthetic and hand-built (metadata only): the shape mirrors
what the portal returns, but no captured payloads are checked in.
"""

import datetime
import io
import unittest
import urllib.request
from unittest import mock

import meetings

UTC = datetime.timezone.utc
D = datetime.date


def make_record(**fields):
    """Build a record in the portal's {"Attributes": [{"Name", "Value"}]} shape."""
    return {"Attributes": [{"Name": name, "Value": value} for name, value in fields.items()]}


def wire_at(dt_utc):
    """A tz-aware UTC datetime -> the portal's /Date(ms)/ string."""
    return f"/Date({int(dt_utc.timestamp() * 1000)})/"


def wire_date(date):
    """A date -> /Date(ms)/ at 18:00 UTC (an afternoon Eastern meeting time)."""
    return wire_at(datetime.datetime(date.year, date.month, date.day, 18, 0, tzinfo=UTC))


def filtered(records, start, end):
    meetings_, _notes = meetings.filter_meetings(records, start, end)
    return meetings_


def notes_for(records, start, end):
    _meetings, notes = meetings.filter_meetings(records, start, end)
    return notes


class ParseWireDateTests(unittest.TestCase):
    def test_afternoon_meeting_keeps_its_day(self):
        self.assertEqual(
            meetings.parse_wire_date(wire_date(D(2026, 5, 11))), D(2026, 5, 11)
        )

    def test_est_offset_pushes_early_utc_to_previous_day(self):
        # 04:30 UTC in January is 23:30 EST the day before.
        v = wire_at(datetime.datetime(2026, 1, 15, 4, 30, tzinfo=UTC))
        self.assertEqual(meetings.parse_wire_date(v), D(2026, 1, 14))

    def test_edt_offset_keeps_same_early_utc_day(self):
        # 04:30 UTC in July is 00:30 EDT the same day.
        v = wire_at(datetime.datetime(2026, 7, 15, 4, 30, tzinfo=UTC))
        self.assertEqual(meetings.parse_wire_date(v), D(2026, 7, 15))

    def test_spring_forward_weekend(self):
        # DST begins 2026-03-08 02:00 local. 04:30 UTC that day is 23:30 EST on
        # the 7th; the day before, 04:30 UTC is also still EST.
        self.assertEqual(
            meetings.parse_wire_date(wire_at(datetime.datetime(2026, 3, 8, 4, 30, tzinfo=UTC))),
            D(2026, 3, 7),
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
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 13)),
                        crf6e_meetingstatus="Completed"),
            make_record(crf6e_name="Community Affairs and Planning Committee",
                        crf6e_meetingdate=wire_date(D(2026, 4, 7)), crf6e_meetingstatus="Cancelled"),
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 20)),
                        crf6e_meetingstatus="Scheduled"),
        ]
        out = filtered(records, D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual(
            [(m["date"], m["status"]) for m in out],
            [("2026-04-07", "Cancelled"), ("2026-04-13", "Completed"), ("2026-04-20", "Scheduled")],
        )

    def test_out_of_scope_type_dropped_quietly(self):
        records = [make_record(crf6e_name="Committee of Adjustment",
                               crf6e_meetingdate=wire_date(D(2026, 4, 10)))]
        meetings_, notes = meetings.filter_meetings(records, D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual(meetings_, [])
        self.assertEqual(notes, [])

    def test_date_range_bounds_inclusive(self):
        records = [
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 1))),
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 30))),
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 3, 31))),
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 5, 1))),
        ]
        out = filtered(records, D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual([m["date"] for m in out], ["2026-04-01", "2026-04-30"])

    def test_missing_name_or_date_skipped_and_noted(self):
        records = [
            make_record(crf6e_meetingdate=wire_date(D(2026, 4, 10)), crf6e_meetingstatus="Cancelled"),
            make_record(crf6e_name="Council"),
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 12))),
        ]
        meetings_, notes = meetings.filter_meetings(records, D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual([m["date"] for m in meetings_], ["2026-04-12"])
        self.assertEqual(len(notes), 1)
        self.assertIn("2 record(s) skipped: missing name or date", notes[0])

    def test_renamed_in_scope_meeting_is_noted(self):
        records = [
            make_record(crf6e_name="Special General Government Committee",
                        crf6e_meetingdate=wire_date(D(2026, 4, 9))),
            make_record(crf6e_name="Council (Special)", crf6e_meetingdate=wire_date(D(2026, 4, 9))),
        ]
        meetings_, notes = meetings.filter_meetings(records, D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual(meetings_, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("Special General Government Committee", notes[0])
        self.assertIn("Council (Special)", notes[0])

    def test_output_sorted_by_date(self):
        records = [
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 20))),
            make_record(crf6e_name="Special GGC", crf6e_meetingdate=wire_date(D(2026, 4, 5))),
            make_record(crf6e_name="General Government Committee",
                        crf6e_meetingdate=wire_date(D(2026, 4, 13))),
        ]
        out = filtered(records, D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual([m["date"] for m in out], ["2026-04-05", "2026-04-13", "2026-04-20"])

    def test_bad_wire_date_raises_valueerror(self):
        records = [make_record(crf6e_name="Council", crf6e_meetingdate="/Date(nope)/")]
        with self.assertRaises(ValueError):
            meetings.filter_meetings(records, D(2026, 4, 1), D(2026, 4, 30))


class FakeOpener:
    def __init__(self, body):
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def open(self, req, timeout=None):
        return io.BytesIO(self._body)


class FetchRecordsTests(unittest.TestCase):
    VIEW_CONFIG = {
        "getDataUrl": "/_services/data",
        "layouts": [{"Base64SecureConfiguration": "cfg", "SortExpression": "crf6e_meetingdate DESC"}],
    }

    def test_truncated_result_set_raises(self):
        opener = FakeOpener('{"Records": [], "MoreRecords": true}')
        with self.assertRaises(RuntimeError):
            meetings.fetch_records(opener, self.VIEW_CONFIG, "tok")

    def test_complete_result_set_returns_records(self):
        opener = FakeOpener('{"Records": [1, 2], "MoreRecords": false}')
        self.assertEqual(meetings.fetch_records(opener, self.VIEW_CONFIG, "tok"), [1, 2])


class FetchCsrfTokenTests(unittest.TestCase):
    def test_extracts_token(self):
        opener = FakeOpener('<input name="__RequestVerificationToken" value="Tok-123" />')
        self.assertEqual(meetings.fetch_csrf_token(opener), "Tok-123")

    def test_raises_when_absent(self):
        with self.assertRaises(RuntimeError):
            meetings.fetch_csrf_token(FakeOpener("<html>no token here</html>"))


class BuildOpenerTests(unittest.TestCase):
    def test_opener_carries_cookie_processor(self):
        opener = meetings.build_opener()
        self.assertTrue(
            any(isinstance(h, urllib.request.HTTPCookieProcessor) for h in opener.handlers)
        )


class MainTests(unittest.TestCase):
    def test_bad_wire_date_reports_error_not_traceback(self):
        bad = [make_record(crf6e_name="Council", crf6e_meetingdate="/Date(nope)/")]
        with mock.patch.object(meetings, "collect_meetings", return_value=bad):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                rc = meetings.main(["--start", "2026-04-01", "--end", "2026-04-30"])
        self.assertEqual(rc, 1)
        self.assertIn("error: failed to fetch meetings", err.getvalue())

    def test_notes_go_to_stderr_json_to_stdout(self):
        recs = [
            make_record(crf6e_name="Council", crf6e_meetingdate=wire_date(D(2026, 4, 13))),
            make_record(crf6e_name="Special GGC (rescheduled)",
                        crf6e_meetingdate=wire_date(D(2026, 4, 9))),
        ]
        with mock.patch.object(meetings, "collect_meetings", return_value=recs):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                 mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                rc = meetings.main(["--start", "2026-04-01", "--end", "2026-04-30"])
        self.assertEqual(rc, 0)
        self.assertIn('"2026-04-13"', out.getvalue())
        self.assertIn("note:", err.getvalue())
        self.assertIn("Special GGC (rescheduled)", err.getvalue())


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
