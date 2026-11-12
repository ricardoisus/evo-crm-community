-- Before setting passwords: issue these SETs as separate protocol messages in
-- the same DB session, confirm them, then issue the password DDL separately.
-- Never combine SET and a password in one psql -c / multi-statement request.
SET log_statement = 'none';
SET log_min_duration_statement = -1;
-- Operator binds passwords in memory; never put passwords in this file.
-- Dedicated DB only. Execute roles/database as DBA, outside a transaction.
CREATE ROLE vetorial_agent_owner LOGIN; -- ephemeral migration password supplied privately
CREATE ROLE vetorial_agent_app LOGIN; -- runtime password supplied privately
CREATE DATABASE vetorial_agente OWNER vetorial_agent_owner;
REVOKE ALL ON DATABASE vetorial_agente FROM PUBLIC;
GRANT CONNECT ON DATABASE vetorial_agente TO vetorial_agent_app;
-- Connect to vetorial_agente as DBA before running the image's migrate command:
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO vetorial_agent_owner;
GRANT USAGE ON SCHEMA public TO vetorial_agent_app;
-- AFTER migration, as DBA in vetorial_agente:
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO vetorial_agent_app;
ALTER ROLE vetorial_agent_owner NOLOGIN PASSWORD NULL;
