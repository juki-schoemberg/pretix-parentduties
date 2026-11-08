from django.urls import path

from . import views

prefix = "control/event/<str:organizer>/<str:event>/parentduties/"

urlpatterns = [
    path(prefix, views.SlotListView.as_view(), name="slots"),
    path(prefix + "slots/add", views.SlotEditView.as_view(), name="slot.add"),
    path(prefix + "slots/<int:pk>/", views.SlotEditView.as_view(), name="slot.edit"),
    path(prefix + "slots/<int:pk>/duplicate", views.SlotDuplicateView.as_view(), name="slot.duplicate"),
    path(prefix + "slots/<int:pk>/delete", views.SlotDeleteView.as_view(), name="slot.delete"),
    path(prefix + "signups/", views.SignupListView.as_view(), name="signups"),
    path(prefix + "signups/add", views.SignupAddView.as_view(), name="signup.add"),
    path(prefix + "signups/<int:pk>/action", views.SignupActionView.as_view(), name="signup.action"),
    path(prefix + "items/", views.ItemsView.as_view(), name="items"),
    path(prefix + "settings/", views.SettingsView.as_view(), name="settings"),
]

# Presale (event domain): the parents' view of their duty, also used as deeplink in e-mails.
event_patterns = [
    path("order/<str:order>/<str:secret>/parentduty/", views.OrderDutyView.as_view(), name="order.duty"),
]
