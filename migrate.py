"""Run once to create database tables.

Usage:
    cd backend && python migrate.py
"""

from dotenv import load_dotenv

load_dotenv()

from db import init_db, get_connection, put_connection, close_db
from knowledge import ensure_knowledge_schema
import template_library  # registers all templates into TEMPLATE_REGISTRY

SQL = """
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL DEFAULT 'Local User',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS projects (
    id          SERIAL PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(id),
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS assemblies (
    id          SERIAL PRIMARY KEY,
    project_id  INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS parts (
    id              SERIAL PRIMARY KEY,
    assembly_id     INT NOT NULL REFERENCES assemblies(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    cadquery_script TEXT NOT NULL,
    position_x      DOUBLE PRECISION NOT NULL DEFAULT 0,
    position_y      DOUBLE PRECISION NOT NULL DEFAULT 0,
    position_z      DOUBLE PRECISION NOT NULL DEFAULT 0,
    rotation_x      DOUBLE PRECISION NOT NULL DEFAULT 0,
    rotation_y      DOUBLE PRECISION NOT NULL DEFAULT 0,
    rotation_z      DOUBLE PRECISION NOT NULL DEFAULT 0,
    material        TEXT NOT NULL DEFAULT 'steel',
    color           TEXT NOT NULL DEFAULT '#888888',
    visible         BOOLEAN NOT NULL DEFAULT true,
    locked          BOOLEAN NOT NULL DEFAULT false,
    bbox_min_x      DOUBLE PRECISION,
    bbox_min_y      DOUBLE PRECISION,
    bbox_min_z      DOUBLE PRECISION,
    bbox_max_x      DOUBLE PRECISION,
    bbox_max_y      DOUBLE PRECISION,
    bbox_max_z      DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          SERIAL PRIMARY KEY,
    project_id  INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    script      TEXT,
    part_id     INT REFERENCES parts(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS operations (
    id            SERIAL PRIMARY KEY,
    part_id       INT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    sequence      INT NOT NULL,
    operation     TEXT NOT NULL,
    parameters    JSONB NOT NULL,
    parent_op_id  INT REFERENCES operations(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_operations_part_seq ON operations(part_id, sequence);

-- Scale columns (Phase 1B)
DO $$ BEGIN
    ALTER TABLE parts ADD COLUMN scale_x DOUBLE PRECISION NOT NULL DEFAULT 1;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE parts ADD COLUMN scale_y DOUBLE PRECISION NOT NULL DEFAULT 1;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE parts ADD COLUMN scale_z DOUBLE PRECISION NOT NULL DEFAULT 1;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Part hierarchy columns (Phase 2A)
DO $$ BEGIN
    ALTER TABLE parts ADD COLUMN parent_part_id INT REFERENCES parts(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE parts ADD COLUMN part_type TEXT NOT NULL DEFAULT 'body';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE parts ADD COLUMN sort_order INT NOT NULL DEFAULT 0;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE parts ADD COLUMN sketch_json JSONB;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE parts ADD COLUMN sketch_plane JSONB;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Parametric template columns
DO $$ BEGIN
    ALTER TABLE parts ADD COLUMN parametric_type TEXT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TABLE parts ADD COLUMN parametric_params JSONB;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- STEP import column
ALTER TABLE parts ADD COLUMN IF NOT EXISTS step_data BYTEA;

-- Auth tables (Phase 3A)
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url TEXT;

CREATE TABLE IF NOT EXISTS organizations (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS org_members (
    id          SERIAL PRIMARY KEY,
    org_id      INT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'member',
    invited_by  INT REFERENCES users(id),
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(org_id, user_id)
);

CREATE TABLE IF NOT EXISTS teams (
    id          SERIAL PRIMARY KEY,
    org_id      INT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team_members (
    id          SERIAL PRIMARY KEY,
    team_id     INT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(team_id, user_id)
);

CREATE TABLE IF NOT EXISTS project_shares (
    id          SERIAL PRIMARY KEY,
    project_id  INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     INT REFERENCES users(id) ON DELETE CASCADE,
    team_id     INT REFERENCES teams(id) ON DELETE CASCADE,
    org_id      INT REFERENCES organizations(id) ON DELETE CASCADE,
    permission  TEXT NOT NULL DEFAULT 'view',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (user_id IS NOT NULL OR team_id IS NOT NULL OR org_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS invitations (
    id          SERIAL PRIMARY KEY,
    org_id      INT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'member',
    token       TEXT UNIQUE NOT NULL,
    invited_by  INT NOT NULL REFERENCES users(id),
    accepted_at TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    ALTER TABLE projects ADD COLUMN org_id INT REFERENCES organizations(id);
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Part script snapshots (for AI edit undo)
CREATE TABLE IF NOT EXISTS part_script_snapshots (
    id         SERIAL PRIMARY KEY,
    part_id    INT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    script     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT 'ai_edit',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_snapshots_part ON part_script_snapshots(part_id, created_at DESC);

-- Seed a default user for single-user mode
INSERT INTO users (email, name) VALUES ('local@localhost', 'Local User')
ON CONFLICT (email) DO NOTHING;

-- ========================================================================
-- Parametric Feature System (Phase 4 — Fusion 360-style feature tree)
-- ========================================================================

-- Named parameters (like Fusion's "Change Parameters" dialog)
CREATE TABLE IF NOT EXISTS parameters (
    id          SERIAL PRIMARY KEY,
    assembly_id INT REFERENCES assemblies(id) ON DELETE CASCADE,
    part_id     INT REFERENCES parts(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    expression  TEXT NOT NULL,
    value       DOUBLE PRECISION,
    description TEXT NOT NULL DEFAULT '',
    unit        TEXT NOT NULL DEFAULT 'mm',
    group_name  TEXT NOT NULL DEFAULT 'Parameters',
    sort_order  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_parameters_assembly ON parameters(assembly_id);
CREATE INDEX IF NOT EXISTS idx_parameters_part ON parameters(part_id);

-- Feature tree — the real parametric history
CREATE TABLE IF NOT EXISTS features (
    id            SERIAL PRIMARY KEY,
    part_id       INT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    feature_type  TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    sequence      INT NOT NULL,
    params        JSONB NOT NULL DEFAULT '{}',
    suppressed    BOOLEAN NOT NULL DEFAULT false,
    error_message TEXT,
    feature_hash  TEXT,
    source        TEXT NOT NULL DEFAULT 'manual',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_features_part_seq ON features(part_id, sequence);

-- 2D sketches (constraint-based, for Phase 5)
CREATE TABLE IF NOT EXISTS sketches (
    id              SERIAL PRIMARY KEY,
    part_id         INT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    name            TEXT NOT NULL DEFAULT 'Sketch',
    plane_type      TEXT NOT NULL DEFAULT 'XY',
    plane_origin    JSONB DEFAULT '[0,0,0]',
    plane_normal    JSONB DEFAULT '[0,0,1]',
    plane_x_dir     JSONB,
    entities        JSONB NOT NULL DEFAULT '[]',
    constraints     JSONB NOT NULL DEFAULT '[]',
    dof             INT,
    solver_status   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sketches_part ON sketches(part_id);

-- Feature rebuild cache
CREATE TABLE IF NOT EXISTS feature_cache (
    id              SERIAL PRIMARY KEY,
    part_id         INT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    up_to_sequence  INT NOT NULL,
    cumulative_hash TEXT NOT NULL,
    mesh_data       JSONB NOT NULL,
    brep_data       BYTEA,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(part_id, cumulative_hash)
);
CREATE INDEX IF NOT EXISTS idx_feature_cache_part ON feature_cache(part_id, up_to_sequence);

-- Add feature_tree_mode flag to parts
ALTER TABLE parts ADD COLUMN IF NOT EXISTS feature_tree_mode BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE parts ADD COLUMN IF NOT EXISTS feature_hash TEXT;

-- ========================================================================
-- Version Control System — git-like checkpoints, branches, merging
-- ========================================================================

CREATE TABLE IF NOT EXISTS part_branches (
    id          SERIAL PRIMARY KEY,
    part_id     INT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    name        TEXT NOT NULL DEFAULT 'main',
    head_id     INT,   -- FK added after part_versions exists
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(part_id, name)
);
CREATE INDEX IF NOT EXISTS idx_part_branches_part ON part_branches(part_id);

CREATE TABLE IF NOT EXISTS part_versions (
    id          SERIAL PRIMARY KEY,
    part_id     INT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    parent_id   INT REFERENCES part_versions(id) ON DELETE SET NULL,
    branch_id   INT NOT NULL REFERENCES part_branches(id) ON DELETE CASCADE,
    script      TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    author_type TEXT NOT NULL DEFAULT 'human',   -- 'human' | 'ai' | 'system'
    author_info TEXT NOT NULL DEFAULT '',
    auto        BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_part_versions_part ON part_versions(part_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_part_versions_branch ON part_versions(branch_id, created_at DESC);

-- Add head_id FK now that part_versions exists
DO $$ BEGIN
    ALTER TABLE part_branches ADD CONSTRAINT fk_branch_head
        FOREIGN KEY (head_id) REFERENCES part_versions(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Track which branch each part is currently on
ALTER TABLE parts ADD COLUMN IF NOT EXISTS active_branch_id INT;
DO $$ BEGIN
    ALTER TABLE parts ADD CONSTRAINT fk_parts_active_branch
        FOREIGN KEY (active_branch_id) REFERENCES part_branches(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Per-user branch tracking: each user sees their own active branch per part
CREATE TABLE IF NOT EXISTS user_branch_state (
    id              SERIAL PRIMARY KEY,
    user_id         INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    part_id         INT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    branch_id       INT NOT NULL REFERENCES part_branches(id) ON DELETE CASCADE,
    checked_out_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, part_id)
);
CREATE INDEX IF NOT EXISTS idx_user_branch_state_part ON user_branch_state(part_id);
CREATE INDEX IF NOT EXISTS idx_user_branch_state_user ON user_branch_state(user_id);

-- Track who created each branch and whether it has been merged
ALTER TABLE part_branches ADD COLUMN IF NOT EXISTS created_by INT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE part_branches ADD COLUMN IF NOT EXISTS is_merged BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE part_branches ADD COLUMN IF NOT EXISTS merged_at TIMESTAMPTZ;

-- Sentinel AI user for AI branch operations
INSERT INTO users (email, name) VALUES ('ai@system.internal', 'AI Assistant')
ON CONFLICT (email) DO NOTHING;

-- ========================================================================
-- Template Library (Canva-like template system)
-- ========================================================================

CREATE TABLE IF NOT EXISTS template_categories (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    icon        TEXT,
    sort_order  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS templates (
    id            SERIAL PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    category_id   INT REFERENCES template_categories(id),
    tags          TEXT[] DEFAULT '{}',
    param_schema  JSONB NOT NULL DEFAULT '{}',
    generator_key TEXT NOT NULL,
    is_featured   BOOLEAN NOT NULL DEFAULT false,
    is_published  BOOLEAN NOT NULL DEFAULT true,
    use_count     INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_templates_category ON templates(category_id);
CREATE INDEX IF NOT EXISTS idx_templates_slug ON templates(slug);
CREATE INDEX IF NOT EXISTS idx_templates_tags ON templates USING GIN(tags);

-- ========================================================================
-- Shareable Assembly Previews (public share links)
-- ========================================================================

CREATE TABLE IF NOT EXISTS assembly_shares (
    id          SERIAL PRIMARY KEY,
    assembly_id INT NOT NULL REFERENCES assemblies(id) ON DELETE CASCADE,
    token       TEXT UNIQUE NOT NULL,
    created_by  INT REFERENCES users(id),
    is_active   BOOLEAN NOT NULL DEFAULT true,
    view_count  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_assembly_shares_token ON assembly_shares(token);

-- Add user_id to chat_messages if missing
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id);
-- Add mesh_cache and script_hash to parts if missing
ALTER TABLE parts ADD COLUMN IF NOT EXISTS mesh_cache JSONB;
ALTER TABLE parts ADD COLUMN IF NOT EXISTS script_hash TEXT;
"""


def migrate():
    init_db()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SQL)
        conn.commit()
        ensure_knowledge_schema()
        template_library.seed_templates(conn)
        print("Migration complete — all tables created.")
    finally:
        put_connection(conn)
        close_db()


if __name__ == "__main__":
    migrate()
