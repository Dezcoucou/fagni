#!/usr/bin/env python
"""Utilitaire de ligne de commande Django pour les tâches administratives."""
import os
import sys

def main():
    """Exécute les tâches administratives Django."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fagni.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Impossible d'importer Django. Vérifie qu'il est bien installé "
            "et accessible dans ton environnement virtuel (venv). "
            "As-tu bien activé ton environnement virtuel ?"
      ) from exc
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()