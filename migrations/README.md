# Database migrations

Migrations are append-only. Never edit a migration that has been used by a formal run.
Create a new migration for every schema change so `engine_version` remains reproducible.

