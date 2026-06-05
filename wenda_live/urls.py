"""URL configuration for the wenda_live app.

Settings reference these names: wenda_live:login, wenda_live:host_create_game,
wenda_live:home (LOGIN_URL / LOGIN_REDIRECT_URL / LOGOUT_REDIRECT_URL).
"""

from django.urls import path

from . import views

app_name = 'wenda_live'

urlpatterns = [
    # Public entry point: host sign-in CTA + player join form.
    path('', views.home, name='home'),

    # Host authentication (reuses the shared wenda credentials).
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # Single sign-on handoff from Wenda-Quiz (no second login).
    path('sso/', views.sso_login, name='sso_login'),

    # Host flow (two steps: pick subject -> pick questions by topic).
    path('host/new/', views.host_create_game, name='host_create_game'),
    path('host/new/questions/', views.host_select_questions, name='host_select_questions'),
    path('host/game/<str:room_code>/', views.host_game, name='host_game'),

    # Player flow (no account required).
    path('join/', views.join_game, name='join_game'),
    path('play/<str:room_code>/', views.play_game, name='play_game'),

    # A student viewing their own saved live-quiz results.
    path('my-results/', views.my_results, name='my_results'),
]
