from datetime import datetime, timedelta, timezone

import pytest

from scout.dedup import canonical_url, identity_key, job_id
from scout.filters import classify_location, is_undergrad, us_locations
from scout.models import Listing
from scout.notify import format_age
from scout.pipeline import Pipeline
from scout.store import MemoryStore


def listing(**kw) -> Listing:
    base = dict(source="simplify", source_id="1", company="Epic Games",
                title="Tools Programmer Intern - Graphics", url="https://example.com/job/1",
                locations=["Cary, NC"])
    return Listing(**{**base, **kw})


@pytest.mark.parametrize("loc", [
    "Cary, NC", "NYC", "SF", "Remote in USA", "United States", "USA or Australia",
    "Carmel, IN", "Portland, OR", "USA VA Falls Church", "New York",
    "Sunnyvale, California, United States of America", "NC, Raleigh, 701 Corporate Center Dr",
    "US-IA-CEDAR RAPIDS-107 ~ 400 Collins Rd NE", "Albuquerque, NM", "New Mexico",
    "Washington, DC", "Mount Laurel, New Jersey", "Carlsbad, Ca",
])
def test_us_locations(loc):
    assert classify_location(loc) == "us"


@pytest.mark.parametrize("loc", [
    "Vancouver, BC, Canada", "London, UK", "Toronto, ON", "Amsterdam, Netherlands",
    "Singapore", "Kanata, Ottawa, ON, Canada", "Bangalore", "Mexico City, Mexico", "Montréal",
    "Abu Dhabi - United Arab Emirates",
])
def test_foreign_locations(loc):
    assert classify_location(loc) == "foreign"


def test_mixed_locations_keep_only_us():
    assert us_locations(["Seattle, WA", "Vancouver, BC, Canada"]) == ["Seattle, WA"]
    assert us_locations(["London, UK"]) is None
    assert us_locations(["Remote"]) == ["Remote"]
    assert us_locations([]) == []


@pytest.mark.parametrize("title,degrees,expected", [
    ("Software Engineer Intern", ["Bachelor's"], True),
    ("Software Engineer Intern", None, True),
    ("Software Engineer Intern", [], True),
    ("Software Engineer Intern", ["Bachelor's", "Master's"], True),
    ("Research Intern", ["Master's", "PhD"], False),
    ("Research Intern", ["PhD"], False),
    ("PhD Research Intern", None, False),
    ("Software Engineer, New Grad", None, False),
    ("Graduate Intern - Data", None, False),
    ("Undergraduate Research Intern", None, True),
    ("MBA Product Intern", None, False),
    ("Masters Intern", None, False),
])
def test_undergrad(title, degrees, expected):
    assert is_undergrad(listing(title=title, degrees=degrees)) is expected


def test_canonical_url_strips_tracking():
    a = canonical_url("https://www.Boards.greenhouse.io/x/jobs/123?gh_jid=123&utm_source=Simplify&ref=Simplify")
    b = canonical_url("https://boards.greenhouse.io/x/jobs/123/?gh_jid=123")
    assert a == b
    assert canonical_url("https://jobs.lever.co/x/abc/apply") == canonical_url("https://jobs.lever.co/x/abc")


@pytest.mark.parametrize("url,expected", [
    ("https://job-boards.greenhouse.io/astranis/jobs/4715985006", "greenhouse:4715985006"),
    ("https://boards.greenhouse.io/embed/job_app?token=7905463", "greenhouse:7905463"),
    ("https://careers.example.com/open-roles?gh_jid=4715985006&utm_source=Simplify", "greenhouse:4715985006"),
    ("https://intel.wd1.myworkdayjobs.com/external/job/US-Oregon-Hillsboro/AI-Intern_JR0286629",
     "workday:intel:JR0286629"),
    ("https://psu.wd1.myworkdayjobs.com/PSU_Staff/job/Park/Research-Assistant--SRI-_REQ_0000083297-1",
     "workday:psu:REQ_0000083297-1"),
    ("https://wd5.myworkdaysite.com/recruiting/microchiphr/External/job/CA---San-Jose/Intern_R3851-26",
     "workday:microchiphr:R3851-26"),
    ("https://jobs.lever.co/palantir/e0010393-c300-446f-bf67-fa2ef067f16f/apply",
     "uuid:e0010393-c300-446f-bf67-fa2ef067f16f"),
    ("https://jobs.ashbyhq.com/skydio/D39C46CE-a4e9-45d2-99bf-17e3b7dac34b/application?embed=true",
     "uuid:d39c46ce-a4e9-45d2-99bf-17e3b7dac34b"),
    ("https://apply.workable.com/connectprep/j/1AF4F8C65F/apply", "workable:1AF4F8C65F"),
    ("https://jobs.jobvite.com/tylertech/job/oqQJAfwj?nl=1", "jobvite:oqQJAfwj"),
    ("https://amazon.jobs/en/jobs/10556927/2027-data-science-internship", "amazon.jobs:10556927"),
    ("https://careers-calamp.icims.com/jobs/4326/intern%2c-engineer-(summer-2026)/job",
     "careers-calamp.icims.com:4326"),
    ("https://jobs.smartrecruiters.com/SGS/744000152533359-software-intern", "jobs.smartrecruiters.com:744000152533359"),
    ("https://textron.taleo.net/careersection/textron/jobdetail.ftl?job=342550", "textron.taleo.net:342550"),
    ("https://www.tesla.com/careers/search/job/282147", "tesla.com:282147"),
    ("https://example.com/careers/software-intern", None),
])
def test_job_id(url, expected):
    assert job_id(url) == expected


