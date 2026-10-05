import json
import os

import pytest
import requests

import bibsync

online = pytest.mark.skipif(
    not os.environ.get("BIBSYNC_ONLINE"),
    reason="set BIBSYNC_ONLINE=1 to query the databases",
)

# Responses in the formats the databases return them (October 2026).
INSPIRE_GW150914 = """@article{LIGOScientific:2016aoc,
    author = "Abbott, B. P. and others",
    collaboration = "LIGO Scientific, Virgo",
    title = "{Observation of Gravitational Waves from a Binary Black Hole Merger}",
    eprint = "1602.03837",
    archivePrefix = "arXiv",
    primaryClass = "gr-qc",
    reportNumber = "LIGO-P150914",
    doi = "10.1103/PhysRevLett.116.061102",
    journal = "Phys. Rev. Lett.",
    volume = "116",
    number = "6",
    pages = "061102",
    year = "2016"
}
"""
INSPIRE_RIESS = """@article{SupernovaSearchTeam:1998fmf,
    author = "Riess, Adam G. and others",
    collaboration = "Supernova Search Team",
    title = "{Observational evidence from supernovae for an accelerating universe and a cosmological constant}",
    eprint = "astro-ph/9805201",
    archivePrefix = "arXiv",
    doi = "10.1086/300499",
    journal = "Astron. J.",
    volume = "116",
    pages = "1009--1038",
    year = "1998"
}
"""
RIESS_AUTHORS = ["Riess, Adam G.", "Filippenko, Alexei V.", "Challis, Peter"]
RIESS_AUTHORS += ["Clocchiattia, Alejandro", "Diercks, Alan", "Garnavich, Peter M."]
RIESS_AUTHORS += ["Gilliland, Ron L.", "Hogan, Craig J.", "Jha, Saurabh"]
RIESS_AUTHORS += ["Kirshner, Robert P.", "Leibundgut, B.", "Phillips, M.M."]
RIESS_AUTHORS += ["Reiss, David", "Schmidt, Brian P.", "Schommer, Robert A."]
RIESS_AUTHORS += ["Smith, R.Chris", "Spyromilio, J.", "Stubbs, Christopher"]
RIESS_AUTHORS += ["Suntzeff, Nicholas B.", "Tonry, John"]
INSPIRE_RIESS_JSON = json.dumps(
    {
        "hits": {
            "hits": [
                {"metadata": {"authors": [{"full_name": n} for n in RIESS_AUTHORS]}}
            ]
        }
    }
)
CROSSREF = (
    " @article{Hodgkin_1952, title={A quantitative description of membrane current and "
    "its application to conduction and excitation in nerve}, volume={117}, "
    "ISSN={1469-7793}, url={http://dx.doi.org/10.1113/jphysiol.1952.sp004764}, "
    "DOI={10.1113/jphysiol.1952.sp004764}, number={4}, journal={The Journal of Physiology}, "
    "publisher={Wiley}, author={Hodgkin, A. L. and Huxley, A. F.}, year={1952}, "
    "month=Aug, pages={500–544} }"
)
DATACITE = """@article{https://doi.org/10.48550/arxiv.1803.03635,
  doi = {10.48550/ARXIV.1803.03635},
  url = {https://arxiv.org/abs/1803.03635},
  author = {Frankle, Jonathan and Carbin, Michael},
  keywords = {Machine Learning (cs.LG), Artificial Intelligence (cs.AI), Neural and Evolutionary Computing (cs.NE), FOS: Computer and information sciences, FOS: Computer and information sciences},
  title = {The Lottery Ticket Hypothesis: Finding Sparse, Trainable Neural Networks},
  publisher = {arXiv},
  year = {2018},
  copyright = {arXiv.org perpetual, non-exclusive license}
}"""
ARXIV = """@misc{frankle2019lotterytickethypothesisfinding,
      title={The Lottery Ticket Hypothesis: Finding Sparse, Trainable Neural Networks},
      author={Jonathan Frankle and Michael Carbin},
      year={2019},
      eprint={1803.03635},
      archivePrefix={arXiv},
      primaryClass={cs.LG},
      url={https://arxiv.org/abs/1803.03635},
}"""

