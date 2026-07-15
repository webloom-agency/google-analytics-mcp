# Google Analytics 4 (GA4) MCP Server — remote, multi‑user, OAuth 2.1

**🌐 Languages:** **English** · [Français](README.fr.md)

A hosted [Model Context Protocol](https://modelcontextprotocol.io) server for
**Google Analytics 4**. Unlike the official local server, this one is built to
run **remotely for multiple users**: each person signs in with their **own
Google account** through a browser (OAuth 2.1), and their login is **persisted
server‑side** so they don't have to re‑authenticate on every restart.

It works with **any MCP‑capable client** — ChatGPT, Claude, Cursor, and more —
because MCP is a transport standard, not a model. The LLM never sees your Google
credentials; the server holds them and returns plain results.

Built and maintained by [Webloom](https://webloom.fr). 🌱

---

## How it differs from the official Google server

| | Official `analytics-mcp` | This server |
|---|---|---|
| Transport | Local `stdio` (one machine) | Remote **Streamable HTTP** (`/mcp`) |
| Auth | Application Default Credentials (single identity) | **OAuth 2.1 per user**, each with their own Google login |
| Users | One developer, one laptop | **Multi‑user**, hosted |
| Persistence | none | Refresh tokens persisted on disk (survive restarts) |
| Clients documented | Gemini, Claude Code | **ChatGPT, Claude, Cursor** (+ any MCP client) |

---

## Tools 🛠️

Powered by the
[Google Analytics Admin API](https://developers.google.com/analytics/devguides/config/admin/v1)
and
[Data API](https://developers.google.com/analytics/devguides/reporting/data/v1).

| Tool | What it does |
|---|---|
| `get_account_summaries` | List every GA4 account + property you can access. |
| `find_property_by_domain` | **Find the property id from a domain or URL** (e.g. `webloom.fr`). Matches against each property's web data‑stream URL. |
| `get_property_details` | Details for one property (time zone, currency, industry…). |
| `list_google_ads_links` | Google Ads links for a property. |
| `get_custom_dimensions_and_metrics` | Custom dimensions/metrics defined on a property. |
| `run_report` | The main analytics tool — pick dimensions + metrics over a date range. Rich built‑in examples for traffic, channels, landing pages, revenue, events… |
| `run_realtime_report` | Live report over the last ~30 minutes. |

> Don't know a site's property id? Just ask in natural language — e.g.
> *"what were the top channels for webloom.fr last month?"* — and the assistant
> will call `find_property_by_domain` then `run_report` for you.

---

## Connect your MCP client 🔌

You need the server's MCP URL, which ends in **`/mcp`**:

```
https://YOUR-SERVER.onrender.com/mcp
```

The first time you connect, a browser window opens: **sign in with the Google
account that has access to your GA4 properties** and approve the read‑only
Analytics scope. That's it — your login is remembered afterwards.

### Cursor

Add the server to your MCP config — either the project file `.cursor/mcp.json`
or the global `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "google-analytics": {
      "url": "https://YOUR-SERVER.onrender.com/mcp"
    }
  }
}
```

Then open **Settings → Tools & MCP**, confirm `google-analytics` is listed, and
click to **authenticate** when prompted. Once it turns green, ask Cursor an
analytics question.

### Claude

**Claude Desktop / claude.ai** (requires a plan that supports custom
connectors):

1. **Settings → Connectors → Add custom connector**.
2. Name it `Google Analytics` and paste the URL
   `https://YOUR-SERVER.onrender.com/mcp`.
3. Click **Connect** and complete the Google sign‑in.

**Claude Code (CLI):**

```shell
claude mcp add --transport http google-analytics https://YOUR-SERVER.onrender.com/mcp
```

Run `/mcp` inside Claude Code to trigger authentication.

### ChatGPT

Requires a plan with **connectors / developer mode** (Plus, Pro, Business, or
Enterprise):

1. **Settings → Connectors** (enable **Developer mode** under Advanced if
   needed).
2. **Create / Add custom connector** → give it a name and paste the MCP server
   URL `https://YOUR-SERVER.onrender.com/mcp`.
3. Save, then **authenticate** with your Google account.
4. In a chat, enable the connector and ask your GA4 question.

---

## Try it out 🥼

Once connected, ask natural‑language questions:

```
What can the Google Analytics server do?
```

```
Find the GA4 property for webloom.fr.
```

```
What were the top acquisition channels for webloom.fr over the last 28 days?
```

```
Show daily active users and sessions for property 123456789 for the last 7 days.
```

```
What are the most popular events in my property over the last 180 days?
```

```
How many users are on the site right now, by country?
```

---

## Self‑hosting (Render) 🚀

The server is a standard Python ASGI app; any host works. Below is the Render
setup it's designed for.

### 1. Google Cloud setup

1. Create/select a Google Cloud project.
2. Enable both APIs:
   [Analytics Admin API](https://console.cloud.google.com/apis/library/analyticsadmin.googleapis.com)
   and
   [Analytics Data API](https://console.cloud.google.com/apis/library/analyticsdata.googleapis.com).
3. Configure the **OAuth consent screen** and add the scope
   `https://www.googleapis.com/auth/analytics.readonly`. While in *Testing*, add
   each user under **Test users**.
4. Create an OAuth **Web application** client. Add the authorized redirect URI:
   `https://YOUR-SERVER.onrender.com/oauth2callback`.

### 2. Render service

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn server_http:app --host 0.0.0.0 --port $PORT`
- **Add a persistent disk** mounted at **`/data`** (holds per‑user Google
  credentials + OAuth state so logins survive restarts).

### 3. Environment variables

| Variable | Value |
|---|---|
| `MCP_ENABLE_OAUTH21` | `true` |
| `GOOGLE_OAUTH_CLIENT_ID` | your OAuth web client id |
| `GOOGLE_OAUTH_CLIENT_SECRET` | your OAuth web client secret |
| `GA4_EXTERNAL_URL` | `https://YOUR-SERVER.onrender.com` (public HTTPS URL, no trailing slash) |
| `GOOGLE_MCP_CREDENTIALS_DIR` | `/data` |

Deploy, then point your client at `https://YOUR-SERVER.onrender.com/mcp`.

### Local use (single user, stdio)

For a quick local run without the OAuth server, provide a pre‑authorized token
or a service account and run:

```shell
pip install -r requirements.txt
python ga4_server.py
```

See `ga4_server.py` for the credential lookup order (`GA4_OAUTH_TOKEN_PATH`,
service account file, etc.).

---

## Security notes 🔒

- The LLM/client never receives your Google credentials — they stay server‑side.
- Refresh tokens and OAuth state live under `/data` with restrictive
  permissions; keep that disk private and never commit credential files.
- If you run **without** OAuth 2.1 and **without** a bearer token, the `/mcp`
  endpoint is unauthenticated — the server logs a loud warning on startup. For
  any remote deployment, keep `MCP_ENABLE_OAUTH21=true`.

---

## Credits

Made with care by [**Webloom**](https://webloom.fr) — [webloom.fr](https://webloom.fr).

Contributions welcome! See the [Contributing Guide](CONTRIBUTING.md).
