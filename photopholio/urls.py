from django.contrib.auth import views as auth_views
from django.urls import path

from django.urls import reverse_lazy

from . import views
from .forms import NewPasswordForm, PasswordResetRequestForm

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
    path("photographer/availability/", views.pro_availability, name="pro_availability"),
    path("photographer/packages/", views.pro_packages, name="pro_packages"),
    path("photographer/availability/<int:pk>/remove/", views.pro_unblock, name="pro_unblock"),
    path("photographer/reviews/", views.pro_reviews, name="pro_reviews"),
    path("photographer/reviews/<int:pk>/reply/", views.pro_review_reply, name="pro_review_reply"),
    path("photographer/bookings/<int:pk>/delivery/", views.pro_delivery, name="pro_delivery"),
    path("photographer/reschedules/<int:pk>/<slug:action>/", views.pro_reschedule_action, name="pro_reschedule_action"),
    path("photographer/bookings/<int:pk>/cancel/", views.pro_booking_cancel, name="pro_booking_cancel"),
    path("photographer/bookings/<int:pk>/<slug:action>/", views.pro_booking_action, name="pro_booking_action"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    # forgot password (the reset link is emailed; with no mail server set up it is printed in the terminal)
    path("accounts/password-reset/", auth_views.PasswordResetView.as_view(
        template_name="photopholio/password_reset_form.html", form_class=PasswordResetRequestForm,
        email_template_name="photopholio/emails/password_reset.txt",
        html_email_template_name="photopholio/emails/password_reset.html",
        subject_template_name="photopholio/emails/password_reset_subject.txt",
        success_url=reverse_lazy("password_reset_done")), name="password_reset"),
    path("accounts/password-reset/sent/", auth_views.PasswordResetDoneView.as_view(
        template_name="photopholio/password_reset_done.html"), name="password_reset_done"),
    path("accounts/reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="photopholio/password_reset_confirm.html", form_class=NewPasswordForm,
        success_url=reverse_lazy("password_reset_complete")), name="password_reset_confirm"),
    path("accounts/reset/done/", auth_views.PasswordResetCompleteView.as_view(
        template_name="photopholio/password_reset_complete.html"), name="password_reset_complete"),
    path("accounts/profile/", views.profile, name="profile"),
    # bookings
    path("bookings/", views.my_bookings, name="my_bookings"),
    path("bookings/<int:pk>/cancel/", views.booking_cancel, name="booking_cancel"),
    path("saved/", views.my_favourites, name="my_favourites"),
    path("saved/<slug:slug>/toggle/", views.favourite_toggle, name="favourite_toggle"),
    path("bookings/<int:pk>/reschedule/", views.booking_reschedule, name="booking_reschedule"),
    path("reschedules/<int:pk>/withdraw/", views.reschedule_withdraw, name="reschedule_withdraw"),
    path("bookings/<int:pk>/pay/", views.payment, name="payment"),
    path("bookings/<int:pk>/review/", views.feedback_create, name="feedback_create"),
]
