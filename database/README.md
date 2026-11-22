# Database and Migration Conventions

- PostgreSQL is the LongGood operational system of record.
- Use UUID primary keys; UTC timestamptz; created_at/updated_at; optimistic version where concurrent edits are possible.
- Never store raw AML restricted details in general customer or CRM tables. Use restricted schema/database role and scoped references.
- Signed files live in encrypted versioned object storage; DB stores immutable object reference, SHA-256, signer, signature provider evidence and timestamps.
- Every integration event has unique provider/event ID and idempotency key.
- Never hard-delete contract, signature, publication, delivery, finance or audit history. Apply approved retention/legal-hold policies.
- Migrations must be reversible where safe; destructive changes require explicit ADR and data migration.
- Synthetic fixtures only in repository and non-production environments.
