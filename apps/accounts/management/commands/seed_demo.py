"""Seed realistic Bankio data. Idempotent: skips if admin exists."""
import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import Account, Beneficiary
from apps.cards.models import Card
from apps.compliance.models import FraudRule
from apps.customers.models import Customer
from apps.investments.models import Instrument, Position
from apps.lending.models import LoanProduct
from apps.notifications.models import Notification
from apps.transfers.services import execute_transfer
from apps.ledger import services as ledger

FIRST_NAMES = ["Aubrey", "Liam", "Olivia", "Noah", "Emma", "Ethan", "Sophia", "Mason",
               "Isabella", "Lucas", "Mia", "Aiden", "Harper", "Elijah", "Evelyn",
               "James", "Amelia", "Benjamin", "Abigail", "Henry", "Emily"]
LAST_NAMES = ["Sabina", "Johnson", "Smith", "Garcia", "Miller", "Davis", "Wilson",
              "Anderson", "Taylor", "Thomas", "Moore", "Martin", "Lee", "Clark"]


class Command(BaseCommand):
    help = "Seed demo banking data"

    def handle(self, *args, **options):
        User = get_user_model()
        if User.objects.filter(username="admin").exists():
            self.stdout.write("Already seeded.")
            return

        rng = random.Random(42)

        # ---- staff ----
        staff_specs = [
            ("admin", "ADMIN"), ("manager1", "MANAGER"), ("manager2", "MANAGER"),
            ("manager3", "MANAGER"), ("cardops1", "CARD_OPS_ANALYST"),
            ("cardops2", "CARD_OPS_ANALYST"), ("compliance1", "COMPLIANCE_ANALYST"),
            ("compliance2", "COMPLIANCE_ANALYST"), ("support1", "SUPPORT_AGENT"),
            ("support2", "SUPPORT_AGENT"), ("auditor", "AUDITOR"),
        ]
        managers = []
        from apps.managerops.models import BankBranch, ManagerProfile, CustomerManagerAssignment

        branches = [
            BankBranch.objects.create(branch_code="1001", name="Downtown", region="NORTH"),
            BankBranch.objects.create(branch_code="2002", name="Harbor", region="SOUTH"),
        ]
        manager_levels = ["RELATIONSHIP_MANAGER", "BRANCH_MANAGER", "SENIOR_MANAGER"]
        for username, role in staff_specs:
            u = User.objects.create_user(
                username=username, email=f"{username}@bankio.com",
                password="Bankio!2026", first_name=username.capitalize(),
                last_name="Staff", role=role,
            )
            if role == "MANAGER":
                idx = len(managers)
                ManagerProfile.objects.create(user=u, level=manager_levels[idx % 3], branch=branches[idx % 2])
                managers.append(u)

        # ---- customers ----
        customers = []
        pairs = list(zip(FIRST_NAMES * 2, LAST_NAMES * 2))[:25]
        for i, (first, last) in enumerate(pairs):
            role = "PREMIUM_CUSTOMER" if i == 0 else "CUSTOMER"
            u = User.objects.get_or_create(
                username=f"{first.lower()}.{last.lower()}{i}",
                defaults=dict(
                    email=f"{first.lower()}.{last.lower()}{i}@example.com",
                    first_name=first, last_name=last, role=role,
                ),
            )[0]
            u.set_password("Customer!2026")
            u.save()
            mgr = rng.choice(managers)
            Customer.objects.get_or_create(user=u, customer_number=f"CUST-{1000+i}",
                                           defaults=dict(assigned_manager=mgr, branch=mgr.manager_profile.branch))
            CustomerManagerAssignment.objects.get_or_create(
                customer=u, manager=mgr, defaults={"branch": mgr.manager_profile.branch})
        from apps.compliance.models import KYCReview

        for u in customers:
            KYCReview.objects.get_or_create(customer=u, defaults=dict(status="APPROVED", risk_level="LOW"))
            customers.append(u)

        aubrey = customers[0]

        def open_account(user, atype, balance, number):
            la = ledger.get_or_create_account(f"2001-{number}", f"Account {number}", is_customer=True)
            acct = Account.objects.create(
                customer=user, account_number=number, type=atype, ledger_account=la,
            )
            if balance:
                bank_equity = ledger.get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
                ledger.post_journal(
                    reference=f"OPEN-{number}",
                    description="Opening deposit",
                    lines=[(bank_equity, "DEBIT", Decimal(str(balance))), (la, "CREDIT", Decimal(str(balance)))],
                )
            return acct

        # Demo: Aubrey checking $8,450.75
        main = open_account(aubrey, "CHECKING", "8450.75", "4000110001")
        savings = open_account(aubrey, "SAVINGS", "12300.00", "4000110002")
        others = {}
        for i, u in enumerate(customers[1:], start=3):
            bal = rng.choice(["1500", "4200.5", "9800", "275.25", "15600"])
            acc = open_account(u, rng.choice(["CHECKING", "SAVINGS", "SALARY"]), bal, f"40001100{i:02d}")
            others[u] = acc

        # beneficiaries incl. demo names from the dashboard
        alex = User.objects.filter(username__startswith="liam.johnson").first()
        ben_names = [("Alex Johnson", False), ("John Doe", True), ("Maria Garcia", True), ("Netflix", True)]
        for name, ext in ben_names:
            Beneficiary.objects.create(owner=aubrey, name=name, account_number=f"EXT{abs(hash(name)) % 10**10}", is_external=ext, verified=True)
        for u in list(others)[:3]:
            Beneficiary.objects.create(owner=aubrey, name=u.get_full_name(), account_number=others[u].account_number, verified=True)

        # transfers matching dashboard: Alex Johnson $500, John Doe $450, Maria Garcia $350 (+Netflix bill-like)
        alex_acc = others.get(customers[1])
        if alex_acc:
            execute_transfer(actor=aubrey, source_account_id=main.pk, amount="500.00",
                             destination_account_id=alex_acc.pk, description="Dinner split")
        maria = next((u for u in others if u.first_name.lower().startswith(("olivia", "emma"))), None)
        if maria:
            execute_transfer(actor=aubrey, source_account_id=main.pk, amount="350.00",
                             destination_account_id=others[maria].pk, description="Rent share")

        # cards
        Card.objects.create(account=main, type="CREDIT_CARD", last4="3723", holder_name=aubrey.get_full_name(), credit_limit="5000")
        Card.objects.create(account=savings, type="DEBIT_CARD", holder_name=aubrey.get_full_name())

        # instruments & positions
        for sym, name, cat, price in [
            ("AAPL", "Apple Inc.", "STOCK", "212.40"), ("MSFT", "Microsoft", "STOCK", "418.20"),
            ("VTI", "Vanguard Total Market", "ETF", "268.90"), ("BND", "Total Bond Market", "BOND", "72.15"),
            ("TBILL26", "Treasury Note 2026", "FIXED_INCOME", "99.80"),
        ]:
            inst = Instrument.objects.create(symbol=sym, name=name, category=cat, last_price=price)
        vti = Instrument.objects.get(symbol="VTI")
        Position.objects.create(customer=aubrey, instrument=vti, quantity="12.5000", avg_price="254.30")

        # loans
        LoanProduct.objects.get_or_create(code="PERS-STD", defaults=dict(name="Personal Standard", type="PERSONAL"))
        LoanProduct.objects.get_or_create(code="AUTO-STD", defaults=dict(name="Auto Standard", type="AUTO"))
        LoanProduct.objects.get_or_create(code="MTG-STD", defaults=dict(name="Mortgage Standard", type="MORTGAGE"))

        # fraud rules
        FraudRule.objects.get_or_create(name="High value review", rule_type="AMOUNT_ABOVE", action="REVIEW", threshold="9000.00")
        FraudRule.objects.get_or_create(name="Velocity block", rule_type="VELOCITY", action="BLOCK", threshold="8")

        # notifications
        Notification.objects.create(recipient=aubrey, category="TRANSFER", title="Transfer completed", body="$500.00 to Alex Johnson")
        Notification.objects.create(recipient=aubrey, category="CARD", title="New virtual card issued", body="Card ending 3723 is ready")

        # salary income + expenses history for analytics (last 60 days)
        from django.core.management.color import no_style

        equity = ledger.get_or_create_account("3900-OPENING-EQUITY", "Opening Balances", type="EQUITY")
        expense = ledger.get_or_create_account("5000-MERCHANT-CLEARING", "Merchant Clearing", type="ASSET")
        for d in range(1, 3):  # two monthly salaries
            when = timezone.now() - timedelta(days=30 * d)
            ledger.post_journal(reference=f"SAL-{d}-{rng.randint(0,999)}", description="Monthly salary — Northwind Corp.",
                                lines=[(equity, "DEBIT", Decimal("5200.00")), (main.ledger_account, "CREDIT", Decimal("5200.00"))],
                                posted_at=when)
        for label, amt in [("Netflix Subscription", "15.99"), ("Whole Foods Market", "182.40"), ("Shell Energy", "95.20"),
                           ("Spotify", "11.99"), ("Delta Airlines", "450.00")]:
            journal = ledger.post_journal(reference=f"MRC-{rng.randint(0,999999)}", description=label,
                                          lines=[(main.ledger_account, "DEBIT", Decimal(amt)), (expense, "CREDIT", Decimal(amt))])

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(customers)} customers, accounts, cards, transfers."))