def test_same_workday_req_under_different_paths():
    a = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/SWE-Intern_JR1999"
    b = "https://nvidia.wd5.myworkdayjobs.com/en-US/University/job/US-TX-Austin/Software-Intern_JR1999/apply"
    assert identity_key(a) == identity_key(b)


def test_identity_falls_back_to_url():
    assert identity_key("https://example.com/careers/intern/?utm_source=x") == "url:https://example.com/careers/intern"


def test_format_age():
    now = datetime.now(timezone.utc)
    assert format_age(now - timedelta(seconds=5), now) == "just now"
    assert format_age(now - timedelta(minutes=7), now) == "7m"
    assert format_age(now - timedelta(days=2, hours=3), now) == "2d"


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, role):
        self.sent.append(role)
        return True


def amazon(source, source_id, url, locations):
    return listing(source=source, source_id=source_id, company="Amazon",
                   title="Software Development Engineer Intern", url=url, locations=locations)


def test_pipeline_dedups_by_job_id(monkeypatch):
    monkeypatch.setattr("scout.pipeline.time.sleep", lambda s: None)
    store, notifier = MemoryStore(), Recorder()
    p = Pipeline(store, notifier, lambda u: u)

    # First fetch of a source is a silent seed.
    p.process("simplify", [amazon("simplify", "a", "https://amazon.jobs/en/jobs/1001/sde-intern", ["Seattle, WA"])])
    assert p.flush_notifications() == 0

    # Same title, same city, different job IDs: different roles, both notify.
    p.process("simplify", [
        amazon("simplify", "b", "https://amazon.jobs/en/jobs/1002/sde-intern", ["Austin, TX", "London, UK"]),
        amazon("simplify", "c", "https://amazon.jobs/en/jobs/1003/sde-intern", ["Seattle, WA"]),
    ])
    assert p.flush_notifications() == 2
    assert notifier.sent[0].locations == ["Austin, TX"]

    p.process("zshah", [])  # seed
    p.process("zshah", [
        # Same job ID via a differently formatted link: duplicate, extra location merged.
        amazon("zshah", "z1", "https://www.amazon.jobs/en/jobs/1002/other-slug?utm_source=z", ["Dallas, TX"]),
        # New job ID: new role, even though title and city match role a.
        amazon("zshah", "z2", "https://amazon.jobs/en/jobs/1004/sde-intern", ["Seattle, WA"]),
    ])
    assert store.roles[2]["sources"] == ["simplify", "zshah"]
    assert store.roles[2]["locations"] == ["Austin, TX", "Dallas, TX"]
    assert p.flush_notifications() == 1
    assert len(store.roles) == 4


def test_pipeline_survives_restart():
    store = MemoryStore()
    Pipeline(store, Recorder(), lambda u: u).process(
        "simplify", [amazon("simplify", "a", "https://amazon.jobs/en/jobs/1001/x", ["Seattle, WA"])])
    p = Pipeline(store, Recorder(), lambda u: u)
    p.process("zshah", [])
    p.process("zshah", [amazon("zshah", "z", "https://amazon.jobs/en/jobs/1001/y", ["Seattle"])])
    assert len(store.roles) == 1 and store.roles[1]["sources"] == ["simplify", "zshah"]


def test_filtered_roles_never_stored():
    store = MemoryStore()
    p = Pipeline(store, Recorder(), lambda u: u)
    p.process("simplify", [listing(locations=["London, UK"]), listing(source_id="2", title="PhD Intern")])
    assert store.roles == {}
