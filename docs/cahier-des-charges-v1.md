# Cahier des charges – Réseau d'IA locales collaboratives (v1)

Nov 11, 2026 · @Monk

## 1. Contexte, objectif et décisions déjà prises

L'objectif est une application Windows identique sur deux PC : chaque utilisateur a sa propre IA locale, qui fonctionne sans Internet, avec une mémoire partagée et la possibilité de collaborer avec l'IA de l'autre.

**Matériel**

|  | PC 1 | PC 2 |
| --- | --- | --- |
| Processeur | Ryzen 7 7800X3D | Intel i7-12700KF |
| Carte graphique | RX 9070 XT, 16 Go (AMD) | RTX 4060 Ti, 16 Go (NVIDIA) |
| Mémoire | 32 Go DDR5 | 32 Go DDR5 |
| Rôle projet | Propriétaire, décisions critiques | Second utilisateur |

**Décisions prises avec le questionnaire**

- Format : ce document éditable, en français, avec contrats techniques précis.
- Interface et moteur IA : je tranche et je justifie (section 3).
- Répartition du travail : rééquilibrée entre les deux Claude (section 4).
- Compte GitHub : le PC 2 a déjà son propre compte.
- Fine-tuning / LoRA : reporté après la première version (V1).
- Ordre des phases : proposé par moi, selon les dépendances techniques (section 9).

**Règles issues d'IA-partag (déjà en place)**

- Dépôt GitHub commun, branche `main` protégée, pull request obligatoire.
- Branches séparées par agent (`agent-a/…`, `agent-b/…`).
- Autonomie semi-autonome : chacun valide son propre Claude, le propriétaire du PC 1 tranche l'architecture, la sécurité, l'argent et les fusions Git.
- Aucun coût obligatoire ; outils gratuits et locaux privilégiés.

## 2. Deux couches à ne pas confondre

Le mot « deux Claude » désigne deux choses différentes. Les séparer règle l'une des contradictions du cahier initial : le produit final doit marcher sans Claude ni Internet.

|  | Couche A – IA-partag | Couche B – Produit final |
| --- | --- | --- |
| Qui collabore | Les deux Claude de développement (Claude Desktop, onglet Code) | Les deux IA locales (modèles Ollama / llama.cpp) |
| Utilité | Construire le logiciel ensemble | Servir les deux utilisateurs au quotidien |
| Outils | Tailscale, MCP, Git/GitHub, FastAPI/WebSocket | Services locaux, SQLite, synchronisation chiffrée |
| Statut | Phase 0, en place | Objet de ce cahier des charges |
| Besoin de Claude / Internet | Oui, pendant le développement | Non, jamais pour fonctionner |

**Règle de réemploi.** Le réseau privé (Tailscale) et les conventions Git d'IA-partag servent de base au produit, mais le produit n'importe aucun code qui dépend de Claude. Le dépôt étant public, aucune donnée privée, clé ou modèle personnel n'y est jamais enregistré.

### 2.1 Ce que fait déjà IA-partag (lu dans le dépôt)

IA-partag est un bus de messages hébergé sur le PC 1 et joignable par Tailscale. Chaque Claude y accède par un petit serveur MCP local, avec un fichier de configuration secret gardé hors du dépôt.

