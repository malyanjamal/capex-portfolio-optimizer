# Deploying the live demo

The dashboard runs on **Streamlit Community Cloud** and reads from a free hosted PostgreSQL database on **Neon**. Neither needs a credit card. Setup takes about 15 minutes.

```
Your laptop ──(one-time load)──► Neon PostgreSQL ◄──(reads)── Streamlit Community Cloud ◄── visitors
```

## 1. Create the hosted database (Neon)

1. Sign up at [neon.com](https://neon.com/signup). Signing in with GitHub is quickest.
2. Create a project:
   - **Name:** `capex`
   - **Postgres version:** 16 or newer
   - **Region:** **AWS US East (N. Virginia)**. Streamlit Community Cloud runs in the US, so this keeps the app and the database close together.
3. On the project dashboard, click **Connect** and copy the connection string. It looks like this:
   ```
   postgresql://neondb_owner:xxxxxxxx@ep-something-123456.us-east-1.aws.neon.tech/neondb?sslmode=require
   ```
   Keep it private, because it contains the password.

The free plan suspends the database after 5 minutes without traffic. The first visitor after a pause waits a second or two while it wakes up.

## 2. Load the data into Neon (from your laptop, one time)

In the VS Code terminal, with `(.venv)` active, point the pipeline at Neon for this terminal session only. Your `.env` stays unchanged.

```powershell
$env:DATABASE_URL = "postgresql://neondb_owner:xxxxxxxx@ep-....neon.tech/neondb?sslmode=require"
python -m capex.pipeline all
Remove-Item Env:DATABASE_URL
```

The output should end with `Business case written to ...`. You can confirm in Neon's **Tables** view: there is now a `capex` schema containing 12 tables.

You can paste the plain `postgresql://` string exactly as Neon gives it. The project converts it to the psycopg 3 driver automatically.

## 3. Deploy the app (Streamlit Community Cloud)

1. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub, and click **Create app**.
2. Choose **"Yup, I have an app"** and fill in:
   - **Repository:** `malyanjamal/capex-portfolio-optimizer`
   - **Branch:** `main`
   - **Main file path:** `app/streamlit_app.py`
   - **App URL:** for example `capex-optimizer`, which gives `capex-optimizer.streamlit.app`
3. Open **Advanced settings**:
   - **Python version:** 3.12
   - **Secrets:** paste the following, with your own Neon string:
     ```toml
     DATABASE_URL = "postgresql://neondb_owner:xxxxxxxx@ep-....neon.tech/neondb?sslmode=require"
     DEMO_MODE = "1"
     ```
   - Click **Save**.
4. Click **Deploy**. The first build installs the dependencies and takes a few minutes.

`DEMO_MODE = "1"` shows a "live demo" banner with the GitHub link and turns off "Save run to database". Visitors can still change every assumption and re-optimise, but they can't write to your database.

## 4. Link it

Add the demo link at the top of the README, under the title:

```markdown
**[▶ Live demo](https://capex-optimizer.streamlit.app)** · [Sample business case (PDF)](reports/sample_business_case.pdf)
```

You can edit the README directly on GitHub with the ✏️ icon, or edit it locally and `git push`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| App shows "Could not connect to PostgreSQL" | The secret is missing or mistyped. In Streamlit, go to **⋮ → Settings → Secrets**, check the value, and reboot the app. |
| `relation "capex.project" does not exist` | Step 2 was skipped or ran against a different database. Rerun it with the Neon string. |
| Build fails while installing packages | Check that Python 3.12 is selected in Advanced settings, then reboot. |
| App is asleep | Community Cloud sleeps apps without traffic. Visitors click "Yes, get this app back up" and it starts again in under a minute. |
