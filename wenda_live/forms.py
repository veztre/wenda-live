"""Forms for the Wenda-Live HTTP layer (Phase 2).

These cover the non-realtime flow: a host creating a game and a player joining
one by room code. Live gameplay (answering, scoring, leaderboard) is handled by
the WebSocket consumers in Phase 3, not by forms.
"""

from django import forms

from .models import GameSession, Player, QuestionBankEntry, Subject


class HostGameForm(forms.ModelForm):
    """Step 1 of hosting: pick the subject and the per-question timer.

    The host picks the actual questions on the next step (grouped by topic), so
    this form only captures the subject and round timing. The subject choices
    are limited to subjects that actually have questions in the shared bank, so
    a host can't start a game with nothing to ask.
    """

    class Meta:
        model = GameSession
        fields = ['subject', 'seconds_per_question']
        widgets = {
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'seconds_per_question': forms.NumberInput(
                attrs={'class': 'form-control', 'min': 5, 'max': 120}
            ),
        }
        labels = {
            'seconds_per_question': 'Seconds per question',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        subject_ids_with_questions = (
            QuestionBankEntry.objects.filter(subject__isnull=False)
            .values_list('subject_id', flat=True)
            .distinct()
        )
        self.fields['subject'].queryset = Subject.objects.filter(
            pk__in=subject_ids_with_questions
        )
        self.fields['subject'].empty_label = 'Choose a subject…'


class JoinGameForm(forms.Form):
    """A student joining a game by room code + nickname.

    `player_user` is the signed-in student; their own existing player (on
    re-join) is excluded from the nickname-taken check so they can keep it.
    """

    room_code = forms.CharField(
        max_length=8,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control form-control-lg text-center text-uppercase',
                'placeholder': 'GAME PIN',
                'autocomplete': 'off',
                'autofocus': True,
            }
        ),
    )
    nickname = forms.CharField(
        max_length=30,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control form-control-lg text-center',
                'placeholder': 'Nickname',
                'autocomplete': 'off',
            }
        ),
    )

    def __init__(self, *args, player_user=None, **kwargs):
        self.player_user = player_user
        super().__init__(*args, **kwargs)

    def clean_room_code(self):
        # Codes are generated upper-case; normalise so players can type anything.
        code = self.cleaned_data['room_code'].strip().upper()
        try:
            game = GameSession.objects.get(room_code=code)
        except GameSession.DoesNotExist:
            raise forms.ValidationError('No game found with that PIN.')
        if game.status == GameSession.Status.FINISHED:
            raise forms.ValidationError('That game has already finished.')
        if game.status == GameSession.Status.ACTIVE:
            raise forms.ValidationError('That game has already started.')
        self.game = game
        return code

    def clean_nickname(self):
        nickname = self.cleaned_data['nickname'].strip()
        if not nickname:
            raise forms.ValidationError('Please enter a nickname.')
        return nickname

    def clean(self):
        cleaned = super().clean()
        game = getattr(self, 'game', None)
        nickname = cleaned.get('nickname')
        if game and nickname:
            taken = Player.objects.filter(game=game, nickname=nickname)
            if self.player_user is not None:
                # Don't flag the student's own player when they re-join.
                taken = taken.exclude(student_user=self.player_user)
            if taken.exists():
                self.add_error('nickname', 'That nickname is already taken in this game.')
        return cleaned
