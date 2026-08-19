"""What the copilot can answer, and what it declines to turn into a question.

Two distinct complaints from a real deployment drive this file.

The first was that the assistant answered "hi" by reciting table counts. That
happened because the system prompt said nothing about messages that are not
questions, and ``overview`` advertised itself as the thing to call "when asked
a general question" -- which a greeting resembles. The fix is asserted here
against the loop rather than against the prompt text, because the providers in
the fallback chain include small free-tier models that follow instructions
unreliably: a rule that only lives in a prompt is a rule that holds on some
providers and not others.

The second was that the assistant was not useful. Row-level security, KPIs,
calculation groups and Power Query lineage were all extracted and none of them
were reachable from a tool, so entire categories of question could not be
answered at all -- not badly, but not at all. Those tools are exercised here
against the one sample model that actually defines all four.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.agent.chat import ModelChat
from concordance.agent.tools import ModelTools
from concordance.graph.csg import SemanticGraph
from concordance.llm.fake import FakeProvider, says

#: The richest sample: the only one defining roles, a KPI, a calculation group
#: and object-level security together.
RICH = Path("data/models/DiabetesCare.SemanticModel")
#: Has an inactive relationship that a measure genuinely activates, which is
#: the case the risk report has to describe without crying wolf.
QC = Path("data/models/QualityControl.SemanticModel")


def _graph(path: Path) -> SemanticGraph:
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    return SemanticGraph(TmdlAdapter().extract(str(path)))


@pytest.fixture(scope="module")
def rich() -> ModelTools:
    return ModelTools(_graph(RICH))


@pytest.fixture(scope="module")
def qc() -> ModelTools:
    return ModelTools(_graph(QC))


# -- greetings and other things that are not questions ------------------------

GREETINGS = ["hi", "Hi!", "hello", "  HELLO  ", "hey there", "good morning", "yo"]


@pytest.mark.parametrize("greeting", GREETINGS)
def test_a_greeting_never_reaches_the_language_model(greeting: str) -> None:
    """The specific reported failure: "hi" answered with model statistics.

    Asserted by counting provider calls rather than by inspecting the reply,
    because the guarantee that matters is that no model gets the chance to
    decide what a greeting deserves.
    """
    provider = FakeProvider(script=[says("counts and tables and joins")])
    chat = ModelChat(_graph(QC), provider)

    exchange = chat.ask(greeting)

    assert provider.calls == [], "a greeting must not cost a provider call"
    assert exchange.conversational
    assert "Hello" in exchange.answer


def test_a_greeting_offers_objects_that_are_really_in_this_model() -> None:
    """A canned suggestion naming a measure the open model lacks would teach
    someone the assistant invents things, in the first line they ever read."""
    graph = _graph(QC)
    chat = ModelChat(graph, FakeProvider(script=[]))

    answer = chat.ask("hi").answer

    names = {m.name for m in graph.model.measures} | {t.name for t in graph.model.tables}
    quoted = {part.split("`")[0] for part in answer.split("`")[1::2]}
    assert quoted, "the suggestions should name concrete objects"
    assert quoted <= names, f"suggested something not in the model: {quoted - names}"


@pytest.mark.parametrize(
    "question",
    [
        "hi, what measures are there?",
        "hello - which joins are inactive?",
        "thanks, now show me the DAX for OOS Rate",
        "what does OK mean in the status column?",
        "ok so what does Batch Yield % compute",
    ],
)
def test_a_real_question_is_never_swallowed_as_small_talk(question: str) -> None:
    """The failure mode the whole-message rule exists to prevent.

    Matching a greeting as a substring would answer "hi, what measures are
    there?" with a wave. Missing an unusual greeting costs one ordinary round
    trip; eating a real question costs the answer.
    """
    provider = FakeProvider(script=[says("a real answer")])
    chat = ModelChat(_graph(QC), provider)

    exchange = chat.ask(question)

    assert len(provider.calls) == 1, "a real question must reach the provider"
    assert not exchange.conversational


def test_what_can_you_do_is_answered_from_the_tools_that_exist() -> None:
    provider = FakeProvider(script=[])
    chat = ModelChat(_graph(QC), provider)

    answer = chat.ask("what can you do").answer

    assert provider.calls == []
    assert "QualityControl" in answer
    assert "You could ask" in answer


def test_a_greeting_is_not_badged_as_an_unverified_claim() -> None:
    """`grounded` false drives a "no tool used" warning in the interface. On a
    greeting that warning is a lie about a sentence that asserts nothing."""
    exchange = ModelChat(_graph(QC), FakeProvider(script=[])).ask("hi")
    assert exchange.conversational is True
    assert exchange.grounded is False


def test_a_greeting_does_not_take_up_room_in_the_history() -> None:
    """History is resent in full on every request, so a hello that lingered
    would be paid for on every later question."""
    chat = ModelChat(_graph(QC), FakeProvider(script=[says("x")]))
    chat.ask("hi")
    assert chat.history == []


# -- security, which could not be asked about at all before -------------------

def test_security_reports_the_filter_not_just_the_role_name(rich: ModelTools) -> None:
    """"A role called Cohort Clinician exists" tells a reader nothing about who
    sees what. The filter expression is the answer to that question."""
    result = rich.describe_security()

    roles = {role["name"]: role for role in result["roles"]}
    assert "Cohort Clinician" in roles

    filters = roles["Cohort Clinician"]["row_filters"]
    assert filters, "a restricting role must report what it restricts"
    assert any("Age Band" in f["filter_dax"] for f in filters)


def test_object_level_security_is_reported_beside_its_role(rich: ModelTools) -> None:
    """A hidden column restricts what a role sees just as a row filter does, so
    reporting one without the other would answer "who sees what" incompletely."""
    roles = {role["name"]: role for role in rich.describe_security()["roles"]}
    hidden = roles["Cohort Clinician"]["object_level_security"]
    assert any(entry["column"] == "DiabetesPedigreeFunction" for entry in hidden)


def test_a_model_without_security_says_so_rather_than_returning_nothing(
    qc: ModelTools,
) -> None:
    """An empty list reads as "I could not find out". The consequence -- that
    everyone sees every row -- is the actual answer and has to be stated."""
    result = qc.describe_security()
    assert result["roles"] == []
    assert "no row-level" in result["note"]


# -- KPIs and calculation groups ---------------------------------------------

def test_kpis_carry_the_expressions_behind_them(rich: ModelTools) -> None:
    kpi = rich.list_kpis()["kpis"][0]
    assert kpi["measure"] == "Diabetes Prevalence"
    assert kpi["target_expression"]
    assert "DIVIDE" in kpi["status_expression"]


def test_calculation_items_are_listed_with_their_dax(rich: ModelTools) -> None:
    """These rewrite other measures at query time, so a reader looking only at
    a measure's own expression is not seeing what actually runs."""
    group = rich.list_calculation_groups()["calculation_groups"][0]
    items = {item["name"]: item["expression"] for item in group["items"]}
    assert "SELECTEDMEASURE()" in items["All Patients"]
    assert any("Glucose" in expression for expression in items.values())


