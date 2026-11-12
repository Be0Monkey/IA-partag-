# Revue du cahier des charges v1 — Claude B (PC 1)

Revue faite sur `docs/cahier-des-charges-v1.md` (branche `main`, commit `618ee0d`), sans lire la revue de Claude A. Aucun code écrit, aucune installation lancée.

**Ce que j'ai vérifié moi-même, en lecture seule** : CPU AMD Ryzen 7 7800X3D (8 cœurs), 32 Go de RAM, GPU AMD Radeon RX 9070 XT détectée, et `vulkaninfo --summary` la liste comme GPU discret (Vulkan 1.4). **Ce que je n'ai pas pu vérifier** : la taille réelle de la VRAM (voir section 3), les performances de llama.cpp, et le comportement de WebView2 et de Tailscale sur cette machine.

---

## 1. Accords

- Séparation des deux couches (IA-partag d'un côté, produit de l'autre) : je valide, et la règle « le produit n'importe aucun code qui dépend de Claude » est la bonne.
- Vulkan comme socle commun, CUDA en option : je valide le principe. Le benchmark de la phase 1 avant de figer le choix est indispensable.
- SQLite en WAL, un fichier par périmètre : je valide. Le bus d'IA-partag utilise déjà SQLite en WAL, avec un délai d'attente de 5 secondes.
- Journal d'événements en ajout seul et versionné par contrat : je valide.
- Un porteur et un relecteur par module, aucun Claude ne fusionne : je valide le principe, mais voir le point 2.7 sur la façon dont la règle a été contournée.
- Premier jalon visible en phase 4 : je valide l'objectif, mais pas tel qu'il est découpé (voir section 6).
- Tailscale conservé avec plan B : je valide.
- Les tests de sécurité obligatoires de la section 8 : je valide.

## 2. Désaccords et corrections

**2.1 Transport du bus et chiffrement (section 3, « Réseau »).** Le bus d'IA-partag écoute en HTTP simple sur l'adresse Tailscale (`http://100.x:8765`), et s'authentifie par jeton porteur. Le chiffrement vient seulement de Tailscale. Ça suffit pour le bus de développement, mais pas pour le produit : si le tunnel est retiré ou mal configuré, le jeton circule en clair. **Proposition :** faire de la signature d'événement (C1) la vraie garantie d'intégrité, et imposer TLS ou une liaison WireGuard vérifiée par clé pour le trafic produit. Le document dit « la signature protège même si le tunnel est compromis » : c'est vrai pour l'intégrité du contenu, pas pour la confidentialité des métadonnées ni pour le rejeu. Le rejeu doit être couvert par `event_id` + `hlc` + une fenêtre de rejet.

**2.2 Gestion des clés (section 3, « Réseau » et M2).** La signature Ed25519 « de la clé du nœud » est citée dans C1, mais personne ne possède précisément le cycle de vie des clés : génération, stockage, rotation, révocation, appairage. Le bus sait appairer un PC avec un code à usage unique, mais le jeton reste en fichier. **Proposition :** définir dans M2 un stockage des clés privées protégé par Windows (DPAPI ou magasin de certificats, pas de fichier en clair), un identifiant de clé (`kid`) dans l'enveloppe, et une procédure de révocation écrite comme événement signé.

**2.3 Secrets dans Git (section 8 et section 7, point 4).** « Analyse de secrets avant chaque fusion » est trop vague. La recherche par mots-clés que je fais sur les branches (jeton, password, etc.) donne des faux positifs (le mot « clé » apparaît partout en français) et rate les vrais secrets sans mot-clé. **Proposition :** un outil de détection dédié (type gitleaks, ou règles de motifs versionnées) exécuté par le hook `pre-push` et en CI, en plus de la revue humaine.

**2.4 « Hors ligne » et téléchargement des modèles (section 7, point 1 ; phase 2).** La phase 2 demande une IA locale « hors Internet », mais les modèles et les binaires llama.cpp doivent d'abord être téléchargés. Le document ne dit pas comment la première installation se fait. **Proposition :** préciser deux temps : installation (Internet requis, une fois, avec vérification de l'empreinte des fichiers) et fonctionnement (aucun Internet). Ajouter dans C5 une empreinte (SHA-256) par modèle.