| Pièce d'IA-partag | Ce qu'elle fait aujourd'hui | Ce que le produit en reprend |
| --- | --- | --- |
| Messages (`envoyer_message`, `lire_messages`, `statut`) | Un Claude écrit à l'autre ; fils de discussion, priorité, identifiant anti-doublon, accusé de réception à la lecture | Modèle du contrat C7 (messages entre IA) |
| Réveil automatique (`bus_watcher`, hook Stop) | Un message qui arrive réveille le Claude concerné | Modèle des notifications et du réveil des agents (M13, M15) |
| Mémoire partagée (`memoire_lister`, `memoire_lire`, `memoire_ecrire`) | Clés versionnées : on renvoie la version lue, et en cas de modification entre-temps le serveur répond par un conflit avec la valeur actuelle | Base de la règle d'écriture et de conflit du contrat C4 et du module M8 |
| Validations (`demander_validation`, `etat_validation`) | Le Claude demande l'accord de l'humain ; un indicateur « critique » réserve la décision au propriétaire | Modèle du mode `ask_each_time` du contrat C3 |
| Appairage (`bus_pair`) | Un code à usage unique donne au second PC son fichier client, sans afficher le jeton | Modèle pour enrôler un nouveau PC dans M2 |
| Garde-fous Git (hook `pre-push`, liste de commandes interdites) | Chaque Claude ne peut pousser que sur ses branches `agent-x/*` | Repris tels quels pour le développement (section 10) |

### 2.2 Rôles de Claude A et de Claude B

|  | Claude A | Claude B |
| --- | --- | --- |
| PC | PC 2 (RTX 4060 Ti) | PC 1 (RX 9070 XT, héberge le bus) |
| Branches | `agent-a/<tâche>` | `agent-b/<tâche>` |
| Humain derrière | L'autre utilisateur | Le propriétaire |

Dans tout ce document, **« PC 1 » signifie Claude B** et **« PC 2 » signifie Claude A**. Correspondance confirmée par le propriétaire : A = PC 2 et B = PC 1.

**Comment les deux Claude se coordonnent, étape par étape**

