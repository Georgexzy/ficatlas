"""Seed the database with fabricated test stories for UI development.

DANGEROUS AGAINST A REAL INDEX. These works do not exist: they carry invented
engagement counts in the tens of thousands (higher than anything genuine in this
index, so they sort to the top of real searches), they are attributed to real AO3
authors, and their URLs point at unrelated real AO3 works. Eight of them were
found sitting in the live index during an audit, ranking above real fiction.

So this now refuses to run unless you ask twice: --yes, and --force if the index
already holds real data. Every row is tagged `ui_fixture` so it can be found and
removed again with DELETE /api/library/admin/cleanup-seeds.
"""
import argparse
import os, sys
sys.path.insert(0, '/app')
os.environ.setdefault('DATABASE_URL', 'postgresql://ficatlas:ficatlas@db:5432/ficatlas')

from db.session import db_session
from models.story import Story, SiteEnum, RatingEnum, StatusEnum
from datetime import datetime

STORIES = [
    {
        "site": SiteEnum.ao3, "site_id": "1234001",
        "url": "https://archiveofourown.org/works/1234001",
        "title": "The Complexity of Being Found",
        "author": "silentauror", "author_url": "https://archiveofourown.org/users/silentauror",
        "summary": "After the war, Hermione Granger takes a position in the Department of Mysteries and finds Draco Malfoy working in the same corridor. What begins as uneasy coexistence becomes something neither of them planned for.",
        "language": "English", "rating": RatingEnum.mature, "status": StatusEnum.complete,
        "word_count": 182440, "chapter_count": 38, "chapter_count_total": 38,
        "kudos": 24800, "hits": 412000, "bookmarks": 3200, "comments": 1840,
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "relationships": ["Draco Malfoy/Hermione Granger"],
        "characters": ["Hermione Granger", "Draco Malfoy", "Harry Potter"],
        "tags": ["Slow Burn", "Post-War", "Mutual Pining", "Sexual Tension", "Enemies to Lovers"],
        "warnings": ["No Archive Warnings Apply"], "categories": ["F/M"],
        "updated_at": datetime(2023, 4, 12), "published_at": datetime(2021, 1, 5),
    },
    {
        "site": SiteEnum.ao3, "site_id": "1234002",
        "url": "https://archiveofourown.org/works/1234002",
        "title": "All the Wrong Reasons",
        "author": "bex-chan", "author_url": "https://archiveofourown.org/users/bex-chan",
        "summary": "Draco Malfoy needs a reason to avoid his family obligations. Hermione Granger needs a reason to keep her parents' expectations at bay. A fake relationship neither of them can afford to feel too deeply about.",
        "language": "English", "rating": RatingEnum.mature, "status": StatusEnum.complete,
        "word_count": 247861, "chapter_count": 51, "chapter_count_total": 51,
        "kudos": 31200, "hits": 580000, "bookmarks": 4800, "comments": 2900,
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "relationships": ["Draco Malfoy/Hermione Granger"],
        "characters": ["Hermione Granger", "Draco Malfoy"],
        "tags": ["Fake Dating", "Slow Burn", "Enemies to Lovers", "Eventual Romance"],
        "warnings": ["No Archive Warnings Apply"], "categories": ["F/M"],
        "updated_at": datetime(2022, 8, 3), "published_at": datetime(2019, 6, 14),
    },
    {
        "site": SiteEnum.ao3, "site_id": "1234003",
        "url": "https://archiveofourown.org/works/1234003",
        "title": "The Marauders and the Map",
        "author": "mapmaker_anon",
        "summary": "James, Sirius, Remus and Peter in their Hogwarts years. The full story of how four boys became legends — and how one of them became something else entirely.",
        "language": "English", "rating": RatingEnum.teen, "status": StatusEnum.complete,
        "word_count": 312000, "chapter_count": 64, "chapter_count_total": 64,
        "kudos": 18900, "hits": 290000, "bookmarks": 2600, "comments": 3100,
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "relationships": ["James Potter/Lily Evans Potter", "Remus Lupin/Sirius Black"],
        "characters": ["James Potter", "Sirius Black", "Remus Lupin", "Peter Pettigrew", "Lily Evans Potter"],
        "tags": ["Marauders Era", "Slow Burn", "Friendship", "Tragedy", "Found Family"],
        "warnings": ["Major Character Death"], "categories": ["F/M", "M/M"],
        "updated_at": datetime(2024, 1, 20), "published_at": datetime(2020, 9, 1),
    },
    {
        "site": SiteEnum.ao3, "site_id": "1234004",
        "url": "https://archiveofourown.org/works/1234004",
        "title": "A Thousand Beautiful Things",
        "author": "duniazade",
        "summary": "Draco Malfoy tries to make himself a better life after the war, one day at a time. A story about grief, rebuilding, and unexpected connections.",
        "language": "English", "rating": RatingEnum.mature, "status": StatusEnum.complete,
        "word_count": 156000, "chapter_count": 33,
        "kudos": 14200, "hits": 198000, "bookmarks": 1900, "comments": 980,
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "relationships": ["Draco Malfoy/Harry Potter"],
        "characters": ["Draco Malfoy", "Harry Potter", "Hermione Granger"],
        "tags": ["Post-War", "Slow Burn", "Hurt/Comfort", "Redemption", "8th Year"],
        "warnings": ["No Archive Warnings Apply"], "categories": ["M/M"],
        "updated_at": datetime(2023, 11, 5), "published_at": datetime(2022, 2, 28),
    },
    {
        "site": SiteEnum.ao3, "site_id": "1234005",
        "url": "https://archiveofourown.org/works/1234005",
        "title": "Remus Lupin and the Art of Being Wanted",
        "author": "moonwriter99",
        "summary": "Remus has spent his whole life believing he is a burden. Sirius Black is determined to prove him wrong, one infuriating act of devotion at a time.",
        "language": "English", "rating": RatingEnum.teen, "status": StatusEnum.in_progress,
        "word_count": 89000, "chapter_count": 22, "chapter_count_total": None,
        "kudos": 9800, "hits": 134000, "bookmarks": 1200, "comments": 740,
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "relationships": ["Remus Lupin/Sirius Black"],
        "characters": ["Remus Lupin", "Sirius Black", "James Potter"],
        "tags": ["Slow Burn", "Marauders Era", "Angst with a Happy Ending", "Pining Remus Lupin"],
        "warnings": ["No Archive Warnings Apply"], "categories": ["M/M"],
        "updated_at": datetime(2025, 3, 14), "published_at": datetime(2023, 7, 7),
    },
    {
        "site": SiteEnum.ao3, "site_id": "1234006",
        "url": "https://archiveofourown.org/works/1234006",
        "title": "Isolation",
        "author": "Bex-chan",
        "summary": "When eighth-year students are forced to share dormitories in small cross-house groups, Hermione and Draco find themselves with more in common than they ever wanted to admit.",
        "language": "English", "rating": RatingEnum.mature, "status": StatusEnum.complete,
        "word_count": 120293, "chapter_count": 26, "chapter_count_total": 26,
        "kudos": 22100, "hits": 380000, "bookmarks": 2800, "comments": 1650,
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "relationships": ["Draco Malfoy/Hermione Granger"],
        "characters": ["Hermione Granger", "Draco Malfoy"],
        "tags": ["8th Year", "Slow Burn", "Forced Proximity", "Enemies to Lovers"],
        "warnings": ["No Archive Warnings Apply"], "categories": ["F/M"],
        "updated_at": datetime(2021, 5, 18), "published_at": datetime(2018, 11, 2),
    },
    {
        "site": SiteEnum.ao3, "site_id": "1234007",
        "url": "https://archiveofourown.org/works/1234007",
        "title": "The Nietzsche Classes",
        "author": "Beringae",
        "summary": "Malfoy is forced to seek tutoring from Granger. She charges by the hour and makes no promises about what he'll walk away learning.",
        "language": "English", "rating": RatingEnum.explicit, "status": StatusEnum.complete,
        "word_count": 136708, "chapter_count": 29, "chapter_count_total": 29,
        "kudos": 19400, "hits": 310000, "bookmarks": 2400, "comments": 1200,
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "relationships": ["Draco Malfoy/Hermione Granger"],
        "characters": ["Hermione Granger", "Draco Malfoy"],
        "tags": ["Slow Burn", "Post-War", "Literary Themes", "Intellectual Foreplay"],
        "warnings": ["No Archive Warnings Apply"], "categories": ["F/M"],
        "updated_at": datetime(2020, 3, 9), "published_at": datetime(2017, 8, 22),
    },
    {
        "site": SiteEnum.ao3, "site_id": "1234008",
        "url": "https://archiveofourown.org/works/1234008",
        "title": "Make Me a Match",
        "author": "senlinyu",
        "summary": "Hermione Granger is assigned to investigate Draco Malfoy. Draco Malfoy is trying very hard not to fall in love with Hermione Granger. Neither of them is succeeding.",
        "language": "English", "rating": RatingEnum.explicit, "status": StatusEnum.complete,
        "word_count": 203000, "chapter_count": 44,
        "kudos": 28600, "hits": 490000, "bookmarks": 4100, "comments": 2300,
        "fandoms": ["Harry Potter - J. K. Rowling"],
        "relationships": ["Draco Malfoy/Hermione Granger"],
        "characters": ["Hermione Granger", "Draco Malfoy", "Ron Weasley"],
        "tags": ["Auror Hermione", "Slow Burn", "Explicit Sexual Content", "Enemies to Lovers"],
        "warnings": ["No Archive Warnings Apply"], "categories": ["F/M"],
        "updated_at": datetime(2024, 2, 14), "published_at": datetime(2022, 10, 31),
    },
]

