"""HTTP views for Wenda-Live (Phase 2).

This layer handles everything that is *not* real-time:
    - the public landing page (host sign-in + player join),
    - host authentication against the shared wenda credentials,
    - a host creating a GameSession and picking its questions,
    - the host control page and player play page (shells that the Phase 3
      WebSocket consumers will drive live).

The live lobby/timer/leaderboard flow is intentionally NOT here — those pages
render a static shell and will open a WebSocket once consumers exist.
"""

from itertools import groupby

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .consumers import option_texts
from .forms import HostGameForm, JoinGameForm
from .models import (
    GameSession,
    LiveQuizGrade,
    Player,
    QuestionBankEntry,
    Subject,
    Student,
    SubjectStudent,
    User,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _is_student(user) -> bool:
    """True if the authenticated user is a student (the only role that may play)."""
    return user.is_authenticated and getattr(user, 'role', None) == User.Role.STUDENT


def home(request):
    """Landing page: player join-by-PIN form (students) + host sign-in CTA."""
    can_join = _is_student(request.user)
    initial = {}
    if can_join:
        initial['nickname'] = request.user.get_full_name() or request.user.username
    return render(
        request,
        'wenda_live/home.html',
        {'join_form': JoinGameForm(initial=initial), 'can_join': can_join},
    )


# ---------------------------------------------------------------------------
# Host authentication
# ---------------------------------------------------------------------------


def _post_login_redirect(user) -> str:
    """Students go back to the join page; hosts go to game creation."""
    if getattr(user, 'role', None) == User.Role.STUDENT:
        return 'wenda_live:home'
    return 'wenda_live:host_create_game'


def login_view(request):
    """Authenticate against the shared wenda user table (hosts and students)."""
    if request.user.is_authenticated:
        return redirect(_post_login_redirect(request.user))

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            auth_login(request, user)
            messages.success(request, 'Signed in.')
            return redirect(_post_login_redirect(user))
    else:
        form = AuthenticationForm(request)

    return render(request, 'wenda_live/login_page.html', {'form': form})


@require_http_methods(['POST'])
def logout_view(request):
    """Log out the current host."""
    auth_logout(request)
    messages.info(request, 'Signed out.')
    return redirect('wenda_live:home')


# Salt + lifetime for the cross-app SSO token. The salt must match Wenda-Quiz's
# WENDA_SSO_SALT; the token is single-purpose and short-lived (minted at click
# time on the Quiz side, so a tight window is plenty).
SSO_SALT = 'wenda-live-sso'
SSO_MAX_AGE = 60  # seconds


def sso_login(request):
    """Single sign-on entry from Wenda-Quiz.

    Verifies the short-lived signed token (shared WENDA_SSO_SECRET) minted by the
    Quiz app and logs the matching shared-DB user in here, then sends them to the
    right place (hosts to game creation, students to the join page). No second
    login. An invalid/expired token just falls back to the normal login page.
    """
    try:
        data = signing.loads(
            request.GET.get('t', ''),
            key=settings.WENDA_SSO_SECRET,
            salt=SSO_SALT,
            max_age=SSO_MAX_AGE,
        )
    except signing.BadSignature:
        messages.error(request, 'That sign-in link is invalid or has expired. Please sign in.')
        return redirect('wenda_live:login')

    user = User.objects.filter(pk=data.get('uid'), is_active=True).first()
    if user is None:
        messages.error(request, 'Account not found. Please sign in.')
        return redirect('wenda_live:login')

    auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect(_post_login_redirect(user))


# ---------------------------------------------------------------------------
# Host flow
# ---------------------------------------------------------------------------


def _can_host(user) -> bool:
    """Only instructors and admins may host games."""
    return user.role in (User.Role.INSTRUCTOR, User.Role.ADMIN)


def _grouped_by_topic(subject):
    """Return [(topic, [row, ...]), ...] for a subject — topics in name order,
    questions stable (by id) within each topic. Each row is a display dict with
    the question's options (correct one flagged) and Bloom's level, precomputed
    here because templates can't call option_texts() with an argument.
    """
    entries = (
        QuestionBankEntry.objects
        .filter(subject=subject)
        .order_by('topic', 'id')
    )
    groups = []
    for topic, items in groupby(entries, key=lambda e: e.topic or '(untitled topic)'):
        rows = []
        for e in items:
            rows.append({
                'id': e.id,
                'question': e.question,
                'blooms': e.get_blooms_taxonomy_level_display(),
                'options': [
                    {'text': text, 'correct': text == e.correct_answer_text}
                    for text in option_texts(e)
                ],
            })
        groups.append((topic, rows))
    return groups


@login_required
def host_create_game(request):
    """Step 1 of hosting: pick a subject + timer, then go choose the questions."""
    if not _can_host(request.user):
        messages.error(request, 'Only instructors can host games.')
        return redirect('wenda_live:home')

    if request.method == 'POST':
        form = HostGameForm(request.POST)
        if form.is_valid():
            subject = form.cleaned_data['subject']
            seconds = form.cleaned_data['seconds_per_question']
            url = reverse('wenda_live:host_select_questions')
            return redirect(f'{url}?subject={subject.id}&seconds={seconds}')
    else:
        form = HostGameForm()

    return render(request, 'wenda_live/host_create_game.html', {'form': form})


@login_required
def host_select_questions(request):
    """Step 2 of hosting: pick the actual questions (arranged by topic), then
    create the game and open its lobby."""
    if not _can_host(request.user):
        messages.error(request, 'Only instructors can host games.')
        return redirect('wenda_live:home')

    src = request.POST if request.method == 'POST' else request.GET
    subject = Subject.objects.filter(pk=src.get('subject')).first()
    if subject is None:
        messages.error(request, 'Choose a subject to host first.')
        return redirect('wenda_live:host_create_game')

    try:
        seconds = int(src.get('seconds') or src.get('seconds_per_question') or 20)
    except (TypeError, ValueError):
        seconds = 20
    seconds = max(5, min(120, seconds))

    groups = _grouped_by_topic(subject)
    valid_ids = {row['id'] for _, rows in groups for row in rows}

    # None on first view => default to "all selected"; a set once the host posts.
    selected_ids = None
    if request.method == 'POST':
        selected_ids = {
            int(q) for q in request.POST.getlist('questions')
            if q.isdigit() and int(q) in valid_ids
        }
        if selected_ids:
            # Keep the questions in the order shown (grouped by topic).
            ordered = [
                row['id'] for _, rows in groups for row in rows
                if row['id'] in selected_ids
            ]
            game = GameSession.objects.create(
                host=request.user,
                subject=subject,
                question_ids=ordered,
                num_questions=len(ordered),
                seconds_per_question=seconds,
            )
            return redirect('wenda_live:host_game', room_code=game.room_code)
        messages.error(request, 'Select at least one question to start a game.')

    return render(
        request,
        'wenda_live/host_select_questions.html',
        {
            'subject': subject,
            'seconds': seconds,
            'groups': groups,
            'total': len(valid_ids),
            'selected_ids': selected_ids,  # None => all checked by default
        },
    )


@login_required
def host_game(request, room_code):
    """Host control page for a game. Live control arrives in Phase 3 over WS."""
    game = get_object_or_404(GameSession, room_code=room_code)
    if game.host_id != request.user.id:
        messages.error(request, 'You are not the host of that game.')
        return redirect('wenda_live:host_create_game')

    return render(
        request,
        'wenda_live/host_game.html',
        {'game': game, 'players': game.players.all()},
    )


# ---------------------------------------------------------------------------
# Player flow (must be a student enrolled in the game's subject)
# ---------------------------------------------------------------------------


def _enrolled_student(user, subject) -> bool:
    """True if `user` is a student enrolled in `subject` (the game's subject)."""
    student = Student.objects.filter(user_id=user.id).first()
    if student is None:
        return False
    return SubjectStudent.objects.filter(subject=subject, student=student).exists()


@require_http_methods(['POST'])
def join_game(request):
    """Let an enrolled student join a game by PIN.

    Players are no longer anonymous: only a signed-in student who is enrolled in
    the game's subject may join, and each student maps to a single Player per
    game (re-joining returns their existing player).
    """
    if not request.user.is_authenticated:
        # We can only verify enrollment for a known account, so sign-in first.
        messages.info(request, 'Please sign in as a student to join a game.')
        return redirect('wenda_live:login')

    form = JoinGameForm(request.POST, player_user=request.user)
    if not form.is_valid():
        # Re-render the landing page with the populated/invalid form.
        return render(
            request,
            'wenda_live/home.html',
            {'join_form': form, 'can_join': _is_student(request.user)},
        )

    game = form.game

    if not _is_student(request.user):
        messages.error(request, 'Only students can join a game as a player.')
        return redirect('wenda_live:home')

    if not _enrolled_student(request.user, game.subject):
        messages.error(
            request,
            'You are not enrolled in this game’s subject, so you can’t join it.',
        )
        return redirect('wenda_live:home')

    nickname = form.cleaned_data['nickname']
    # One Player per student per game: re-joining returns the same row.
    player, created = Player.objects.get_or_create(
        game=game,
        student_user=request.user,
        defaults={'nickname': nickname},
    )
    if not created and player.nickname != nickname:
        player.nickname = nickname
        player.save(update_fields=['nickname'])

    # Identify this browser as that player on subsequent requests / the WS.
    request.session['player_id'] = player.id
    request.session['room_code'] = game.room_code
    return redirect('wenda_live:play_game', room_code=game.room_code)


def play_game(request, room_code):
    """Player's game page. Live Q&A arrives in Phase 3 over WS."""
    game = get_object_or_404(GameSession, room_code=room_code)

    player_id = request.session.get('player_id')
    player = Player.objects.filter(id=player_id, game=game).first() if player_id else None
    if player is None:
        messages.error(request, 'Join the game with a PIN first.')
        return redirect('wenda_live:home')

    # A player tied to a student account may only be viewed by that account.
    if player.student_user_id and request.user.id != player.student_user_id:
        messages.error(request, 'Please sign in as the student who joined this game.')
        return redirect('wenda_live:home')

    return render(
        request,
        'wenda_live/play_game.html',
        {'game': game, 'player': player},
    )


@login_required
def my_results(request):
    """A student's own saved live-quiz results (view only).

    Lists the LiveQuizGrade rows written when each game the student played
    finished — most recent first. Students only; a student with no profile or no
    finished games just sees an empty state.
    """
    if not _is_student(request.user):
        messages.error(request, 'Only students have live quiz results.')
        return redirect('wenda_live:home')

    student = Student.objects.filter(user_id=request.user.id).first()
    grades = (
        LiveQuizGrade.objects
        .filter(student=student)
        .select_related('game', 'game__subject')
        .order_by('-created_at')
        if student is not None else LiveQuizGrade.objects.none()
    )
    return render(request, 'wenda_live/my_results.html', {'grades': grades})
