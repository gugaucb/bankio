from django.contrib import admin

from .models import JournalEntry, LedgerAccount, LedgerEntry


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    can_delete = False


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    """Read-only: financial history must never be edited via admin."""
    list_display = ("reference", "description", "status", "posted_at")
    search_fields = ("reference", "description")
    inlines = [LedgerEntryInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "type", "currency")
    search_fields = ("code", "name")

    def has_delete_permission(self, request, obj=None):
        return False
