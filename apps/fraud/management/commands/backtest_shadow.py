"""Replay ACTIVE rules over stored shadow snapshots (spec PART 34).

Read-only: produces distribution stats and the enforcement gate verdict.
No decision is ever changed by this command.
"""
import json

from django.core.management.base import BaseCommand

from apps.fraud.backtesting import backtest, enforcement_gate
from apps.fraud.models import RiskRule
from apps.fraud.observability import engine_metrics


class Command(BaseCommand):
    help = "Backtest active rules against shadow evaluation history"

    def add_arguments(self, parser):
        parser.add_argument("--window-hours", type=int, default=24 * 30)
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **opts):
        ruleset = list(RiskRule.objects.filter(enabled=True))
        report = backtest(ruleset)
        gate = enforcement_gate(report)
        metrics = engine_metrics(window_hours=opts["window_hours"])
        out = {
            "ruleset_size": len(ruleset),
            "backtest": report,
            "enforcement_gate": gate,
            "engine_metrics": metrics,
        }
        if opts["as_json"]:
            self.stdout.write(json.dumps(out, indent=2, default=str))
        else:
            self.stdout.write(f"rules={out['ruleset_size']} evaluations={report['total']}")
            self.stdout.write(f"decision_distribution={report['decisions']}")
            self.stdout.write(
                f"labels_available={report['labels_available']} ({report['note']})")
            self.stdout.write(f"gate={'PASS' if gate['pass'] else 'FAIL'} {gate}")
            lat = metrics["latency_ms"]
            self.stdout.write(f"engine_errors_24h={metrics['engine_errors']} latency={lat}")