**2.5 Arrêt d'urgence < 1 s (C3).** L'arrêt doit interrompre une génération en cours. Le contrat C5 a bien `stop`, mais pas de garantie de délai ni de comportement quand le moteur ne répond plus. **Proposition :** le noyau tue le processus moteur après un délai court si `stop` n'a pas répondu, et l'événement de révocation est écrit dans le journal avant l'arrêt.

**2.6 Recouvrement de la mémoire et de l'audit (M3 et M4).** L'audit est décrit comme « signé », mais rien n'empêche de supprimer une ligne du fichier SQLite. Un trigger SQLite ne protège pas contre une modification directe du fichier. **Proposition :** chaîne de hachage (chaque entrée contient le hachage de la précédente) plus signature périodique, et le journal dans un fichier séparé du reste de la mémoire.

**2.7 Gouvernance des fusions (section 10 et section 11).** Le document dit que « la règle GitHub déjà active » impose la validation du propriétaire. Pour la PR n° 6, GitHub indiquait `reviewDecision: REVIEW_REQUIRED` au moment de la fusion : la règle n'a donc pas été appliquée par GitHub, et la section 11 le reconnaît (« ajouter le compte du PC 2 comme collaborateur »). Je n'ai pas vérifié l'état de revue des PR n° 5 et n° 7. **Proposition :** écrire dans le document la règle réelle (qui peut fusionner, avec quelle approbation, et ce qui est enregistré sur le bus), et ne plus parler de contournement comme d'une exception acceptée sans trace.

**2.8 Lisibilité de la règle « PC 1 tranche le commun » (section 1 et section 8).** Le document parle de deux niveaux de décision, mais il ne dit pas qui arbitre si les deux propriétaires ne sont pas d'accord. Ce n'est pas bloquant pour la phase 1, mais il faut une ligne de règle.

## 3. Faisabilité sur mon matériel

**Choix Vulkan comme socle.** Réaliste : Vulkan 1.4 voit la RX 9070 XT comme GPU discret. Je ne peux pas confirmer les performances : elles n'ont pas été mesurées, et le document le reconnaît. **Point à corriger :** la VRAM. Win32_VideoController affiche 4 Go, mais ce champ est limité à 32 bits, donc cette valeur n'est pas fiable pour une carte de 16 Go. Je n'ai pas vérifié les tas de mémoire Vulkan (le résumé `vulkaninfo --summary` ne les donne pas). **Il faut le vérifier avant le benchmark**, sinon le calcul « 16384 Mo » du contrat C5 reste une hypothèse.

**Modules dont je suis porteur :**

- **M1 — Noyau, services, bus d'événements (FastAPI).** Réaliste. Le bus est déjà en Starlette et uvicorn. Leçon concrète : le 11 novembre, la route `/ui/decision` a renvoyé une erreur 500 car `python-multipart` manquait dans les bibliothèques. Les routes qui lisent un formulaire ne plantent qu'à l'exécution. **Proposition :** un fichier de dépendances figé (avec versions) et un test de fumée qui appelle chaque route au démarrage.
- **M2 — Identités, clés, permissions, arrêt d'urgence.** Réaliste pour le contrôle `permissions.check`. Plus difficile : la révocation pendant une génération (point 2.5) et le stockage des clés (point 2.2). Estimation honnête : ce module est le plus long du socle.
- **M3 — Journal d'audit signé.** Réaliste avec une chaîne de hachage et une signature (point 2.6). Le journal du bus, lui, est une simple table SQLite modifiable : il ne peut pas servir de modèle pour la signature.
- **M4 — Mémoire SQLite.** Réaliste. Le bus valide déjà le principe de la version attendue : une écriture qui n'a pas la dernière version est refusée avec la valeur actuelle (409). Les tables du contrat C4 sont plus riches, donc plus de travail, mais le principe est éprouvé. Point d'attention : sqlite-vec n'est pas testé ici.
- **M5 — Synchronisation.** C'est le module le plus risqué des miens. Le bus n'utilise pas d'horloge logique hybride : il ordonne les messages par identifiant local, ce qui suffit pour un bus à deux agents mais pas pour une synchronisation entre deux bases qui s'écrivent en parallèle. Je le juge réaliste seulement après M4 et une semaine de tests de coupure.
- **M6 — Réseau Tailscale.** Réaliste, mais la dépendance à un compte Tailscale est un point à décider (questions, section 7). Le lanceur du bus vérifie d'ailleurs que Tailscale est connecté avant de démarrer.
- **M10 — Adaptateur AMD (Vulkan).** Réaliste mais non vérifié : il faut un binaire llama.cpp compilé pour Vulkan (ou compilé ici, ce qui demande un SDK), et un test de chargement d'un modèle. Je n'ai rien installé, donc rien de cela n'est testé.
- **M16 — Sauvegarde et restauration.** Réaliste et déjà amorcé : le bus fait une copie de sa base à l'aide de l'API de sauvegarde SQLite toutes les 6 heures, et `restore_bus.py` refuse de restaurer si le serveur tourne, vérifie l'intégrité et garde l'ancienne base. **Point à ajouter :** ces sauvegardes sont sur le même disque. Le bus expose déjà une route `/backup/latest` réservée à Claude A, pour récupérer une copie hors du PC 1 : c'est le bon modèle, à généraliser dans C8.
- **M17 — Extensions.** Réaliste seulement après M2 et M3. Une extension est du code qui s'exécute : sans permissions et journal, c'est le risque principal du produit.

