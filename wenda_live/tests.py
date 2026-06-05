"""Tests for the Wenda-Live live-game layer.

Helper tests are pure/offline. The live-flow tests drive the real WebSocket
consumers with channels' WebsocketCommunicator over the project's routing,
asserting the full host+player round (lobby -> question -> scoring -> reveal ->
leaderboard -> game over) and DB persistence.

These run against an isolated test database created by WendaLiveTestRunner (see
settings.TEST_RUNNER), so they need the configured DB service to be reachable
but never touch the shared wenda_db. We use TransactionTestCase because the
consumers read the DB from worker threads, which only see committed rows.
"""

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse

from .consumers import option_texts, score_for
from .models import (
    GameSession,
    LiveQuizGrade,
    Player,
    PlayerAnswer,
    QuestionBankEntry,
    Student,
    Subject,
    SubjectStudent,
    User,
)
from .routing import websocket_urlpatterns

app = URLRouter(websocket_urlpatterns)


# ---------------------------------------------------------------------------
# Pure helpers (no DB, no sockets)
# ---------------------------------------------------------------------------


class JoinEnrollmentTests(TestCase):
    """Only a signed-in student enrolled in the game's subject may join."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='prof', password='pw', role=User.Role.INSTRUCTOR,
        )
        self.subject = Subject.objects.create(name='Maths', code='MATH')
        self.game = GameSession.objects.create(
            host=self.instructor, subject=self.subject,
            question_ids=[], num_questions=1, seconds_per_question=20,
        )

        # An enrolled student.
        self.student_user = User.objects.create_user(
            username='stud', password='pw', role=User.Role.STUDENT,
        )
        self.student = Student.objects.create(
            user=self.student_user, student_number='S1',
            course='CS', section='A', year_level=1,
        )
        SubjectStudent.objects.create(subject=self.subject, student=self.student)

        # A student who exists but is NOT enrolled in this subject.
        self.outsider_user = User.objects.create_user(
            username='stud2', password='pw', role=User.Role.STUDENT,
        )
        Student.objects.create(
            user=self.outsider_user, student_number='S2',
            course='CS', section='A', year_level=1,
        )

    def _join(self, nickname='Nick'):
        return self.client.post(
            reverse('wenda_live:join_game'),
            {'room_code': self.game.room_code, 'nickname': nickname},
        )

    def test_anonymous_is_sent_to_login(self):
        resp = self._join()
        self.assertRedirects(resp, reverse('wenda_live:login'))
        self.assertFalse(Player.objects.filter(game=self.game).exists())

    def test_enrolled_student_can_join(self):
        self.client.login(username='stud', password='pw')
        resp = self._join()
        self.assertRedirects(
            resp, reverse('wenda_live:play_game', args=[self.game.room_code])
        )
        player = Player.objects.get(game=self.game)
        self.assertEqual(player.student_user_id, self.student_user.id)

    def test_non_enrolled_student_is_rejected(self):
        self.client.login(username='stud2', password='pw')
        resp = self._join()
        self.assertRedirects(resp, reverse('wenda_live:home'))
        self.assertFalse(Player.objects.filter(game=self.game).exists())

    def test_instructor_cannot_join_as_player(self):
        self.client.login(username='prof', password='pw')
        resp = self._join()
        self.assertRedirects(resp, reverse('wenda_live:home'))
        self.assertFalse(Player.objects.filter(game=self.game).exists())

    def test_rejoin_returns_same_player(self):
        self.client.login(username='stud', password='pw')
        self._join(nickname='First')
        self._join(nickname='Second')  # same student, keeps one row
        players = Player.objects.filter(game=self.game, student_user=self.student_user)
        self.assertEqual(players.count(), 1)
        self.assertEqual(players.first().nickname, 'Second')


class HostCreateGameTests(TestCase):
    """Two-step host flow: pick subject (step 1), then pick the actual questions
    arranged by topic (step 2)."""

    def setUp(self):
        self.host = User.objects.create_user(
            username='prof', password='pw', role=User.Role.INSTRUCTOR,
        )
        self.subject = Subject.objects.create(name='Maths', code='MATH')
        self.algebra = [
            QuestionBankEntry.objects.create(
                question=f'Algebra Q{i}', options={'A': 'x'}, correct_answer_text='x',
                subject=self.subject, topic='Algebra',
            )
            for i in range(3)
        ]
        self.geometry = [
            QuestionBankEntry.objects.create(
                question=f'Geometry Q{i}', options={'A': 'x'}, correct_answer_text='x',
                subject=self.subject, topic='Geometry',
            )
            for i in range(2)
        ]
        self.client.login(username='prof', password='pw')

    def _latest_game(self):
        return GameSession.objects.filter(host=self.host).latest('created_at')

    # -- step 1 ------------------------------------------------------------

    def test_step1_forwards_to_question_picker(self):
        resp = self.client.post(
            reverse('wenda_live:host_create_game'),
            {'subject': self.subject.id, 'seconds_per_question': 30},
        )
        self.assertRedirects(
            resp,
            reverse('wenda_live:host_select_questions')
            + f'?subject={self.subject.id}&seconds=30',
        )

    def test_step2_lists_questions_grouped_by_topic(self):
        resp = self.client.get(
            reverse('wenda_live:host_select_questions'),
            {'subject': self.subject.id, 'seconds': 20},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Algebra')
        self.assertContains(resp, 'Geometry')
        self.assertContains(resp, 'Algebra Q0')

    # -- step 2 (create) ---------------------------------------------------

    def _select(self, ids, seconds=20):
        return self.client.post(
            reverse('wenda_live:host_select_questions'),
            {'subject': self.subject.id, 'seconds': seconds,
             'questions': [str(i) for i in ids]},
        )

    def test_creates_game_with_exactly_the_picked_questions(self):
        picked = [self.algebra[0].id, self.geometry[1].id]
        resp = self._select(picked, seconds=45)
        game = self._latest_game()
        self.assertRedirects(
            resp, reverse('wenda_live:host_game', args=[game.room_code])
        )
        self.assertEqual(set(game.question_ids), set(picked))
        self.assertEqual(game.num_questions, 2)
        self.assertEqual(game.seconds_per_question, 45)

    def test_questions_stored_in_topic_order(self):
        # Pass them out of order; result should be grouped by topic (Algebra
        # before Geometry), questions stable within a topic.
        picked = [self.geometry[1].id, self.algebra[2].id, self.algebra[0].id]
        self._select(picked)
        game = self._latest_game()
        self.assertEqual(
            game.question_ids,
            [self.algebra[0].id, self.algebra[2].id, self.geometry[1].id],
        )

    def test_no_selection_is_rejected(self):
        resp = self._select([])
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(GameSession.objects.filter(host=self.host).exists())

    def test_missing_subject_redirects_to_step1(self):
        resp = self.client.get(reverse('wenda_live:host_select_questions'))
        self.assertRedirects(resp, reverse('wenda_live:host_create_game'))


class HostAccessControlTests(TestCase):
    """Only instructors/admins may reach the host flow; students are bounced."""

    def setUp(self):
        self.subject = Subject.objects.create(name='Maths', code='MATH')
        self.student_user = User.objects.create_user(
            username='stud', password='pw', role=User.Role.STUDENT,
        )
        self.client.login(username='stud', password='pw')

    def test_student_cannot_open_step1(self):
        resp = self.client.get(reverse('wenda_live:host_create_game'))
        self.assertRedirects(resp, reverse('wenda_live:home'))

    def test_student_cannot_post_step1(self):
        resp = self.client.post(
            reverse('wenda_live:host_create_game'),
            {'subject': self.subject.id, 'seconds_per_question': 30},
        )
        self.assertRedirects(resp, reverse('wenda_live:home'))

    def test_student_cannot_open_step2(self):
        resp = self.client.get(
            reverse('wenda_live:host_select_questions'),
            {'subject': self.subject.id, 'seconds': 20},
        )
        self.assertRedirects(resp, reverse('wenda_live:home'))

    def test_student_cannot_create_game_via_step2(self):
        entry = QuestionBankEntry.objects.create(
            question='Q', options={'A': 'x'}, correct_answer_text='x',
            subject=self.subject, topic='Algebra',
        )
        resp = self.client.post(
            reverse('wenda_live:host_select_questions'),
            {'subject': self.subject.id, 'seconds': 20, 'questions': [str(entry.id)]},
        )
        self.assertRedirects(resp, reverse('wenda_live:home'))
        self.assertFalse(GameSession.objects.exists())

    def test_student_does_not_see_host_button_on_home(self):
        resp = self.client.get(reverse('wenda_live:home'))
        self.assertContains(resp, 'hosting is for instructors only')
        self.assertNotContains(resp, reverse('wenda_live:host_create_game'))


class HelperTests(SimpleTestCase):
    def test_wrong_answer_scores_zero(self):
        self.assertEqual(score_for(False, 0, 20), 0)

    def test_instant_correct_scores_full(self):
        self.assertEqual(score_for(True, 0, 20), 1000)

    def test_buzzer_correct_scores_floor(self):
        self.assertEqual(score_for(True, 20_000, 20), 500)

    def test_faster_correct_scores_higher(self):
        self.assertGreater(score_for(True, 2_000, 20), score_for(True, 10_000, 20))

    def test_overshoot_time_is_clamped(self):
        self.assertEqual(score_for(True, 999_999, 20), 500)

    def test_option_texts_handles_strings_and_dicts(self):
        entry = type('E', (), {'options': ['a', {'text': 'b'}, {'label': 'c'}]})()
        self.assertEqual(option_texts(entry), ['a', 'b', 'c'])

    def test_option_texts_handles_label_keyed_dict(self):
        # The real wenda-quiz shape: {label: text}; return texts in label order.
        entry = type('E', (), {'options': {'B': 'two', 'A': 'one', 'C': 'three'}})()
        self.assertEqual(option_texts(entry), ['one', 'two', 'three'])

    def test_option_texts_handles_empty(self):
        self.assertEqual(option_texts(type('E', (), {'options': None})()), [])


# ---------------------------------------------------------------------------
# Live WebSocket flow
# ---------------------------------------------------------------------------


async def _wait_for(comm, event, skip=('lobby', 'joined', 'answer_count'), tries=20):
    """Receive until `event` arrives, skipping known background noise."""
    seen = []
    for _ in range(tries):
        msg = await comm.receive_json_from(timeout=5)
        seen.append(msg.get('event'))
        if msg.get('event') == event:
            return msg
        assert msg.get('event') in skip, (
            f'unexpected {msg.get("event")} awaiting {event}; full={msg}; seen={seen}'
        )
    raise AssertionError(f'never received {event}')


async def _connect_player(player_id, room_code):
    """Connect a player and wait out the full connect handshake.

    'joined' is the consumer's last connect message, so receiving it guarantees
    the late-join check already ran — keeping the start-of-game deterministic
    instead of racing the connect tail.
    """
    comm = WebsocketCommunicator(app, f'ws/play/{room_code}/')
    comm.scope['session'] = {'player_id': player_id}
    connected, _ = await comm.connect()
    if connected:
        await _wait_for(comm, 'joined', skip=('lobby',))
    return comm, connected


class LiveGameFlowTests(TransactionTestCase):
    def setUp(self):
        self.host = User.objects.create(
            username='host', role=User.Role.INSTRUCTOR, is_active=True,
        )
        self.subject = Subject.objects.create(name='Maths', code='MATH')
        self.questions = [
            QuestionBankEntry.objects.create(
                question=f'Q{i}',
                # Mirror the real wenda-quiz shape: label-keyed dict, with the
                # correct answer being the full text value (not the letter).
                options={'A': 'Right', 'B': 'Wrong1', 'C': 'Wrong2'},
                correct_answer_text='Right',
                subject=self.subject,
            )
            for i in range(2)
        ]
        self.game = GameSession.objects.create(
            host=self.host,
            subject=self.subject,
            question_ids=[q.id for q in self.questions],
            num_questions=2,
            seconds_per_question=20,
        )
        self.alice = Player.objects.create(game=self.game, nickname='Alice')
        self.bob = Player.objects.create(game=self.game, nickname='Bob')

    # -- connection / auth gating ------------------------------------------

    def test_host_without_auth_is_rejected(self):
        async def flow():
            comm = WebsocketCommunicator(app, f'ws/host/{self.game.room_code}/')
            connected, _ = await comm.connect()
            await comm.disconnect()
            return connected
        self.assertFalse(async_to_sync(flow)())

    def test_player_without_session_is_rejected(self):
        async def flow():
            comm = WebsocketCommunicator(app, f'ws/play/{self.game.room_code}/')
            connected, _ = await comm.connect()
            await comm.disconnect()
            return connected
        self.assertFalse(async_to_sync(flow)())

    def test_host_of_other_game_is_rejected(self):
        async def flow():
            other = await database_sync_to_async(User.objects.create)(
                username='intruder', role=User.Role.INSTRUCTOR, is_active=True,
            )
            comm = WebsocketCommunicator(app, f'ws/host/{self.game.room_code}/')
            comm.scope['user'] = other
            connected, _ = await comm.connect()
            await comm.disconnect()
            return connected
        self.assertFalse(async_to_sync(flow)())

    # -- full round --------------------------------------------------------

    def test_full_round_flow(self):
        async_to_sync(self._full_round)()

    async def _full_round(self):
        # Host connects and gets the initial lobby snapshot.
        host = WebsocketCommunicator(app, f'ws/host/{self.game.room_code}/')
        host.scope['user'] = self.host
        connected, _ = await host.connect()
        self.assertTrue(connected)
        first = await host.receive_json_from(timeout=5)
        self.assertEqual(first['event'], 'lobby')

        # Players connect (handshake fully settled before we start).
        pa, ca = await _connect_player(self.alice.id, self.game.room_code)
        pb, cb = await _connect_player(self.bob.id, self.game.room_code)
        self.assertTrue(ca and cb)

        # Start -> everyone receives question 0.
        await host.send_json_to({'action': 'start'})
        hq = await _wait_for(host, 'question')
        aq = await _wait_for(pa, 'question')
        await _wait_for(pb, 'question')
        self.assertEqual(hq['index'], 0)
        self.assertGreaterEqual(len(aq['options']), 2)

        # Alice answers correctly and fast; Bob answers wrong.
        await pa.send_json_to(
            {'action': 'answer', 'question_index': 0, 'choice': 'Right', 'ms': 1000}
        )
        await _wait_for(pa, 'answer_ack')
        await pb.send_json_to(
            {'action': 'answer', 'question_index': 0, 'choice': 'Wrong1', 'ms': 5000}
        )
        await _wait_for(pb, 'answer_ack')

        # All answered -> host auto-reveals.
        res = await _wait_for(host, 'results')
        self.assertEqual(res['correct'], 'Right')
        self.assertTrue(res['has_next'])
        scores = {r['nickname']: r['score'] for r in res['leaderboard']}
        self.assertGreater(scores['Alice'], 0)
        self.assertEqual(scores['Bob'], 0)
        self.assertGreater(scores['Alice'], scores['Bob'])

        # Players also see the reveal.
        ra = await _wait_for(pa, 'results')
        self.assertEqual(ra['correct'], 'Right')

        # Advance to Q1, reveal it manually, then finish.
        await host.send_json_to({'action': 'next'})
        hq1 = await _wait_for(host, 'question')
        self.assertEqual(hq1['index'], 1)
        await host.send_json_to({'action': 'reveal'})
        res2 = await _wait_for(host, 'results')
        self.assertFalse(res2['has_next'])

        await host.send_json_to({'action': 'end'})
        over = await _wait_for(host, 'game_over')
        self.assertEqual(len(over['leaderboard']), 2)
        self.assertEqual(over['leaderboard'][0]['nickname'], 'Alice')  # winner first

        # Persistence: the answers were written through.
        count = await database_sync_to_async(
            PlayerAnswer.objects.filter(player__game=self.game).count
        )()
        self.assertGreaterEqual(count, 2)

        await host.disconnect()
        await pa.disconnect()
        await pb.disconnect()

    def test_duplicate_answer_is_ignored(self):
        async_to_sync(self._duplicate_answer)()

    async def _duplicate_answer(self):
        host = WebsocketCommunicator(app, f'ws/host/{self.game.room_code}/')
        host.scope['user'] = self.host
        await host.connect()
        await host.receive_json_from(timeout=5)

        pa, _ = await _connect_player(self.alice.id, self.game.room_code)

        await host.send_json_to({'action': 'start'})
        await _wait_for(host, 'question')
        await _wait_for(pa, 'question')

        await pa.send_json_to(
            {'action': 'answer', 'question_index': 0, 'choice': 'Right', 'ms': 1000}
        )
        await _wait_for(pa, 'answer_ack')
        # Second answer for the same question must not double-score.
        await pa.send_json_to(
            {'action': 'answer', 'question_index': 0, 'choice': 'Wrong1', 'ms': 100}
        )

        await host.send_json_to({'action': 'reveal'})
        res = await _wait_for(host, 'results')

        rows = await database_sync_to_async(
            lambda: list(PlayerAnswer.objects.filter(player=self.alice, question_index=0))
        )()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_correct)

        await host.disconnect()
        await pa.disconnect()


# ---------------------------------------------------------------------------
# Live-quiz grades (gradeable result per enrolled student, separate table)
# ---------------------------------------------------------------------------


class LiveQuizGradeTests(TransactionTestCase):
    """Finishing a game writes a LiveQuizGrade row per enrolled student whose
    percentage is the speed-weighted score over the max achievable score."""

    def setUp(self):
        self.host = User.objects.create(
            username='host', role=User.Role.INSTRUCTOR, is_active=True,
        )
        self.subject = Subject.objects.create(name='Maths', code='MATH')
        self.questions = [
            QuestionBankEntry.objects.create(
                question=f'Q{i}',
                options={'A': 'Right', 'B': 'Wrong1', 'C': 'Wrong2'},
                correct_answer_text='Right',
                subject=self.subject,
            )
            for i in range(2)
        ]
        self.game = GameSession.objects.create(
            host=self.host,
            subject=self.subject,
            question_ids=[q.id for q in self.questions],
            num_questions=2,
            seconds_per_question=20,
        )
        # An enrolled student, joined as a player tied to their account.
        self.student_user = User.objects.create(
            username='stud', role=User.Role.STUDENT, is_active=True,
        )
        self.student = Student.objects.create(
            user=self.student_user, student_number='S1',
            course='CS', section='A', year_level=1,
        )
        SubjectStudent.objects.create(subject=self.subject, student=self.student)
        self.player = Player.objects.create(
            game=self.game, nickname='Alice', student_user=self.student_user,
        )

    def test_finishing_writes_grade_for_enrolled_student(self):
        async_to_sync(self._play_and_finish)()

        grade = LiveQuizGrade.objects.get(game=self.game, student=self.student)
        # Answered 1 of 2 correctly; the correct answer at 1000ms (of 20s) scores
        # 975 speed-weighted points. The grade is that score over the max
        # achievable (1000 * 2 = 2000): 975 / 2000 = 48.75%.
        self.assertEqual(grade.correct_count, 1)
        self.assertEqual(grade.total_questions, 2)
        self.assertEqual(str(grade.total_score), '975.00')
        self.assertEqual(str(grade.percentage), '48.75')

    def test_anonymous_player_gets_no_grade(self):
        Player.objects.create(game=self.game, nickname='Ghost')  # no student_user
        async_to_sync(self._play_and_finish)()
        # Only the enrolled student is graded; the anonymous row is skipped.
        self.assertEqual(LiveQuizGrade.objects.filter(game=self.game).count(), 1)

    async def _play_and_finish(self):
        # A lone player answering trips the "everyone answered -> reveal early"
        # path, so results/question/answer_count all interleave; skip any
        # background event and wait only for the one we care about.
        noise = ('lobby', 'joined', 'answer_count', 'answer_ack', 'results', 'question')

        host = WebsocketCommunicator(app, f'ws/host/{self.game.room_code}/')
        host.scope['user'] = self.host
        await host.connect()
        await host.receive_json_from(timeout=5)

        player = WebsocketCommunicator(app, f'ws/play/{self.game.room_code}/')
        player.scope['session'] = {'player_id': self.player.id}
        player.scope['user'] = self.student_user  # the student-bound player must match
        await player.connect()
        await _wait_for(player, 'joined', skip=('lobby',))

        # Q0: answer correctly.
        await host.send_json_to({'action': 'start'})
        await _wait_for(host, 'question', skip=noise)
        await _wait_for(player, 'question', skip=noise)
        await player.send_json_to(
            {'action': 'answer', 'question_index': 0, 'choice': 'Right', 'ms': 1000}
        )
        await _wait_for(player, 'answer_ack', skip=noise)
        await host.send_json_to({'action': 'reveal'})
        await _wait_for(host, 'results', skip=noise)

        # Q1: answer incorrectly.
        await host.send_json_to({'action': 'next'})
        await _wait_for(host, 'question', skip=noise)
        await _wait_for(player, 'question', skip=noise)
        await player.send_json_to(
            {'action': 'answer', 'question_index': 1, 'choice': 'Wrong1', 'ms': 1000}
        )
        await _wait_for(player, 'answer_ack', skip=noise)

        await host.send_json_to({'action': 'end'})
        await _wait_for(host, 'game_over', skip=noise)

        await host.disconnect()
        await player.disconnect()


# ---------------------------------------------------------------------------
# Student viewing their own live-quiz results
# ---------------------------------------------------------------------------


class MyResultsTests(TestCase):
    """A student can view (not download) their own saved LiveQuizGrade rows."""

    def setUp(self):
        self.host = User.objects.create_user(
            username='prof', password='pw', role=User.Role.INSTRUCTOR,
        )
        self.subject = Subject.objects.create(name='Maths', code='MATH')
        self.game = GameSession.objects.create(
            host=self.host, subject=self.subject,
            question_ids=[], num_questions=2, seconds_per_question=20,
        )

        self.student_user = User.objects.create_user(
            username='stud', password='pw', role=User.Role.STUDENT,
        )
        self.student = Student.objects.create(
            user=self.student_user, student_number='S1',
            course='CS', section='A', year_level=1,
        )
        self.grade = LiveQuizGrade.objects.create(
            game=self.game, student=self.student,
            correct_count=1, total_questions=2,
            total_score='975.00', percentage='48.75',
        )

        # Another student's grade — must never leak into the first student's page.
        self.other_user = User.objects.create_user(
            username='stud2', password='pw', role=User.Role.STUDENT,
        )
        self.other_student = Student.objects.create(
            user=self.other_user, student_number='S2',
            course='CS', section='A', year_level=1,
        )
        LiveQuizGrade.objects.create(
            game=self.game, student=self.other_student,
            correct_count=2, total_questions=2,
            total_score='2000.00', percentage='100.00',
        )

    def test_student_sees_their_own_result(self):
        self.client.login(username='stud', password='pw')
        resp = self.client.get(reverse('wenda_live:my_results'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '48.75')
        self.assertContains(resp, '1 / 2')
        self.assertContains(resp, self.game.room_code)

    def test_student_does_not_see_other_students_result(self):
        self.client.login(username='stud', password='pw')
        resp = self.client.get(reverse('wenda_live:my_results'))
        self.assertNotContains(resp, '100.00')

    def test_student_without_grades_sees_empty_state(self):
        self.client.login(username='stud2', password='pw')
        LiveQuizGrade.objects.filter(student=self.other_student).delete()
        resp = self.client.get(reverse('wenda_live:my_results'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "don't have any live quiz results yet")

    def test_instructor_is_rejected(self):
        self.client.login(username='prof', password='pw')
        resp = self.client.get(reverse('wenda_live:my_results'))
        self.assertRedirects(resp, reverse('wenda_live:home'))

    def test_anonymous_is_sent_to_login(self):
        resp = self.client.get(reverse('wenda_live:my_results'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('wenda_live:login'), resp['Location'])
