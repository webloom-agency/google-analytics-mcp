# Serveur MCP Google Analytics 4 (GA4) — distant, multi‑utilisateur, OAuth 2.1

**🌐 Langues :** [English](README.md) · **Français**

Un serveur [Model Context Protocol](https://modelcontextprotocol.io) hébergé
pour **Google Analytics 4**. Contrairement au serveur local officiel, celui‑ci
est conçu pour fonctionner **à distance et pour plusieurs utilisateurs** :
chaque personne se connecte avec **son propre compte Google** via le navigateur
(OAuth 2.1), et sa session est **conservée côté serveur** afin de ne pas devoir
se ré‑authentifier à chaque redémarrage.

Il fonctionne avec **n'importe quel client compatible MCP** — ChatGPT, Claude,
Cursor, et autres — car MCP est un standard de transport, pas un modèle. Le LLM
ne voit jamais vos identifiants Google : le serveur les conserve et ne renvoie
que des résultats.

Conçu et maintenu par [Webloom](https://webloom.fr). 🌱

---

## Différences avec le serveur officiel de Google

| | `analytics-mcp` officiel | Ce serveur |
|---|---|---|
| Transport | `stdio` local (une machine) | **HTTP Streamable** distant (`/mcp`) |
| Authentification | Identifiants par défaut (une seule identité) | **OAuth 2.1 par utilisateur**, chacun avec sa connexion Google |
| Utilisateurs | Un développeur, un ordinateur | **Multi‑utilisateur**, hébergé |
| Persistance | aucune | Jetons de rafraîchissement conservés sur disque (survivent aux redémarrages) |
| Clients documentés | Gemini, Claude Code | **ChatGPT, Claude, Cursor** (+ tout client MCP) |

---

## Outils 🛠️

Basé sur l'
[API Admin Google Analytics](https://developers.google.com/analytics/devguides/config/admin/v1)
et l'
[API Data](https://developers.google.com/analytics/devguides/reporting/data/v1).

| Outil | Rôle |
|---|---|
| `get_account_summaries` | Liste tous les comptes et propriétés GA4 auxquels vous avez accès. |
| `find_property_by_domain` | **Trouve l'identifiant de propriété à partir d'un domaine ou d'une URL** (ex. `webloom.fr`). Compare avec l'URL du flux de données web de chaque propriété. |
| `get_property_details` | Détails d'une propriété (fuseau horaire, devise, secteur…). |
| `list_google_ads_links` | Liens Google Ads d'une propriété. |
| `get_custom_dimensions_and_metrics` | Dimensions et métriques personnalisées d'une propriété. |
| `run_report` | L'outil d'analyse principal — choisissez dimensions + métriques sur une période. De nombreux exemples intégrés : trafic, canaux, pages de destination, revenus, événements… |
| `run_realtime_report` | Rapport en temps réel (≈ 30 dernières minutes). |

> Vous ne connaissez pas l'identifiant de propriété d'un site ? Demandez
> simplement en langage naturel — par ex. *« quels ont été les meilleurs canaux
> pour webloom.fr le mois dernier ? »* — et l'assistant appellera
> `find_property_by_domain` puis `run_report` pour vous.

---

## Connecter votre client MCP 🔌

Il vous faut l'URL MCP du serveur, qui se termine par **`/mcp`** :

```
https://VOTRE-SERVEUR.onrender.com/mcp
```

Lors de la première connexion, une fenêtre de navigateur s'ouvre :
**connectez‑vous avec le compte Google ayant accès à vos propriétés GA4** et
autorisez le périmètre Analytics en lecture seule. C'est tout — votre connexion
est ensuite mémorisée.

### Cursor

Ajoutez le serveur à votre configuration MCP — soit le fichier de projet
`.cursor/mcp.json`, soit le fichier global `~/.cursor/mcp.json` :

```json
{
  "mcpServers": {
    "google-analytics": {
      "url": "https://VOTRE-SERVEUR.onrender.com/mcp"
    }
  }
}
```

Ouvrez ensuite **Settings → Tools & MCP**, vérifiez que `google-analytics`
apparaît, puis cliquez pour **vous authentifier** lorsque c'est demandé. Une
fois le voyant vert, posez une question analytique à Cursor.

### Claude

**Claude Desktop / claude.ai** (nécessite une offre compatible avec les
connecteurs personnalisés) :

1. **Réglages → Connecteurs → Ajouter un connecteur personnalisé**.
2. Nommez‑le `Google Analytics` et collez l'URL
   `https://VOTRE-SERVEUR.onrender.com/mcp`.
3. Cliquez sur **Connecter** et terminez la connexion Google.

**Claude Code (CLI) :**

```shell
claude mcp add --transport http google-analytics https://VOTRE-SERVEUR.onrender.com/mcp
```

Lancez `/mcp` dans Claude Code pour déclencher l'authentification.

### ChatGPT

Nécessite une offre avec **connecteurs / mode développeur** (Plus, Pro, Business
ou Enterprise) :

1. **Réglages → Connecteurs** (activez le **mode développeur** dans Avancé si
   nécessaire).
2. **Créer / Ajouter un connecteur personnalisé** → donnez un nom et collez
   l'URL du serveur MCP `https://VOTRE-SERVEUR.onrender.com/mcp`.
3. Enregistrez, puis **authentifiez‑vous** avec votre compte Google.
4. Dans une conversation, activez le connecteur et posez votre question GA4.

---

## Essayez‑le 🥼

Une fois connecté, posez des questions en langage naturel :

```
Que peut faire le serveur Google Analytics ?
```

```
Trouve la propriété GA4 de webloom.fr.
```

```
Quels ont été les meilleurs canaux d'acquisition pour webloom.fr sur les 28 derniers jours ?
```

```
Montre les utilisateurs actifs et les sessions par jour pour la propriété 123456789 sur les 7 derniers jours.
```

```
Quels sont les événements les plus fréquents de ma propriété sur les 180 derniers jours ?
```

```
Combien d'utilisateurs sont sur le site en ce moment, par pays ?
```

---

## Auto‑hébergement (Render) 🚀

Le serveur est une application Python ASGI standard ; n'importe quel hébergeur
convient. Voici la configuration Render pour laquelle il est prévu.

### 1. Configuration Google Cloud

1. Créez/sélectionnez un projet Google Cloud.
2. Activez les deux API :
   [API Admin Analytics](https://console.cloud.google.com/apis/library/analyticsadmin.googleapis.com)
   et
   [API Data Analytics](https://console.cloud.google.com/apis/library/analyticsdata.googleapis.com).
3. Configurez l'**écran de consentement OAuth** et ajoutez le périmètre
   `https://www.googleapis.com/auth/analytics.readonly`. En mode *Test*, ajoutez
   chaque utilisateur sous **Utilisateurs tests**.
4. Créez un client OAuth **Application Web**. Ajoutez l'URI de redirection
   autorisée : `https://VOTRE-SERVEUR.onrender.com/oauth2callback`.

### 2. Service Render

- **Commande de build :** `pip install -r requirements.txt`
- **Commande de démarrage :** `uvicorn server_http:app --host 0.0.0.0 --port $PORT`
- **Ajoutez un disque persistant** monté sur **`/data`** (contient les
  identifiants Google par utilisateur + l'état OAuth pour que les connexions
  survivent aux redémarrages).

### 3. Variables d'environnement

| Variable | Valeur |
|---|---|
| `MCP_ENABLE_OAUTH21` | `true` |
| `GOOGLE_OAUTH_CLIENT_ID` | l'identifiant de votre client OAuth web |
| `GOOGLE_OAUTH_CLIENT_SECRET` | le secret de votre client OAuth web |
| `GA4_EXTERNAL_URL` | `https://VOTRE-SERVEUR.onrender.com` (URL HTTPS publique, sans slash final) |
| `GOOGLE_MCP_CREDENTIALS_DIR` | `/data` |

Déployez, puis pointez votre client vers
`https://VOTRE-SERVEUR.onrender.com/mcp`.

### Usage local (mono‑utilisateur, stdio)

Pour un test local rapide sans le serveur OAuth, fournissez un jeton
pré‑autorisé ou un compte de service, puis lancez :

```shell
pip install -r requirements.txt
python ga4_server.py
```

Voir `ga4_server.py` pour l'ordre de résolution des identifiants
(`GA4_OAUTH_TOKEN_PATH`, fichier de compte de service, etc.).

---

## Notes de sécurité 🔒

- Le LLM/client ne reçoit jamais vos identifiants Google : ils restent côté
  serveur.
- Les jetons de rafraîchissement et l'état OAuth se trouvent sous `/data` avec
  des permissions restrictives ; gardez ce disque privé et ne commitez jamais de
  fichiers d'identifiants.
- Si vous exécutez le serveur **sans** OAuth 2.1 et **sans** jeton bearer, le
  point d'accès `/mcp` n'est pas authentifié — le serveur affiche un
  avertissement bien visible au démarrage. Pour tout déploiement distant,
  gardez `MCP_ENABLE_OAUTH21=true`.

---

## Crédits

Réalisé avec soin par [**Webloom**](https://webloom.fr) — [webloom.fr](https://webloom.fr).

Les contributions sont les bienvenues ! Voir le [Guide de contribution](CONTRIBUTING.md).
