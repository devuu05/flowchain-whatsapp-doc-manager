# Rollback

1. **Application**: Render → service → *Deploys* → choose the last good deploy → *Rollback*
   (or redeploy the previous git commit). Images are immutable per commit.
2. **Database**: releases only add backward-compatible migrations, so the previous app version
   runs against the newer schema. Only if a migration itself is faulty:
   `alembic downgrade -1` after taking a backup (never on destructive downgrades without restore plan).
3. **Configuration**: env var changes are versioned by Render deploys; revert the variable and redeploy.
4. **Integrations**: if a Wasender/Drive change broke processing, disconnect → reconnect with the
   previous credentials; retry dead jobs from *Failures*.
5. Verify: `/health/ready`, send "hi" from an authorized number, upload a synthetic PDF, search, select.
