# Free public report portal (Render + Neon)

This mode keeps discovery, website auditing, AI, Gmail, and the administrator dashboard on your Windows computer. A small hosted FastAPI process serves the signed public report, affiliate redirect, opt-out routes, and one fixed shared email-logo asset:

```text
/r/{short-token}
/go/{short-token}
/u/{short-token}
/unsubscribe/{legacy-token}  # backward compatibility
/email-assets/leadflow-logo.png  # shared, cacheable, no recipient token
```

Both local LeadFlow and the hosted portal use the same Neon PostgreSQL database and the same `APP_SECRET`. That makes report links, click records, and opt-outs immediately visible to the local application without exposing Composio or Gmail credentials to Render. The public report uses a responsive professional layout with an executive summary, bounded score dimensions, severity findings, action plan, selected non-sensitive technical facts, optional disclosed hosting comparison and print styling.

## Important free-tier limitations

- Render's free web service can sleep after inactivity. The first report request after sleep may be slow.
- Neon and Render limits/pricing can change; review their current official pages before deployment.
- An `onrender.com` hostname is stable but less trustworthy than a future branded domain.
- Keep `SENDING_ENABLED=false` until the public report, affiliate redirect, opt-out, and shared database have all been tested.

## Recommended: one-command setup wizard

Run only:

```powershell
python .\start.py --setup-free-portal
```

The wizard opens the official Neon, GitHub, and Render browser pages; reads copied URLs from the clipboard; migrates PostgreSQL to Neon; creates a sanitized deployment repository; commits and pushes it to the private GitHub repository; securely copies only `DATABASE_URL` and `APP_SECRET` for Render; saves `PUBLIC_REPORT_BASE_URL`; validates portal health; and then starts local LeadFlow. Existing GitHub `main` history is fetched and preserved before updates, so reruns never require a manual pull or force push. On Windows, topmost graphical dialogs guide each Render paste and deploy action—no secret or confirmation text is entered into PowerShell. If a clean extracted folder has a new SQLite `.env`, the wizard scans sibling LeadFlow release folders, selects the newest reachable PostgreSQL/Neon configuration, backs up the temporary file, and recovers it automatically without printing values.

External providers still require you to create/authorize their accounts and click their consent/deploy buttons in the browser. `start.py` cannot legally accept third-party terms or authorize an account on your behalf. If Git for Windows is missing, the wizard opens its official installer page; install it and rerun the same command. Completed steps are resumable: a configured portal skips GitHub entirely, and an incomplete setup asks whether the GitHub repository already exists before offering to create another. If an existing Render service is unhealthy, the wizard goes directly to environment repair, recopies both values in an enforced order, triggers redeployment, and waits for `/health`. Only the public root service URL such as `https://leadflow-report-portal.onrender.com` is accepted; `dashboard.render.com/blueprint/...` addresses are detected and replaced rather than used in customer links.

The remaining sections document what the wizard performs and provide recovery details.

## Update portal source after a code-only deployment failure

If Render builds successfully but reports a missing installed Python module at startup, update only the existing private source repository:

```powershell
python .\start.py --update-public-portal
```

This command preserves remote Git history, changes no Neon/Render secrets, pushes the corrected source and shared logo, opens Render, and waits for the existing service to redeploy. Run it once for v1.14.0 so Gmail can load the logo without a MIME attachment.

## Repair an existing failed Render service

If `leadflow-report-portal` already exists (even with **Failed deploy**), do not create another Blueprint. Run:

```powershell
python .\start.py --repair-render-portal
```

This path skips Neon and GitHub, opens only the existing Render service, hands off the current local `DATABASE_URL` and `APP_SECRET` through Windows dialogs, triggers **Save and Deploy**, replaces any saved dashboard URL with the actual `.onrender.com` origin, and waits for `/health`.

## Emergency credential rotation

If a Neon connection string or `APP_SECRET` is ever pasted into chat, a ticket, terminal confirmation prompt, screenshot, or another untrusted location, treat it as compromised. Stop the wizard and run:

```powershell
python .\start.py --rotate-portal-credentials
```

The rotation wizard opens Neon so you can click **Reset password**, reads the newly copied Direct URL without shell pasting, refuses an unchanged URL, generates a new local `APP_SECRET`, updates only the existing Render service in an enforced order, replaces any dashboard URL with the actual public service origin, waits for health, and starts LeadFlow. Existing signed report and unsubscribe links become invalid, which is expected after rotating `APP_SECRET`.

## 1. Create the Neon database

