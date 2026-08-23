from decimal import Decimal

from django.conf import settings
from django.db import models


class Instrument(models.Model):
    CATEGORIES = [("STOCK", "Stocks"), ("ETF", "ETFs"), ("FUND", "Funds"), ("BOND", "Bonds"), ("FIXED_INCOME", "Fixed Income")]

    symbol = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=16, choices=CATEGORIES, default="STOCK")
    last_price = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.symbol} {self.last_price}"


class Position(models.Model):
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="positions")
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))
    avg_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    class Meta:
        unique_together = ("customer", "instrument")

    @property
    def market_value(self):
        return (self.quantity * self.instrument.last_price).quantize(Decimal("0.01"))


class Order(models.Model):
    idempotency_key = models.CharField(max_length=64, unique=True)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT)
    side = models.CharField(max_length=4, choices=[("BUY", "Buy"), ("SELL", "Sell")])
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    journal = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
