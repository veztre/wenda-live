"""Recompute existing LiveQuizGrade rows from each player's stored score.

Grades written before the scoring change hold the old values (``total_score`` =
correct-answer count, ``percentage`` = plain accuracy). This recomputes them to
the speed-weighted grade used now:

    total_score = Player.score (sum of points_awarded)
    percentage  = total_score / (POINTS_BASE * total_questions) * 100

It reads each grade's matching Player (same game + student account) and rewrites
only ``total_score`` and ``percentage``; ``correct_count`` / ``total_questions``
are left as-is. Idempotent — re-running on already-correct rows changes nothing.

Usage:
    python manage.py backfill_live_grades
    python manage.py backfill_live_grades --dry-run
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from wenda_live.consumers import POINTS_BASE
from wenda_live.models import LiveQuizGrade, Player


class Command(BaseCommand):
    help = 'Recompute existing LiveQuizGrade rows from each player\'s speed-weighted score.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would change without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        grades = (
            LiveQuizGrade.objects
            .select_related('game', 'student')
            .order_by('game_id', 'student_id')
        )

        updated = unchanged = skipped = 0
        for grade in grades:
            total_questions = grade.total_questions or len(grade.game.question_ids)
            if not total_questions:
                self.stdout.write(self.style.WARNING(
                    f'  skip grade #{grade.id}: game {grade.game.room_code} has no questions'
                ))
                skipped += 1
                continue

            player = Player.objects.filter(
                game_id=grade.game_id,
                student_user_id=grade.student.user_id,
            ).first()
            if player is None:
                self.stdout.write(self.style.WARNING(
                    f'  skip grade #{grade.id}: no matching player for '
                    f'{grade.student} in game {grade.game.room_code}'
                ))
                skipped += 1
                continue

            max_score = POINTS_BASE * total_questions
            new_total_score = Decimal(player.score)
            new_percentage = (
                Decimal(player.score) / Decimal(max_score) * 100
            ).quantize(Decimal('0.01'))

            if (grade.total_score == new_total_score
                    and grade.percentage == new_percentage):
                unchanged += 1
                continue

            self.stdout.write(
                f'  grade #{grade.id} ({grade.student} @ {grade.game.room_code}): '
                f'{grade.total_score} -> {new_total_score} pts, '
                f'{grade.percentage}% -> {new_percentage}%'
            )
            if not dry_run:
                grade.total_score = new_total_score
                grade.percentage = new_percentage
                grade.save(update_fields=['total_score', 'percentage'])
            updated += 1

        verb = 'would update' if dry_run else 'updated'
        self.stdout.write(self.style.SUCCESS(
            f'Done: {verb} {updated}, unchanged {unchanged}, skipped {skipped}.'
        ))
