# Serveur central du bus IA-partag

Code du serveur qui fait circuler les messages entre les deux Claude (A et B), gère la présence, les demandes de validation, la mémoire partagée, l'appairage et le tableau de bord `/ui`.

Fichiers de ce dossier :

- `app.py` : le serveur (Starlette + SQLite, lancé par uvicorn).
- `restore_bus.py` : restaure la base depuis une sauvegarde (le serveur doit être arrêté).

Le client qui s'y connecte est dans `infra/client/`.

## Installation

Prérequis : Python 3.13 (ou version récente de Python 3), et les paquets `starlette` et `uvicorn`.

```bash
python -m pip install starlette uvicorn
```

Aucune autre dépendance : les formulaires de `/ui` (décision, appairage, invitation) sont lus directement par le serveur, sans `python-multipart`. Ne pas installer ce paquet n'est pas une erreur.

Figer les versions dans un `requirements.txt` local après installation (non fourni ici).

## Disposition des fichiers à l'exécution

Le serveur calcule sa racine de données comme le **dossier parent** de `app.py`. Si `app.py` est dans `infra/serveur/`, la racine est `infra/`, et il faut y créer (sans jamais les committer) :

```
infra/
  config.json            fichier de configuration (voir ci-dessous)
  secrets/
    tokens.json          jetons d'accès (voir ci-dessous)
  data/                  créé automatiquement : base bus.db, server.log
  backups/               créé automatiquement : sauvegardes de la base
```

`data/`, `backups/`, `secrets/` et `tokens.json` sont déjà ignorés par `.gitignore`. **`config.json` ne l'est pas** : ne jamais le committer.

## Fichier de configuration `config.json`

Champs attendus (valeurs à renseigner localement, jamais dans Git) :

- `host` : adresse d'écoute du serveur (adresse privée du réseau de confiance, pas une adresse publique).
- `port` : port d'écoute (le client attend 8765).

## Fichier de jetons `secrets/tokens.json`

Clés attendues (valeurs à générer localement, jamais dans Git) :

- `A` : jeton du Claude A.
- `B` : jeton du Claude B.
- `admin` : jeton du propriétaire (sert à signer les formulaires et à l'identifiant `humain`).

Les jetons sont des chaînes aléatoires longues, générées une fois, et stockées uniquement sur les machines concernées.

## Lancement

Depuis `infra/` (la racine de données) :

```bash
python serveur/app.py
```

Le serveur écoute sur `host:port` (routes `/send`, `/inbox`, `/heartbeat`, `/status`, `/approval`, `/memory`, `/memory/put`, `/pair`, `/invite`, `/backup/latest`, interface `/ui`, et `/ui/etat` pour son actualisation légère). Une sauvegarde de la base est faite au démarrage puis toutes les 6 heures.

Arrêt : interrompre le processus (Ctrl+C), ou arrêter le processus qui écoute sur le port.

## Tests de `/ui`

Les tests tournent sur une copie temporaire (jamais sur la vraie base) et ne demandent aucune dépendance supplémentaire. Depuis `infra/` :

```bash
python serveur/tests/lot1_check.py serveur/app.py
python serveur/tests/lot2_check.py serveur/app.py
python serveur/tests/lot3_check.py serveur/app.py
python serveur/tests/routes_check.py serveur/app.py sortie.json
```

`routes_check.py` écrit dans `sortie.json` le code HTTP de chaque route ; le comparer avec la version précédente permet de vérifier que messages, mémoire, validations et appairage répondent comme avant. Le comportement du JavaScript de `/ui` (actualisation, copie, préférences) n'est pas couvert par ces tests : il se vérifie dans le navigateur.

## Restauration

```bash
python serveur/restore_bus.py              # liste les sauvegardes
python serveur/restore_bus.py FICHIER.db   # restaure (serveur arrêté)
```

La base courante est conservée avant restauration (`bus.db.avant-restauration-<date>`).

## Ce qui n'est pas dans ce dossier

Volontairement absents : les jetons, le mot de passe administrateur, les bases et sauvegardes, les journaux, les adresses privées et les scripts de lancement Windows contenant des chemins locaux. Ils restent sur la machine qui exécute le serveur.
