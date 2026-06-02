"""Test runner that makes the Wenda-Live test suite self-contained.

The wenda_live app mirrors several wenda-quiz tables as *unmanaged* models
(quiz_user, quiz_instructor, subjects_subject, question_bank_questionbankentry,
quiz_student, subjects_subject_students), and its split migrations are
deliberately tailored to the shared production database. Neither is convenient
for tests.

So for tests we build a throwaway test database straight from the current model
definitions instead of from migrations:

  * all migrations are disabled, so Django creates the schema via run_syncdb;
  * the unmanaged mirrors are temporarily marked managed, so run_syncdb
    creates their tables too (syncdb skips unmanaged models).

Django still uses a separate ``test_<name>`` database, so the shared wenda_db is
never touched. Configured via TEST_RUNNER in settings.
"""

from django.test.runner import DiscoverRunner

# Models whose tables wenda owns; we let tests create them locally.
_UNMANAGED_MIRRORS = (
    'User', 'Instructor', 'Subject', 'QuestionBankEntry', 'Student', 'SubjectStudent',
)


class _DisableMigrations:
    """Map every app label to None so the test DB is built via run_syncdb."""

    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


class WendaLiveTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        from django.conf import settings
        from wenda_live import models

        settings.MIGRATION_MODULES = _DisableMigrations()
        for name in _UNMANAGED_MIRRORS:
            getattr(models, name)._meta.managed = True

        return super().setup_databases(**kwargs)