FIXTURE_TAG = "ui_fixture"
REAL_INDEX_THRESHOLD = 1000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true",
                    help="Required. Confirms you want fabricated stories inserted.")
    ap.add_argument("--force", action="store_true",
                    help="Also insert when the index already contains real data.")
    args = ap.parse_args()

    if not args.yes:
        print("Refusing to run: these are FABRICATED stories with invented kudos, "
              "attributed to real authors, whose URLs point at unrelated real works.\n"
              "They will outrank genuine fiction in search. Pass --yes if that is "
              "really what you want.")
        raise SystemExit(2)

    with db_session() as db:
        total = db.query(Story).count()
        if total > REAL_INDEX_THRESHOLD and not args.force:
            print(f"Refusing to run: the index holds {total:,} stories, so this is not "
                  f"an empty development database. Pass --force to insert anyway.")
            raise SystemExit(2)

        count = 0
        for data in STORIES:
            existing = (db.query(Story)
                        .filter(Story.site == data["site"], Story.site_id == data["site_id"])
                        .first())
            if not existing:
                row = dict(data)
                # Tag every fixture so cleanup-seeds can find them by provenance
                # rather than by guessing at the site_id pattern.
                row["tags"] = [*row.get("tags", []), FIXTURE_TAG]
                db.add(Story(**row))
                count += 1
        print(f"Inserted {count} fabricated stories (tagged '{FIXTURE_TAG}').")
        print("Remove with: DELETE /api/library/admin/cleanup-seeds")


if __name__ == "__main__":
    main()
