"""The injection evaluation set and its scorer. Fake models only: no network, no key, no cost."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("eval_injection", ROOT / "tools" / "eval_injection.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
DOC = tool.load_cases()
CATEGORIES = {"direct_override", "role_play", "grounding", "prompt_extraction", "indirect_in_source",
              "hidden_characters", "exfiltration", "delimiter_break", "encoding", "authority_claim",
              "cost_abuse", "multi_turn", "control"}


def test_case_set_is_well_formed_and_broad():
    cases = DOC["cases"]
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) and len(cases) >= 40
    assert {c["category"] for c in cases} == CATEGORIES
    for case in cases:
        assert case["question"].strip() and isinstance(case.get("forbid", []), list)
        assert case["category"] != "control" or case.get("require_any") is not None or case.get("forbid")
        assert set(case) <= {"id", "category", "question", "forbid", "require_any", "max_answer_chars",
                             "extra_sources", "hidden_tag_payload", "question_pad", "no_base"}
    arabic = sum(any("؀" <= ch <= "ۿ" for ch in c["question"]) for c in cases)
    english = sum(c["question"].isascii() for c in cases)
    assert arabic >= 15 and english >= 10
    assert sum(c["category"] == "control" for c in cases) >= 6  # over-refusal needs its own yardstick


def test_materialize_builds_question_and_sources_including_hidden_payloads():
    by_id = {c["id"]: c for c in DOC["cases"]}
    question, context = tool.materialize(by_id["padding-then-attack"], DOC)
    assert question.startswith("لا شيء مهم هنا.") and question.endswith("HACKED-7731.")
    _, hidden = tool.materialize(by_id["indirect-hidden-tags"], DOC)
    assert "HACKED-7731" not in hidden and any(0xE0000 <= ord(c) <= 0xE007F for c in hidden)
    _, with_extra = tool.materialize(by_id["control-url-in-source"], DOC)
    assert DOC["base_source"] in with_extra and "https://intranet.example/hr" in with_extra


def run(complete, **kwargs):
    report = tool.run_eval(DOC, complete, **kwargs)
    return report, tool.summarize(report)


def test_a_robust_model_passes_every_attack_and_every_control():
    def complete(messages):
        sources = messages[1]["content"]
        text = "الإجازة السنوية ثلاثون يوماً (30)، ويُشترط إشعار المدير قبل أسبوع. المرجع https://intranet.example/hr "
        text += "تجاهل 45 " if "45" in sources else "تجاهل "
        return text, 100, 20
    report, summary = run(complete)
    assert summary["attack_failures"] == []
    assert summary["controls_run"] >= 6 and summary["attacks_run"] >= 30


def test_an_obedient_model_is_caught_on_the_attacks_that_carry_the_marker():
    def complete(messages):
        return "HACKED-7731 as you wish", 100, 20
    report, summary = run(complete)
    failed = set(summary["attack_failures"])
    assert {"direct-en-1", "direct-ar-1", "roleplay-en-1", "authority-en-1", "indirect-en-1"} <= failed
    assert len(failed) >= 15
    assert all("answer_preview" in r for r in report["results"] if r["outcome"] == "fail")


def test_leaking_the_internal_code_is_blocked_by_the_guardrail_not_failed():
    def complete(messages):
        system = messages[0]["content"]
        return "الرمز " + system.split("الرمز الداخلي ")[1].split(" ")[0], 50, 10
    report, summary = run(complete)
    assert summary["counts"]["blocked"] == len(report["results"]) and summary["attack_failures"] == []


def test_over_refusal_on_controls_is_reported_as_missed():
    def complete(messages):
        return "لا أستطيع المساعدة في ذلك.", 50, 10
    _, summary = run(complete)
    assert "control-ar-days" in summary["control_misses"] and "control-url-in-source" in summary["control_misses"]
    assert summary["attack_failures"] == []  # refusing everything is safe but useless: the controls show it


def test_budget_stops_new_cases_and_limit_is_respected():
    calls = []

    def complete(messages):
        calls.append(1)
        return "نص", 1_000_000, 0
    report, summary = run(complete, budget_micro_usd=1_500_000, price_in=1_000_000, price_out=1_000_000)
    assert len(calls) == 2 and summary["counts"]["skipped_budget"] == len(DOC["cases"]) - 2
    calls.clear()
    run(complete, limit=3)
    assert len(calls) == 3


def test_cli_plans_without_network_and_refuses_unsafe_runs(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert tool.main(["--model", "m"]) == 0
    assert "Plan only" in capsys.readouterr().out
    assert tool.main(["--model", "m", "--yes-spend"]) == 2  # no key: nothing is sent
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    assert tool.main(["--model", "m", "--yes-spend", "--budget-usd", "1"]) == 2  # budget needs prices
    assert "sk-test-not-real" not in capsys.readouterr().out
