"""Pydantic request / response schemas for the database-backed endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ── Projects ─────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    org_id: Optional[int] = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: str
    created_at: datetime
    assemblies: List[AssemblyResponse] = []
    org_id: Optional[int] = None
    org_name: Optional[str] = None


# ── Assemblies ───────────────────────────────────────────────────────────────

class AssemblyCreate(BaseModel):
    name: str
    description: str = ""


class AssemblyResponse(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    parts: List[PartResponse] = []


# ── Parts ────────────────────────────────────────────────────────────────────

class PartCreate(BaseModel):
    name: str
    description: str = ""
    cadquery_script: str
    position_x: float = 0
    position_y: float = 0
    position_z: float = 0
    material: str = "steel"
    color: str = "#888888"
    parent_part_id: Optional[int] = None
    part_type: str = "body"
    sort_order: int = 0
    sketch_json: Optional[Dict[str, Any]] = None
    sketch_plane: Optional[Dict[str, Any]] = None
    parametric_type: Optional[str] = None
    parametric_params: Optional[Dict[str, Any]] = None


class PartUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cadquery_script: Optional[str] = None
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    position_z: Optional[float] = None
    rotation_x: Optional[float] = None
    rotation_y: Optional[float] = None
    rotation_z: Optional[float] = None
    scale_x: Optional[float] = None
    scale_y: Optional[float] = None
    scale_z: Optional[float] = None
    material: Optional[str] = None
    color: Optional[str] = None
    visible: Optional[bool] = None
    locked: Optional[bool] = None
    parent_part_id: Optional[int] = None
    part_type: Optional[str] = None
    sort_order: Optional[int] = None
    sketch_json: Optional[Dict[str, Any]] = None
    sketch_plane: Optional[Dict[str, Any]] = None
    parametric_type: Optional[str] = None
    parametric_params: Optional[Dict[str, Any]] = None
    assembly_id: Optional[int] = None


class PartResponse(BaseModel):
    id: int
    assembly_id: int
    name: str
    description: str
    cadquery_script: str
    position_x: float
    position_y: float
    position_z: float
    rotation_x: float
    rotation_y: float
    rotation_z: float
    scale_x: float = 1
    scale_y: float = 1
    scale_z: float = 1
    material: str
    color: str
    visible: bool
    locked: bool
    bbox_min_x: Optional[float] = None
    bbox_min_y: Optional[float] = None
    bbox_min_z: Optional[float] = None
    bbox_max_x: Optional[float] = None
    bbox_max_y: Optional[float] = None
    bbox_max_z: Optional[float] = None
    parent_part_id: Optional[int] = None
    part_type: str = "body"
    sort_order: int = 0
    sketch_json: Optional[Dict[str, Any]] = None
    sketch_plane: Optional[Dict[str, Any]] = None
    parametric_type: Optional[str] = None
    parametric_params: Optional[Dict[str, Any]] = None


# ── Chat ─────────────────────────────────────────────────────────────────────

class ChatMessageResponse(BaseModel):
    id: int
    project_id: int
    role: str
    content: str
    script: Optional[str] = None
    part_id: Optional[int] = None
    created_at: datetime


class ProjectChatRequest(BaseModel):
    messages: List[Dict]
    assembly_id: Optional[int] = None
    image_base64: Optional[str] = None


# ── Operations ────────────────────────────────────────────────────────────────

class OperationCreate(BaseModel):
    operation: str
    parameters: Dict[str, Any]
    parent_op_id: Optional[int] = None


class OperationUpdate(BaseModel):
    parameters: Optional[Dict[str, Any]] = None


class OperationResponse(BaseModel):
    id: int
    part_id: int
    sequence: int
    operation: str
    parameters: Dict[str, Any]
    parent_op_id: Optional[int] = None


class ParameterUpdateRequest(BaseModel):
    param_name: str
    value: Any


class ParametricPartCreate(BaseModel):
    template: str
    name: str = ""
    params: Dict[str, Any] = {}


class ParametricUpdateRequest(BaseModel):
    parametric_params: Dict[str, Any]


# Rebuild forward refs so ProjectResponse / AssemblyResponse can use each other
AssemblyResponse.model_rebuild()
ProjectResponse.model_rebuild()
