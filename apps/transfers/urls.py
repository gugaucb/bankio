from django.urls import path

from . import views

urlpatterns = [
    path("transfers/", views.transfer_list_create, name="transfers"),
    path("transfers/quick/", views.quick_transfer_form, name="quick_transfer_form"),
]
