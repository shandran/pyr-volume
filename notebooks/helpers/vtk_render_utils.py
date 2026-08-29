"""Utilities for saving VTK render snapshots and JSONL metadata logs.

The main entry point is intended for a notebook save cell that runs after an
interactive VTK render has been closed. It reuses the same actor dictionary and
mutable VTK camera object so manually adjusted camera state is preserved.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    import numpy as np
except ImportError:  # pragma: no cover - notebooks using this helper have numpy.
    np = None


SCHEMA_VERSION = 1
LOG_FILENAME = "vtk_render_log.jsonl"
MESH_PREFIX_TO_CATEGORY = {
    "astro_": "astro",
    "neuro_": "neuro",
    "vasc_": "vasc",
    "mito_": "mito",
}
CATEGORY_ORDER = ("astro", "neuro", "vasc", "mito")
SYNAPSE_ACTOR_KEYS = ("presyn_actor", "postsyn_actor")


def json_safe(value: Any) -> Any:
    """Convert common notebook values to JSON-compatible Python objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Path):
        return str(value)

    if np is not None:
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, np.ndarray):
            return json_safe(value.tolist())

    if isinstance(value, tuple):
        return [json_safe(item) for item in value]

    if isinstance(value, list):
        return [json_safe(item) for item in value]

    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}

    if hasattr(value, "__fspath__"):
        return str(value)

    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable by "
        "vtk_render_utils.json_safe. Provide a summary instead."
    )


def parse_mesh_actor_key(actor_key: str) -> dict[str, str] | None:
    """Parse a canonical mesh actor key into category and CAVE root ID."""
    for prefix, category in MESH_PREFIX_TO_CATEGORY.items():
        if actor_key.startswith(prefix):
            root_id = actor_key[len(prefix) :]
            if root_id:
                return {
                    "actor_key": actor_key,
                    "category": category,
                    "root_id": str(root_id),
                }
    return None