def test_items_come_back_in_the_order_they_are_applied(rich: ModelTools) -> None:
    group = rich.list_calculation_groups()["calculation_groups"][0]
    ordinals = [item["ordinal"] for item in group["items"]]
    assert ordinals == sorted(ordinals)


# -- lineage ------------------------------------------------------------------

def test_trace_source_names_the_system_and_the_steps(qc: ModelTools) -> None:
    result = qc.trace_source("Batch")
    assert any(source["detail"].endswith(".csv") for source in result["sources"])
    assert [step["order"] for step in result["steps"]] == sorted(
        step["order"] for step in result["steps"]
    )


def test_trace_source_flags_steps_that_change_the_row_count(rich: ModelTools) -> None:
    """A table filtered on the way in is not its source file, and someone
    reconciling against that file needs to know which step explains the gap."""
    for table in rich.model.tables:
        result = rich.trace_source(table.name)
        for step in result.get("steps", []):
            assert isinstance(step["can_change_row_count"], bool)
            if step["can_change_row_count"]:
                assert step["name"] in result["steps_that_can_change_row_count"]


def test_an_unknown_table_suggests_rather_than_inventing(qc: ModelTools) -> None:
    result = qc.trace_source("Bacth")
    assert "error" in result
    assert "Batch" in result["did_you_mean"]


def test_a_table_with_no_query_is_distinguished_from_one_loading_nothing(
    rich: ModelTools,
) -> None:
    """Two different claims. "Loads from nowhere" would be a confident wrong
    answer where "no query was recorded" is the honest one."""
    without = [t for t in rich.model.tables if not t.power_query]
    if not without:
        pytest.skip("every table in this model has a query")
    result = rich.trace_source(without[0].name)
    assert result["sources"] == []
    assert "note" in result


# -- searching inside expressions --------------------------------------------

