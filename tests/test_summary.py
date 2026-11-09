"""browser_view's Upload → Summary page: the union of transcripts, letters and profiles."""

import json

import pytest

from browser_view.app import _summary_rows, create_app


@pytest.fixture
def cfg(tmp_path):
    work, letters, personal, pdfs = (tmp_path / n for n in (
        "work/transcripts", "in/letters", "work/personal", "in/transcripts"))
    for d in (work, letters, personal, pdfs):
        d.mkdir(parents=True)
    (work / "ann_lee.common.json").write_text("{}")
    (work / "bob.common.json").write_text("{}")
    (pdfs / "bob.pdf").write_bytes(b"%PDF")  # a PDF alone is not a key of its own
    (pdfs / "cy.pdf").write_bytes(b"%PDF")   # not converted: not uploaded
    for name in ("ann_lee_letter1.pdf", "ann_lee_letter2.pdf", "dee_letter1.pdf"):
        (letters / name).write_bytes(b"%PDF")
    (letters / "ann_lee_letter1.txt").write_text("text")  # only PDFs count
    (tmp_path / "work" / "letters").mkdir()
    (tmp_path / "work" / "letters" / "dee_letter2.txt").write_text("text")  # nor work files
    (personal / "ann_lee.json").write_text(json.dumps({"name": "Ann M. Lee"}))
    (personal / "eve.json").write_text(json.dumps({"age": 20}))
    config = tmp_path / "config.yaml"
    config.write_text(f"input-folder: {tmp_path / 'in'}\nwork-folder: {tmp_path / 'work'}\n")
    return {"input-folder": str(tmp_path / "in"), "work-folder": str(tmp_path / "work"),
            "config": config}


def test_rows_are_the_union_of_all_three(cfg):
    rows = {r["key"]: r for r in _summary_rows(cfg)}
    assert list(rows) == ["ann_lee", "bob", "dee", "eve"]
    assert rows["ann_lee"] == {"key": "ann_lee", "name": "Ann M. Lee", "profile": True,
                               "transcript": True, "recommendations": 2}
    assert rows["bob"] == {"key": "bob", "name": "Bob", "profile": False,
                           "transcript": True, "recommendations": 0}
    assert rows["dee"]["recommendations"] == 1 and not rows["dee"]["transcript"]
    assert rows["eve"]["name"] == "Eve" and rows["eve"]["profile"]  # no "name": from the key


def test_missing_folders_give_no_rows(tmp_path):
    assert _summary_rows({"input-folder": str(tmp_path / "x"),
                          "work-folder": str(tmp_path / "y")}) == []
    assert _summary_rows({}) == []  # nothing configured


def test_summary_page_is_under_upload(cfg):
    body = create_app(cfg["config"]).test_client().get("/summary").get_data(as_text=True)
    assert 'href="/summary" class="sub active" aria-current="page">Summary' in body
    nav = body[body.index('<nav class="side"'):body.index("</nav>")]
    assert nav.index(">Upload</span>") < nav.index(">Summary</a>") < nav.index(">View</span>")
    assert "Ann M. Lee" in body and "<td>Bob</td>" not in body  # Bob's cell carries a title
    assert '<td title="bob">Bob</td>' in body
    assert body.count('<span class="pass">✓</span>') == 4  # ann x2, bob transcript, eve profile
    assert '<a href="/" class="home">Upload Summary</a>' in body and "<h2>Summary</h2>" not in body
    assert ">Reload</button>" not in body
    assert "Total records" not in body
    # The Show filter and the shown-row count sit between the counts and the students.
    assert (body.index('class="summary-counts"') < body.index('id="summary-filter"')
            < body.index('id="shown-count"') < body.index('id="summary"'))
    counts = body[body.index('class="summary-counts"'):body.index('id="summary"')]
    assert '<th class="num">Students</th><th class="num">Available</th>' in counts
    for label, available, missing in (("Profiles", 2, 2), ("Transcripts", 2, 2),
                                      ("Recommendations", 2, 2)):
        assert f'<th scope="row">{label}</th><td class="num">4</td><td class="num">{available}</td>' in counts
        assert f'<td class="num fail">{missing}</td>' in counts


def test_summary_page_with_nothing_says_so(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"input-folder: {tmp_path / 'in'}\nwork-folder: {tmp_path / 'w'}\n")
    body = create_app(config).test_client().get("/summary").get_data(as_text=True)
    assert "no transcripts, letters or profiles yet" in body