1. Une tâche est une clé `taches/<id>` de la mémoire partagée : titre, assigné (A ou B), branche, statut. Avant de commencer, un Claude liste `taches/` et ne prend pas une tâche assignée à l'autre.
2. Les échanges passent par `envoyer_message`, avec un fil par module ou par contrat.
3. Le relecteur (le Claude qui n'est pas porteur du module) lit la branche de l'autre, lance les tests sur son propre PC et répond dans le fil.
4. Pour une fusion vers `main`, un changement de contrat ou toute décision critique, le Claude appelle `demander_validation` avec l'indicateur critique. Seul le propriétaire du PC 1 tranche et fusionne.
5. Les rapports de la phase 1 sont écrits chacun sur sa branche, puis comparés par message.

**Limites à connaître.** Le bus ne sert qu'au développement : le produit final n'en dépend jamais. Il tourne sur le PC 1, donc s'il est éteint les deux Claude ne se parlent plus. Et dans la branche `main` du dépôt, je n'ai trouvé que le client : le code du serveur n'y est pas (voir sections 7 et 11).

**Mise à jour (vérifiée dans le dépôt).** Le code du serveur du bus est maintenant sur `main` (`infra/serveur/` : `app.py`, `restore_bus.py`, `README.md`). Je n'y ai trouvé aucun jeton, mot de passe ni adresse privée : le serveur lit ses secrets dans des fichiers locaux ignorés par Git. La fusion a été faite une fois par contournement de la règle de relecture, faute de second relecteur.

## 3. Architecture cible et choix techniques

Chaque PC fait tourner la même application, organisée en services locaux autour d'un noyau. Seule la couche d'accès au GPU change d'un PC à l'autre. Le schéma placé sous le tableau montre comment les pièces s'emboîtent.

&#91;embedded content: architecture d'un PC · 7 services, 1 tunnel, 1 moteur IA\]

L'interface parle au noyau, qui orchestre les services ; la synchronisation fait sortir uniquement les événements autorisés par le tunnel.

| Domaine | Choix proposé | Pourquoi | À valider par |
| --- | --- | --- | --- |
| Noyau et services | Python 3.12, FastAPI, un processus par service, bus d'événements interne | Déjà maîtrisé sur ton écosystème, identique sur les deux PC | Test de démarrage sur les deux PC |
| Interface | React servi par le noyau local, affichée dans une fenêtre WebView2 | Une seule interface pour Windows et navigateur, comme demandé ; PySide6 obligerait à développer l'interface Web à part | Maquette de chat sur les deux PC |
| Moteur IA | llama.cpp en mode serveur, backend Vulkan en commun ; backend CUDA optionnel sur le PC 2 ; Ollama accepté comme moteur alternatif | Vulkan fonctionne sur AMD et NVIDIA avec les mêmes commandes, ce qui protège la parité ; la RX 9070 XT est récente, donc ROCm n'est pas pris pour acquis | Benchmark sur les deux GPU (phase 1) |
| Mémoire | SQLite (mode WAL) + extension de recherche vectorielle (sqlite-vec ou équivalent) + modèle d'embeddings local | Un seul fichier par périmètre, sans serveur, sauvegardable | Test de recherche sur 10 000 notes |
| Synchronisation | Journal d'événements en ajout seul, horloge logique hybride, un journal par périmètre | Permet reprise après coupure, historique complet et détection de conflits | Test de coupure réseau (phase 4) |
| Réseau | Tailscale (WireGuard) pour le tunnel, signature de chaque événement par clé propre à l'application | Chiffrement gratuit déjà en place via IA-partag ; la signature protège même si le tunnel est compromis | Reprise après coupure Internet |
| Voix | whisper.cpp (reconnaissance) et Piper (synthèse), hors ligne | Gratuits, locaux, fonctionnent sur CPU | Test de latence sur les deux PC |
| Automatisation | API d'accessibilité Windows (UI Automation) via une bibliothèque Python, liste blanche d'applications | Contrôlable et révocable, contrairement à une simulation de clics aveugle | Test de permissions |
| Packaging | Installateur unique, même version sur les deux PC | Parité imposée par le build | Installation propre sur les deux PC |

**Principe de parité.** Le moteur IA est lancé comme programme séparé derrière une interface commune (contrat C5). Le reste de l'application ne sait pas quelle carte graphique tourne dessous.

## 4. Modules, parité et répartition rééquilibrée

**Parité imposée.** Les deux PC reçoivent le même installateur et toutes les fonctionnalités. Peuvent différer : les modèles IA installés, les performances, les données privées et les autorisations accordées. Un module n'est validé que s'il passe ses tests sur les deux PC, quel que soit le Claude qui l'a écrit.

**Pourquoi rééquilibrer.** Dans la répartition initiale, le PC 1 portait presque tout le critique (sécurité, mémoire, synchronisation, réseau). Je propose de déplacer vers le PC 2 les modules qui dépendent peu du socle de sécurité, pour équilibrer la charge et faire relire chaque socle par l'autre.

| # | Module | Porteur | Relecteur | Fonction du cahier |
| --- | --- | --- | --- | --- |
| M1 | Noyau, services locaux, bus d'événements | PC 1 | PC 2 | 1 |
| M2 | Identités, clés, moteur de permissions, arrêt d'urgence (cœur) | PC 1 | PC 2 | 9, sécurité |
| M3 | Journal d'audit signé (traçabilité) | PC 1 | PC 2 | sécurité |
| M4 | Stockage mémoire SQLite (personnelle, commune, projet) | PC 1 | PC 2 | 2 |
| M5 | Synchronisation des événements et fichiers, modes auto / manuel / suspendu | PC 1 | PC 2 | 3 |
| M6 | Réseau privé chiffré et mode hors ligne | PC 1 | PC 2 | 3 |
| M7 | Recherche sémantique (embeddings, index) | PC 2 | PC 1 | 2, 4 |
| M8 | Historique, détection et résolution des conflits, suppression contrôlée | PC 2 | PC 1 | 4 |
| M9 | Gestion des modèles et ressources, surveillance GPU/RAM | PC 2 | PC 1 | 1, 10 |
| M10 | Adaptateur IA : AMD (Vulkan) | PC 1 | PC 2 | 1 |
| M11 | Adaptateur IA : NVIDIA (Vulkan + CUDA) | PC 2 | PC 1 | 1 |
| M12 | Interface Windows et Web, messagerie, projets, supervision | PC 2 | PC 1 | 7 |
| M13 | Orchestrateur, agents spécialisés, délégation de tâches, messages entre IA | PC 2 | PC 1 | 5, 6 |
| M14 | Automatisation Windows avec permissions | PC 2 | PC 1 | 9 |
| M15 | Commandes vocales, synthèse vocale, notifications | PC 2 | PC 1 | 8 |
| M16 | Sauvegarde, restauration, versions stables | PC 1 | PC 2 | 10 |
| M17 | Extensions et mises à jour contrôlées | PC 1 | PC 2 | 12 |
| M18 | Apprentissage supervisé (retours, règles) ; LoRA en phase ultérieure | PC 2 | PC 1 | 11 |

**Lecture.** Le PC 1 garde les modules où une faille coûte cher (M1 à M6, M16, M17). Le PC 2 reçoit l'expérience utilisateur et l'intelligence (M7 à M9, M11 à M15, M18). Chaque adaptateur GPU est écrit par le Claude dont la carte est installée, mais testé par l'autre via un jeu de tests commun.

**Garde-fou.** Un Claude ne modifie pas un module dont il n'est pas le porteur : il envoie sa demande par message dans le fil du module, et le porteur l'intègre sur sa propre branche. Aucun Claude ne fusionne vers `main` (section 10).

## 5. Dépendances entre modules

M1 (noyau) et M2 (permissions) bloquent presque tout le reste : ils passent en premier. Ensuite les deux branches M10/M11 (IA locale) et M4 (mémoire) peuvent avancer en parallèle.

| Module | Dépend de | Est utilisé par |
| --- | --- | --- |
| M1 Noyau | contrats C1, C2 | tous |
| M2 Permissions | M1, contrat C3 | M5, M13, M14, M17 |
| M3 Audit | M1, M2 | tous les modules qui agissent |
| M4 Mémoire | M1, M2, contrat C4 | M5, M7, M8, M13 |
| M5 Synchronisation | M3, M4, M6, contrat C1 | M8, M13 |
| M6 Réseau | M1, M2 | M5, M13 |
| M7 Recherche sémantique | M4, M10 ou M11 (embeddings) | M13 |
| M8 Conflits | M4, M5 | M12 |
| M9 Ressources | M10, M11 | M12, M13 |
| M10, M11 Adaptateurs IA | contrat C5 | M7, M9, M13 |
| M12 Interface | contrat C2, tous en lecture | utilisateur |
| M13 Orchestrateur | M2, M4, M5, M10/M11, contrats C6, C7 | M14, M15 |
| M14 Automatisation | M2, M3, M13 | M13 |
| M15 Voix | M1, M13 | M12 |
| M16 Sauvegarde | M3, M4 | M17 |
| M17 Extensions | M2, M3, M16, contrat C9 | tous |
| M18 Apprentissage | M4, M13 | M13 |

**Chemin critique** : M1 → M2 → M4 → M5/M6 → M8, puis M13. C'est lui qui détermine l'ordre des phases (section 9).

## 6. Contrats techniques versionnés

Un contrat est un texte précis qui dit comment deux modules se parlent. Chacun est un fichier du dossier `contracts/` du dépôt, avec un numéro de version (X.Y.Z). Changer un contrat = pull request relue et validée par les deux Claude ; les tests de compatibilité du contrat doivent passer avant fusion.

| Contrat | Objet | Porteur | Consommateurs |
| --- | --- | --- | --- |
| C1 | Enveloppe d'événement (sync, audit, bus) | PC 1 | tous |
| C2 | API locale (REST + WebSocket) | PC 1 | M12, M13, M15 |
| C3 | Permissions et capacités | PC 1 | M13, M14, M17 |
| C4 | Schéma de la mémoire SQLite | PC 1 | M7, M8, M13 |
| C5 | Adaptateur de modèle IA | PC 1 et PC 2 | M7, M9, M13 |
| C6 | Manifeste d'agent et d'outil | PC 2 | M13, M14, M17 |
| C7 | Message entre IA et délégation de tâche | PC 2 | M5, M13 |
| C8 | Format de sauvegarde | PC 1 | M16 |
| C9 | Manifeste d'extension | PC 1 | M17 |

**C1 – Enveloppe d'événement (version 1.0.0).** Tout changement de mémoire, de permission ou de configuration est un événement, jamais une modification directe.

```json
{
  "contract": "C1", "version": "1.0.0",
  "event_id": "uuid",
  "scope": "personal | common | project:<id>",
  "node_id": "pc1 | pc2",
  "actor": "user:<id> | agent:<id>",
  "type": "memory.upsert | memory.delete | permission.grant | task.delegate | ...",
  "hlc": "horloge logique hybride",
  "parent_ids": ["event_id précédent connu"],
  "payload": {},
  "signature": "Ed25519 de la clé du nœud"
}
```

Reçu par le PC distant, un événement de portée `personal` est refusé par défaut : seules les portées `common` et `project` voyagent, après autorisation.

**C3 – Permission (version 1.0.0).** Une permission est une capacité nommée, limitée dans le temps, révocable, traçable.

```json
{
  "capability": "windows.app.control",
  "subject": "agent:assistant-bureau",
  "resource": "app:notepad.exe",
  "mode": "ask_each_time | allow_until | deny",
  "expires_at": "2026-12-01T00:00:00Z",
  "granted_by": "user:<owner>",
  "revoked": false
}
```

Tout module appelle `permissions.check(sujet, capacité, ressource)` avant d'agir. Aucun module ne contourne ce point. L'arrêt d'urgence (`kill_switch`) coupe toutes les capacités actives en moins d'une seconde.

**C4 – Mémoire SQLite (version 1.0.0).** Un fichier par périmètre : `personal.db`, `common.db`, `project_<id>.db`.

| Table | Rôle |
| --- | --- |
| `memory_item` | id, type, contenu, étiquettes, version, `deleted_at` (suppression douce) |
| `memory_version` | historique complet : item\_id, version, contenu, event\_id, auteur |
| `embedding` | item\_id, modèle, vecteur |
| `conflict` | item\_id, versions en conflit, état (ouvert, résolu), décideur |
| `event_log` | copie locale des événements C1, ajout seul |
| `sync_state` | pour chaque néud distant : dernier événement reçu, mode (auto, manuel, suspendu) |

Règle d'écriture reprise de la mémoire partagée d'IA-partag : pour modifier un élément, on envoie la version lue ; si l'autre IA l'a modifié entre-temps, l'écriture est refusée avec la valeur actuelle, et l'élément passe dans la table `conflict` au lieu d'être écrasé.

**C5 – Adaptateur de modèle IA (version 1.0.0).** Même interface sur les deux PC.

```json
{
  "methods": ["list_models", "load_model", "unload_model", "generate", "embed", "stop", "status"],
  "status": { "backend": "vulkan | cuda", "vram_used_mb": 0, "vram_total_mb": 16384, "loaded": [] },
  "errors": ["out_of_memory", "model_missing", "timeout", "cancelled"]
}
```

**C7 – Message entre IA (version 1.0.0).** Le premier message de ce type est le premier jalon visible du projet.

```json
{
  "contract": "C7", "version": "1.0.0",
  "message_id": "uuid", "thread": "fil-du-module", "priority": "normale | urgente", "from": "ai:pc1", "to": "ai:pc2",
  "kind": "question | task | answer | memory_conflict_query",
  "scope": "common",
  "body": "texte",
  "requires_owner_approval": true,
  "deadline": null
}
```

Pour un conflit de mémoire, l'IA locale envoie d'abord un message `memory_conflict_query` à l'IA distante si les permissions le permettent ; sans réponse ou en cas de désaccord, le conflit est mis en file pour validation humaine.

Le contrat reprend les mécanismes du bus IA-partag : fil de discussion, priorité, identifiant unique qui évite les doublons quand on réessaie après une coupure, et accusé de réception à la lecture.

C2, C6, C8 et C9 seront rédigés au même niveau de détail en phase 1, avant tout code.

## 7. Incompatibilités et risques, avec solutions

Huit points du cahier initial se contredisent ou restent fragiles. Chacun a une solution proposée, à valider.

| # | Problème | Pourquoi c'est un problème | Solution proposée |
| --- | --- | --- | --- |
| 1 | « Hors ligne » et « synchronisation entre PC » | Les deux PC sont dans deux logements : se parler demande Internet | Distinguer deux modes : chaque IA marche seule sans Internet ; la synchronisation reprend dès que le tunnel revient, via une file d'attente locale |
| 2 | Tailscale dépend d'un service tiers pour la connexion initiale | Contredit « indépendant », mais tunnel déjà établi ensuite | Garder Tailscale (gratuit, déjà installé) ; prévoir une option WireGuard direct ou Headscale si le service devient un risque |
| 3 | Parité AMD / NVIDIA | Les piles ROCm et CUDA diffèrent ; la RX 9070 XT est récente | Backend Vulkan commun comme socle ; CUDA en option sur le PC 2 ; benchmark en phase 1 avant de figer |
| 4 | Dépôt GitHub public | Risque de fuite de données privées ou de clés | Dossier de données hors dépôt, `.gitignore` strict, analyse de secrets avant chaque fusion |
| 5 | « Le propriétaire garde l'autorité sur son PC » contre « le PC 1 tranche » | Deux humains, deux autorités | Deux niveaux : chaque propriétaire décide sur sa machine ; le propriétaire du PC 1 tranche seulement ce qui est commun (architecture, sécurité, argent, fusions) |
| 6 | LoRA / fine-tuning sur 16 Go de VRAM | Lourd, surtout sur AMD, risque de bloquer la V1 | Report en phase ultérieure ; la V1 apprend par mémoire et retours supervisés, sans modifier les modèles |
| 7 | Deux Claude qui écrivent en parallèle | Risque de conflits Git ou d'écrasement de fonctions | Un porteur par module, branches séparées, contrats versionnés, revue croisée obligatoire |
| 8 | Le bus IA-partag tourne sur le PC 1 ; son code serveur n'est pas dans la branche main du dépôt | PC 1 éteint = les deux Claude ne se parlent plus ; le serveur ne peut pas être reconstruit depuis GitHub | Versionner le code du serveur sans secrets ni données ; ne pas écrire au bus des informations qui n'existent nulle part ailleurs ; le produit final n'en dépend jamais |

**Risque restant à mesurer.** Je n'ai pas testé la vitesse réelle des modèles sur chaque carte : le choix Vulkan est une hypothèse prudente, que le benchmark de la phase 1 confirmera ou corrigera.

## 8. Sécurité et règles de gouvernance

**Principes non négociables**

- Le propriétaire d'un PC garde la main sur sa machine ; l'arrêt d'urgence stoppe tous les agents immédiatement.
- Les données de portée `personal` ne quittent jamais le PC sans autorisation explicite.
- Toute action sensible (suppression, automatisation Windows, partage, installation) demande une confirmation.
- Tout changement passe par un événement signé, donc traçable dans le journal d'audit.
- Une version stable est toujours récupérable ; aucune fonction validée n'est supprimée sans validation humaine.
- Le logiciel final ne dépend d'aucun Claude, d'aucun service cloud payant ni d'Internet pour fonctionner.

**Qui décide quoi**

| Décision | Qui tranche |
| --- | --- |
| Action locale d'un Claude sur son PC | Le propriétaire de ce PC |
| Architecture, sécurité, dépenses, fusion sur `main` | Le propriétaire du PC 1 |
| Modification d'un contrat (C1 à C9) | Les deux Claude d'accord, puis le propriétaire du PC 1 |
| Conflit de mémoire non résolu entre les deux IA | Validation humaine du ou des propriétaires concernés |
| Partage d'une donnée personnelle | Son propriétaire, au cas par cas |

**Tests de sécurité obligatoires avant la V1** : refus d'un événement non signé, refus d'un partage `personal`, expiration et révocation d'une permission, arrêt d'urgence en moins d'une seconde, absence de secrets dans le dépôt.

## 9. Plan de développement par phases

Les phases suivent le chemin critique de la section 5. Aucune date n'est fixée : une phase se termine quand son critère de validation est atteint **sur les deux PC**, puis le propriétaire du PC 1 donne le feu vert.

| Phase | Contenu | Modules | Critère de validation (sur les deux PC) |
| --- | --- | --- | --- |
| 0 | Infrastructure des deux Claude (fait) | IA-partag | Réseau, MCP et Git opérationnels |
| 1 | Validation d'architecture : rapports croisés, contrats C1 à C9 écrits, essais de faisabilité (benchmark Vulkan/CUDA, WebView2, tunnel) | tous, sans code produit | Les deux Claude et les deux humains valident le même dossier |
| 2 | Socle : noyau, permissions, audit, adaptateurs IA | M1, M2, M3, M9, M10, M11 | Une IA locale répond en ligne de commande, hors Internet, avec permissions et journal |
| 3 | Mémoire et recherche : stockage, versions, embeddings | M4, M7 | La mémoire personnelle est retrouvée par sens, historique complet |
| 4 | Interface de discussion et réseau : chat local, tunnel, messages entre IA, synchronisation | M12 (chat), M5, M6, M13 (messages) | **Premier jalon visible** : deux IA échangent un message ; une note commune écrite sur un PC apparaît sur l'autre |
| 5 | Fiabilité des données : conflits, historique, suppression contrôlée, modes de synchronisation | M8, M5 | Coupure Internet, modifications des deux côtés, puis retour : aucun écrasement, conflit proposé à l'humain |
| 6 | Agents : orchestrateur, agents spécialisés, délégation de tâches, projets | M13, M12 (projets) | Une tâche déléguée d'une IA à l'autre est exécutée et tracée |
| 7 | Voix et automatisation Windows | M14, M15 | Commande vocale hors ligne ; action Windows refusée sans permission, acceptée avec |
| 8 | Exploitation : supervision, sauvegarde, restauration, extensions, mises à jour | M16, M17, M12 (supervision) | Restauration complète depuis une sauvegarde ; extension installée puis annulée |
| 9 | Durcissement et V1 : tests de sécurité, hors ligne, récupération, non-régression ; installateur | tous | Tous les tests verts sur les deux PC ; installateur identique |
| 10 | Après la V1 : apprentissage supervisé, puis LoRA | M18 | Décision séparée, après mesure de la VRAM disponible |

**Pourquoi ce premier jalon.** Tu n'as pas choisi de priorité. Je propose donc « deux IA qui s'échangent un message » en phase 4, parce que c'est la première preuve visible que le projet fonctionne de bout en bout, et qu'elle ne demande ni agents ni voix.

## 10. Stratégie de tests, Git et revue croisée

**Tests** (chaque module en livre avant d'être proposé à la fusion)

- Tests unitaires du module et tests de contrat (le module respecte bien C1 à C9).
- Tests de sécurité : voir la section 8.
- Tests hors ligne : l'application démarre et répond sans Internet ni Claude.
- Tests de récupération : coupure du tunnel en pleine synchronisation, arrêt brutal, restauration d'une sauvegarde.
- Tests de non-régression : toute fonction validée est rejouée à chaque fusion.
- Tests de parité : le même jeu de tests est lancé sur le PC 1 et sur le PC 2 ; un résultat rouge sur l'un des deux bloque la validation.

**Git**

- Une branche par agent et par tâche : `agent-a/<tâche>` (Claude A, PC 2) et `agent-b/<tâche>` (Claude B, PC 1). Chacun ne pousse que sur les siennes ; le hook `pre-push` d'IA-partag le vérifie.
- Aucun Claude ne touche à `main` ni ne fusionne. La fusion est une décision critique : le Claude la demande avec `demander_validation` (indicateur critique), et seul le propriétaire du PC 1 tranche et fusionne par pull request (règle GitHub déjà active).
- Aucune réécriture d'historique : ni push forcé, ni `reset --hard`, ni `rebase`, ni `clean`, ni suppression de branche.
- Avant toute demande de fusion, le Claude lance les tests et copie le résultat dans sa demande.
- En cas de conflit Git, le Claude s'arrête et demande une validation critique avec la liste des fichiers concernés.
- Les tâches se répartissent par la mémoire partagée : clé `taches/<id>` = titre, assigné (A ou B), branche, statut.
- Les contrats vivent dans `contracts/` et se modifient par pull request dédiée : accord des deux Claude, puis validation du propriétaire du PC 1.
- Après chaque phase validée, le propriétaire pose une étiquette de version stable (aucun Claude ne touche à `main`).
- Aucun secret dans Git, le dépôt étant public.
- Chaque module contient sa documentation, la liste de ses dépendances et ses permissions.

**Revue croisée** : le relecteur est le Claude qui n'est pas porteur du module. Il lit la branche de l'autre, lance les tests sur son propre PC, vérifie le code, la documentation, le respect des contrats et l'absence de fonction supprimée, puis répond dans le fil du module. Le porteur corrige sur sa branche ; la validation finale reste humaine.

## 11. Décisions à valider ensemble avant de coder

Coche chaque point une fois validé par toi et par l'autre humain. Aucun code ni aucune installation ne commence avant que tout soit coché.

- [ ] Les deux couches (IA-partag et produit final) sont bien séparées, comme en section 2.
- [ ] Interface React + WebView2 servie par le noyau FastAPI local.
- [ ] Moteur IA : llama.cpp avec Vulkan en socle commun, CUDA optionnel sur le PC 2, benchmark d'abord.
- [ ] Mémoire SQLite avec recherche vectorielle, un fichier par périmètre.
- [ ] Synchronisation par journal d'événements signés ; « hors ligne » = chaque IA seule, synchro à la reconnexion.
- [ ] Tailscale conservé, avec plan B WireGuard ou Headscale.
- [ ] Répartition rééquilibrée des modules M1 à M18 (section 4).
- [ ] Contrats C1 à C9 à compléter en phase 1 (C1, C3, C4, C5, C7 déjà esquissés).
- [ ] Premier jalon visible : deux IA échangent un message (phase 4).
- [ ] LoRA et fine-tuning reportés après la V1.
- [ ] Règle de décision à deux niveaux (propriétaire local, propriétaire du PC 1 pour le commun).
- [x] Correspondance confirmée : Claude A = PC 2 et Claude B = PC 1 (section 2.2).
- [x] Le code du serveur du bus IA-partag est ajouté au dépôt, sans secrets ni données (section 7, ligne 8).
- [ ] Les règles Git d'IA-partag (section 10) s'appliquent telles quelles au développement du produit.
- [x] Le propriétaire fusionne la branche `agent-b/serveur-bus` sur `main` (pull request).
- [x] `config.json` est ajouté au `.gitignore` : le README du serveur indique qu'il n'y est pas encore et qu'il ne doit jamais être envoyé dans Git.
- [ ] Mettre en place la vraie relecture : ajouter le compte GitHub du PC 2 comme collaborateur avec accès en écriture, pour que la règle d'approbation de `main` fonctionne sans contournement.

**Questions encore ouvertes pour l'autre humain** : accepte-t-il le tunnel Tailscale partagé sur son PC, et quelles applications Windows autorise-t-il à piloter en premier ?

**Prochaine étape** : comparer ce document avec le rapport de l'autre Claude, puis lancer la phase 1.
