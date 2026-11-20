-- DOLAB PostgreSQL bootstrap (plan 4.10.1د, decision 20).
--
-- Run once per database by the infrastructure bootstrap identity (a superuser, or the managed
-- service's admin role), never by the API or the deploy job:
--
--     psql "$BOOTSTRAP_URL" -v ON_ERROR_STOP=1 -f dbtools/bootstrap.sql
--
-- Safe to run again: it creates what is missing and resets attributes, memberships and grants
-- to the values below. It sets no passwords; the infrastructure sets those separately.
--
-- Roles (cluster-wide, all dedicated to DOLAB):
--   dolab_schema_owner  NOLOGIN. Owns the dolab schema and every ordinary object in it.
--   dolab_deleter       NOLOGIN. Will own the SECURITY DEFINER deletion functions (step C).
--   dolab_deploy        LOGIN, NOINHERIT. Runs migrations. May only SET ROLE to the two above.
--   dolab_app           LOGIN. The API. Table rights come from the migrations, table by table.
--   dolab_reconciler    LOGIN, CONNECTION LIMIT 2. The reconciliation job (step C).

\set QUIET on

DO $roles$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('dolab_schema_owner', false, -1),
        ('dolab_deleter',      false, -1),
        ('dolab_deploy',       true,  -1),
        ('dolab_app',          true,  -1),
        ('dolab_reconciler',   true,   2)
    ) AS v(name, can_login, conn_limit)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.name) THEN
            EXECUTE format('CREATE ROLE %I', r.name);
        END IF;
        EXECUTE format(
            'ALTER ROLE %I %s NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT %s',
            r.name, CASE WHEN r.can_login THEN 'LOGIN' ELSE 'NOLOGIN' END, r.conn_limit);
    END LOOP;
END
$roles$;

-- Memberships. Only dolab_deploy is a member of anything, and only to switch identity.
DO $members$
DECLARE
    m record;
BEGIN
    -- Drop any membership between DOLAB roles that is not the two allowed below.
    FOR m IN
        SELECT g.rolname AS granted, u.rolname AS member
        FROM pg_auth_members am
        JOIN pg_roles g ON g.oid = am.roleid
        JOIN pg_roles u ON u.oid = am.member
        WHERE u.rolname IN ('dolab_schema_owner', 'dolab_deleter', 'dolab_deploy', 'dolab_app', 'dolab_reconciler')
          AND NOT (u.rolname = 'dolab_deploy' AND g.rolname IN ('dolab_schema_owner', 'dolab_deleter')
                   AND am.set_option AND NOT am.inherit_option AND NOT am.admin_option)
    LOOP
        EXECUTE format('REVOKE %I FROM %I CASCADE', m.granted, m.member);
    END LOOP;
END
$members$;

GRANT dolab_schema_owner TO dolab_deploy WITH INHERIT FALSE, SET TRUE, ADMIN FALSE;
GRANT dolab_deleter      TO dolab_deploy WITH INHERIT FALSE, SET TRUE, ADMIN FALSE;

-- Database: nobody connects or makes temporary tables unless named here.
DO $database$
BEGIN
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO dolab_app, dolab_deploy, dolab_reconciler', current_database());
    -- Settings live on the role in this database only.
    EXECUTE format('ALTER ROLE dolab_app IN DATABASE %I SET search_path = pg_catalog, dolab', current_database());
    EXECUTE format('ALTER ROLE dolab_reconciler IN DATABASE %I SET search_path = pg_catalog, dolab', current_database());
    EXECUTE format('ALTER ROLE dolab_reconciler IN DATABASE %I SET statement_timeout = ''30s''', current_database());
    -- Migrations name every object in full; dolab must not be the default schema for Alembic.
    EXECUTE format('ALTER ROLE dolab_deploy IN DATABASE %I SET search_path = pg_catalog', current_database());
END
$database$;

-- The public schema is not used. Nobody but its owner may use it.
REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- The dolab schema.
CREATE SCHEMA IF NOT EXISTS dolab AUTHORIZATION dolab_schema_owner;
ALTER SCHEMA dolab OWNER TO dolab_schema_owner;
REVOKE ALL ON SCHEMA dolab FROM PUBLIC, dolab_app, dolab_reconciler, dolab_deleter, dolab_deploy;
GRANT USAGE ON SCHEMA dolab TO dolab_app, dolab_reconciler, dolab_deleter;

-- Default privileges for objects created later, set for each creating role. PostgreSQL grants
-- EXECUTE on functions and USAGE on types to PUBLIC by default; a schema-scoped revoke cannot
-- remove that, so it is revoked for the whole role (both roles are DOLAB-only).
ALTER DEFAULT PRIVILEGES FOR ROLE dolab_schema_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE dolab_schema_owner REVOKE USAGE ON TYPES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE dolab_deleter REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE dolab_deleter REVOKE USAGE ON TYPES FROM PUBLIC;
