# Revue critique du cahier des charges v1 – Claude A (PC 2)

Relu : `docs/cahier-des-charges-v1.md` sur `main` (commit `618ee0d`). Auteur : Claude A, PC 2 (i7-12700KF, RTX 4060 Ti 16 Go, 32 Go DDR5).
Cette revue a été écrite seul : je n'ai pas lu la revue du Claude B avant de la terminer. Je n'ai écrit aucun code et rien installé.

## 0. Ce que je n'ai pas pu vérifier

Honnêteté d'abord, parce que plusieurs avis ci-dessous en dépendent :

- **Aucun benchmark n'a été lancé** (ni Vulkan, ni CUDA, ni llama.cpp). Les ordres de grandeur de vitesse et de VRAM sont des estimations tirées de mes connaissances générales, pas des mesures. Je les marque « estimation ».
- Je n'ai **pas testé WebView2**, ni whisper.cpp, ni Piper, ni sqlite-vec, ni Tailscale sur ce PC.
- Je n'ai **pas relu le code du serveur du bus** (`infra/serveur/`) : je m'appuie sur la description du cahier.
- Je ne connais pas l'état actuel (nov. 2026) de certains projets (maturité de sqlite-vec, support de la RX 9070 XT dans les piles AMD, conditions d'usage gratuit de Tailscale). Ce sont des points à vérifier, pas des affirmations.
- Faits sûrs sur mon PC : le **i7-12700KF n'a pas de carte graphique intégrée** (le « F » le dit) ; l'affichage Windows passe donc par la RTX 4060 Ti et consomme de la VRAM.
- Je n'ai pas pu lire la section « embedded content » (schéma d'architecture, ligne 87), qui n'est pas dans le fichier texte.

## 1. Accords

- Séparer la couche A (IA-partag, développement) de la couche B (produit) : règle la contradiction « hors ligne / Claude ». Valide.
- Le produit n'importe aucun code dépendant de Claude, et le dépôt public ne contient ni secret ni donnée privée. Valide.
- Un journal d'événements en ajout seul comme source de vérité, avec un journal par périmètre. Bonne base pour reprise après coupure et audit.
- Un fichier SQLite par périmètre (`personal`, `common`, `project_<id>`) : isole bien le privé du partagé.
- Le principe de parité par interface (C5) : l'application ne sait pas quelle carte tourne dessous.
- Un module n'est validé que s'il passe ses tests sur les deux PC. Valide, avec la réserve du point D5.
- Le moteur IA lancé comme programme séparé (processus llama.cpp serveur) : isole les plantages GPU du noyau.
- Fine-tuning / LoRA reporté après la V1 : bon choix pour 16 Go de VRAM.
- Distinguer deux niveaux de décision (propriétaire local / propriétaire du PC 1 pour le commun).
- Chaque adaptateur GPU écrit par le Claude dont la carte est installée, relu par l'autre.
- Un porteur par module, interdiction de modifier le module d'un autre : bon garde-fou contre les conflits.
- Arrêt d'urgence, signature de chaque événement, liste blanche d'applications pour l'automatisation : bons principes de départ.
- Le chemin critique M1 → M2 → M4 → M5/M6 → M8 → M13 est globalement le bon (voir D7 pour un défaut de dépendance).

## 2. Désaccords et corrections

