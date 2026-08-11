# Database migrations

The v0.2 chain intentionally starts from a clean baseline. It does not upgrade or preserve the
v0.1 public-schema database.

After the v0.2.0 baseline is accepted, migrations are append-only. Never edit a revision that has
been used by a published v0.2 artifact. Create a new revision for every schema change so the
engine and schema revision remain traceable.

Use the isolated test database for destructive migration tests:

```powershell
docker compose up -d postgres-test
$testDatabaseUrl = `
  "postgresql+psycopg://style_rotation:style_rotation@localhost:55432/style_rotation_test"
$env:STYLE_ROTATION_TEST_DATABASE_URL = $testDatabaseUrl
$env:STYLE_ROTATION_DATABASE_URL = $testDatabaseUrl
style-rotation db reset --confirm-database style_rotation_test
pytest -m integration
```

`style-rotation db reset` is restricted to localhost, local/test environments, project-scoped
database names, and an exact database-name confirmation. It drops the target database's schemas,
including the old `public` contents, before applying the clean v0.2 head.
