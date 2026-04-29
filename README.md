# 🎯 Manatoufer — Bot Discord de gestion de sessions de jeu

Manatoufer est un bot Discord conçu pour **organiser et planifier des sessions de jeu multijoueur** au sein d'un serveur. Il automatise la création de rôles, de salons privés et de sondages de disponibilité pour faciliter la coordination entre joueurs.

---

## ✨ Fonctionnalités

### 🗂️ Gestion d'événements
- Création automatique d'un **salon privé** et d'un **rôle dédié** pour chaque événement
- Les ressources sont regroupées sous une catégorie `PLANIFICATION STRATEGIQUE`
- Attribution automatique d'un emoji par événement
- Support du **renommage** et de la **suppression** propre d'un événement (salon + rôle)
- Inscription par réaction ✅ : les membres rejoignent l'événement en réagissant au message d'annonce

### 📊 Moomle — Sondages de disponibilité
- Création de sondages de type "Doodle" pour trouver les meilleurs créneaux
- Détection automatique des sessions/événements actifs du serveur
- Vote par réaction (🇦 🇧 🇨 … jusqu'à 20 créneaux)
- Mise à jour en temps réel de l'embed de résultats à chaque vote
- Persistance des sondages dans un fichier JSON local (`moomle_polls.json`)
- Suggestions automatiques de sessions selon les disponibilités communes
- Limite configurable : durée max, nombre de créneaux, nombre de sessions

### ⚙️ Slash Commands
Le bot utilise exclusivement les **slash commands** Discord (`/`). Les commandes sont synchronisées au démarrage, sur le serveur défini via `GUILD_ID` ou globalement.

---

## 🚀 Installation

### Prérequis

- Python **3.10+**
- Un **token de bot Discord** (depuis le [Discord Developer Portal](https://discord.com/developers/applications))
- L'**ID du serveur** Discord cible (optionnel mais recommandé pour des syncs de commandes rapides)

### 1. Cloner le dépôt

```bash
git clone https://github.com/MyselfFake/Manatoufer.git
cd Manatoufer
```

### 2. Installer les dépendances

```bash
pip install discord.py
```

### 3. Configurer les variables d'environnement

Le bot lit sa configuration depuis les variables d'environnement suivantes :

`DISCORD_TOKEN` ou `TOKEN` : Token du bot Discord 

Exemple avec un fichier `.env` (à charger manuellement ou via `python-dotenv`) :

```env
DISCORD_TOKEN=ton_token_ici
```

### 4. Lancer le bot

```bash
python manatoufer.py
```

---

## 🔐 Permissions Discord requises

Le bot a besoin des permissions suivantes sur le serveur :

- Gérer les rôles
- Gérer les salons
- Lire les messages / Historique des messages
- Envoyer des messages
- Ajouter des réactions
- Mentionner les membres

Ainsi que les **Intents privilegiés** suivants (à activer dans le Developer Portal) :

- `Server Members Intent`
- `Message Content Intent` *(désactivé par défaut dans le code, à activer si nécessaire)*

---

## 📁 Structure du projet

```
Manatoufer/
├── manatoufer.py        # Code principal du bot
└── moomle_polls.json    # Persistance des sondages (créé automatiquement)
```

---

## 🛠️ Configuration avancée

Plusieurs constantes sont définies en haut de `manatoufer.py` et peuvent être ajustées :

| Constante | Valeur par défaut | Description |
|---|---|---|
| `MAX_MOOMLE_SLOTS` | `20` | Nombre maximum de créneaux par sondage |
| `MAX_MOOMLE_SESSIONS` | `25` | Nombre maximum de sessions par sondage |
| `MAX_MOOMLE_DURATION_HOURS` | `720` | Durée maximale d'un sondage (en heures) |
| `MOOMLE_AUTO_SUGGEST_CHECK_SECONDS` | `30` | Intervalle de vérification pour les suggestions auto |
| `EVENT_CATEGORY_NAME` | `PLANIFICATION STRATEGIQUE` | Nom de la catégorie Discord pour les événements |

---

## 📖 Utilisation rapide

1. **Créer un événement** : utilisez la commande `/moomle_event_create` avec le nom de l'événement. Un salon privé et un rôle sont créés automatiquement.
2. **S'inscrire** : les membres réagissent avec ✅ au message d'annonce pour rejoindre l'événement et obtenir le rôle.
3. **Lancer un sondage** : utilisez `/moomle_create` pour créer un sondage de disponibilité. Les créneaux sont votables par réaction.
4. **Consulter les résultats** : l'embed du sondage se met à jour en temps réel.
5. **Suggérer une session** : `/moomle_pool_suggest` analyse les votes et propose automatiquement le meilleur créneau.
6. **Supprimer un événement** : `/moomle_event_delete` supprime proprement le salon et le rôle associés.

---

## 📄 Licence

Ce projet est open source. Consultez le dépôt pour plus de détails.
