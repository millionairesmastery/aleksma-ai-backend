"""Real-time collaboration: WebSocket room manager, presence, and sync."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class Participant:
    ws: Any
    user_id: str
    display_name: str
    color: str
    joined_at: float = field(default_factory=time.time)
    cursor_position: Optional[Dict] = None
    active_tool: Optional[str] = None
    selected_part_id: Optional[int] = None
    locked_part_id: Optional[int] = None


@dataclass
class Room:
    project_id: int
    participants: Dict[str, Participant] = field(default_factory=dict)
    part_locks: Dict[int, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


PARTICIPANT_COLORS = [
    '#818cf8', '#f472b6', '#34d399', '#fbbf24', '#fb923c',
    '#a78bfa', '#22d3ee', '#f87171', '#4ade80', '#e879f9',
]

_rooms: Dict[int, Room] = {}
_rebuild_timers: Dict[int, asyncio.TimerHandle] = {}

REBUILD_DEBOUNCE_MS = 300


def get_or_create_room(project_id: int) -> Room:
    if project_id not in _rooms:
        _rooms[project_id] = Room(project_id=project_id)
    return _rooms[project_id]


def remove_empty_room(project_id: int):
    room = _rooms.get(project_id)
    if room and not room.participants:
        del _rooms[project_id]


def assign_color(room: Room) -> str:
    used = {p.color for p in room.participants.values()}
    for c in PARTICIPANT_COLORS:
        if c not in used:
            return c
    return PARTICIPANT_COLORS[len(room.participants) % len(PARTICIPANT_COLORS)]


async def broadcast(room: Room, message: dict, exclude_id: Optional[str] = None):
    payload = json.dumps(message)
    dead = []
    for pid, participant in room.participants.items():
        if pid == exclude_id:
            continue
        try:
            await participant.ws.send_text(payload)
        except Exception:
            dead.append(pid)
    for pid in dead:
        await handle_disconnect(room, pid)


async def handle_connect(room: Room, ws, user_id: str, display_name: str) -> str:
    pid = str(uuid.uuid4())[:8]
    color = assign_color(room)
    room.participants[pid] = Participant(
        ws=ws, user_id=user_id, display_name=display_name, color=color,
    )

    presence_list = [
        {
            'id': p_id,
            'userId': p.user_id,
            'name': p.display_name,
            'color': p.color,
            'cursor': p.cursor_position,
            'activeTool': p.active_tool,
            'selectedPartId': p.selected_part_id,
            'lockedPartId': p.locked_part_id,
        }
        for p_id, p in room.participants.items()
    ]

    try:
        await ws.send_text(json.dumps({
            'type': 'welcome',
            'participantId': pid,
            'color': color,
            'presence': presence_list,
            'partLocks': {str(k): v for k, v in room.part_locks.items()},
        }))
    except Exception:
        pass

    await broadcast(room, {
        'type': 'participant_joined',
        'participantId': pid,
        'userId': user_id,
        'name': display_name,
        'color': color,
    }, exclude_id=pid)

    return pid


async def handle_disconnect(room: Room, pid: str):
    participant = room.participants.pop(pid, None)
    if not participant:
        return

    parts_to_unlock = [
        part_id for part_id, locker in room.part_locks.items() if locker == pid
    ]
    for part_id in parts_to_unlock:
        del room.part_locks[part_id]

    await broadcast(room, {
        'type': 'participant_left',
        'participantId': pid,
        'unlockedParts': parts_to_unlock,
    })


async def handle_message(room: Room, pid: str, data: dict):
    msg_type = data.get('type')
    participant = room.participants.get(pid)
    if not participant:
        return

    if msg_type == 'cursor_move':
        participant.cursor_position = data.get('position')
        await broadcast(room, {
            'type': 'cursor_update',
            'participantId': pid,
            'position': participant.cursor_position,
        }, exclude_id=pid)

    elif msg_type == 'tool_change':
        participant.active_tool = data.get('tool')
        await broadcast(room, {
            'type': 'tool_update',
            'participantId': pid,
            'tool': participant.active_tool,
        }, exclude_id=pid)

    elif msg_type == 'select_part':
        participant.selected_part_id = data.get('partId')
        await broadcast(room, {
            'type': 'selection_update',
            'participantId': pid,
            'partId': participant.selected_part_id,
        }, exclude_id=pid)

    elif msg_type == 'lock_part':
        part_id = data.get('partId')
        if part_id is not None:
            current_locker = room.part_locks.get(part_id)
            if current_locker is None or current_locker == pid:
                room.part_locks[part_id] = pid
                participant.locked_part_id = part_id
                await broadcast(room, {
                    'type': 'part_locked',
                    'participantId': pid,
                    'partId': part_id,
                    'name': participant.display_name,
                    'color': participant.color,
                })
            else:
                locker = room.participants.get(current_locker)
                try:
                    await participant.ws.send_text(json.dumps({
                        'type': 'lock_denied',
                        'partId': part_id,
                        'lockedBy': locker.display_name if locker else 'Unknown',
                    }))
                except Exception:
                    pass

    elif msg_type == 'unlock_part':
        part_id = data.get('partId')
        if part_id is not None and room.part_locks.get(part_id) == pid:
            del room.part_locks[part_id]
            participant.locked_part_id = None
            await broadcast(room, {
                'type': 'part_unlocked',
                'participantId': pid,
                'partId': part_id,
            })

    elif msg_type == 'operation_added':
        await broadcast(room, {
            'type': 'operation_added',
            'participantId': pid,
            'name': participant.display_name,
            'partId': data.get('partId'),
            'operation': data.get('operation'),
        }, exclude_id=pid)

    elif msg_type == 'part_created':
        await broadcast(room, {
            'type': 'part_created',
            'participantId': pid,
            'name': participant.display_name,
            'partId': data.get('partId'),
            'partName': data.get('partName'),
        }, exclude_id=pid)

    elif msg_type == 'part_updated':
        await broadcast(room, {
            'type': 'part_updated',
            'participantId': pid,
            'partId': data.get('partId'),
            'updates': data.get('updates'),
        }, exclude_id=pid)

    elif msg_type == 'part_deleted':
        part_id = data.get('partId')
        if part_id in room.part_locks:
            del room.part_locks[part_id]
        await broadcast(room, {
            'type': 'part_deleted',
            'participantId': pid,
            'partId': part_id,
        }, exclude_id=pid)

    elif msg_type == 'mesh_update':
        await broadcast(room, {
            'type': 'mesh_update',
            'participantId': pid,
            'partId': data.get('partId'),
        }, exclude_id=pid)

    elif msg_type == 'chat_message':
        await broadcast(room, {
            'type': 'chat_message',
            'participantId': pid,
            'name': participant.display_name,
            'color': participant.color,
            'content': data.get('content'),
            'isAI': data.get('isAI', False),
        })

    elif msg_type == 'ping':
        try:
            await participant.ws.send_text(json.dumps({'type': 'pong'}))
        except Exception:
            pass


# ── AI lock helpers ───────────────────────────────────────────────────────────

async def lock_part_for_ai(project_id: int, part_id: int) -> Optional[str]:
    """Claim a part lock on behalf of the AI.
    Always takes over (overrides human locks too).
    Returns the previous locker's pid so it can be restored on unlock, or None."""
    room = _rooms.get(project_id)
    if not room:
        return None  # No active room — proceed freely
    previous = room.part_locks.get(part_id)  # may be a human pid or None
    room.part_locks[part_id] = 'ai'
    await broadcast(room, {
        'type': 'part_locked',
        'participantId': 'ai',          # needed so collaboration.js stores id correctly
        'partId': part_id,
        'name': 'AI Assistant',
        'color': '#a855f7',
    })
    return previous


async def unlock_part_for_ai(project_id: int, part_id: int, restore_pid: Optional[str] = None) -> None:
    """Release the AI lock. If restore_pid is given, hand the lock back to that participant."""
    room = _rooms.get(project_id)
    if not room:
        return
    # Only release if AI still holds it (don't stomp a lock acquired after us)
    if room.part_locks.get(part_id) != 'ai':
        return
    if restore_pid and restore_pid in room.participants:
        # Restore the human's lock so the UI stays consistent
        participant = room.participants[restore_pid]
        room.part_locks[part_id] = restore_pid
        await broadcast(room, {
            'type': 'part_locked',
            'participantId': restore_pid,
            'partId': part_id,
            'name': participant.display_name,
            'color': participant.color,
        })
    else:
        del room.part_locks[part_id]
        await broadcast(room, {'type': 'part_unlocked', 'partId': part_id})


async def broadcast_mesh_update(project_id: int, part_id: int) -> None:
    """Tell all clients to refresh the mesh for a given part."""
    room = _rooms.get(project_id)
    if not room:
        return
    await broadcast(room, {'type': 'mesh_update', 'partId': part_id})
