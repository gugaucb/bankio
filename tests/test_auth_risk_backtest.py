"""R3 — LOGIN metrics & backtest over real wired evaluations (SHADOW)."""
import pytest
from django.test import Client

from apps.fraud.auth_metrics import login_backtest, login_evaluations, login_metrics
from apps.fraud.models import RiskEvaluation, RiskRule

PW = "Str0ng-pass!x"


@pytest.fixture(autouse=True)
def shadow(settings):
    settings.FRAUD_MODE = "SHADOW"


@pytest.fixture
def alice(django_user_model):
    return django_user_model.objects.create_user(
        "bt-alice", email="ba@t.io", password=PW, role="CUSTOMER")


def _seed_logins(n=6):
    for i in range(n):
        c = Client(HTTP_USER_AGENT=f"BT-{i}/1")
        c.post("/login/", {"username": "bt-alice", "password": PW})
    # a few failures for signal variety
    Client().post("/login/", {"username": "bt-alice", "password": "nope"})


# ------------------------------------------------------------------- metrics

@pytest.mark.django_db
def test_login_metrics_shape_and_rates(alice):
    _seed_logins()
    m = login_metrics(window_hours=None)
    assert m["operation"] == "LOGIN"
    assert m["total_logins_evaluated"] >= 5      # failed password is not evaluated
    assert set(m["decisions"]) == {"ALLOW", "CHALLENGE", "REVIEW", "BLOCK"}
    assert set(m["levels"]) == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert m["labels_available"] is False and "not fabricated" in m["note"]
    # SHADOW with no rules: everything ALLOW → zero interventions, honestly reported
    assert m["intervention_rate"] in (0, 0.0)
    assert m["challenge_rate"] == 0 and m["block_rate"] == 0
    assert m["latency_ms"]["p50"] is not None


@pytest.mark.django_db
def test_login_metrics_counts_decisions_with_rules(alice):
    RiskRule.objects.create(rule_id="BT-NEWDEV", name="n", score=45,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True,
                            operation_types=["LOGIN"],
                            conditions=[{"signal": "NEW_DEVICE", "op": "is",
                                         "value": True}])
    _seed_logins(4)
    m = login_metrics(window_hours=None)
    total = m["total_logins_evaluated"]
    assert m["decisions"]["CHALLENGE"] + m["decisions"]["ALLOW"] == total
    assert m["challenge_rate"] > 0               # new device rule fires on fresh logins
    assert m["block_rate"] == 0                  # nothing blocks in shadow sample


@pytest.mark.django_db
def test_engine_errors_counted_for_login(alice, monkeypatch):
    from django.test import RequestFactory

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("apps.fraud.rules.evaluate_rules", boom)
    from apps.identity.services import attempt_login

    rf = RequestFactory()
    attempt_login("bt-alice", PW, rf.post("/login/", HTTP_USER_AGENT="Err/1"))
    m = login_metrics(window_hours=None)
    assert m["engine_errors"] >= 1


# ----------------------------------------------------------------- backtest

@pytest.mark.django_db
def test_login_backtest_replays_real_evaluations(alice):
    RiskRule.objects.create(rule_id="BT-BIG", name="n", score=100,
                            lifecycle=RiskRule.Lifecycle.ACTIVE, enabled=True,
                            operation_types=["LOGIN"])
    _seed_logins(4)
    bt = login_backtest()
    assert bt["operation"] == "LOGIN"
    assert bt["total"] >= 4                      # replayed stored snapshots only
    assert bt["decisions"]["BLOCK"] == bt["total"]   # score-100 rule hits all
    assert bt["rates"]["block_rate"] == 1.0
    assert bt["gate"]["pass"] is False           # implausible block share → gate holds


@pytest.mark.django_db
def test_backtest_gate_passes_reasonable_ruleset(alice):
    _seed_logins(4)
    bt = login_backtest()                        # empty ruleset → all ALLOW
    assert bt["gate"]["pass"] is True
    assert bt["precision_recall"] is None        # never fabricated


@pytest.mark.django_db
def test_promotion_report_data_available(alice):
    """Everything the AUTH RISK PROMOTION REPORT needs is computable."""
    _seed_logins()
    m = login_metrics(window_hours=None)
    bt = login_backtest()
    report = {
        "Stage": "SHADOW",
        "Evaluated logins": m["total_logins_evaluated"],
        "ALLOW": m["decisions"]["ALLOW"],
        "CHALLENGE": m["decisions"]["CHALLENGE"],
        "REVIEW": m["decisions"]["REVIEW"],
        "BLOCK": m["decisions"]["BLOCK"],
        "Engine errors": m["engine_errors"],
        "Intervention rate": m["intervention_rate"],
        "Backtest gate": bt["gate"],
        "Known limitations": m["note"],
    }
    assert report["Evaluated logins"] >= 5
    assert report["Engine errors"] == 0
    assert isinstance(report["Backtest gate"]["pass"], bool)
    assert login_evaluations().filter(status="FAILED").count() == \
        RiskEvaluation.objects.filter(operation_type="LOGIN", status="FAILED").count()