GW150914 = "LIGOScientific:2016aoc"
RIESS = "SupernovaSearchTeam:1998fmf"
RESPONSES = {
    bibsync.inspire_search(GW150914, format="bibtex"): INSPIRE_GW150914,
    "https://inspirehep.net/api/doi/10.1103/physrevlett.116.061102": INSPIRE_GW150914,
    "https://inspirehep.net/api/arxiv/1602.03837": INSPIRE_GW150914,
    bibsync.inspire_search(RIESS, format="bibtex"): INSPIRE_RIESS,
    bibsync.inspire_search(RIESS, fields="authors.full_name"): INSPIRE_RIESS_JSON,
    "https://doi.org/10.1113/jphysiol.1952.sp004764": CROSSREF,
    "https://doi.org/10.48550/arxiv.1803.03635": DATACITE,
    "https://arxiv.org/bibtex/1803.03635": ARXIV,
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Answer requests from RESPONSES; anything else is an unknown record."""
    requested = []

    def fake_get(url, accept):
        requested.append(url)
        response = RESPONSES.get(url)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(bibsync, "http_get", fake_get)
    return requested


def write(tmp_path, text):
    path = tmp_path / "refs.bib"
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_keeps_text_verbatim_and_reads_fields():
    text = (
        '% comment with @ sign\n@string{prd = "Phys. Rev. D"}\n\n'
        "@article{A:2020ab, title = {{Nested} braces}, journal = prd, "
        'note = "a {"} quote" # " joined", year = 2020}\n'
        "@misc{nofields}\ntrailing text\n"
    )
    items = bibsync.parse(text)
    assert "".join(x if isinstance(x, str) else x.raw for x in items) == text
    a, empty = [x for x in items if isinstance(x, bibsync.Entry)]
    assert a.key == "A:2020ab"
    assert a.fields == [
        ("title", "{{Nested} braces}"),
        ("journal", "prd"),
        ("note", '"a {"} quote" # " joined"'),
        ("year", "2020"),
    ]
    assert a.get("title") == "{Nested} braces"
    assert (empty.key, empty.fields) == ("nofields", [])
    with pytest.raises(ValueError, match="unbalanced"):
        bibsync.parse('@misc{a, title = "unterminated}')


def test_identifier_patterns():
    assert all(
        bibsync.TEXKEY.match(k) for k in ("Einstein:1916vd", GW150914, "tHooft:1973alw")
    )
    assert not bibsync.TEXKEY.match("Einstein1905")
    assert all(
        bibsync.ARXIV.match(i) for i in ("1602.03837", "1602.03837v2", "hep-th/9711200")
    )
    assert bibsync.norm_eprint("arXiv:1602.03837v2") == "1602.03837"
    assert (
        bibsync.norm_doi("https://doi.org/10.1103/PhysRevLett.116.061102")
        == "10.1103/physrevlett.116.061102"
    )


def test_check_reports_a_stale_entry_and_update_fixes_it_keeping_local_fields(
    tmp_path, capsys
):
    stale = (
        "@article{LIGOScientific:2016aoc,\n  author = {Abbott, B. P.},\n"
        "  title = {Old title},\n  keywords = {appendme},\n}"
    )
    unmanaged = "@misc{ligo,\n  howpublished = {\\url{https://www.ligo.org}},\n}"
    path = write(tmp_path, f"% header\n\n{stale}\n\n{unmanaged}\n")

    assert bibsync.main(["check", str(path)]) == 1
    assert "DIFFERS    LIGOScientific:2016aoc (INSPIRE)" in capsys.readouterr().out
    assert bibsync.main(["update", str(path)]) == 0

    expected = bibsync.render(
        "article",
        GW150914,
        bibsync.parse(INSPIRE_GW150914)[0].fields + [("keywords", "{appendme}")],
    )
    assert path.read_text() == f"% header\n\n{expected}\n\n{unmanaged}\n"
    assert bibsync.main(["check", str(path)]) == 0
    assert "unmanaged  ligo" in capsys.readouterr().out


def test_records_not_on_inspire_come_from_doi_org_and_arxiv(tmp_path, offline):
    path = write(
        tmp_path,
        "@article{HodgkinHuxley, doi = {10.1113/jphysiol.1952.sp004764}}\n"
        "@misc{LotteryTicket, eprint = {1803.03635}}\n",
    )
    assert bibsync.main(["update", str(path)]) == 0
    hh, lottery = bibsync.entries(bibsync.parse(path.read_text()))
    assert (hh.key, hh.get("journal")) == ("HodgkinHuxley", "The Journal of Physiology")
    assert hh.get("pages") == "500–544"  # the registrar's characters, unchanged
    assert (lottery.key, lottery.get("primaryClass")) == ("LotteryTicket", "cs.LG")
    assert offline[:2] == [
        "https://inspirehep.net/api/doi/10.1113/jphysiol.1952.sp004764",
        "https://doi.org/10.1113/jphysiol.1952.sp004764",
    ]


def test_a_record_with_a_contradicting_doi_is_refused(tmp_path, capsys):
    text = f"@article{{{GW150914}, doi = {{10.1000/other}}, eprint = {{9999.99999}}}}\n"
    path = write(tmp_path, text)
    assert bibsync.main(["update", str(path)]) == 1
    assert "different doi and eprint" in capsys.readouterr().out
    assert path.read_text() == text


def test_unknown_record_fails(tmp_path, capsys):
    path = write(tmp_path, "@article{Nobody:2099zzz, title = {x}}\n")
    assert bibsync.main(["check", str(path)]) == 1
    assert "FAILED     Nobody:2099zzz" in capsys.readouterr().out


def test_add_uses_the_inspire_texkey_and_the_result_checks_clean(tmp_path):
    path = write(tmp_path, "")
    assert (
        bibsync.main(
            ["add", str(path), "https://doi.org/10.1103/PhysRevLett.116.061102"]
        )
        == 0
    )
    assert bibsync.parse(path.read_text())[0].key == GW150914
    assert bibsync.main(["check", str(path)]) == 0
    with pytest.raises(SystemExit, match="already in"):
        bibsync.main(["add", str(path), "1602.03837"])
    with pytest.raises(SystemExit, match="cite it with that key"):
        bibsync.main(
            ["add", str(tmp_path / "other.bib"), "1602.03837", "--key", "MyKey"]
        )


def test_add_outside_inspire_needs_a_key_and_drops_database_keywords(tmp_path):
    path = write(tmp_path, "")
    with pytest.raises(SystemExit, match="choose a citation key"):
        bibsync.main(["add", str(path), "10.48550/arXiv.1803.03635"])
    assert (
        bibsync.main(
            ["add", str(path), "10.48550/arXiv.1803.03635", "--key", "LotteryTicket"]
        )
        == 0
    )
    entry = bibsync.parse(path.read_text())[0]
    assert (entry.key, entry.get("keywords")) == ("LotteryTicket", None)
    assert bibsync.main(["check", str(path)]) == 0


def test_cited_keys_skip_comments_and_find_custom_cite_macros(tmp_path):
    tex = tmp_path / "a.tex"
    tex.write_text(
        "See \\ccite{LIGOScientific:2016aoc} and \\textcite[p.~3][]{A:2020ab, B:2021cd}.\n"
        "% \\cite{Commented:2020xx}\n"
        "\\newcommand{\\ccite}[1]{Ref.~\\cite{#1}}\n"
        "50\\% of \\parencite*{A:2020ab}\\nocite{*}\\fullcite{LotteryTicket}\n"
        "\\Cite{C} \\textcites(see)()[p.~3]{D}[]{E, F} \\cite{G} {\\em text}\n"
    )
    assert bibsync.cited_keys([tex]) == [
        GW150914,
        "A:2020ab",
        "B:2021cd",
        "LotteryTicket",
        *"CDEFG",
    ]


def test_missing_lists_absent_keys_and_adds_the_texkeys(tmp_path, capsys):
    bib = write(tmp_path, "@misc{LotteryTicket, eprint = {1803.03635}}\n")
    tex = tmp_path / "a.tex"
    tex.write_text("\\cite{LIGOScientific:2016aoc, LotteryTicket, Einstein1905}\n")

    assert bibsync.main(["missing", str(bib), str(tex)]) == 1
    out = capsys.readouterr().out
    assert f"missing    {GW150914}\n" in out
    assert "Einstein1905: not a texkey" in out
    assert "LotteryTicket" not in out

    # Einstein1905 remains
    assert bibsync.main(["missing", str(bib), str(tex), "--add"]) == 1
    keys = [e.key for e in bibsync.entries(bibsync.parse(bib.read_text()))]
    assert keys == ["LotteryTicket", GW150914]
    tex.write_text("\\cite{LIGOScientific:2016aoc, LotteryTicket}\n")
    assert bibsync.main(["missing", str(bib), str(tex)]) == 0


def test_missing_reports_unknown_texkeys(tmp_path, capsys):
    bib = write(tmp_path, "")
    tex = tmp_path / "a.tex"
    tex.write_text("\\cite{Nobody:2099zzz}\n")
    assert bibsync.main(["missing", str(bib), str(tex), "--add"]) == 1
    assert (
        "FAILED     Nobody:2099zzz: no database has this record"
        in capsys.readouterr().out
    )
    assert bib.read_text() == ""


def test_keep_fields_and_manual_entries_are_left_alone(tmp_path, capsys):
    manual = "@book{NAS:2023abc,\n  title = {Decadal survey},\n  bibsync = {manual},\n}"
    path = write(
        tmp_path,
        f"@article{{{GW150914}, note = {{Erratum}}, usera = {{mine}}, year = {{1}}}}\n\n"
        + manual
        + "\n",
    )
    assert bibsync.main(["update", str(path), "--keep", "note,usera"]) == 0
    gw, kept = bibsync.entries(bibsync.parse(path.read_text()))
    assert (gw.get("note"), gw.get("usera"), gw.get("year")) == (
        "Erratum",
        "mine",
        "2016",
    )
    assert kept.raw == manual
    assert "unmanaged  NAS:2023abc: bibsync = {manual}" in capsys.readouterr().out


def test_prune_lists_and_removes_uncited_entries(tmp_path, capsys):
    path = write(
        tmp_path,
        "% header\n\n@misc{A, title = {a}}\n\n@misc{B, title = {b}}\n\n"
        "% about C\n@misc{C, title = {c}}\n\n@misc{D, title = {d}}\n",
    )
    tex = tmp_path / "a.tex"
    tex.write_text("\\cite{C} % \\cite{B}\n")

    assert bibsync.main(["prune", str(path), str(tex)]) == 1
    assert capsys.readouterr().out.split() == [
        "uncited",
        "A",
        "uncited",
        "B",
        "uncited",
        "D",
    ]
    assert bibsync.main(["prune", str(path), str(tex), "--remove"]) == 0
    assert path.read_text() == "% header\n\n% about C\n@misc{C, title = {c}}\n"
    assert bibsync.main(["prune", str(path), str(tex)]) == 0


def test_prune_refuses_when_everything_is_cited(tmp_path):
    path = write(tmp_path, "@misc{A, title = {a}}\n")
    tex = tmp_path / "a.tex"
    tex.write_text("\\nocite{*}\n")
    with pytest.raises(SystemExit, match="nothing to prune"):
        bibsync.main(["prune", str(path), str(tex), "--remove"])
    assert path.read_text() == "@misc{A, title = {a}}\n"


def test_full_authors_replaces_inspires_shortened_list(tmp_path, capsys):
    path = write(
        tmp_path,
        f"@article{{{RIESS},\n  keywords = {{dark energy}},\n  bibsync = {{full-authors}},\n}}\n",
    )
    assert bibsync.main(["update", str(path)]) == 0
    entry = bibsync.parse(path.read_text())[0]
    # not "Riess, Adam G. and others"
    assert entry.get("author") == " and ".join(RIESS_AUTHORS)
    assert entry.get("journal") == "Astron. J."
    assert (entry.get("keywords"), entry.get("bibsync")) == (
        "dark energy",
        "full-authors",
    )
    assert bibsync.main(["check", str(path)]) == 0

    other = write(tmp_path, "")
    assert bibsync.main(["add", str(other), RIESS, "--full-authors"]) == 0
    assert other.read_text() == path.read_text().replace(
        "  keywords = {dark energy},\n", ""
    )


def test_unknown_bibsync_setting_fails(tmp_path, capsys):
    path = write(tmp_path, f"@article{{{GW150914}, bibsync = {{full-author}}}}\n")
    assert bibsync.main(["check", str(path)]) == 1
    assert "unknown setting bibsync = {full-author}" in capsys.readouterr().out


def test_a_network_failure_fails_only_that_entry(tmp_path, capsys, monkeypatch):
    bad_url = bibsync.inspire_search("Bad:2020aa", format="bibtex")
    monkeypatch.setitem(RESPONSES, bad_url, requests.ConnectionError("reset"))
    path = write(
        tmp_path, f"@article{{Bad:2020aa}}\n@article{{{GW150914}, year = {{1}}}}\n"
    )
    assert bibsync.main(["update", str(path)]) == 1
    assert "FAILED     Bad:2020aa: reset" in capsys.readouterr().out
    assert bibsync.entries(bibsync.parse(path.read_text()))[1].get("year") == "2016"

    bib = write(tmp_path, "")
    tex = tmp_path / "a.tex"
    tex.write_text("\\cite{Bad:2020aa, LIGOScientific:2016aoc}\n")
    assert bibsync.main(["missing", str(bib), str(tex), "--add"]) == 1
    assert "FAILED     Bad:2020aa: reset" in capsys.readouterr().out
    assert bibsync.entries(bibsync.parse(bib.read_text()))[0].key == GW150914


def test_a_response_without_bibtex_falls_through_to_the_next_source(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(RESPONSES, "https://doi.org/10.1000/x", "Not BibTeX\n")
    path = write(
        tmp_path, "@misc{LotteryTicket, doi = {10.1000/x}, eprint = {1803.03635}}\n"
    )
    assert bibsync.main(["update", str(path)]) == 0
    assert bibsync.parse(path.read_text())[0].get("primaryClass") == "cs.LG"


def test_http_get_returns_none_without_a_record(monkeypatch):
    def fake_response(status, content_type, body):
        r = requests.Response()
        r.status_code, r._content = status, body.encode("utf-8")
        r.headers["Content-Type"] = content_type
        return r

    monkeypatch.undo()  # the real http_get
    for response, expected in [
        (
            fake_response(200, "application/x-bibtex", "@misc{Schrödinger}"),
            "@misc{Schrödinger}",
        ),
        (fake_response(404, "text/plain", "not found"), None),
        (fake_response(200, "text/html; charset=utf-8", "@media{}"), None),
    ]:
        monkeypatch.setattr(bibsync.session, "get", lambda *a, **k: response)
        assert bibsync.http_get("https://example.org", "*/*") == expected
    monkeypatch.setattr(
        bibsync.session, "get", lambda *a, **k: fake_response(503, "", "")
    )
    with pytest.raises(requests.HTTPError):
        bibsync.http_get("https://example.org", "*/*")


def test_errors_are_reported_without_a_traceback(tmp_path):
    path = write(tmp_path, '@misc{a, title = "unterminated}\n')
    with pytest.raises(SystemExit, match="^bibsync: unbalanced"):
        bibsync.main(["check", str(path)])


@online
def test_online_sources_still_answer_in_the_expected_format(monkeypatch):
    monkeypatch.undo()  # real HTTP
    source, record = bibsync.resolve(GW150914)
    assert (source, record.key, record.get("doi")) == (
        "INSPIRE",
        GW150914,
        "10.1103/PhysRevLett.116.061102",
    )
    assert record.get("author") == "Abbott, B. P. and others"  # INSPIRE shortens >10
    assert bibsync.resolve("", doi="10.1113/jphysiol.1952.sp004764")[0] == "doi.org"
    assert bibsync.resolve("", doi="10.48550/arxiv.1803.03635")[0] == "doi.org"
    assert bibsync.resolve("", eprint="astro-ph/9805201")[0] == "INSPIRE"
    assert bibsync.resolve("", eprint="1803.03635")[0] == "arXiv"
    assert bibsync.resolve("Nobody:2099zzz") is None
    full = bibsync.full_authors(bibsync.resolve(RIESS)[1]).get("author")
    assert full == " and ".join(RIESS_AUTHORS)
