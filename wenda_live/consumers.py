"""WebSocket consumers for the Wenda-Live live-game flow (Phase 3).

Two consumers drive every game, both joined to the same broadcast group so
host and players stay in lock-step:

    HostConsumer  ws/host/<room_code>/   (authenticated instructor/admin)
    PlayConsumer  ws/play/<room_code>/   (anonymous, identified by session)

The host connection is *authoritative*: it owns the question timer and decides
when to start, reveal, advance, and finish. Players only send answers. This
keeps the game loop in one place and avoids races between many sockets.

Channel groups per room:
    game_<code>        broadcast group — host + every player (questions, results)
    game_<code>_ctrl   control group — host only — internal triggers such as the
                       "everyone has answered, reveal early" nudge a player sends.

State lives in MySQL (GameSession.status / current_question_index, Player.score,
PlayerAnswer rows); the channel layer (in-memory for dev, Redis for prod) only
carries messages. Restarting the host socket therefore resyncs from the DB.
"""

import asyncio
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import F

from .models import GameSession, Player, PlayerAnswer, QuestionBankEntry, User


# ---------------------------------------------------------------------------
# Scoring + option helpers
# ---------------------------------------------------------------------------

POINTS_BASE = 1000  # awarded for an instant correct answer
POINTS_FLOOR = 500  # awarded for a correct answer right on the buzzer


def score_for(is_correct: bool, ms_to_answer: int, seconds: int) -> int:
    """Speed-weighted points: full value for a fast correct answer, less as the
    clock runs down, zero for a wrong answer."""
    if not is_correct:
        return 0
    limit_ms = max(1, seconds * 1000)
    ms = max(0, min(ms_to_answer, limit_ms))
    fraction_left = 1 - (ms / limit_ms)
    return int(round(POINTS_FLOOR + (POINTS_BASE - POINTS_FLOOR) * fraction_left))


def option_texts(entry: QuestionBankEntry) -> list[str]:
    """Normalise QuestionBankEntry.options into a plain list of choice strings.

    wenda-quiz stores options as a label-keyed dict, e.g.
    ``{"A": "Cloud cost management", "B": ...}``, with correct_answer_text being
    the chosen *value* (the full text). For that shape we return the values in
    label order (A, B, C, ...) so the choices and the correct answer line up.
    A list of strings, or a list of ``{text/label: ...}`` dicts, is also handled.
    """
    options = entry.options or []
    if isinstance(options, dict):
        return [str(options[key]) for key in sorted(options)]
    texts = []
    for opt in options:
        if isinstance(opt, dict):
            texts.append(str(opt.get('text', opt.get('label', ''))))
        else:
            texts.append(str(opt))
    return texts


# ---------------------------------------------------------------------------
# Shared base consumer
# ---------------------------------------------------------------------------