def test_find_in_dax_matches_what_a_name_search_cannot(rich: ModelTools) -> None:
    """`OOS Rate` has no "DIVIDE" in its name, so `search` can never find it
    from the question "which measures divide by something"."""
    result = rich.find_in_dax("DIVIDE")

    assert result["total_matches"] > 0
    assert result["measures"], "a measure using DIVIDE should be found"
    assert all("DIVIDE" in m["expression"].upper() for m in result["measures"])


def test_find_in_dax_reaches_every_kind_of_expression(rich: ModelTools) -> None:
    """A security filter and a calculation item change numbers just as a
    measure does. Searching only measures would make an empty result mean two
    different things."""
    result = rich.find_in_dax("DIVIDE")
    assert result["calculation_items"], "calculation items must be searched"
    assert result["kpis"], "KPI expressions must be searched"


def test_find_in_dax_says_which_kinds_this_model_even_has(qc: ModelTools) -> None:
    """"No calculation items matched" and "this model has no calculation
    groups" are the same empty list, and only one means look elsewhere."""
    result = qc.find_in_dax("CALCULATE")
    assert "no calculation items" in result["note"] or "no KPIs" in result["note"]


def test_an_empty_search_is_refused_rather_than_matching_everything(
    qc: ModelTools,
) -> None:
    assert "error" in qc.find_in_dax("   ")


# -- structural risks ---------------------------------------------------------

def test_an_inactive_join_is_reported_with_what_activates_it(qc: ModelTools) -> None:
    """The nuance that keeps this honest. An inactive relationship is not a
    defect when a measure invokes it with USERELATIONSHIP, and reporting it
    without checking would be crying wolf on correct design."""
    findings = {f["kind"]: f for f in qc.find_structural_risks()["findings"]}

    inactive = findings["inactive_relationship"]
    assert inactive["count"] >= 1
    assert inactive["measures_invoking_userelationship"], (
        "this model does activate it, and the finding must say so"
    )


def test_two_measures_sharing_one_definition_are_proven_not_guessed() -> None:
    """Identical canonical DAX means identical logic -- the same fingerprint
    argument the rest of the project rests on, not a similarity score."""
    graph = _graph(QC)
    model = graph.model
    original = model.measures[0]
    twin = type(original)(
        table=original.table,
        name=f"{original.name} Copy",
        expression=original.expression,
        fingerprint=original.fingerprint,
    )
    model.measures.append(twin)
    try:
        findings = {
            f["kind"]: f for f in ModelTools(graph).find_structural_risks()["findings"]
        }
        assert "same_definition_under_two_names" in findings
        pair = findings["same_definition_under_two_names"]["detail"][0]
        assert f"{original.table}[{twin.name}]" in pair
    finally:
        model.measures.remove(twin)


def test_every_finding_names_the_objects_it_is_about(qc: ModelTools) -> None:
    """A finding a reader cannot check is an assertion, not evidence."""
    for finding in qc.find_structural_risks()["findings"]:
        assert finding["detail"], f"{finding['kind']} named nothing"
        assert finding["why_it_matters"]
        assert finding["count"] >= 1


def test_the_risk_report_does_not_call_its_findings_defects(qc: ModelTools) -> None:
    """Each of these may be entirely deliberate, and a tool that says otherwise
    teaches a reviewer to ignore it."""
    result = qc.find_structural_risks()
    assert "not defects" in result["how_to_read_this"]


def test_the_risk_report_is_stable_across_runs(qc: ModelTools) -> None:
    """Computed from the graph by fixed rules, so it can be checked by hand."""
    assert qc.find_structural_risks() == qc.find_structural_risks()


# -- the tool surface as a whole ---------------------------------------------

def test_new_tools_are_declared_and_dispatchable(qc: ModelTools) -> None:
    """A handler nobody declared is unreachable; a declaration with no handler
    fails only once someone asks the right question."""
    declared = {spec.name for spec in qc.specs()}
    for name in (
        "find_in_dax",
        "describe_security",
        "list_kpis",
        "list_calculation_groups",
        "trace_source",
        "find_structural_risks",
    ):
        assert name in declared
        assert name in qc._handlers


def test_no_tool_raises_on_the_argument_a_model_would_guess(qc: ModelTools) -> None:
    """A wrong argument must come back as a correctable message, never as a
    traceback that ends the conversation."""
    for spec in qc.specs():
        result = qc.dispatch(spec.name, {"nonsense": "value"})
        assert isinstance(result, (dict, list))
