"""Admin registrations for Wenda-Live's own (managed) models.

The unmanaged wenda mirrors (User, Instructor, Subject, QuestionBankEntry) are
deliberately NOT registered here — they're owned and administered by wenda-quiz.
"""

from django.contrib import admin

from .models import GameSession, Player, PlayerAnswer


class PlayerInline(admin.TabularInline):
    model = Player
    extra = 0
    readonly_fields = ('nickname', 'score', 'joined_at')
    can_delete = False


@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):
    list_display = ('room_code', 'status', 'host', 'subject', 'num_questions', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('room_code',)
    readonly_fields = ('room_code', 'question_ids', 'created_at', 'started_at', 'finished_at')
    inlines = (PlayerInline,)


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ('nickname', 'game', 'score', 'joined_at')
    list_filter = ('game',)
    search_fields = ('nickname',)


@admin.register(PlayerAnswer)
class PlayerAnswerAdmin(admin.ModelAdmin):
    list_display = ('player', 'question_index', 'is_correct', 'points_awarded', 'ms_to_answer')
    list_filter = ('is_correct',)
