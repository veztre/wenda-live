"""Wenda-Live models.

The unmanaged models below (User, Subject, Instructor, QuestionBankEntry)
mirror tables owned by the sibling wenda-quiz project. They MUST stay
field-for-field in sync with wenda's definitions, or queries against the
shared MySQL database will silently break.

If a wenda migration adds, renames, or drops a field on any of these models,
mirror the change here in the same commit so the two projects stay aligned.
Mirrored tables:
    quiz_user                       (wenda quiz.User)
    quiz_instructor                 (wenda quiz.Instructor)
    subjects_subject                (wenda subjects.Subject)
    question_bank_questionbankentry (wenda question_bank.QuestionBankEntry)

Managed models (GameSession, Player, PlayerAnswer) are owned by Wenda-Live.
Their tables keep the original ``kahoot_<modelname>`` names via an explicit
db_table so the existing rows in the shared database are preserved across the
app rename; Wenda must not touch these.
"""

import secrets
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models


# ---------------------------------------------------------------------------
# Unmanaged mirrors of wenda-quiz tables
# ---------------------------------------------------------------------------


class User(AbstractUser):
    """Mirror of wenda's quiz.User. Read+write via Django auth, schema owned by wenda."""

    class Role(models.TextChoices):
        INSTRUCTOR = 'instructor', 'Instructor (Faculty)'
        STUDENT = 'student', 'Student'
        ADMIN = 'admin', 'Administrator'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'quiz_user'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"


class Instructor(models.Model):
    """Mirror of wenda's quiz.Instructor. Read-only from Wenda-Live."""

    user = models.OneToOneField(
        User,
        on_delete=models.DO_NOTHING,
        related_name='instructor_profile',
        db_column='user_id',
    )
    department = models.CharField(max_length=100)
    subject = models.CharField(max_length=100)
    employee_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'quiz_instructor'
        ordering = ['department', 'subject']

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.subject}"


