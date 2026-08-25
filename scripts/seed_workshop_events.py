"""Load the approved 2026-27 library workshop programme into a SQLite database.

Usage: python scripts/seed_workshop_events.py [path-to-gateway.db]

This replaces future WORKSHOP records from 1 September 2026 onward. It does
not affect past events, announcements, or Book Club events.
"""
import sqlite3
import sys
from datetime import datetime

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "gateway.db"
PROGRAMME_START = "2026-09-01T00:00"

# start|end (blank unless multi-day)|title|delivery|display label
EVENTS = """
2026-09-07T09:00|2026-09-11T18:00|Library Induction Week|In class for Level 3 and Level 4 students|7-11 Sep 2026 | During scheduled classes
2026-09-08T15:00||Navigating Our Library Website: The Local Catalogue, Physical Books and Online Databases|In person in Astana Campus|Tue, 8 Sep 2026 | 15:00-16:00 KZ
2026-09-16T13:00||Searching Academic Databases|Online (UK librarian Tom Page)|Wed, 16 Sep 2026 | 13:00-14:00 KZ
2026-09-16T15:00||Navigating Our Library Website: The Local Catalogue, Physical Books and Online Databases from UK|In person in Astana Campus|Wed, 16 Sep 2026 | 15:00-16:00 KZ
2026-10-05T15:00||Literature Search Tools Comparison: ProQuest Central, Ebook Central, Scopus, JSTOR, ScienceDirect, Google Scholar and Open Access|Hybrid|Mon, 5 Oct 2026 | 15:00-16:00 KZ
2026-10-08T15:00||Searching Academic Databases|Online|Thu, 8 Oct 2026 | 15:00-16:00 KZ
2026-10-09T15:00||APA Style Resources: Key Rules|Online|Fri, 9 Oct 2026 | 15:00-16:00 KZ
2026-10-12T15:00||Productivity Tools for Students: Planning, Organisation and Note-taking with NotebookLM|In person|Mon, 12 Oct 2026 | 15:00-16:00 KZ
2026-10-15T15:00||Searching Academic Databases|In person|Thu, 15 Oct 2026 | 15:00-16:00 KZ
2026-10-19T15:00||Open Access Week 2026: The Cost of Knowledge|Hybrid|Mon, 19 Oct 2026 | 15:00-16:00 KZ
2026-10-22T15:00||Quick Referencing Tools|Online|Thu, 22 Oct 2026 | 15:00-16:00 KZ
2026-10-26T15:00||Ethical Use of AI: Accuracy, Privacy, Bias and Academic Integrity|Hybrid|Mon, 26 Oct 2026 | 15:00-16:00 KZ
2026-10-29T15:00||Gemini Notebook (formerly NotebookLM): Working with Academic Sources|Hybrid|Thu, 29 Oct 2026 | 15:00-16:00 KZ
2026-11-02T15:00||Research Discovery Tools I: ResearchRabbit|In person|Mon, 2 Nov 2026 | 15:00-16:00 KZ
2026-11-09T15:00||Research Discovery Tools II: Connected Papers|In person|Mon, 9 Nov 2026 | 15:00-16:00 KZ
2026-11-16T15:00||Research Discovery Tools III: Litmaps|In person|Mon, 16 Nov 2026 | 15:00-16:00 KZ
2026-11-23T15:00||Citation and Referencing Tools Comparison|Hybrid|Mon, 23 Nov 2026 | 15:00-16:00 KZ
2026-11-30T15:00||Plagiarism and Its Consequences: How to Use Sources Responsibly|Hybrid|Mon, 30 Nov 2026 | 15:00-16:00 KZ
2026-12-02T15:00||Exam Support Week: Anti-Stress Week|Hybrid|Wed, 2 Dec 2026 | 15:00-16:00 KZ
2026-12-04T15:00||Exam Support Week: Anti-Stress Week|In person|Fri, 4 Dec 2026 | 15:00-16:00 KZ
2027-01-31T23:59||Induction Week|Hybrid|January 2027 - date to be confirmed | 15:00-16:00 KZ
2027-01-31T23:59||Finding and Evaluating Reliable Academic Sources|In person|January 2027 - date to be confirmed | 15:00-16:00 KZ
2027-01-31T23:59||Searching for Physical Literature with AI Librarian|In person|January 2027 - date to be confirmed | 15:00-16:00 KZ
2027-02-28T23:59||Advanced Database Searching: Keywords, Boolean Operators and Filters|Hybrid|February 2027 - date to be confirmed | 15:00-16:00 KZ
2027-02-28T23:59||Plagiarism and Its Consequences|In person|February 2027 - date to be confirmed | 15:00-16:00 KZ
2027-02-28T23:59||From Search Results to a Literature Review|Hybrid|February 2027 - date to be confirmed | 15:00-16:00 KZ
2027-02-28T23:59||Searching Academic Databases|Hybrid|February 2027 - date to be confirmed | 15:00-16:00 KZ
2027-03-31T23:59||Women in Research: Finding and Citing Women Scholars|Hybrid|March 2027 - date to be confirmed | 15:00-16:00 KZ
2027-03-31T23:59||International Women's Day Library Week: Empowering Women - Leadership and Innovation|Hybrid|March 2027 - date to be confirmed | 15:00-16:00 KZ
2027-03-31T23:59||Nauryz Heritage and Culture Festival: Books on Kazakh Traditions|In person|March 2027 - date to be confirmed | 15:00-16:00 KZ
2027-03-31T23:59||Searching Academic Databases|Hybrid|March 2027 - date to be confirmed | 15:00-16:00 KZ
2027-04-05T15:00||Open Educational Resources: Finding and Using Free Academic Content|Hybrid|Mon, 5 Apr 2027 | 15:00-16:00 KZ
2027-04-12T15:00||Sustainable Study Practices and Digital Wellbeing|In person|Mon, 12 Apr 2027 | 15:00-16:00 KZ
2027-04-22T15:00||Citation and Referencing Tools|Hybrid|Thu, 22 Apr 2027 | 15:00-16:00 KZ
2027-04-26T15:00||Research and Referencing for Final Assignments|Hybrid|Mon, 26 Apr 2027 | 15:00-16:00 KZ
2027-05-31T23:59||Exam Support Week: Anti-Stress Week|In person|May 2027 - date to be confirmed | 15:00-16:00 KZ
2027-05-31T23:59||Exam Support Week: Anti-Stress Week|In person|May 2027 - date to be confirmed | 15:00-16:00 KZ
"""


def parse_events():
    for line in EVENTS.strip().splitlines():
        start, end, title, delivery, label = line.split("|", 4)
        yield start, end or None, title, delivery, label


def seed():
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(events)")}
        if "event_end_date" not in columns:
            con.execute("ALTER TABLE events ADD COLUMN event_end_date TEXT")

        con.execute(
            "DELETE FROM events WHERE type = 'WORKSHOP' AND event_date >= ?",
            (PROGRAMME_START,),
        )
        con.executemany(
            """INSERT INTO events
               (title, type, description, location, date_display, event_date,
                event_end_date, color, created_at)
               VALUES (?, 'WORKSHOP', ?, 'Library', ?, ?, ?, '#0055B7', ?)""",
            [(title, delivery, label, start, end, now) for start, end, title, delivery, label in parse_events()],
        )
    print(f"Imported {sum(1 for _ in parse_events())} workshop events into {DB_PATH}.")


if __name__ == "__main__":
    seed()
