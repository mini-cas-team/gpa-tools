"""rank_ai: Student Rank's filters from a request in plain words, with fake LLMs."""

import json
from urllib.parse import parse_qsl, urlsplit

import pytest

import rank_ai
from browser_view.app import create_app
from rank_ai import answer, prompt as prompts
from rank_ai.providers import PROVIDERS
from summary import Filter
from summary.personal import Profile
from summary.recommendation import Recommendation
from summary.transcript import Transcript

PEOPLE = [  # key, name, state, program, age
    ("ann", "Ann Lee", "New York", "Biology", 22),
    ("bob", "Bob Ray", "Texas", "History", 30),
    ("cy", "Cy Fox", "Maine", "Computer Science", 25),
]


@pytest.fixture
def cfg(tmp_path):
    work, inp = tmp_path / "work", tmp_path / "in"
    for d in (work / "jev", work / "transcripts", work / "personal", work / "letters", inp / "letters"):
        d.mkdir(parents=True)
    rows = ["student,STEM_GPA,all_courses_GPA,stem_all_pct,file"]
    for key, name, state, program, age in PEOPLE:
        (work / "transcripts" / f"{key}.common.json").write_text(json.dumps(
            {"source_file": f"{key}.pdf", "student": {"name": name}}))
        rows.append(f"{name},3.{age % 10},3.0,50,{key}.pdf")
        (work / "personal" / f"{key}.json").write_text(json.dumps(
            {"name": name, "age": age, "birth_date": f"{2026 - age}-01-01", "program": program,
             "major_residence": {"residence_country": "United States", "residence_state": state}}))
    (work / "jev" / "stem_gpa.csv").write_text("\n".join(rows) + "\n")
    (work / "jev" / "law_gpa.csv").write_text("student,LAW_GPA,file\n")
    config = tmp_path / "config.yaml"
    config.write_text(f"input-folder: {inp}\nwork-folder: {work}\nlog-folder: {tmp_path / 'log'}\n"
                      "active-category-builder: jev\nrank-ai:\n  providers: [fake]\n")
    return {"input-folder": str(inp), "work-folder": str(work), "log-folder": str(tmp_path / "log"),
            "rank-ai": {"providers": ["fake"]}, "config": config}


def objects(cfg, **profile):
    return [Transcript(cfg, "jev", "STEM", categories=["LAW", "STEM"]), Recommendation(cfg),
            Profile(cfg, **profile)]


