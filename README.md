# Manatoufer - Bot Discord de planification

Manatoufer aide a organiser des events et des disponibilites de jeu sur Discord:
- creation automatique de role + salon prive pour un event
- inscription/retrait des membres par reaction
- sondages de disponibilites (type doodle)
- suggestions automatiques de sessions selon les votes

## Commandes slash

### Events
- `/moomle_event_create`
- `/moomle_event_change`
- `/moomle_event_delete`

### Sondages
- `/moomle_pool_create`
- `/moomle_pool_suggest`
- `/moomle_pool_delete`
- `/moomle_status`

## Installation rapide

1. Installer Python 3.10+
2. Installer les dependances:

```bash
pip install -r requirements.txt
```

3. Configurer les variables d'environnement:

```env
DISCORD_TOKEN=your_bot_token
# optionnel
GUILD_ID=123456789012345678
PORT=19045
```

4. Lancer le bot:

```bash
python manatoufer.py
```

## Structure actuelle

```
.
|- manatoufer.py
|- moomle_polls.json
|- botcore/
|  |- config.py
|  |- storage.py
|  |- health.py
|  |- views.py
|  |- moomle_formatting.py
|  |- runtime.py
|  |- app.py
|  |- features/
|     |- events.py
|     |- moomle.py
```

## Notes de partage (bonnes pratiques)

- Ne jamais commiter de token Discord.
- Garder `moomle_polls.json` en local (et l'ignorer dans git si besoin).
- Preferer un bot token de dev distinct du token de prod.
- Ajouter des tests avant les grosses evolutions de logique de suggestion.
