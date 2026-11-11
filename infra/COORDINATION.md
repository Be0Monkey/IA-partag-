# Coordination Git entre les deux Claude (étape 7)

## Règles
1. **Une branche par agent et par tâche** : `agent-a/<tache>` pour le Claude A, `agent-b/<tache>` pour le Claude B. Chacun ne pousse que sur ses branches.
2. **Personne ne touche à `main`** : ni Claude. La fusion vers `main` est une décision **critique** : le Claude fait `demander_validation(..., critique=True)` et seul le propriétaire tranche et fusionne.
3. **Aucune réécriture d'historique** : pas de `push --force`, `reset --hard`, `rebase`, `clean`, ni de suppression de branche.
4. **Tests avant toute demande de fusion** : le Claude lance les tests du projet et copie le résultat dans sa demande de validation.
5. **Conflit** : on s'arrête, on ne résout pas seul. `demander_validation(critique=True)` avec la liste des fichiers en conflit ; le propriétaire décide.
6. **Secrets** : jamais dans Git (dépôt public).

## Répartition des tâches (par la mémoire partagée)
Une tâche = une clé `taches/<id>` écrite avec `memoire_ecrire`, valeur : `titre | assigné (A ou B) | branche | statut (à faire, en cours, à valider, fini)`. Avant de commencer, un Claude lit `memoire_lister("taches/")` : il ne prend pas une tâche déjà assignée à l'autre. Un changement de statut se fait avec la version lue (en cas de conflit, relire).

## Garde-fous techniques (en plus des consignes)
- `infra/git-hooks/pre-push` refuse, pour une session Claude, tout push hors de ses branches `agent-x/*`, tout push forcé et toute suppression de branche distante. Il ne s'applique que si la variable `IA_PARTAG_AGENT` est définie : un humain dans son terminal n'est pas concerné. Activation dans un clone : `git config core.hooksPath infra/git-hooks`.
- Dans le `.claude/settings.json` de chaque projet Claude : `"env": { "IA_PARTAG_AGENT": "A" }` (ou `"B"`) et une liste `permissions.deny` : `Bash(git push --force*)`, `Bash(git push -f*)`, `Bash(git push * --force*)`, `Bash(git reset --hard*)`, `Bash(git clean*)`, `Bash(git branch -D*)`, `Bash(git merge*)`, `Bash(git rebase*)`, `Bash(git push origin main*)`, `Bash(git checkout main*)`, `Bash(rm -rf*)`.
- Recommandé (action du propriétaire sur GitHub, un Claude ne peut pas la faire) : protéger la branche `main` (Settings, Branches, règle « Require a pull request before merging », sans contournement).