## 4. Relecture des modules de Claude A (sans réécriture)

- **M7 — Recherche sémantique.** Sous-estimé : le modèle d'embeddings et le LLM partagent les 16 Go de VRAM sur le PC 2. Il faut définir la priorité de chargement et ce qui se passe quand les deux ne tiennent pas. Il faut aussi indiquer que les vecteurs sont à recalculer quand le modèle change (le contrat C4 doit stocker le modèle par vecteur).
- **M8 — Conflits et suppression contrôlée.** Mal défini : la suppression doit passer par des marqueurs de suppression (tombstones) avec une durée de conservation, sinon une suppression reçue en retard réapparaît. Le document ne le dit pas.
- **M9 — Ressources et GPU.** Sous-estimé : mesurer la VRAM et la charge GPU sous Windows n'a pas d'API unique entre AMD et NVIDIA. Pour le PC 2 (NVIDIA), NVML est disponible ; pour le PC 1 il faut un autre moyen. M9 doit donc être décrit par fournisseur.
- **M11 — Adaptateur NVIDIA (Vulkan + CUDA).** Mal défini : le document ne dit pas quand CUDA est utilisé à la place de Vulkan, ni comment la parité est vérifiée si les deux backends donnent des résultats différents. Il faut un critère de choix écrit.
- **M12 — Interface.** Sous-estimé et trop chargé : chat, projets et supervision sont trois produits. Le document met tout dans un seul module porté par le PC 2. Je propose de le découper dans le plan de phase, sans changer l'attribution.
- **M13 — Orchestrateur et délégation.** Le module le plus flou : la délégation de tâche n'a pas de sémantique (qui décide, délai, annulation, résultat partiel). Les contrats C6 et C7 ne sont pas écrits. Risque de boucles : le bus d'IA-partag limite déjà à 6 messages par fil et 20 par minute, c'est un garde-fou à reprendre explicitement.
- **M14 — Automatisation Windows (UI Automation).** Sous-estimé : l'API d'accessibilité couvre mal les applications Electron, les jeux et certaines applications web. Le périmètre utile doit être défini par liste blanche, avec un test de couverture par application.
- **M15 — Voix.** Réaliste sur CPU, mais la latence de whisper.cpp et Piper doit être mesurée. Les licences des voix Piper sont à vérifier avant d'inclure une voix.
- **M18 — Apprentissage supervisé.** Mal défini : « retours et règles » n'a ni format ni règle de validation. Risque d'empoisonnement de la mémoire : une règle apprise doit passer par les mêmes permissions et le même journal que n'importe quelle écriture.

## 5. Contrats

**C1 — Enveloppe d'événement.** Manque :
- `kid` : identifiant de la clé de signature (point 2.2).
- `canon` : règle de sérialisation avant signature (par exemple JSON canonique RFC 8785). Sans elle, deux implémentations ne vérifient pas les mêmes octets.
- `schema` : version du schéma du `payload` par type d'événement. Le champ `type` seul ne suffit pas.
- Un identifiant d'idempotence (le bus utilise un `uid` pour éviter les doublons).
- Une taille maximale et une règle de rejet.
- Les événements de suppression : un type `memory.tombstone` avec durée de conservation.
- La sémantique de `parent_ids` en cas de fusion (plusieurs parents).

