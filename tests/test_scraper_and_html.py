"""Tests for HTML JobPosting extraction, deadline parsing, link filtering, and index.html invariants."""
from pathlib import Path
import backend.scraper as sc

SAMPLE_JOB_HTML = """
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "JobPosting",
  "title": "Industrial PhD (ErhvervsPhD) in Real-Time Data Platforms",
  "hiringOrganization": {"@type": "Organization", "name": "Technical University of Denmark"},
  "jobLocation": {"@type": "Place", "address": {"addressLocality": "Lyngby"}},
  "validThrough": "2027-03-15",
  "description": "<p>We seek a candidate skilled in Apache Kafka, Spark, and cloud lakehouse architectures. Salary: 34,000 DKK/month. Contact Prof. Anders Lind at anders.lind@dtu.dk.</p>"
}
</script>
</head>
<body><h1>Fallback Title</h1></body>
</html>
"""


def test_detail_extracts_jsonld_and_contacts():
    d = sc.detail("https://example.dk/job/101", html=SAMPLE_JOB_HTML)
    assert d["title"] == "Industrial PhD (ErhvervsPhD) in Real-Time Data Platforms"
    assert d["institution"] == "Technical University of Denmark"
    assert d["city"] == "Lyngby"
    assert d["deadline"] == "2027-03-15"
    assert "anders.lind@dtu.dk" in d["contact_emails"]
    assert "DKK" in d["funding"]


def test_find_deadline_formats():
    assert sc.find_deadline("Application deadline: 2027-02-28 for all candidates.") == "2027-02-28"
    assert sc.find_deadline("Date limite de candidature : 14/04/2027.") == "2027-04-14"


def test_links_of_filters_navigation_noise():
    html = """
    <a href="/login">Login</a>
    <a href="/privacy">Privacy</a>
    <a href="/jobs/9988">PhD Position in Big Data Engineering</a>
    """
    links = sc.links_of(html, "https://jobs.example.eu/search")
    assert len(links) == 1
    assert links[0][1] == "https://jobs.example.eu/jobs/9988"


def test_index_html_has_no_tracker_and_has_clear_state():
    html = (Path(__file__).resolve().parent.parent / "index.html").read_text("utf-8")
    assert "Clear Page State" in html
    assert "delete S.run" in html
    assert "Why ${pctVal(o.chance)}%?" in html
    # Ensure application tracker tab was completely removed
    assert '["apps"' not in html