class Subject(models.Model):
    """Mirror of wenda's subjects.Subject. Read-only from Wenda-Live.

    The wenda model has a Student M2M; Wenda-Live doesn't need it and so it is
    intentionally omitted from this mirror.
    """

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30, unique=True)
    description = models.TextField(blank=True)
    faculty = models.ForeignKey(
        Instructor,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name='subjects',
        db_column='faculty_id',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'subjects_subject'
        ordering = ['name', 'code']

    def __str__(self):
        return f"{self.code} - {self.name}"


class QuestionBankEntry(models.Model):
    """Mirror of wenda's question_bank.QuestionBankEntry. Read-only from Wenda-Live.

    Note: `options` is a JSONField holding the choice list (no separate Choice
    table). `correct_answer_text` is the string of the correct option.
    """

    class BloomsTaxonomyLevel(models.TextChoices):
        REMEMBERING = 'remembering', 'Remembering'
        UNDERSTANDING = 'understanding', 'Understanding'
        APPLYING = 'applying', 'Applying'
        ANALYZING = 'analyzing', 'Analyzing'
        EVALUATING = 'evaluating', 'Evaluating'
        CREATING = 'creating', 'Creating'

    question = models.TextField()
    topic = models.CharField(max_length=255, blank=True, default='')
    blooms_taxonomy_level = models.CharField(
        max_length=16,
        choices=BloomsTaxonomyLevel.choices,
        default=BloomsTaxonomyLevel.REMEMBERING,
    )
    options = models.JSONField()
    correct_answer_text = models.CharField(max_length=255)
    question_set_title = models.CharField(max_length=255, blank=True)
    source_document = models.CharField(max_length=255, blank=True)
    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name='question_bank_entries',
        db_column='instructor_id',
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name='question_bank_entries',
        db_column='subject_id',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'question_bank_questionbankentry'
        ordering = ['-created_at']

    def __str__(self):
        short = self.question
        return short[:75].rstrip() + '...' if len(short) > 75 else short


class Student(models.Model):
    """Mirror of wenda's quiz.Student. Read-only from Wenda-Live.

    Lets us resolve a logged-in User to their student profile so we can check
    enrollment before allowing them to join a game. Schema owned by wenda.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.DO_NOTHING,
        related_name='student_profile',
        db_column='user_id',
    )
    student_number = models.CharField(max_length=50, unique=True)
    course = models.CharField(max_length=100)
    section = models.CharField(max_length=50)
    year_level = models.PositiveIntegerField()
    adviser = models.ForeignKey(
        Instructor,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name='advisees',
        db_column='adviser_id',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'quiz_student'
        ordering = ['year_level', 'section', 'student_number']

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.student_number})"


class SubjectStudent(models.Model):
    """Mirror of the subjects.Subject.students M2M join table (enrollment).

    Read-only from Wenda-Live; used to check whether a student is enrolled in a
    game's subject before letting them join. wenda owns this table (the auto
    table for its Subject.students ManyToManyField).
    """

    subject = models.ForeignKey(
        Subject,
        on_delete=models.DO_NOTHING,
        related_name='enrollments',
        db_column='subject_id',
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.DO_NOTHING,
        related_name='enrollments',
        db_column='student_id',
    )

    class Meta:
        managed = False
        db_table = 'subjects_subject_students'
        ordering = ['subject', 'student']
        constraints = [
            models.UniqueConstraint(
                fields=['subject', 'student'],
                name='uniq_subject_student_enrollment',
            ),
        ]

    def __str__(self):
        return f"student {self.student_id} in subject {self.subject_id}"


# ---------------------------------------------------------------------------
# Managed models owned by Wenda-Live
# ---------------------------------------------------------------------------


def _generate_room_code() -> str:
    """Return a 6-character upper-case alphanumeric room code (no ambiguous chars)."""
    # Skip 0/O/1/I/L to keep codes easy to read aloud and type on a phone.
    alphabet = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'
    return ''.join(secrets.choice(alphabet) for _ in range(6))


class GameSession(models.Model):
    """A single hosted live quiz game (one host, many players)."""

    class Status(models.TextChoices):
        LOBBY = 'lobby', 'Lobby (waiting for players)'
        ACTIVE = 'active', 'Active (in progress)'
        FINISHED = 'finished', 'Finished'

    host = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='hosted_games',
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.DO_NOTHING,
        related_name='+',
        db_column='subject_id',
    )
    room_code = models.CharField(max_length=8, unique=True, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.LOBBY)
    num_questions = models.PositiveIntegerField(default=10)
    seconds_per_question = models.PositiveIntegerField(default=20)
    current_question_index = models.PositiveIntegerField(default=0)
    question_ids = models.JSONField(
        default=list,
        help_text='Ordered list of QuestionBankEntry IDs picked at game start.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'kahoot_gamesession'  # kept across the rename to preserve rows
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.room_code:
            while True:
                code = _generate_room_code()
                if not GameSession.objects.filter(room_code=code).exists():
                    self.room_code = code
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.room_code} ({self.get_status_display()})"


class Player(models.Model):
    """A participant in a GameSession. No auth — identified by nickname + session."""

    game = models.ForeignKey(GameSession, on_delete=models.CASCADE, related_name='players')
    nickname = models.CharField(max_length=30)
    # The enrolled student (shared wenda User) this player is. Nullable so the
    # legacy anonymous rows that pre-date enrollment gating still validate; new
    # players are always tied to the authenticated student who joined.
    student_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='played_games',
        db_column='student_user_id',
    )
    score = models.PositiveIntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'kahoot_player'  # kept across the rename to preserve rows
        ordering = ['-score', 'joined_at']
        constraints = [
            models.UniqueConstraint(
                fields=['game', 'nickname'],
                name='uniq_player_nickname_per_game',
            ),
            # One player per enrolled student per game (legacy NULLs are exempt:
            # MySQL allows multiple NULLs in a unique index).
            models.UniqueConstraint(
                fields=['game', 'student_user'],
                name='uniq_player_student_per_game',
            ),
        ]

    def __str__(self):
        return f"{self.nickname} ({self.score})"


class PlayerAnswer(models.Model):
    """One Player's answer to one question in a GameSession."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='answers')
    question_index = models.PositiveIntegerField(
        help_text='Index into GameSession.question_ids (0-based).',
    )
    chosen_text = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)
    ms_to_answer = models.PositiveIntegerField(default=0)
    points_awarded = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'kahoot_playeranswer'  # kept across the rename to preserve rows
        ordering = ['player', 'question_index']
        constraints = [
            models.UniqueConstraint(
                fields=['player', 'question_index'],
                name='uniq_answer_per_player_per_question',
            ),
        ]

    def __str__(self):
        return f"{self.player.nickname} Q{self.question_index} {'check' if self.is_correct else 'x'}"


class LiveQuizGrade(models.Model):
    """A student's gradeable result from one finished live quiz game.

    Written when a GameSession finishes — one row per (game, enrolled student).
    Kept in its own table so the live-quiz ``percentage`` can feed wenda-quiz's
    grade computation (which averages QuizResult.percentage) without touching
    that table. Only players tied to an enrolled Student get a row; legacy
    anonymous players are skipped. ``percentage`` is plain accuracy (correct /
    total) — the speed-weighted leaderboard value is NOT a grade and stays on
    Player.score.

    The table uses the app's ``kahoot_*`` prefix for consistency with the other
    Wenda-Live-owned tables in the shared database.
    """

    game = models.ForeignKey(
        GameSession,
        on_delete=models.CASCADE,
        related_name='grades',
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.DO_NOTHING,
        related_name='live_quiz_grades',
        db_column='student_id',
    )
    correct_count = models.PositiveIntegerField(default=0)
    total_questions = models.PositiveIntegerField(default=0)
    total_score = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Number of correct answers, as a decimal (mirrors QuizResult.total_score).',
    )
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='correct_count / total_questions * 100.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'kahoot_livequizgrade'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['game', 'student'],
                name='uniq_grade_per_student_per_game',
            ),
        ]

    def __str__(self):
        return f"{self.student} - {self.game.room_code}: {self.percentage}%"
