#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

from pathlib import Path

from dotenv import load_dotenv


def _default_runserver_port(argv):
    """Default the dev server bind to DJANGO_RUNSERVER_HOST:DJANGO_RUNSERVER_PORT
    (127.0.0.1:8001) when no address is given, so it doesn't collide with
    wenda-quiz on :8000. Set the host to 0.0.0.0 to reach it from another
    machine (e.g. a test VM). `runserver <addr:port>` still wins."""
    if len(argv) >= 2 and argv[1] == 'runserver':
        given = [a for a in argv[2:] if not a.startswith('-')]
        if not given:
            host = os.getenv('DJANGO_RUNSERVER_HOST', '127.0.0.1')
            port = os.getenv('DJANGO_RUNSERVER_PORT', '8001')
            argv = argv + [f'{host}:{port}']
    return argv


def main():
    """Run administrative tasks."""
    load_dotenv(Path(__file__).resolve().parent / '.env')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'configWendaLive.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(_default_runserver_port(sys.argv))


if __name__ == '__main__':
    main()
