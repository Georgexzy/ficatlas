# Google Search Console access for FicAtlas

`tools/search_console.py` connects this machine to the Google account that owns
the FicAtlas Search Console property. It uses Google's desktop OAuth flow with
PKCE and a loopback callback. The default permission is read-only. It does not
run in the public website or background worker.

## One-time Google setup

1. In [Google Cloud Console](https://console.cloud.google.com/), select or
   create a project you control. Enable **Google Search Console API** in its
   API Library.
2. Configure the Google Auth Platform consent screen if prompted. For an
   external app in Testing, add the Google account you use for Search Console
   as a test user.
3. In **Google Auth Platform → Clients**, create an OAuth client of type
   **Desktop app**. Download its JSON file. Do not create an API key or a Web
   application client.
4. Put the JSON on this host outside the repository:

   ```bash
   install -d -m 700 ~/.config/ficatlas/search-console
   install -m 600 ~/Downloads/client_secret_*.json \
     ~/.config/ficatlas/search-console/client_secret.json
   ```

   Adjust the source filename if your browser used another name. Do not paste
   the JSON or any token into chat, `.env`, or Git.

## Authorize and check

From `/home/george/ficatlas`:

```bash
python3 tools/search_console.py status
python3 tools/search_console.py auth
python3 tools/search_console.py properties
python3 tools/search_console.py sitemaps
python3 tools/search_console.py inspect https://ficatlas.com/
python3 tools/search_console.py performance --days 28 --dimension query
```

`auth` prints a Google URL and tries to open it in the local browser. Sign in
with the Google account that has access to `ficatlas.com`. The browser redirects
to a temporary listener on `127.0.0.1`; the listener closes after the response
or a five-minute timeout. If the browser is on a different computer, run
`auth --port 8765 --no-browser` and forward that port over SSH before opening
the printed URL, for example `ssh -L 8765:127.0.0.1:8765 george@HOST`.

The default property is `sc-domain:ficatlas.com`. Run `properties` first to
confirm its exact name. If Google lists a URL-prefix property instead, pass
`--property 'https://ficatlas.com/'` **before** the command name.

The token is stored at `~/.local/share/ficatlas/search-console/token.json`
with mode `0600`. Its parent directory is mode `0700`. Access tokens refresh
automatically. If Google revokes or expires the refresh token, repeat `auth
--reauthorize`. Remove access in your Google account's connected-app settings
and delete the local token to disconnect.

## Changes

When a specific Search Console action is needed, grant write scope separately:

```bash
python3 tools/search_console.py auth --write --reauthorize
python3 tools/search_console.py submit-sitemap https://ficatlas.com/sitemap.xml
python3 tools/search_console.py submit-sitemap https://ficatlas.com/sitemap.xml --apply
```

The first submit command previews the action. `--apply` sends it. Google also
checks the signed-in account's property role; an OAuth write scope alone does
not confer ownership. Google requires property owner access to submit a sitemap
in Search Console. For a handful of pages, request indexing through Search
Console's URL Inspection UI; Google's separate Indexing API is restricted to
job posting and livestream video pages.

References: [Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app),
[Search Console API scopes](https://developers.google.com/webmaster-tools/v1/how-tos/authorizing),
[Search Console API methods](https://developers.google.com/webmaster-tools/v1/api_reference_index),
[sitemap permissions](https://support.google.com/webmasters/answer/7451001).