def parse_rendered_meshes(actor_dict: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return mesh records parsed from the actor dictionary's canonical keys."""
    mesh_records = []
    for actor_key in actor_dict:
        parsed = parse_mesh_actor_key(str(actor_key))
        if parsed is not None:
            mesh_records.append(parsed)
    return mesh_records


def rendered_categories(mesh_records: list[dict[str, str]]) -> list[str]:
    """Return rendered mesh categories in canonical filename/log order."""
    present = {record["category"] for record in mesh_records}
    return [category for category in CATEGORY_ORDER if category in present]


def ids_by_category(mesh_records: list[dict[str, str]]) -> dict[str, list[str]]:
    """Group rendered mesh root IDs by canonical category."""
    grouped = {category: [] for category in CATEGORY_ORDER}
    for record in mesh_records:
        grouped[record["category"]].append(str(record["root_id"]))
    return {category: grouped[category] for category in CATEGORY_ORDER if grouped[category]}


def detect_synapse_actors(actor_dict: Mapping[str, Any]) -> dict[str, bool]:
    """Report whether recognized synapse actor keys are present."""
    return {key: key in actor_dict for key in SYNAPSE_ACTOR_KEYS}


def serialize_camera(camera: Any) -> dict[str, Any]:
    """Serialize useful vtkCamera properties to plain JSON-compatible values."""
    getters = {
        "position": "GetPosition",
        "focal_point": "GetFocalPoint",
        "view_up": "GetViewUp",
        "clipping_range": "GetClippingRange",
        "view_angle": "GetViewAngle",
        "parallel_projection": "GetParallelProjection",
        "parallel_scale": "GetParallelScale",
        "distance": "GetDistance",
        "roll": "GetRoll",
        "window_center": "GetWindowCenter",
    }

    camera_record = {}
    for field_name, getter_name in getters.items():
        getter = getattr(camera, getter_name, None)
        if getter is None:
            camera_record[field_name] = None
            continue
        camera_record[field_name] = json_safe(getter())
    return camera_record


def build_render_filename(
    categories: list[str],
    synapses_rendered: bool,
    timestamp: str,
    extension: str = "png",
) -> str:
    """Build a pyr-prefixed render filename from rendered categories."""
    category_part = "_".join(categories) if categories else "render"
    synapse_part = "_syn" if synapses_rendered else ""
    return f"pyr_{category_part}{synapse_part}_{timestamp}.{extension}"


def unique_path(path: Path) -> Path:
    """Return path, or a suffixed variant, without overwriting existing files."""
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter:02d}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def save_vtk_render_snapshot(
    actor_dict: Mapping[str, Any],
    camera: Any,
    output_dir: str | Path,
    render_scale: int | float,
    notebook_name: str,
    materialization_version: int | str,
    decimation_percent: int | float | str,
    mesh_directory: str | Path,
    voxel_resolution_nm: Any = None,
    datastack: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    spatial_features: Mapping[str, Any] | None = None,
    save_render: bool = True,
    log_filename: str = LOG_FILENAME,
    now_fn: Callable[[], _dt.datetime] | None = None,
) -> dict[str, Any] | None:
    """Save a VTK PNG snapshot and append one JSONL metadata record.

    This function does not open an interactive renderer. It should be called in
    a separate save cell after the interactive render has closed, using the same
    actor dictionary and mutable VTK camera object.
    """
    if not save_render:
        return None

    if not actor_dict:
        raise ValueError("actor_dict is empty; there is nothing to save.")

    from meshparty import trimesh_vtk

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    now_local = now_fn() if now_fn is not None else _dt.datetime.now().astimezone()
    if now_local.tzinfo is None:
        now_local = now_local.astimezone()
    now_utc = now_local.astimezone(_dt.timezone.utc)
    timestamp = now_local.strftime("%Y_%m_%d_%H%M%S")

    mesh_records = parse_rendered_meshes(actor_dict)
    categories = rendered_categories(mesh_records)
    synapse_actor_presence = detect_synapse_actors(actor_dict)
    synapses_rendered = any(synapse_actor_presence.values())
    spatial_feature_record = dict(spatial_features or {})

    filename = build_render_filename(categories, synapses_rendered, timestamp)
    png_path = unique_path(output_path / filename)
    log_path = output_path / log_filename

    trimesh_vtk.render_actors(
        actor_dict.values(),
        filename=str(png_path),
        do_save=True,
        scale=render_scale,
        camera=camera,
    )

    if not png_path.exists():
        raise RuntimeError(f"VTK render save did not create expected PNG: {png_path}")

    record = {
        "schema_version": SCHEMA_VERSION,
        "timestamp_local": now_local.isoformat(),
        "timestamp_utc": now_utc.isoformat(),
        "image_filename": png_path.name,
        "image_path": str(png_path),
        "notebook_name": str(notebook_name),
        "render": {
            "scale": json_safe(render_scale),
            "actor_keys": [str(key) for key in actor_dict.keys()],
            "non_mesh_actor_keys": [
                str(key)
                for key in actor_dict.keys()
                if parse_mesh_actor_key(str(key)) is None
            ],
            "meshes": {
                "categories": categories,
                "ids_by_category": ids_by_category(mesh_records),
                "count": len(mesh_records),
                "records": mesh_records,
            },
            "spatial_features": {
                "synapses": {
                    "rendered": synapses_rendered,
                    "actor_keys": synapse_actor_presence,
                },
                "nuclei": json_safe(spatial_feature_record.get("nuclei")),
            },
        },
        "provenance": {
            "datastack": datastack,
            "materialization_version": json_safe(materialization_version),
            "decimation_percent": json_safe(decimation_percent),
            "mesh_directory": str(mesh_directory),
            "voxel_resolution_nm": json_safe(voxel_resolution_nm),
        },
        "camera": serialize_camera(camera),
        "metadata": json_safe(dict(metadata or {})),
    }

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, sort_keys=True) + "\n")

    return record


__all__ = [
    "CATEGORY_ORDER",
    "LOG_FILENAME",
    "MESH_PREFIX_TO_CATEGORY",
    "SCHEMA_VERSION",
    "SYNAPSE_ACTOR_KEYS",
    "build_render_filename",
    "detect_synapse_actors",
    "ids_by_category",
    "json_safe",
    "parse_mesh_actor_key",
    "parse_rendered_meshes",
    "rendered_categories",
    "save_vtk_render_snapshot",
    "serialize_camera",
    "unique_path",
]
