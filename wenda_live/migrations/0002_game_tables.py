# The Wenda-Live-owned (managed) tables. These DO emit DDL and create the
# kahoot_gamesession / kahoot_player / kahoot_playeranswer tables in wenda_db.
# (The table names keep the original kahoot_ prefix via an explicit db_table so
# existing rows survive the app rename to wenda_live.)
# Split out from 0001 so 0001 (mirrors only) can be marked applied without
# running, keeping the shared django_migrations history consistent.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('wenda_live', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='GameSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('room_code', models.CharField(db_index=True, max_length=8, unique=True)),
                ('status', models.CharField(choices=[('lobby', 'Lobby (waiting for players)'), ('active', 'Active (in progress)'), ('finished', 'Finished')], default='lobby', max_length=10)),
                ('num_questions', models.PositiveIntegerField(default=10)),
                ('seconds_per_question', models.PositiveIntegerField(default=20)),
                ('current_question_index', models.PositiveIntegerField(default=0)),
                ('question_ids', models.JSONField(default=list, help_text='Ordered list of QuestionBankEntry IDs picked at game start.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('host', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hosted_games', to=settings.AUTH_USER_MODEL)),
                ('subject', models.ForeignKey(db_column='subject_id', on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='wenda_live.subject')),
            ],
            options={
                'db_table': 'kahoot_gamesession',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='Player',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nickname', models.CharField(max_length=30)),
                ('score', models.PositiveIntegerField(default=0)),
                ('joined_at', models.DateTimeField(auto_now_add=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='players', to='wenda_live.gamesession')),
            ],
            options={
                'db_table': 'kahoot_player',
                'ordering': ['-score', 'joined_at'],
            },
        ),
        migrations.CreateModel(
            name='PlayerAnswer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question_index', models.PositiveIntegerField(help_text='Index into GameSession.question_ids (0-based).')),
                ('chosen_text', models.CharField(max_length=255)),
                ('is_correct', models.BooleanField(default=False)),
                ('ms_to_answer', models.PositiveIntegerField(default=0)),
                ('points_awarded', models.PositiveIntegerField(default=0)),
                ('submitted_at', models.DateTimeField(auto_now_add=True)),
                ('player', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='answers', to='wenda_live.player')),
            ],
            options={
                'db_table': 'kahoot_playeranswer',
                'ordering': ['player', 'question_index'],
            },
        ),
        migrations.AddConstraint(
            model_name='player',
            constraint=models.UniqueConstraint(fields=('game', 'nickname'), name='uniq_player_nickname_per_game'),
        ),
        migrations.AddConstraint(
            model_name='playeranswer',
            constraint=models.UniqueConstraint(fields=('player', 'question_index'), name='uniq_answer_per_player_per_question'),
        ),
    ]
