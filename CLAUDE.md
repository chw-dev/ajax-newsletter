# CLAUDE.md

## Project
ajax-newsletter produces a newsletter that details what happens in Community Affairs and Planning meetings, Ajax Council Meetings and General Government Committee meetings, both special and regular. Ajax residents will read this newsletter because it offers an easy-to-understand, quick-to-read, neutral-but-lively summation of these meetings and the important information and decisions impacting them. The cadence is weekly, when these meetings occur.

## Commands
- Setup: pip install -r requirements.txt
- Tests: python -m unittest
- Run: python meetings.py --start <YYYY-MM-DD> --end <YYYY-MM-DD>

## Context
- Meeting data comes from https://ajax-publicmeetings.powerappsportals.com/
- The portal holds roughly 582 meeting records going back to 2018, so historical lookups are
  possible, not only recent weeks.
- Past decisions and their reasoning are in DECISIONS.md. Read it before changing dependencies
  or how the portal is accessed.

## Working rules
- Scope work so the diff is reviewable in ten minutes, roughly 150 lines. If it will be
  bigger, stop and propose a split instead.
- Use plan mode for anything non-trivial. Propose before editing files.
- Never report that something works. Show the command you ran and its output.
- Write the test in the same change as the code, never as a follow-up.
- Do not write documents specifying systems that do not exist yet. Decisions already taken
  go in DECISIONS.md.
- When a request is ambiguous, ask. Do not pick and proceed.
- Standard library preferred. If you need a package, stop and tell me which one and why
  before adding it.

## Out of bounds
- Never commit PDFs, transcripts, captions, scraped source material, or credentials.
- Fixtures are metadata only.