### D1. Vulkan « en commun » : à traiter comme chemin de référence, pas comme chemin par défaut sur NVIDIA
- **Problème.** Le cahier présente Vulkan comme socle commun pour protéger la parité, CUDA seulement en option. Sur une carte NVIDIA, le backend CUDA de llama.cpp est en général nettement mieux optimisé que Vulkan, surtout pour le traitement du prompt (estimation : écart important, à mesurer). Imposer Vulkan par défaut sur le PC 2 risque de donner une application sensiblement plus lente sur ma machine, pour une « parité » qui porte surtout sur l'interface.
- **Parité numérique impossible.** Deux backends différents (et même deux GPU différents) ne donnent pas les mêmes sorties octet pour octet, même avec la même graine. Un « test de parité » qui compare des textes générés échouera pour de mauvaises raisons.
- **Proposition.** (a) Parité **fonctionnelle** : mêmes méthodes C5, mêmes erreurs, mêmes formats, tests de contrat sur les réponses (forme, ordre de grandeur, absence d'erreur), jamais sur le texte exact. (b) Sur le PC 2, **CUDA par défaut si le benchmark de la phase 1 le justifie**, Vulkan gardé comme chemin de secours et de référence commun. (c) Décider d'après les mesures de la phase 1, pas avant ; la case « Vulkan en socle » de la section 11 ne devrait être cochée qu'après le benchmark.

### D2. Installateur « identique » et backend CUDA
- **Problème.** CUDA demande ses bibliothèques d'exécution (plusieurs centaines de Mo, estimation) ; l'installateur « unique, même version » doit donc embarquer les deux backends ou télécharger à l'installation (contredit « sans Internet »).
- **Proposition.** Un seul installateur contenant les deux backends, avec détection du GPU au premier lancement et choix consigné dans la configuration locale. Documenter la taille et décider si les modèles sont hors installateur (ils doivent l'être, ne serait-ce que pour les licences).

### D3. La règle d'écriture « version lue » (C4) vient d'un serveur central ; elle ne se transpose pas à deux pairs hors ligne
- **Problème.** Dans IA-partag, un serveur unique arbitre : « je te renvoie la valeur actuelle, relis ». Dans le produit, deux PC écrivent chacun localement, souvent hors ligne. L'écriture locale ne peut pas être « refusée » parce que l'autre l'a modifiée : on ne le sait pas encore. Le conflit apparaît **à la synchronisation**, pas à l'écriture.
- **Proposition.** L'écriture locale est toujours acceptée et produit un événement avec `parent_ids` (la version qu'elle remplace). À la réception, si un élément a deux descendants concurrents d'un même parent, il passe dans `conflict`. La règle « version lue » reste utile *à l'intérieur* d'un PC (deux agents qui écrivent le même élément), pas entre PC.

### D4. Le refus des événements `personal` se fait trop tard (C1)
- **Problème.** « Reçu par le PC distant, un événement `personal` est refusé » : à ce moment la donnée a déjà quitté le PC. Le principe de la section 8 (« ne quittent jamais le PC sans autorisation ») n'est pas tenu.
- **Proposition.** Filtre **à l'émission** (le journal `personal` n'est jamais exposé au service de synchronisation, au niveau du code et des permissions de fichier), le refus à la réception n'étant qu'une seconde barrière. Ajouter un test de sécurité : « le service de synchronisation n'a pas accès en lecture à `personal.db` ».

### D5. Tests de parité et modèles de langage
- Les tests « rouge sur un PC bloque la validation » (section 10) supposent des résultats déterministes. Pour les modules qui touchent au modèle (M7, M9, M13), séparer : tests déterministes (contrats, formats, erreurs, permissions) qui bloquent, et mesures de qualité/performance (latence, débit) qui sont **consignées** avec seuils indicatifs par PC. Sinon la phase 2 se bloquera sur des écarts normaux entre AMD et NVIDIA.

### D6. Les vecteurs d'embeddings ne doivent pas être synchronisés
- La table `embedding` est dans C4. Si le modèle d'embeddings ou sa quantification diffère entre PC (ou entre backends), les vecteurs ne sont pas comparables.
- **Proposition.** Ne synchroniser que le contenu et l'identifiant du modèle d'embeddings ; recalculer localement. Fixer **un seul modèle d'embeddings, même fichier et même quantification, sur les deux PC** (et le faire tourner sur CPU si besoin pour que les vecteurs soient reproductibles). Ajouter `content_hash` et `model_hash` pour détecter un vecteur périmé.

### D7. Dépendance circulaire entre M1, M2 et C1 (signature)
- C1 exige une signature de nœud, donc des clés ; or les clés/identités sont dans M2, qui « dépend de M1 » (section 5). M1 ne peut pas émettre d'événement signé sans identité.
- **Proposition.** Séparer M2 en M2a « identités et clés du nœud » (très petit, en premier, avec M1) et M2b « moteur de permissions et arrêt d'urgence ». Préciser où vit la clé (DPAPI / coffre Windows) et comment elle est révoquée.

### D8. La phase 1 se dit « sans code produit » mais exige des essais
- Benchmark Vulkan/CUDA, WebView2, tunnel : ce sont des installations et du code jetable. Or la consigne de la section 11 interdit tout code et toute installation « avant que tout soit coché », et la case « Vulkan » ne peut être cochée qu'après le benchmark.
- **Proposition.** Définir explicitement des **« spikes »** (code jetable dans un dossier `spikes/`, jamais fusionné dans les modules, installations autorisées par validation) et lever l'interdiction pour eux seulement.

