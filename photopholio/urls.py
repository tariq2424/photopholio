from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

urlpatterns = [
    path("", views.home, name="home"),
    path("photographers/", views.photographer_list, name="photographer_list"),
    path("photographers/<slug:slug>/", views.photographer_detail, name="photographer_detail"),
    path("contact/", views.contact, name="contact"),
    # accounts
    path("accounts/register/", views.register, name="register"),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="photopholio/login.html", authentication_form=LoginForm), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/profile/", views.profile, name="profile"),
    # bookings
    path("bookings/", views.my_bookings, name="my_bookings"),
    path("bookings/<int:pk>/cancel/", views.booking_cancel, name="booking_cancel"),
    path("bookings/<int:pk>/pay/", views.payment, name="payment"),
    path("bookings/<int:pk>/review/", views.feedback_create, name="feedback_create"),
]
