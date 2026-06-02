# Wenda-Live

Real-time, multiplayer quiz game (Kahoot-style) for the **Wenda** learning platform.
Instructors host a live game from their existing question bank, and enrolled
students join from any device to answer timed questions and compete on a live
leaderboard.

Wenda-Live is a companion to the main **wenda-quiz** application: it shares the
same MySQL database (users, subjects, instructors, and the question bank) but
runs as its own Django project so the live, WebSocket-driven gameplay stays
isolated from the core LMS.

## Features

- **Host flow (instructors):** sign in with shared Wenda credentials, pick a
  subject, then hand-pick questions grouped by topic for the round.
- **Player flow (students):** join with a 6-character room code; only students
  enrolled in the game's subject can join.
- **Live gameplay over WebSockets:** synchronized question reveal, per-question
  countdown timer, and instant scoring.
- **Live leaderboard:** scores update in real time, with faster correct answers
  earning more points.
- **Shared question bank:** questions, options, and correct answers come from the
  same bank used by wenda-quiz — no duplication.
- **Gradeable results:** when a game finishes, each enrolled student's accuracy
  (`correct / total`) is saved as a `LiveQuizGrade` in its own table, ready to
  feed into the student's overall grade. The speed-weighted leaderboard score is
  kept separate and is not used for grading.

## Tech stack

| Component        | Choice                                   |
| ---------------- | ---------------------------------------- |
| Framework        | Django 6.0                               |
| Realtime / ASGI  | Django Channels 4 + Daphne               |
| Channel layer    | Redis (`channels_redis`) for production; in-memory for dev |
| Database         | MySQL (shared `wenda_db`, via `mysqlclient`) |
| Config           | `python-dotenv` (project-root `.env`)    |

## Architecture

- **Django project:** `configWendaLive` &nbsp;·&nbsp; **App:** `wenda_live`
- **Shared, unmanaged models** (`managed = False`) mirror tables owned by
  wenda-quiz and must stay field-for-field in sync with it:
  - `quiz_user` (User), `quiz_instructor` (Instructor), `quiz_student` (Student)
  - `subjects_subject` (Subject), `subjects_subject_students` (enrollment M2M)
  - `question_bank_questionbankentry` (QuestionBankEntry)
- **Managed models owned by Wenda-Live** — keep their original `kahoot_*` table
  names so existing rows survive the project rename:
  - `GameSession` (`kahoot_gamesession`)
  - `Player` (`kahoot_player`)
  - `PlayerAnswer` (`kahoot_playeranswer`)
  - `LiveQuizGrade` (`kahoot_livequizgrade`) — per-student accuracy grade written
    when a game finishes; the gradeable counterpart to wenda-quiz's `QuizResult`
- **WebSocket routing** (`wenda_live/routing.py`):
  - `ws/host/<room_code>/` → `HostConsumer`
  - `ws/play/<room_code>/` → `PlayConsumer`

> ⚠️ The unmanaged mirror models share a live database with wenda-quiz. If a
> wenda migration adds, renames, or drops a field on one of those tables, mirror
> the change here in the same commit or shared queries will silently break.

## Getting started

### Prerequisites

- Python 3.12+
- MySQL with the shared `wenda_db` database accessible
- Redis (optional for local dev; required for production)

### Setup

```powershell
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
copy .env.example .env
#    then edit .env with your real values (see below)

# 4. Apply migrations (only the managed kahoot_* tables are created/altered)
python manage.py migrate

# 5. Run the ASGI development server (Daphne, via runserver)
python manage.py runserver
```

Then open http://127.0.0.1:8000/.

### Environment variables

Copy `.env.example` to `.env` and fill in real values. `.env` is gitignored.

| Variable                      | Description                                            |
| ----------------------------- | ------------------------------------------------------ |
| `DJANGO_SECRET_KEY`           | Django secret key (generate with `secrets.token_urlsafe(64)`) |
| `DJANGO_DEBUG`                | `1` for development, `0` for production                |
| `DJANGO_ALLOWED_HOSTS`        | Comma-separated allowed hosts                          |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated trusted origins (for HTTPS/prod)       |
| `DB_ENGINE`                   | Database backend (`django.db.backends.mysql`)          |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | Shared `wenda_db` connection credentials       |
| `DB_HOST` / `DB_PORT`         | Database host and port                                 |
| `CHANNEL_REDIS_URL`           | Redis URL for the channel layer; leave empty to use the in-memory layer in dev |

## Running tests

```powershell
python manage.py test
```

## How to play

1. **Host:** an instructor signs in, picks a subject, selects questions by topic,
   and opens the game — a room code is generated.
2. **Players:** enrolled students go to the home page, enter the room code and a
   nickname, and land in the lobby.
3. **Live round:** the host advances through questions; each is revealed
   simultaneously with a countdown. Faster correct answers score more.
4. **Results:** the live leaderboard updates after every question until the game
   finishes.

## Screenshots

> Add screenshots to a `screenshots/` folder and reference them here.

| Home / Join          | Host – select questions      | Live game                 |
| -------------------- | ---------------------------- | ------------------------- |
| ![Home](screenshots/home.png) | ![Host](screenshots/host-select-questions.png) | ![Game](screenshots/live-game.png) |

| Lobby                | Leaderboard                  |
| -------------------- | ---------------------------- |
| ![Lobby](screenshots/lobby.png) | ![Leaderboard](screenshots/leaderboard.png) |

> Replace the placeholder paths above with your actual screenshots.

## Project status

Core gameplay (subject/question selection, enrollment-gated join, live host and
player consumers, scoring, and leaderboard) is implemented. See the project's
internal notes for the current roadmap.

## License

This project is open source under the [MIT License](LICENSE) — anyone is free to
download, use, modify, and distribute it.