1. Create a free Neon project. Select a PostgreSQL version compatible with your local PostgreSQL installation when offered.
2. Open **Connect** in Neon.
3. Turn **Connection pooling off** and click the copy icon beside the **direct connection string**.
4. Do not copy the browser address bar (`https://console.neon.tech/...`). The clipboard must contain text beginning with `postgresql://` (the wizard also accepts `DATABASE_URL='postgresql://...'` or `psql 'postgresql://...'`).
5. Do not post that URL in chat or commit it to Git. It contains the database password.

A Neon URL normally resembles:

```text
postgresql://USER:PASSWORD@HOST.neon.tech/DATABASE?sslmode=require&channel_binding=require
```

The provided migration script converts it to SQLAlchemy's `postgresql+psycopg` form automatically.

## 2. Copy current LeadFlow data to Neon

Stop LeadFlow with `Ctrl+C`. From the project folder run:

```powershell
.\.venv\Scripts\python.exe .\scripts\migrate_to_neon.py
```

When prompted:

1. Copy the Neon **direct** URL to your clipboard.
2. Return to PowerShell and press Enter.
3. Check the displayed source and target host/database names.
4. Type `MIGRATE TO NEON` exactly.

The script:

- discovers the PostgreSQL 18/17 command-line tools on Windows;
- creates a local custom-format backup without placing passwords in command arguments;
- restores the schema and records into Neon in one transaction;
- verifies the `users` and `leads` tables;
- backs up the previous `.env`;
- changes local `DATABASE_URL` to Neon;
- keeps `SENDING_ENABLED=false`;
- clears the clipboard and temporary password file.

The local PostgreSQL source is not modified. If migration fails, the dump is retained under `backups/`; protect it because it contains LeadFlow data.

Start local LeadFlow and verify the same lead count:

```powershell
python .\start.py
```

## 3. Put the project in a private Git repository

Render deploys from GitHub, GitLab, or Bitbucket. GitHub Desktop is the simplest Windows option:

1. Create a new **private** repository from the extracted `leadflow` folder.
2. Before publishing, confirm `.env`, `.env.before-*`, `backups/`, `data/`, and `.venv/` are absent from the commit list.
3. Publish the private repository.

The supplied `.gitignore` excludes those sensitive/generated files. Never override that exclusion.

## 4. Deploy the Render Blueprint

1. In Render, choose **New → Blueprint**.
2. Connect the private repository containing `render.yaml`.
3. Select the free plan if Render offers it for the web service.
4. Render asks for two secret values marked `sync: false`:
   - `DATABASE_URL`: the same Neon direct URL now stored in local `.env`.
   - `APP_SECRET`: exactly the same local `APP_SECRET`; changing it invalidates signed links.

Copy each value without printing it:

```powershell
.\.venv\Scripts\python.exe .\scripts\copy_env_value.py DATABASE_URL
```

Paste it into Render, then run:

```powershell
.\.venv\Scripts\python.exe .\scripts\copy_env_value.py APP_SECRET
```

Paste it into Render. Do not add Composio, Gmail, Google Maps, JWT, administrator, or sender mailbox credentials to the public service.

Deploy the Blueprint. `scripts/start_public_portal.py` automatically:

- normalizes the Neon URL for psycopg;
- runs Alembic migrations;
- verifies the schema;
- disables sending, scheduling, metrics, and workers;
- starts only `app.public_portal` on Render's assigned port;
- disables Uvicorn access logs so signed tokens are not written to ordinary access logs.

## 5. Point local report links to Render

After deployment, Render displays a stable URL such as:

```text
https://leadflow-report-portal.onrender.com
```

In local `.env`, set the dedicated report origin while leaving the local dashboard origin independent:

```dotenv
PUBLIC_REPORT_BASE_URL=https://YOUR-ACTUAL-RENDER-URL.onrender.com
SENDING_ENABLED=false
```

Restart local LeadFlow:

```powershell
python .\start.py
```

On a live lead, use **Open public score page** and test from a phone on mobile data. The URL must use the Render hostname, not `127.0.0.1`.

## 6. Required validation before sending

1. `/health` returns `status: ok`.
2. A signed `/r/{token}` report loads from outside your home network.
3. The report has `noindex` and no tracking pixel.
4. `/go/{token}` records a privacy-hashed click and redirects to the exact affiliate URL.
5. A test opt-out POST cancels future messages in the shared Neon database.
6. Local LeadFlow immediately shows the click/opt-out after refresh.
7. Render contains no Composio/Gmail credentials.
8. `SENDING_ENABLED` remains false until all checks pass.

## Rollback

The migration script creates `.env.before-neon-TIMESTAMP`. To return local LeadFlow to the original PostgreSQL server, stop LeadFlow and restore that file as `.env`. The original local PostgreSQL database remains unchanged.