**C3 — Permissions.** Manque :
- Le format du sujet (`agent:<id>` lié à quel nœud ?).
- La liste fermée des capacités (sinon `windows.app.control` est une chaîne libre).
- Le mode `allow_until` utilise-t-il `expires_at` ? Le document ne le dit pas.
- Le fonctionnement de `ask_each_time` : par quel canal la demande arrive, et que se passe-t-il sans réponse (délai, refus par défaut).
- Un identifiant d'événement de révocation, pour relier la révocation au journal.
- La règle « refus par défaut » doit être écrite en toutes lettres.

**C4 — Mémoire SQLite.** Manque :
- Une table de migrations (version du schéma).
- Pour `memory_item` : date de création, auteur, périmètre, niveau de sensibilité.
- Pour `embedding` : identifiant du modèle et dimension, pour invalider les vecteurs au changement de modèle.
- Une table de marqueurs de suppression avec durée de conservation (point M8).
- Le document écrit « néud » à la place de « nœud » dans la liste des tables : à corriger.

**C5 — Adaptateur de modèle.** Manque :
- Le streaming de sortie (sans lui, pas de chat fluide).
- Une empreinte du modèle et de la quantification, pour que « même modèle sur les deux PC » soit vérifiable.
- Les erreurs `backend_unavailable` et `driver_reset` (un reset de pilote GPU est fréquent sous Windows).
- Le délai maximal de `load_model`.
- L'exemple chiffre `vram_total_mb: 16384` en dur : à remplacer par la valeur mesurée (point 3).

**C7 — Message entre IA.** Manque :
- `in_reply_to` pour relier une réponse à une question.
- Les accusés : le bus n'accuse que la lecture, pas la réception ; le contrat doit dire lequel.
- Une protection contre les boucles : le bus limite à 6 messages par fil. C7 doit reprendre cette limite.
- Un type de message pour les demandes de validation, sinon elles passent par un autre canal.
- L'identifiant de l'expéditeur doit être signé (C1), pas seulement déclaré dans `from`.

## 6. Dépendances et ordre des phases

- **Bloquant :** M1 puis M2 avant tout, comme le dit le document. Le stockage des clés (point 2.2) doit être décidé avant M3 et C1, sinon la signature n'a pas de base.
- **Parallèle possible :** M10 (adaptateur AMD) et M4 (mémoire) peuvent avancer en même temps une fois C4 et C5 écrits. M9 peut suivre.
- **Premier jalon (phase 4) : réaliste pour l'échange de messages, risqué pour la note commune.** Un message entre deux IA ne demande que C7, M6 (tunnel), et un minimum de M1 et M2. Une note commune qui apparaît sur l'autre PC demande M5 (synchronisation), le module le plus risqué. Je propose de scinder la phase 4 : 4a (messages via le tunnel) et 4b (note commune synchronisée), avec un critère propre à chacune.
- **Chemin critique :** M1 → M2 → M4 → M5 → M13. Il faut ajouter M3 (le journal doit exister avant les premiers événements signés) et le stockage des clés, qui n'est dans aucun chemin.

## 7. Questions pour le propriétaire

1. Qui doit approuver les fusions du propriétaire lui-même ? Le document dit « seul le propriétaire fusionne », mais la PR n° 6 a été fusionnée sans approbation du PC 2. Veut-on que cela reste une exception, ou une règle à appliquer partout ?
2. Quel compte Tailscale sert au produit, et que se passe-t-il si ce compte change ? Le document dit que le réseau est « indépendant », mais il dépend d'un compte.
3. Première installation : acceptez-vous une connexion Internet pour télécharger les modèles (une fois, vérifiée par empreinte), ou faut-il un canal de transfert hors ligne ?
4. Quels modèles en premier, et avec quelles licences ? Le choix affecte directement le benchmark.
5. Le bus de développement reste-t-il en HTTP simple, ou faut-il TLS dès maintenant (point 2.1) ?
6. Quelle est la règle si les deux propriétaires ne sont pas d'accord sur une décision commune ?
7. La VRAM de la RX 9070 XT est-elle bien de 16 Go ? Je ne peux pas le confirmer depuis WMI : une lecture dans le pilote AMD ou un test Vulkan peut la donner.