### D9. Tailscale : questions non tranchées
- « Gratuit, déjà en place » est vrai pour IA-partag, mais le cahier suppose que le produit pilote Tailscale. Qui installe et configure Tailscale chez l'utilisateur (hors installateur) ? Les deux PC sont-ils sur le **même tailnet** (un seul compte), ou deux tailnets avec partage de machine ? Le partage de nœud a des limites que je n'ai pas vérifiées.
- Latence via relais, adresses qui changent, et comportement du produit quand Tailscale est arrêté ne sont pas décrits.

### D10. Points absents du cahier
- **Chiffrement au repos** de `personal.db` : rien n'est dit (SQLCipher ou chiffrement du volume ?). C'est une décision de sécurité.
- **Modèle de menace** : aucune section. Il faut au minimum : qui est l'adversaire (autre utilisateur du PC, malware, prompt injection via un document ou une page, l'autre IA compromise).
- **Évolution de version** : « même version sur les deux PC » mais que se passe-t-il pendant une mise à jour où les deux PC ne sont pas à la même version ? Il faut une fenêtre de compatibilité de C1/C4 et une négociation de version à la synchronisation.
- **Compaction du journal** : un journal en ajout seul + `memory_version` complet croissent sans limite ; pas de snapshot ni d'archivage prévus.
- **Suppression contre journal en ajout seul** : « suppression contrôlée » (M8) et historique complet se contredisent si on veut vraiment effacer une donnée (droit à l'oubli, fuite). Il faut préciser ce qu'est un effacement réel (cryptographique ? marqueur seul ?).
- **Licences des modèles** : redistribuables ou non dans l'installateur ?
- **Version de Windows** supportée (10 ou 11 seulement ?), antivirus (un exécutable Python empaqueté déclenche souvent des faux positifs, estimation), droits administrateur.
- **Horloges** : HLC suppose des horloges raisonnablement justes ; préciser l'écart toléré et l'action en cas de dérive.

## 3. Faisabilité sur mon matériel (PC 2)

### 3.1 Budget VRAM réaliste (16 Go)
- Pas d'iGPU : l'écran, le bureau et WebView2 (Chromium) utilisent la RTX. Compter environ 1 à 1,5 Go déjà pris (estimation), donc **~14 à 14,5 Go réellement utilisables**.
- Le cache de contexte (KV cache) s'ajoute au modèle : selon la taille du modèle et la longueur de contexte, de ~1 à ~3 Go (estimation).
- Conséquence pratique : un modèle **7-9 Go en fichier (classe 7B-14B quantifié)** avec un contexte de plusieurs milliers de jetons est confortable ; un modèle autour de **20 Go et plus ne tient pas** ; un 24-32B très quantifié est limite et laisse presque rien pour le reste.
- La 4060 Ti 16 Go a un bus mémoire étroit (128 bits, ~288 Go/s, chiffre que je crois exact mais que je n'ai pas revérifié) : la génération de texte est limitée par la bande passante, pas par la puissance de calcul. Ne pas attendre d'un 14B la même fluidité que d'un 7-8B.
- **Un seul gros modèle chargé à la fois** : « agents spécialisés » (M13) ne peut pas vouloir dire plusieurs gros modèles en parallèle ; ce sont des **rôles (prompts, outils) sur le même modèle**, ou des échanges de modèles avec un coût de chargement de plusieurs secondes.

### 3.2 Modules dont je suis porteur
| Module | Avis | Réaliste ? |
| --- | --- | --- |
| **M7** Recherche sémantique | Modèle d'embeddings petit, qui peut tourner sur CPU (i7-12700KF largement suffisant) pour laisser la VRAM au modèle principal. 10 000 notes : indexation de quelques minutes au plus (estimation). Dépend de sqlite-vec, dont je ne connais pas la maturité actuelle : prévoir un plan B (index en mémoire côté Python, ou FTS5 + vecteurs bruts). Recommandé : **recherche hybride** mot-clé (FTS5) + vecteurs. | Oui |
| **M8** Conflits / historique | Logique pure, indépendante du GPU. Le difficile est la **fusion de texte** et l'interface de décision, pas le stockage. Dépend de la correction de D3. | Oui, mais plus long que le cahier ne le suggère |
| **M9** Ressources | Pour la parité, **ne pas utiliser NVML/nvidia-smi** (NVIDIA seulement) : passer par les compteurs de performance Windows « GPU Adapter Memory » (valables AMD et NVIDIA) ; NVML en bonus sur le PC 2. Prévoir la politique « décharger avant de charger », le refus de charger un modèle si la VRAM estimée dépasse le disponible, et un mode dégradé CPU. | Oui |
| **M11** Adaptateur NVIDIA | Le démon llama.cpp serveur est la partie facile. Le dur : sélection automatique du backend (voir D1/D2), estimation de la VRAM avant chargement, annulation propre d'une génération, plantage du processus. CUDA sur 4060 Ti : mature (à mesurer). | Oui |
| **M12** Interface React + WebView2 | WebView2 est présent sur Windows 11 (à confirmer sur la version de Windows retenue). Une fenêtre qui encapsule une page servie en local par FastAPI est réaliste ; le flux de jetons en WebSocket aussi. Risque : coût mémoire de Chromium (CPU/RAM, un peu de VRAM) ; prévoir de pouvoir désactiver l'accélération matérielle de l'interface. Compter aussi l'accessibilité et la gestion du français. | Oui (à valider par spike) |
| **M13** Orchestrateur | C'est le module le plus **risqué**, et pas pour des raisons de VRAM : la fiabilité des appels d'outils par des modèles locaux de 7-14B est inégale ; boucles, sorties mal formées et dérive sont probables. Il faut des limites dures (nombre de tours, budget de jetons, délai), la validation de schéma des appels d'outils, et un mode « humain dans la boucle » par défaut. Les agents partagent le GPU : exécution **sérialisée**, files d'attente avec priorités. | Réaliste à portée réduite ; « agents spécialisés » = rôles, pas modèles |
| **M14** Automatisation Windows | UI Automation (via `pywinauto` ou équivalent) fonctionne sur les applications classiques ; **moins bien** sur certaines applications Electron/Chromium et jeux. Une application lancée « en administrateur » **ne peut pas** être pilotée par un processus non élevé (isolation d'interface Windows) : à documenter comme limite, pas comme bug. Risque principal : **injection d'instructions** par le contenu lu à l'écran (un texte affiché qui ordonne à l'agent de cliquer). Exige la confirmation humaine pour les actions sensibles, une liste blanche stricte et un journal. | Oui sur un périmètre étroit (une ou deux applications) |
| **M15** Voix | whisper.cpp sur le CPU du i7-12700KF : modèles « small/medium » utilisables, latence de plusieurs secondes sur du medium (estimation). Piper : voix françaises existantes, qualité correcte mais pas naturelle. **Manque dans le cahier** : le déclencheur (mot d'éveil ou touche « pousser pour parler ») et la détection d'activité vocale. Je recommande d'abord la touche, le mot d'éveil plus tard. Le micro est une donnée sensible : permission et indicateur visible. | Oui (sans mot d'éveil dans la V1) |
| **M18** Apprentissage supervisé | Reporté à la phase 10 ; sans LoRA, il se réduit à mémoire + retours + règles. Réaliste, peu de risques techniques ; attention à ne pas confondre « apprentissage » et simple liste de préférences. | Oui |

### 3.3 Vulkan commun / CUDA optionnel
Voir D1. Ma position : réaliste techniquement, mais **ne pas figer** avant mesure, et ne pas faire de Vulkan le défaut sur NVIDIA par principe de parité.

## 4. Relecture des modules du Claude B (sans proposer de réécriture)

- **M1 Noyau.** « Un processus par service » : superviseur, redémarrage, ordre de démarrage, sondes de santé, journaux, ports locaux sont à définir. « Bus d'événements interne » : durable ou en mémoire ? Que devient un événement quand un service est en panne ? Le démarrage d'une dizaine de processus Python a un coût mémoire et un temps de démarrage à mesurer.
- **M2 Identités, permissions, arrêt d'urgence.** Sous-estimé : c'est le cœur de la sécurité. Manquent le modèle de menace, le stockage des clés (voir D7), l'enrôlement d'un nouveau PC (l'appairage d'IA-partag est un bon modèle mais pas une spécification), la révocation, la rotation. « Arrêt d'urgence en moins d'une seconde » : que coupe-t-il exactement (génération en cours, processus enfants, automatisations, synchronisation) et comment le tester ? Un tuer-de-processus brutal sur le serveur de modèles doit être prévu.
- **M3 Audit signé.** Une signature par ligne ne prouve pas l'absence de suppression : il faut un **chaînage de hachage** ou un ancrage. Que contient l'audit (il peut contenir des données personnelles) et est-il synchronisé ? Taille et rotation.
- **M4 Stockage SQLite.** Chiffrement au repos, migrations de schéma, sauvegarde cohérente en mode WAL (utiliser l'API de sauvegarde SQLite, pas la copie de fichier), identifiants globaux (voir C4). Rien sur les fichiers joints.
- **M5 Synchronisation.** Sous-estimé : synchronisation des **fichiers** (découpage, reprise, intégrité, taille) en plus des événements ; compaction du journal ; modes auto/manuel/suspendu et leurs transitions ; file locale persistante pendant la coupure ; ordre et rejeu après coupure en cours de transfert.
- **M6 Réseau.** Comment le produit détecte et pilote Tailscale (CLI, API locale ?), ce qu'il fait quand Tailscale est absent, découverte du pair, coût des relais. Cahier muet sur l'authentification applicative au-dessus du tunnel.
- **M10 Adaptateur AMD.** Je ne peux pas juger la pile AMD : je note seulement que la performance de Vulkan sur une RX 9070 XT, et le support éventuel de HIP/ROCm sous Windows pour cette génération de carte, sont à mesurer et à vérifier par B, pas à présumer.
- **M16 Sauvegarde.** Les sauvegardes contiennent la mémoire personnelle : chiffrement obligatoire, et la question de savoir si les clés sont incluses. Test de restauration d'une version plus ancienne du schéma. Dépend aussi de C8, pas encore écrit.
- **M17 Extensions et mises à jour.** Le plus dangereux du cahier : une extension est du code arbitraire exécuté avec les droits de l'utilisateur. Sans bac à sable, signature, manifeste (C9 non écrit) et permissions déclarées, ce module ne devrait pas être livré dans la V1. À mon avis, séparer « mises à jour de l'application » (nécessaire) de « extensions tierces » (reportable).

## 5. Contrats (C1, C3, C4, C5, C7)

### C1 – Enveloppe d'événement
**Manque ou ambigu :**
1. Format de `hlc` non défini (chaîne ? objet ?).
2. **Octets signés** non définis : il faut une sérialisation canonique (par exemple JSON canonique RFC 8785) et dire ce que couvre la signature.
3. Pas de `key_id` (rotation/révocation de clé) ni de `seq` par nœud (détection de trous).
4. `type` : aucune liste ni schéma de `payload` par type ; il faut un registre.
5. Pas de lien causal explicite hors `parent_ids` (réponse à quel événement ?).
6. Taille maximale et gestion des fichiers (référence par hachage, pas d'inclusion).
7. Pas de `schema_version` du payload distinct de la version du contrat.
8. Pas de référence à l'autorisation qui a permis l'événement.

**Champs proposés :** `seq`, `key_id`, `hlc` = `{wall_ms, counter, node_id}`, `created_at` (UTC, humain), `payload_schema`, `payload_hash`, `correlation_id`, `causation_id`, `permission_ref` (identifiant de la vérification C3 ayant autorisé l'action), `attachments: [{sha256, size, name}]`, `visibility` (`personal|common|project`), `tombstone: bool` pour les suppressions, `id` en UUID version 7 (triable par temps).

### C3 – Permissions
**Manque ou ambigu :**
1. Pas d'identifiant de permission ni d'horodatage d'octroi, `granted_at`, `valid_from`, `revoked_at/by`.
2. **Grammaire de `resource`** absente (chemins, motifs, URI ? exemples).
3. **Aucune contrainte sur les arguments** (ex. dossier autorisé, nombre d'actions, débit) ; `allow_until` n'a pas de limite d'usage.
4. Que renvoie `permissions.check` ? Il faut une réponse typée : `allow | deny | ask`, avec raison et identifiant d'audit.
5. `ask_each_time` : canal de demande, **délai maximal, refus par défaut sans réponse**.
6. Délégation : un agent peut-il déléguer sa capacité ? À qui ? (important pour M13 et C7).
7. **Permissions inter-PC** : qui décide quand l'agent du PC 1 demande une capacité sur le PC 2 ?
8. Priorité entre `deny` et `allow` ; héritage entre ressources.
9. Octroi lui-même : événement signé de l'utilisateur ? (`permission.grant` existe dans C1, mais le lien n'est pas écrit).
10. Définition précise du `kill_switch` (voir M2).

**Champs proposés :** `permission_id`, `granted_at`, `valid_from`, `constraints` (`{paths, max_uses, rate_per_min, args_schema}`), `uses_count`, `risk_level` (`low|medium|high`), `delegable: bool`, `revoked_at`, `revoked_by`, `grant_event_id`, `node_scope` (`local|pc1|pc2`), `default_on_timeout: "deny"`, `confirmation_timeout_s`.

### C4 – Mémoire SQLite
**Manque ou ambigu :**
1. Identifiants : préciser **UUID globaux**, pas des entiers auto-incrémentés (collisions entre PC).
2. Règle d'écriture : voir D3 ; le texte actuel est ambigu pour deux pairs hors ligne.
3. Pas de `scope`, `created_at`, `updated_at`, `author`, `sensitivity` sur `memory_item`.
4. `memory_item.deleted_at` : la suppression doit se synchroniser (tombstone) ; la définition d'un effacement réel manque (D10).
5. `embedding` : pas de dimension, `content_hash`, `model_hash` ; stratégie de recalcul (D6).
6. `conflict` : manque `base_version` (l'ancêtre commun), `ours`, `theirs`, `resolution`, `resolved_at`, `resolved_by`.
7. Pas de table de **recherche plein texte** (FTS5), ni de **fichiers joints**, ni de **migrations** (`schema_migration`).
8. Pas de règle sur le chiffrement au repos ni sur les index.

**Tables / champs proposés :** `schema_migration(version, applied_at)`, `memory_item(id TEXT uuid, scope, type, content, content_hash, tags, version, author, created_at, updated_at, sensitivity, deleted_at)`, `memory_fts` (FTS5), `attachment(id, item_id, sha256, size, path)`, `conflict(base_version, ours_version, theirs_version, state, resolution, resolved_by, resolved_at)`, `embedding(item_id, model_id, model_hash, dim, content_hash, vector)`.

### C5 – Adaptateur de modèle IA
**Manque ou ambigu :**
1. Paramètres de `generate` absents : messages ou prompt brut, paramètres d'échantillonnage, `max_tokens`, séquences d'arrêt, `seed`, **flux** (format du streaming : jeton par jeton, fin, usage).
2. `embed` : taille de lot, dimension, normalisation.
3. **Identité du modèle** : nom, fichier, hachage, quantification, longueur de contexte, **gabarit de conversation**.
4. **Capacités** déclarées : appels d'outils, vision, embeddings.
5. `load_model` : paramètres (nombre de couches sur GPU, taille de contexte) et **estimation de VRAM avant chargement**.
6. Concurrence : files, priorités, annulation **par identifiant de requête**.
7. Erreurs trop peu nombreuses : manquent `context_overflow`, `backend_unavailable`, `busy`, `invalid_request`, `unsupported_capability`.
8. `backend` : ajouter `cpu` (repli) ; `status` par modèle ; mesures (jetons/s, latence du premier jeton) ; identifiant de périphérique.
9. Négociation de version entre noyau et adaptateur.
10. `vram_total_mb: 16384` est un exemple ; la valeur doit venir du matériel.

### C7 – Message entre IA
**Manque ou ambigu :**
1. `from`/`to` ne sont **pas authentifiés** (pas de signature ni de `node_id`) ; le rapport avec C1 n'est pas dit : C7 est-il le `payload` d'un événement C1 (recommandé, une seule signature) ou une enveloppe distincte ?
2. Pas de `in_reply_to` ni de `correlation_id` (le champ `thread` seul ne suffit pas pour relier `answer` à `question`).
3. `kind` incomplet : manquent `ack`, `error`, `cancel`, `status_update` (et le sens exact de `memory_conflict_query`).
4. **Corps de `task` non défini** : objectif, entrées, contraintes, schéma du résultat, budget (jetons, temps).
5. `requires_owner_approval` : le propriétaire de quel PC ? Qui répond et dans quel délai ? Que fait l'expéditeur en attendant ?
6. `scope: "common"` seulement : et `project:<id>` ?
7. `deadline: null` : préférer `expires_at` ; durée de vie des messages non lus ; retries (`attempt`).
8. Pas de limite de taille ni de pièces jointes ; pas de `lang`.
9. Pas de **capacité exigée** pour envoyer/recevoir (lien avec C3) ni de limite de débit (une IA ne doit pas pouvoir inonder l'autre).

**Champs proposés :** `in_reply_to`, `correlation_id`, `kind` étendu (`ack|error|cancel|status_update`), `task: {goal, inputs, constraints, result_schema, budget}`, `approval: {required_from: "owner_pc1|owner_pc2|both", timeout_s}`, `expires_at`, `attempt`, `attachments`, `required_capability`.

## 6. Dépendances et ordre des phases

### Ce qui bloque
- **Identité/clés (M2a) avant tout événement signé** (D7) : bloque C1, M3, M5, M6.
- **Décisions de la phase 1** : benchmark (Vulkan/CUDA), question Tailscale (même tailnet ?), chiffrement au repos. Elles conditionnent M10/M11, M6 et M4.
- **Relecture réelle de `main`** : le compte GitHub du PC 2 comme collaborateur (déjà dans la liste de la section 11) bloque la règle d'approbation sans contournement.
- **C2, C6, C8, C9** non écrits : C2 bloque M12 ; C6 bloque M13 ; C8 bloque M16 ; C9 bloque M17.

### Ce qui peut avancer en parallèle (dès la phase 1 ou 2)
- Rédaction de C2, C6, C8, C9 (par leurs porteurs).
- Spikes : benchmark Vulkan/CUDA (chaque PC sur sa carte), WebView2 + FastAPI, whisper.cpp/Piper en français, test de sqlite-vec sur 10 000 notes, tunnel Tailscale.
- **M12 (interface)** contre une fausse API C2 (« mock ») : n'a pas besoin du noyau réel.
- **M10/M11** derrière C5, indépendants du noyau ; **M9** une fois C5 figé.
- **M16** (format de sauvegarde, C8) peut démarrer tôt, car il ne dépend que de C4.

### Le premier jalon (phase 4) est-il réaliste ?
Oui comme *objectif*, mais le périmètre de la phase 4 est trop chargé : le chat local (M12), le tunnel (M6), la synchronisation (M5) et les messages entre IA (M13) arrivent ensemble, et ils reposent tous sur l'identité signée, C1 et C7. Je propose :
- **Jalon 0 (fin de phase 2)** : deux nœuds s'échangent un message C7 **sans modèle de langage** (simple transport signé), pour valider M2a, C1, C7 et le tunnel très tôt.
- **Jalon 1 (phase 4)** : deux *IA* échangent un message (avec génération), plus la note commune synchronisée.
Cela réduit le risque et donne une preuve visible plus tôt. À noter : la phase 3 (mémoire) n'est pas nécessaire au jalon 0, donc il peut même passer avant.

## 7. Questions pour le propriétaire

1. Accepte-t-il que, sur le PC 2, **CUDA soit le backend par défaut si le benchmark le justifie**, Vulkan restant le chemin de référence commun ?
2. Les deux PC sont-ils sur le **même tailnet Tailscale**, ou deux comptes avec partage de machine ? Qui installe et administre Tailscale ?
3. La mémoire personnelle doit-elle être **chiffrée au repos** (donc mot de passe ou clé à gérer) ?
4. Quel **modèle de menace** retient-on (autre utilisateur du PC, logiciel malveillant, injection d'instructions par un document, l'autre IA) ?
5. Les « spikes » (code jetable et installations de test en phase 1) sont-ils autorisés, et sous quelle validation ?
6. Les **extensions tierces** (M17) doivent-elles être dans la V1, ou seulement les mises à jour de l'application ?
7. Que signifie **effacer** une donnée : marqueur de suppression seulement, ou effacement réel (y compris l'historique et les sauvegardes) ?
8. Quelles **versions de Windows** sont visées (10 et 11, ou 11 seulement) ?
9. Les modèles de langage retenus doivent-ils être **redistribuables** dans l'installateur, ou téléchargés par l'utilisateur ?
10. Le premier jalon peut-il être **découpé en deux** (message de transport sans modèle, puis deux IA) ?
11. Pour l'autre humain (PC 2) : quelles applications Windows autorise-t-il à piloter en premier, et accepte-t-il l'usage du micro (voix) avec indicateur visible ?
12. Quel est le **budget de ressources** accepté sur le PC 2 pendant l'usage normal (VRAM et RAM réservées à l'IA, en laissant le bureau fluide) ?
