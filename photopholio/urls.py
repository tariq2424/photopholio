from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("photographers/", views.photographer_list, name="photographer_list"),
    path("photographers/<slug:slug>/", views.photographer_detail, name="photographer_detail"),
    path("contact/", views.contact, name="contact"),
    path("cancellation-policy/", views.cancellation_policy, name="cancellation_policy"),
    # accounts
    path("accounts/register/", views.register, name="register"),
    path("accounts/login/", views.SmartLoginView.as_view(), name="login"),
    # photographer area
    path("photographer/login/", views.SmartLoginView.as_view(extra_context={"login_role": "photographer"}), name="pro_login"),
    path("photographer/dashboard/", views.pro_dashboard, name="pro_dashboard"),
    path("photographer/profile/", views.pro_profile, name="pro_profile"),
    path("photographer/portfolio/", views.pro_portfolio, name="pro_portfolio"),
    path("photographer/photos/<int:pk>/delete/", views.pro_photo_delete, name="pro_photo_delete"),
    path("photographer/portfolios/<int:pk>/delete/", views.pro_portfolio_delete, name="pro_portfolio_delete"),
    path("photographer/submit/", views.pro_submit, name="pro_submit"),
    path("photographer/bookings/<int:pk>/cancel/", views.pro_booking_cancel, name="pro_booking_cancel"),
    path("photographer/bookings/<int:pk>/<slug:action>/", views.pro_booking_action, name="pro_booking_action"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/profile/", views.profile, name="profile"),
    # bookings
    path("bookings/", views.my_bookings, name="my_bookings"),
    path("bookings/<int:pk>/cancel/", views.booking_cancel, name="booking_cancel"),
    path("bookings/<int:pk>/pay/", views.payment, name="payment"),
    path("bookings/<int:pk>/review/", views.feedback_create, name="feedback_create"),
]
