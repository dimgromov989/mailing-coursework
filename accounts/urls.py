"""Account registration, sessions, recovery and moderation URLs."""
from django.contrib.auth import views as auth
from django.urls import path

from . import views

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("activate/<str:token>/", views.activate, name="activate"),
    path("login/", auth.LoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password/reset/", auth.PasswordResetView.as_view(), name="password_reset"),
    path("password/sent/", auth.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password/change/<uidb64>/<token>/", auth.PasswordResetConfirmView.as_view(),
         name="password_reset_confirm"),
    path("password/updated/", auth.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("users/", views.users, name="user-list"),
    path("users/<int:pk>/block/", views.block_user, name="user-block"),
]
