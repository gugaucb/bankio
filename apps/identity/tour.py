"""First-access tutorial: server-side state is the single authority.

The client library (Driver.js) is presentation-only — whether the tour runs
is decided here, from TourProgress + a one-shot session replay flag. The
tour never starts during login/OTP/challenge/errors because only the
dashboard view (reached strictly after full authentication) renders it.
"""
from django.utils import timezone

from apps.identity.models import TourProgress

TOUR_VERSION = "v1"
SESSION_REPLAY_KEY = "tour_replay"


def tour_state(user, request):
    """Return (show_tour, progress) for this user+request.

    show_tour is True when:
      - there is no TourProgress row (first access), or
      - the session carries the one-shot replay flag ("Ver tutorial novamente").
    """
    if not getattr(user, "is_authenticated", False):
        return False, None
    progress = TourProgress.objects.filter(user=user).first()
    replay = request.session.get(SESSION_REPLAY_KEY, False)
    return (progress is None or replay), progress


def consume_replay(request):
    """One-shot: after the tour HTML was rendered once, clear the flag."""
    request.session.pop(SESSION_REPLAY_KEY, None)


def request_replay(request):
    """Ajuda → 'Ver tutorial novamente': armazenado no servidor; o próximo
    carregamento do dashboard exibe o tour uma única vez."""
    request.session[SESSION_REPLAY_KEY] = True


def mark_completed(user, tour_version=TOUR_VERSION):
    progress, _ = TourProgress.objects.get_or_create(user=user, defaults={"tour_version": tour_version})
    progress.tour_version = tour_version
    progress.completed_at = timezone.now()
    progress.skipped_at = None
    progress.save(update_fields=["tour_version", "completed_at", "skipped_at"])
    return progress


def mark_skipped(user, tour_version=TOUR_VERSION):
    progress, _ = TourProgress.objects.get_or_create(user=user, defaults={"tour_version": tour_version})
    progress.tour_version = tour_version
    progress.skipped_at = timezone.now()
    progress.save(update_fields=["tour_version", "skipped_at"])
    return progress


def _step(target, title, body):
    return {"element": f'[data-tour="{target}"]', "popover": {"title": title, "description": body}}


def customer_steps():
    """Tour script for customer roles. Steps reference data-tour hooks that
    exist in templates; staff-only screens never appear here."""
    step = _step
    return [
        {"popover": {"title": "Bem-vindo ao Bankio 👋",
                     "description": "Um passeio rápido de 1 minuto pelo que você mais vai usar. "
                                    "Use Próximo/Voltar, ou Pular quando quiser."}},
        step("nav-dashboard", "Dashboard", "Seu resumo: saldos, movimentos recentes e atalhos."),
        step("nav-accounts", "Contas", "Saldo detalhado de cada conta sua."),
        step("nav-transactions", "Extrato", "Histórico completo com filtros e busca."),
        step("nav-transfers", "Transferências", "Envie para contas Bankio ou beneficiários externos."),
        step("nav-cards", "Cartões", "Saldo da fatura, congelar cartão, limites e compras."),
        step("nav-notifications", "Notificações", "Avisos de segurança e movimentações importantes."),
        step("nav-security", "Segurança", "Senha, MFA, dispositivos e sessões ativas."),
        step("nav-settings", "Configurações", "Preferências — e o link para rever este tutorial."),
        {"popover": {"title": "Pronto! 🎉",
                     "description": "Você pode rever este tutorial em Configurações → Ver tutorial novamente."}},
    ]


def staff_steps(role_label="staff"):
    """Staff see only resources they are authorized to use; no admin/fraud
    screens are ever referenced unless the role actually owns them."""
    step = _step
    return [
        {"popover": {"title": f"Bem-vindo, time {role_label}",
                     "description": "Passeio rápido pelas ferramentas operacionais disponíveis para o seu papel."}},
        step("nav-dashboard", "Overview", "Indicadores operacionais do seu perfil de acesso."),
    ]
