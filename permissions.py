"""Permission checking for projects, organizations, and teams."""

from __future__ import annotations

from db import get_connection, put_connection


def get_user_id_for_request(user) -> int:
    """Get user ID — supports both authenticated users and legacy single-user mode."""
    if user is None:
        return 1  # Legacy single-user mode (DEFAULT_USER_ID)
    return user.id


def can_view_project(user_id: int, project_id: int) -> bool:
    """Check if user can view a project (owner, or shared via user/team/org)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Owner check
            cur.execute("SELECT user_id FROM projects WHERE id = %s", (project_id,))
            row = cur.fetchone()
            if not row:
                return False
            if row[0] == user_id:
                return True

            # Shared directly with user
            cur.execute(
                "SELECT id FROM project_shares WHERE project_id = %s AND user_id = %s",
                (project_id, user_id),
            )
            if cur.fetchone():
                return True

            # Shared via team the user belongs to
            cur.execute(
                """SELECT ps.id FROM project_shares ps
                   JOIN team_members tm ON tm.team_id = ps.team_id
                   WHERE ps.project_id = %s AND tm.user_id = %s""",
                (project_id, user_id),
            )
            if cur.fetchone():
                return True

            # Shared via org the user belongs to
            cur.execute(
                """SELECT ps.id FROM project_shares ps
                   JOIN org_members om ON om.org_id = ps.org_id
                   WHERE ps.project_id = %s AND om.user_id = %s""",
                (project_id, user_id),
            )
            if cur.fetchone():
                return True

            return False
    finally:
        put_connection(conn)


def can_edit_project(user_id: int, project_id: int) -> bool:
    """Check if user can edit a project (owner, or shared with edit/admin permission)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Owner check
            cur.execute("SELECT user_id FROM projects WHERE id = %s", (project_id,))
            row = cur.fetchone()
            if not row:
                return False
            if row[0] == user_id:
                return True

            # Shared directly with edit+ permission
            cur.execute(
                "SELECT id FROM project_shares WHERE project_id = %s AND user_id = %s AND permission IN ('edit', 'admin')",
                (project_id, user_id),
            )
            if cur.fetchone():
                return True

            # Shared via team with edit+ permission
            cur.execute(
                """SELECT ps.id FROM project_shares ps
                   JOIN team_members tm ON tm.team_id = ps.team_id
                   WHERE ps.project_id = %s AND tm.user_id = %s AND ps.permission IN ('edit', 'admin')""",
                (project_id, user_id),
            )
            if cur.fetchone():
                return True

            return False
    finally:
        put_connection(conn)


def is_org_member(user_id: int, org_id: int) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM org_members WHERE org_id = %s AND user_id = %s",
                (org_id, user_id),
            )
            return cur.fetchone() is not None
    finally:
        put_connection(conn)


def is_org_admin(user_id: int, org_id: int) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM org_members WHERE org_id = %s AND user_id = %s AND role IN ('admin', 'owner')",
                (org_id, user_id),
            )
            return cur.fetchone() is not None
    finally:
        put_connection(conn)
