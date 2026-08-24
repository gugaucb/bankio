"""Admin user-management views. Views validate HTTP + authorize; logic lives in admin_services."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.identity.models import Role

from . import admin_services
from .admin_services import AdminUserError, require_admin


@login_required
@require_admin
def users_list(request):
    query = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    status = request.GET.get("status", "ALL")
    try:
        page = int(request.GET.get("page", "1"))
    except ValueError:
        page = 1
    result = admin_services.list_users(query=query, role=role, status=status, page=page)
    return render(request, "manager/users.html", {
        "nav": "users", "page_heading": "User Management",
        "users": result["items"], "total": result["total"],
        "page": result["page"], "pages": result["pages"],
        "query": query, "role": role, "status": status,
        "roles": Role.choices,
    })


@login_required
@require_admin
def user_create(request):
    error = None
    form = {"username": "", "email": "", "first_name": "", "last_name": "",
            "phone": "", "role": Role.CUSTOMER}
    if request.method == "POST":
        form = {k: request.POST.get(k, "").strip() for k in form}
        form["role"] = request.POST.get("role", Role.CUSTOMER)
        try:
            user = admin_services.create_user(
                actor=request.user, username=form["username"], email=form["email"],
                password=request.POST.get("password", ""), role=form["role"],
                first_name=form["first_name"], last_name=form["last_name"],
                phone=form["phone"], request=request,
            )
            messages.success(request, f"User {user.username} created.")
            return redirect("admin_user_detail", user_id=user.pk)
        except AdminUserError as e:
            error = e.message if hasattr(e, "message") and e.message else e.code
        except Exception:
            error = "Could not create user."
    return render(request, "manager/user_form.html", {
        "nav": "users", "page_heading": "New User",
        "form": form, "error": error, "roles": Role.choices,
    })


@login_required
@require_admin
def user_detail(request, user_id):
    user = admin_services.get_user(user_id)
    if user is None:
        return render(request, "manager/user_detail.html", {
            "nav": "users", "not_found": True,
        }, status=404)
    return render(request, "manager/user_detail.html", {
        "nav": "users", "target": user,
        "page_heading": f"User: {user.username}",
    })


@login_required
@require_admin
@require_POST
def user_block(request, user_id):
    reason = request.POST.get("reason", "")
    try:
        admin_services.block_user(actor=request.user, user_id=user_id,
                                  reason=reason, request=request)
        messages.success(request, "User blocked.")
    except AdminUserError as e:
        messages.error(request, f"Block failed: {e.code}")
    return redirect("admin_user_detail", user_id=user_id)


@login_required
@require_admin
@require_POST
def user_unblock(request, user_id):
    reason = request.POST.get("reason", "")
    try:
        admin_services.unblock_user(actor=request.user, user_id=user_id,
                                    reason=reason, request=request)
        messages.success(request, "User unblocked.")
    except AdminUserError as e:
        messages.error(request, f"Unblock failed: {e.code}")
    return redirect("admin_user_detail", user_id=user_id)