class RoomConsumer(AsyncJsonWebsocketConsumer):
    """Group plumbing + broadcast handlers shared by host and player sockets.

    Every ``game.*`` message sent to the broadcast group is relayed verbatim to
    the socket; both consumers define the same handlers so neither errors when a
    message it doesn't directly care about arrives.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.room_code = None
        self.game_id = None

    @property
    def room_group(self) -> str:
        return f'game_{self.room_code}'

    @property
    def ctrl_group(self) -> str:
        return f'game_{self.room_code}_ctrl'

    # -- DB helpers shared by both consumers --------------------------------

    @database_sync_to_async
    def _load_game(self):
        return GameSession.objects.filter(room_code=self.room_code).first()

    @database_sync_to_async
    def _lobby_payload(self) -> dict:
        game = GameSession.objects.get(pk=self.game_id)
        players = list(game.players.values_list('nickname', flat=True))
        return {
            'players': players,
            'count': len(players),
            'status': game.status,
        }

    @database_sync_to_async
    def _leaderboard(self) -> list[dict]:
        players = (
            Player.objects.filter(game_id=self.game_id)
            .order_by('-score', 'joined_at')
            .values('nickname', 'score')
        )
        return list(players)

    # -- broadcast relay handlers (game.* -> socket) ------------------------

    async def game_lobby(self, event):
        await self.send_json({'event': 'lobby', **event['payload']})

    async def game_question(self, event):
        await self.send_json({'event': 'question', **event['payload']})

    async def game_answer_count(self, event):
        await self.send_json({'event': 'answer_count', **event['payload']})

    async def game_results(self, event):
        await self.send_json({'event': 'results', **event['payload']})

    async def game_over(self, event):
        await self.send_json({'event': 'game_over', **event['payload']})

    # -- broadcast helper ---------------------------------------------------

    async def broadcast(self, kind: str, payload: dict):
        await self.channel_layer.group_send(
            self.room_group, {'type': f'game.{kind}', 'payload': payload}
        )


# ---------------------------------------------------------------------------
# Host consumer — authoritative game loop
# ---------------------------------------------------------------------------


class HostConsumer(RoomConsumer):
    """The host's control socket. Owns the timer and all state transitions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._timer = None          # asyncio task counting down the open question
        self._revealed = set()      # question indices already revealed (idempotency)

    async def connect(self):
        self.room_code = self.scope['url_route']['kwargs']['room_code'].upper()
        user = self.scope.get('user')
        game = await self._load_game()

        if game is None or user is None or not user.is_authenticated:
            await self.close()
            return
        if game.host_id != user.id or not self._can_host(user):
            await self.close()
            return

        self.game_id = game.id
        await self.channel_layer.group_add(self.room_group, self.channel_name)
        await self.channel_layer.group_add(self.ctrl_group, self.channel_name)
        await self.accept()

        # Send the host the current lobby/state so a reconnect resyncs.
        await self.send_json({'event': 'lobby', **(await self._lobby_payload())})

    @staticmethod
    def _can_host(user) -> bool:
        return getattr(user, 'role', None) in (User.Role.INSTRUCTOR, User.Role.ADMIN)

    async def disconnect(self, code):
        self._cancel_timer()
        if self.room_code:
            await self.channel_layer.group_discard(self.room_group, self.channel_name)
            await self.channel_layer.group_discard(self.ctrl_group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        action = content.get('action')
        if action == 'start':
            await self._start_question(0)
        elif action == 'next':
            await self._advance()
        elif action == 'reveal':
            await self._reveal()
        elif action == 'end':
            await self._finish()

    # -- internal control-group trigger (a player nudges the host) ----------

    async def ctrl_reveal(self, event):
        """A player reports everyone has answered; reveal early."""
        await self._reveal()

    # -- game loop ----------------------------------------------------------

    async def _start_question(self, index: int):
        game = await self._begin_question(index)
        if game is None:
            return
        question = await self._question_payload(index)
        if question is None:
            await self._finish()
            return
        self._revealed.discard(index)
        await self.broadcast('question', question)
        self._arm_timer(index, question['seconds'])

    async def _advance(self):
        index = await self._current_index()
        await self._start_question(index + 1)

    async def _reveal(self):
        index = await self._current_index()
        if index in self._revealed:
            return
        self._revealed.add(index)
        self._cancel_timer()
        payload = await self._results_payload(index)
        await self.broadcast('results', payload)

    async def _finish(self):
        self._cancel_timer()
        await self._mark_finished()
        leaderboard = await self._leaderboard()
        await self.broadcast('over', {'leaderboard': leaderboard})

    # -- timer --------------------------------------------------------------

    def _arm_timer(self, index: int, seconds: int):
        self._cancel_timer()
        self._timer = asyncio.create_task(self._countdown(index, seconds))

    def _cancel_timer(self):
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _countdown(self, index: int, seconds: int):
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        # Only reveal if we are still on this question.
        if await self._current_index() == index:
            await self._reveal()

    # -- DB transitions -----------------------------------------------------

    @database_sync_to_async
    def _begin_question(self, index):
        from django.utils import timezone

        game = GameSession.objects.get(pk=self.game_id)
        if index >= len(game.question_ids):
            return None
        if game.status == GameSession.Status.LOBBY:
            game.started_at = timezone.now()
        game.status = GameSession.Status.ACTIVE
        game.current_question_index = index
        game.save(update_fields=['status', 'started_at', 'current_question_index'])
        return game

    @database_sync_to_async
    def _current_index(self) -> int:
        return GameSession.objects.values_list(
            'current_question_index', flat=True
        ).get(pk=self.game_id)

    @database_sync_to_async
    def _question_payload(self, index):
        return _build_question_payload(self.game_id, index, include_answer=False)

    @database_sync_to_async
    def _results_payload(self, index):
        game = GameSession.objects.get(pk=self.game_id)
        if index >= len(game.question_ids):
            return {'index': index, 'correct': '', 'tally': {}, 'leaderboard': []}
        entry = QuestionBankEntry.objects.filter(
            pk=game.question_ids[index]
        ).first()
        correct = entry.correct_answer_text if entry else ''

        tally = {}
        answers = PlayerAnswer.objects.filter(
            player__game_id=self.game_id, question_index=index
        )
        for chosen in answers.values_list('chosen_text', flat=True):
            tally[chosen] = tally.get(chosen, 0) + 1

        leaderboard = list(
            Player.objects.filter(game_id=self.game_id)
            .order_by('-score', 'joined_at')
            .values('nickname', 'score')
        )
        has_next = (index + 1) < len(game.question_ids)
        return {
            'index': index,
            'correct': correct,
            'tally': tally,
            'leaderboard': leaderboard,
            'has_next': has_next,
        }

    @database_sync_to_async
    def _mark_finished(self):
        from django.utils import timezone

        GameSession.objects.filter(pk=self.game_id).update(
            status=GameSession.Status.FINISHED, finished_at=timezone.now()
        )


# ---------------------------------------------------------------------------
# Player consumer
# ---------------------------------------------------------------------------


class PlayConsumer(RoomConsumer):
    """A player's socket. Identified by the session player_id set at HTTP join."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.player_id = None

    async def connect(self):
        self.room_code = self.scope['url_route']['kwargs']['room_code'].upper()
        game = await self._load_game()
        session = self.scope.get('session')
        player_id = session.get('player_id') if session else None

        player = await self._load_player(game, player_id) if game else None
        if player is None:
            await self.close()
            return

        # A player tied to a student account may only be driven by that account
        # (the HTTP join already gated enrollment; this stops session/player_id
        # reuse by a different or signed-out browser).
        if player.student_user_id:
            user = self.scope.get('user')
            if not getattr(user, 'is_authenticated', False) or user.id != player.student_user_id:
                await self.close()
                return

        self.game_id = game.id
        self.player_id = player.id
        await self.channel_layer.group_add(self.room_group, self.channel_name)
        await self.accept()

        # Tell the host the roster changed.
        await self.broadcast('lobby', await self._lobby_payload())

        # If a question is already open, drop the late joiner straight into it.
        # (Clients dedupe questions by index, so the rare connect-races-start
        # case where this also arrives via broadcast is harmless.)
        live = await self._live_question()
        if live is not None:
            await self.send_json({'event': 'question', **live})

        # Welcome last, so a client/test that waits for 'joined' knows the whole
        # connect handshake — including the late-join check — has completed.
        await self.send_json({'event': 'joined', 'nickname': player.nickname})

    @database_sync_to_async
    def _load_player(self, game, player_id):
        if not player_id:
            return None
        return Player.objects.filter(id=player_id, game=game).first()

    async def disconnect(self, code):
        if self.room_code and self.game_id:
            await self.channel_layer.group_discard(self.room_group, self.channel_name)
            await self.broadcast('lobby', await self._lobby_payload())

    async def receive_json(self, content, **kwargs):
        if content.get('action') == 'answer':
            await self._submit_answer(content)

    async def _submit_answer(self, content):
        index = content.get('question_index')
        choice = content.get('choice')
        ms = content.get('ms', 0)
        if index is None or choice is None:
            return

        recorded, answered_count, player_count = await self._record_answer(
            index, str(choice), int(ms or 0)
        )
        if not recorded:
            return

        # Acknowledge to just this player (no correctness yet — that's revealed
        # for everyone at once when the question closes).
        await self.send_json({'event': 'answer_ack', 'question_index': index})

        # Let the host show how many have answered.
        await self.broadcast('answer_count', {
            'index': index, 'answered': answered_count, 'total': player_count,
        })

        # Everyone answered -> nudge the host to reveal without waiting out the clock.
        if answered_count >= player_count and player_count > 0:
            await self.channel_layer.group_send(
                self.ctrl_group, {'type': 'ctrl.reveal'}
            )

    @database_sync_to_async
    def _record_answer(self, index, choice, ms):
        """Score and persist the answer. Returns (recorded, answered, total).

        Idempotent per (player, question): a second submission is ignored.
        """
        game = GameSession.objects.get(pk=self.game_id)
        if game.status != GameSession.Status.ACTIVE:
            return False, 0, 0
        if index != game.current_question_index:
            return False, 0, 0
        if index >= len(game.question_ids):
            return False, 0, 0

        if PlayerAnswer.objects.filter(
            player_id=self.player_id, question_index=index
        ).exists():
            answered = PlayerAnswer.objects.filter(
                player__game_id=self.game_id, question_index=index
            ).count()
            total = Player.objects.filter(game_id=self.game_id).count()
            return False, answered, total

        entry = QuestionBankEntry.objects.filter(
            pk=game.question_ids[index]
        ).first()
        is_correct = bool(entry and choice == entry.correct_answer_text)
        points = score_for(is_correct, ms, game.seconds_per_question)

        PlayerAnswer.objects.create(
            player_id=self.player_id,
            question_index=index,
            chosen_text=choice,
            is_correct=is_correct,
            ms_to_answer=max(0, ms),
            points_awarded=points,
        )
        if points:
            Player.objects.filter(pk=self.player_id).update(
                score=F('score') + points
            )

        answered = PlayerAnswer.objects.filter(
            player__game_id=self.game_id, question_index=index
        ).count()
        total = Player.objects.filter(game_id=self.game_id).count()
        return True, answered, total

    @database_sync_to_async
    def _live_question(self):
        game = GameSession.objects.get(pk=self.game_id)
        if game.status != GameSession.Status.ACTIVE:
            return None
        return _build_question_payload(
            self.game_id, game.current_question_index, include_answer=False
        )


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _build_question_payload(game_id, index, include_answer=False):
    """Build the on-the-wire question payload (sync; call via database_sync_to_async)."""
    game = GameSession.objects.get(pk=game_id)
    if index >= len(game.question_ids):
        return None
    entry = QuestionBankEntry.objects.filter(pk=game.question_ids[index]).first()
    if entry is None:
        return None
    payload = {
        'index': index,
        'total': len(game.question_ids),
        'question': entry.question,
        'options': option_texts(entry),
        'seconds': game.seconds_per_question,
        'ends_at': int((time.time() + game.seconds_per_question) * 1000),
    }
    if include_answer:
        payload['correct'] = entry.correct_answer_text
    return payload
