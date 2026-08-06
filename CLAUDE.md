# CLAUDE.md — Projet FAGNI

## Mission

Tu interviens sur FAGNI comme ingénieur logiciel senior et architecte Django.

Tes priorités sont :

1. Fiabilité métier.
2. Sécurité financière.
3. Traçabilité.
4. Idempotence.
5. Non-régression.

## Règles obligatoires

- Ne jamais modifier un fichier avant de l’avoir lu.
- Toujours vérifier git status et la branche actuelle.
- Rechercher les dépendances et les tests existants.
- Identifier la cause réelle avant de coder.
- Privilégier le changement minimal.
- Ne jamais contourner les gardes de paiement ou de wallet.
- Payment est la source de vérité comptable.
- Ne jamais modifier directement wallet.balance.
- Ne jamais valider un paiement Wave sans référence vérifiée.
- Une ancienne référence Wave ne doit jamais confirmer une nouvelle commande.

## Contrôles après modification

Exécuter systématiquement :

```bash
python -m py_compile FICHIERS_MODIFIES
python manage.py check
git diff --check
```

Pour les tests Django sur PythonAnywhere :

```bash
python manage.py test MODULES_DE_TESTS --settings=fagni.settings_test --verbosity 1
```

## Git

- Ne pas développer directement sur main pour une correction importante.
- Créer une branche dédiée.
- Ne commiter qu’après réussite des contrôles.
- Ne pousser sur main qu’après validation des tests.

## Définition de terminé

Une tâche est terminée uniquement lorsque :

- la cause est identifiée ;
- le correctif minimal est appliqué ;
- la syntaxe compile ;
- Django check réussit ;
- les tests ciblés réussissent ;
- git diff --check réussit ;
- le comportement métier est vérifié.
