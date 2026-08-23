"""Manager access control: branch/assignment scoping for every manager query."""
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.customers.models import Customer
from apps.managerops.models import BankBranch, CustomerManagerAssignment, ManagerLevel, ManagerProfile


def get_manager_profile(user) -> ManagerProfile:
    if not getattr(user, "is_authenticated", False) or user.role != "MANAGER":
        raise PermissionDenied("Manager role required")
    profile = getattr(user, "manager_profile", None)
    if profile is None:
        raise PermissionDenied("Manager profile missing")
    return profile


def visible_customers(profile):
    """Assignment-based for relationship managers; branch scope above; region for regional."""
    qs = Customer.objects.filter(
        Q(user__manager_assignments__status="ACTIVE", user__manager_assignments__manager=profile.user)
        | Q(assigned_manager=profile.user)
    )
    if profile.rank >= 2 and profile.branch_id:
        branch_q = Customer.objects.filter(branch=profile.branch)
        qs = qs | branch_q
    if profile.rank >= 4:
        regions = BankBranch.objects.filter(region=profile.branch.region).values_list("id", flat=True) if profile.branch else []
        qs = qs | Customer.objects.filter(branch_id__in=list(regions))
    return qs.distinct()


def assert_customer_access(profile, customer):
    if not visible_customers(profile).filter(pk=customer.pk).exists():
        raise PermissionDenied("Customer outside your authority")


def assign_customer(manager_user, customer, branch=None):
    return CustomerManagerAssignment.objects.get_or_create(
        customer=customer, manager=manager_user,
        defaults={"branch": branch},
    )[0]
