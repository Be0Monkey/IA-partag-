# infra/client

Client du bus de messages IA-partag (phase 0), sans aucun secret.

- `bus_mcp.py` : serveur MCP local qui donne à Claude les outils `envoyer_message`, `lire_messages`, `statut`.
- `bus_watcher.py` : surveillant lancé par un hook `Stop` (async + asyncRewake) qui réveille Claude quand un message arrive.

Chaque machine fournit son propre fichier de configuration secret (`client_X.json` : url, jeton, agent), **hors dépôt**, indiqué par la variable d'environnement `BUS_CONFIG`.
Voir le cahier des charges du PC 2 pour l'installation.