class Script:
    """A fake provider: gives its replies in turn and keeps every prompt."""
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, cfg):  # the factory PROVIDERS holds
        return self

    def ask(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(filters, explanation="ok", unsupported=()):
    return ("Sure, here it is.\nBEGIN\n```json\n"
            + json.dumps({"filters": filters, "explanation": explanation,
                          "unsupported": list(unsupported)}) + "\n```\nEND")


# ---- the prompt: built from the objects, the request pasted in unchanged ----

QUESTIONS = ["find northeast resident students", "only the ones older than 24, sorted by STEM %",
             "students with excellent letters and a GPA over 3.5", "élèves de Californie? \"quoted\""]


def test_the_prompt_is_the_same_whatever_is_asked_but_the_request(cfg):
    objs = objects(cfg)
    specs = [s for o in objs for s in o.filter_specs()]
    current = [f for o in objs for f in o.current_filters()]
    built = [prompts.build(q, specs, current, {}, "2026-01-02") for q in QUESTIONS]
    for q, p in zip(QUESTIONS, built):
        assert f"REQUEST:\n{q}\n" in p
    # nothing in the prompt depends on the request except the request itself
    assert len({p.replace(q, "<q>") for q, p in zip(QUESTIONS, built)}) == 1


def test_the_prompt_lists_every_field_its_meaning_and_known_values(cfg):
    objs = objects(cfg)
    p = prompts.build("x", [s for o in objs for s in o.filter_specs()],
                      [f for o in objs for f in o.current_filters()],
                      {o.title: o.description for o in objs}, "2026-01-02")
    assert p.count("COLUMN Profile: The applicant's personal information") == 1
    assert '- M.state (in): M = major residence: the state the applicant mainly lives in; known values: "Maine", "New York", "Texas"' in p
    assert "- age (range, number): The applicant's age, in years" in p
    assert '- category (is): ' in p and 'known values: "LAW", "STEM"' in p
    assert "- category-gpa (range, number from 0 to 4): GPA (0–4) of the applicant's STEM courses" in p
    assert "- letters (range, number from 0 to any)" in p
    assert "Today is 2026-01-02." in p
    # the current filters, in the answer's own format
    assert '{"column": "Transcript", "field": "category", "op": "is", "value": "STEM"}' in p
    assert '{"column": "Profile", "field": "show", "op": "in", "values": ["institution", "program"]}' in p
    assert p.rstrip().endswith("END")


def test_the_prompt_says_a_new_condition_replaces_the_fields_filter_and_how_to_exclude_a_bound(cfg):
    objs = objects(cfg)
    p = prompts.build("x", [s for o in objs for s in o.filter_specs()],
                      [f for o in objs for f in o.current_filters()], {}, "2026-01-02")
    assert "replaces that field's current filter" in p
    assert "even when the current filter is stricter or already fits the request" in p
    assert '"more than 5" is "min": 6' in p


def test_a_long_value_list_is_cut_in_the_prompt(cfg):
    from summary import FilterSpec
    line = prompts.field_line(FilterSpec("C", "f", "in", "m", values=[str(i) for i in range(prompts.MAX_VALUES + 5)]))
    assert line.endswith(", and 5 more")


# ---- reading and checking an answer ----

def test_extract_takes_the_last_answer_object_in_any_wrapping():
    text = reply([{"column": "Profile", "field": "age", "op": "range", "min": 1}])
    assert answer.extract(text)["filters"][0]["field"] == "age"
    # a model that corrects itself: the last object counts
    two = 'BEGIN {"filters": [], "explanation": "a"} oops BEGIN {"filters": [], "explanation": "b"} END'
    assert answer.extract(two)["explanation"] == "b"
    assert answer.extract('{"filters": [], "explanation": "raw"}')["explanation"] == "raw"
    with pytest.raises(answer.NoAnswer):
        answer.extract("I can't help with that. {\"x\": 1}")


def test_check_spells_values_as_the_data_does_and_reports_what_does_not_fit(cfg):
    specs = [s for o in objects(cfg) for s in o.filter_specs()]
    checked = answer.check({"filters": [
        {"column": "profile", "field": "m.state", "op": "in", "values": ["new york", "MAINE", "NY"]},
        {"column": "Profile", "field": "age", "op": "range", "min": 30, "max": 20},  # swapped
        {"column": "Transcript", "field": "category-gpa", "op": "range", "min": 5},  # above 4
        {"column": "Transcript", "field": "category", "op": "in", "values": ["law"]},  # op from the spec
        {"column": "Profile", "field": "zodiac", "op": "in", "values": ["Leo"]},
        {"column": "Grades", "field": "x", "op": "in"},
        {"column": "Profile", "field": "birth_date", "op": "range", "max": "2001-02-03T00:00"},
    ], "explanation": "e", "unsupported": "same state as the school"}, specs)
    assert [f.to_dict() for f in checked.filters] == [
        {"column": "Profile", "field": "M.state", "op": "in", "values": ["New York", "Maine"]},
        {"column": "Profile", "field": "age", "op": "range", "min": 20.0, "max": 30.0},
        {"column": "Transcript", "field": "category", "op": "is", "value": "LAW"},
        {"column": "Profile", "field": "birth_date", "op": "range", "max": "2001-02-03"},
    ]
    assert checked.unsupported == ["same state as the school"] and checked.explanation == "e"
    problems = " | ".join(checked.problems)
    assert "Profile M.state has no value 'NY'" in problems and '"Maine", ' not in problems
    assert "Transcript category-gpa min: 5 is outside 0–4" in problems
    assert "column Profile has no field 'zodiac'" in problems
    assert "there is no column 'Grades'" in problems


def test_check_rejects_a_filters_value_that_is_not_a_list():
    assert answer.check({"filters": "none"}, []).problems == ['"filters" must be a list']


# ---- each object's filters back as Student Rank query params ----

def test_params_give_each_object_its_filters(cfg):
    filters = [Filter("Profile", "M.state", "in", ["Maine", "New York"]),
               Filter("Profile", "age", "range", min=20.0),
               Filter("Transcript", "category-pct", "range", min=10.0, max=50.5),
               Filter("Transcript", "rank-by", "is", ["category-pct"]),
               Filter("Recommend Letter", "group-rank", "range", min=80.0)]
    assert rank_ai.params_for(objects(cfg), filters) == [
        ("category", "STEM"), ("by", "category-pct"), ("pct_min", "10"), ("pct_max", "50.5"),
        ("score_min", "80"),
        # a filtered field is shown (only a shown field filters), after the ones shown now
        ("prof_show", "institution"), ("prof_show", "program"), ("prof_show", "M.state"),
        ("prof_show", "age"), ("prof_in.M.state", "Maine"), ("prof_in.M.state", "New York"),
        ("prof_min.age", "20")]
    # a show filter replaces the fields shown
    shown = rank_ai.params_for(objects(cfg), [Filter("Profile", "show", "in", ["age"])])
    assert [v for k, v in shown if k == "prof_show"] == ["age"]


def test_current_filters_round_trip_to_the_same_params(cfg):
    objs = objects(cfg, show=["program", "age"], ranges={"age": (20, None)}, values={"program": ["Biology"]})
    current = [f for o in objs for f in o.current_filters()]
    assert rank_ai.params_for(objs, current) == [
        ("category", "STEM"), ("by", "category-gpa"), ("prof_show", "program"), ("prof_show", "age"),
        ("prof_min.age", "20"), ("prof_in.program", "Biology")]


# ---- ask: providers in turn, problems told back ----

def test_ask_tells_the_problems_back_and_uses_the_corrected_answer(cfg, monkeypatch):
    fake = Script(reply([{"column": "Profile", "field": "M.state", "op": "in", "values": ["NY"]}]),
                  reply([{"column": "Profile", "field": "M.state", "op": "in", "values": ["New York"]}],
                        "New York only"))
    monkeypatch.setitem(PROVIDERS, "fake", fake)
    result = rank_ai.ask(cfg, "  " + QUESTIONS[0] + " ", objects(cfg), today="2026-01-02")
    assert f"REQUEST:\n{QUESTIONS[0]}\n" in fake.prompts[0]
    assert fake.prompts[1].startswith(fake.prompts[0])
    assert "YOUR LAST ANSWER:" in fake.prompts[1] and "Profile M.state has no value 'NY'" in fake.prompts[1]
    assert result.source == "fake" and result.explanation == "New York only" and result.problems == []
    assert ("prof_in.M.state", "New York") in result.params
    assert [a.error is None for a in result.attempts] == [False, True]
    log = (rank_ai.load_config(cfg)["log-folder"] / rank_ai.LOG_NAME).read_text()
    assert "request: \"find northeast resident students\"" in log and "answer by fake" in log


def test_ask_applies_what_fits_once_the_repairs_are_used_up(cfg, monkeypatch):
    bad = reply([{"column": "Profile", "field": "M.state", "op": "in", "values": ["New York", "Ohio"]}])
    monkeypatch.setitem(PROVIDERS, "fake", Script(bad, bad))
    result = rank_ai.ask(cfg, "q", objects(cfg))
    assert ("prof_in.M.state", "New York") in result.params and len(result.problems) == 1


def test_ask_goes_on_to_the_next_provider_when_one_fails(cfg, monkeypatch):
    monkeypatch.setitem(PROVIDERS, "fake", Script(OSError("unreachable")))
    monkeypatch.setitem(PROVIDERS, "other", Script("no json here", reply([], "nothing to change")))
    cfg["rank-ai"] = {"providers": ["fake", "other"], "repairs": 1}
    result = rank_ai.ask(cfg, "q", objects(cfg))
    assert result.source == "other" and result.explanation == "nothing to change"
    assert [a.line().split(" (")[0] for a in result.attempts] == ["fake try 1", "other try 1", "other try 2"]


def test_ask_fails_when_no_provider_answers(cfg, monkeypatch):
    monkeypatch.setitem(PROVIDERS, "fake", Script("?", "?"))
    with pytest.raises(rank_ai.AskFailed, match="fake try 2"):
        rank_ai.ask(cfg, "q", objects(cfg))
    with pytest.raises(ValueError):
        rank_ai.ask(cfg, "   ", objects(cfg))


def test_load_config_defaults_and_checks(cfg):
    cfg.pop("rank-ai")
    cfg["category-builder"] = {"llm": {"category-definition-model": "big:7b"}}
    merged = rank_ai.load_config(cfg)
    assert merged["providers"] == ["ollama"] and merged["model"] == "big:7b"
    assert merged["profile-folder"].name == "rank-ai" and merged["repairs"] == 1
    cfg["rank-ai"] = {"providers": "gemini", "model": "small:3b"}
    assert rank_ai.load_config(cfg)["providers"] == ["gemini"]
    assert rank_ai.load_config(cfg)["model"] == "small:3b"
    cfg["rank-ai"] = {"providers": ["bard"]}
    with pytest.raises(ValueError, match="bard"):
        rank_ai.load_config(cfg)


def test_ollama_provider_holds_the_reply_to_the_answer_schema(monkeypatch):
    from rank_ai import providers
    sent = {}

    def post(url, body, headers, timeout):
        sent.update(url=url, body=body)
        return {"response": '{"filters": []}'}
    monkeypatch.setattr(providers, "_post", post)
    out = providers.Ollama({"ollama-url": "http://h:1/", "model": "m", "timeout": 5, "num-ctx": 8192}).ask("p")
    assert out == '{"filters": []}' and sent["url"] == "http://h:1/api/generate"
    assert sent["body"]["format"] is answer.SCHEMA and sent["body"]["options"]["num_ctx"] == 8192


def test_claude_provider_needs_its_key_in_the_environment(monkeypatch):
    from rank_ai import providers
    monkeypatch.delenv("MY_KEY", raising=False)
    claude = providers.Claude({"claude-model": "m", "claude-api-key-env": "MY_KEY", "timeout": 5})
    with pytest.raises(OSError, match="MY_KEY"):
        claude.ask("p")
    monkeypatch.setenv("MY_KEY", "k")
    monkeypatch.setattr(providers, "_post", lambda url, body, headers, timeout: {
        "content": [{"type": "text", "text": "BEGIN "}, {"type": "text", "text": "{}"}]})
    assert claude.ask("p") == "BEGIN {}"


def test_a_browser_leg_enters_a_prompt_of_several_lines_with_shift_enter(monkeypatch):
    from category_definition_builder import browser_legs
    keys = []

    class Keyboard:
        def type(self, text): keys.append(("type", text))
        def insert_text(self, text): keys.append(("insert", text))
        def press(self, key): keys.append(("press", key))

    class Box:
        def wait_for(self, timeout): pass
        def click(self): pass

    class Page:
        keyboard = Keyboard()
        def goto(self, url): pass
        def get_by_role(self, role, name): return Box()

    class Context:
        pages = [Page()]
        def close(self): pass

    class Playwright:
        def stop(self): pass

    monkeypatch.setattr(browser_legs, "launch", lambda *a, **k: (Playwright(), Context()))
    monkeypatch.setattr(browser_legs, "_wait_for_stable_text", lambda *a, **k: "reply")
    assert browser_legs.query("gemini", "one\n\ntwo", "/tmp/x") == "reply"
    assert keys == [("insert", "one"), ("press", "Shift+Enter"), ("press", "Shift+Enter"),
                    ("insert", "two"), ("press", "Enter")]
    keys.clear()
    browser_legs.query("gemini", "one line", "/tmp/x")  # the definer's: typed as before
    assert keys == [("type", "one line"), ("press", "Enter")]


# ---- the page: Config → Ask AI ----

def test_the_page_has_the_ask_box(cfg):
    body = create_app(cfg["config"]).test_client().get("/view/rank").get_data(as_text=True)
    config = body[body.index('id="rank-panel-config"'):]
    assert '<form class="rank-ask" id="ask-form"' in config and '<button type="submit" id="ask-go">Go</button>' in config
    assert "fetch(`/view/rank/ask${location.search}`" in body
    # the requests sent are kept in the browser; ↑ ↓ move over them
    assert 'HISTORY_KEY = "student-rank-ask-history"' in body and "remember(question);" in body
    assert 'e.key !== "ArrowUp" && e.key !== "ArrowDown"' in body


def test_the_page_marks_new_or_changed_columns(cfg):
    body = create_app(cfg["config"]).test_client().get("/view/rank").get_data(as_text=True)
    assert 'COLUMNS_KEY = "student-rank-columns"' in body and 'th.classList.add("changed")' in body
    assert "#rank-summary td.changed" in body


def test_the_old_rank_routes_are_gone(cfg):
    client = create_app(cfg["config"]).test_client()
    assert client.get("/view/summary").status_code == 404
    assert client.post("/view/summary/ask", json={"question": "q"}).status_code == 404
    assert client.get("/view/rank").status_code == 200


def test_ask_route_starts_from_the_page_filters_and_returns_the_new_url(cfg, monkeypatch):
    fake = Script(reply([{"column": "Profile", "field": "M.state", "op": "in", "values": ["maine"]},
                         {"column": "Profile", "field": "age", "op": "range", "min": 21}],
                        "Maine", ["same state as the school"]))
    monkeypatch.setitem(PROVIDERS, "fake", fake)
    client = create_app(cfg["config"]).test_client()
    resp = client.post("/view/rank/ask?prof_show=age&prof_min.age=21",
                       json={"question": "students from Maine"})
    data = resp.get_json()
    assert resp.status_code == 200 and data["source"] == "fake"
    assert data["unsupported"] == ["same state as the school"] and data["explanation"] == "Maine"
    assert '{"column": "Profile", "field": "age", "op": "range", "min": 21.0}' in fake.prompts[0]
    url = urlsplit(data["url"])
    assert url.path == "/view/rank"
    assert parse_qsl(url.query) == [("category", "STEM"), ("by", "category-gpa"), ("prof_show", "age"),
                                    ("prof_show", "M.state"), ("prof_in.M.state", "Maine"),
                                    ("prof_min.age", "21")]
    # the page that URL opens applies those filters
    page = client.get(data["url"]).get_data(as_text=True)
    assert "M.state: Maine" in page and "age ≥ 21" in page


def test_ask_route_errors(cfg, monkeypatch):
    client = create_app(cfg["config"]).test_client()
    assert client.post("/view/rank/ask", json={"question": " "}).status_code == 400
    monkeypatch.setitem(PROVIDERS, "fake", Script(OSError("down")))
    resp = client.post("/view/rank/ask", data={"question": "q"})
    assert resp.status_code == 502 and resp.get_json()["attempts"] == ["fake try 1 (0.0s) failed: OSError: down"]
