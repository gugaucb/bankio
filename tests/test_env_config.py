"""FASE 10 B1 — external configuration & bootstrap_admin."""
import os

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from config.env_utils import env_bool, env_int, env_list, secret_or_file


# ---------------------------------------------------------------- env helpers
def test_env_bool(monkeypatch):
    monkeypatch.setenv("X_BOOL", "TRUE")
    assert env_bool("X_BOOL") is True
    monkeypatch.setenv("X_BOOL", "0")
    assert env_bool("X_BOOL") is False
    monkeypatch.delenv("X_BOOL")
    assert env_bool("X_BOOL", default=True) is True


def test_env_int(monkeypatch):
    monkeypatch.setenv("X_INT", "42")
    assert env_int("X_INT", 7) == 42
    monkeypatch.setenv("X_INT", "nope")
    with pytest.raises(ValueError):
        env_int("X_INT")


def test_env_list(monkeypatch):
    monkeypatch.setenv("X_LIST", "a, b,,c")
    assert env_list("X_LIST") == ["a", "b", "c"]
    monkeypatch.delenv("X_LIST")
    assert env_list("X_LIST", ["d"]) == ["d"]


def test_secret_or_file_direct_and_file(tmp_path, monkeypatch):
    monkeypatch.setenv("S_X", "abc")
    assert secret_or_file("S_X") == "abc"
    monkeypatch.delenv("S_X")
    f = tmp_path / "s.txt"
    f.write_text("fromfile\n")
    monkeypatch.setenv("S_X_FILE", str(f))
    assert secret_or_file("S_X") == "fromfile"


def test_secret_or_file_missing_raises(monkeypatch):
    monkeypatch.delenv("S_MISSING", raising=False)
    monkeypatch.delenv("S_MISSING_FILE", raising=False)
    with pytest.raises(ValueError):
        secret_or_file("S_MISSING")


# ------------------------------------------------------------- settings rules
def _setup_subprocess(env_overrides):
    import subprocess, sys
    env = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_SECRET_KEY")}
    env.update(env_overrides)
    code = ("import django,os;"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup()")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)


def test_production_requires_real_secret_key():
    """No DJANGO_SECRET_KEY + DEBUG=false must fail loudly."""
    r = _setup_subprocess({"DJANGO_DEBUG": "false"})
    assert r.returncode != 0
    assert "DJANGO_SECRET_KEY" in (r.stderr + r.stdout)


def test_production_rejects_insecure_dev_key():
    r = _setup_subprocess({"DJANGO_DEBUG": "false", "DJANGO_SECRET_KEY": "dev-only-secret-key-change-me"})
    assert r.returncode != 0
    assert "insecure" in (r.stderr + r.stdout)


def test_csrf_origin_without_scheme_rejected():
    r = _setup_subprocess({"DJANGO_DEBUG": "true", "DJANGO_CSRF_TRUSTED_ORIGINS": "localhost:8000"})
    assert r.returncode != 0


# ------------------------------------------------------------ bootstrap_admin
@pytest.mark.django_db
def test_bootstrap_admin_creates_admin_from_env(monkeypatch):
    from apps.identity.models import Role, User
    monkeypatch.setenv("BANKIO_ADMIN_USERNAME", "bootadmin")
    monkeypatch.setenv("BANKIO_ADMIN_PASSWORD", "S3cure-Boot!")
    call_command("bootstrap_admin")
    u = User.objects.get(username="bootadmin")
    assert u.role == Role.ADMIN and u.is_superuser and u.check_password("S3cure-Boot!")


@pytest.mark.django_db
def test_bootstrap_admin_idempotent_never_resets(monkeypatch):
    from apps.identity.models import User
    monkeypatch.setenv("BANKIO_ADMIN_USERNAME", "bootadmin2")
    monkeypatch.setenv("BANKIO_ADMIN_PASSWORD", "First-Pass-1!")
    call_command("bootstrap_admin")
    monkeypatch.setenv("BANKIO_ADMIN_PASSWORD", "Second-Pass-2!")
    call_command("bootstrap_admin")  # must not touch existing account
    assert len(User.objects.filter(username="bootadmin2")) == 1
    assert User.objects.get(username="bootadmin2").check_password("First-Pass-1!")


@pytest.mark.django_db
def test_bootstrap_admin_fails_clearly_without_credentials(monkeypatch):
    for name in ("BANKIO_ADMIN_USERNAME", "BANKIO_ADMIN_USERNAME_FILE",
                 "BANKIO_ADMIN_PASSWORD", "BANKIO_ADMIN_PASSWORD_FILE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(CommandError):
        call_command("bootstrap_admin")


@pytest.mark.django_db
def test_bootstrap_admin_reads_password_from_file(tmp_path, monkeypatch):
    from apps.identity.models import User
    f = tmp_path / "pw"
    f.write_text("File-Pass-9!\n")
    monkeypatch.setenv("BANKIO_ADMIN_USERNAME", "bootadmin3")
    monkeypatch.setenv("BANKIO_ADMIN_PASSWORD_FILE", str(f))
    call_command("bootstrap_admin")
    assert User.objects.get(username="bootadmin3").check_password("File-Pass-9!")
