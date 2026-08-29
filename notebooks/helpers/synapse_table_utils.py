"""Utilities for one-root CA3 synapse table artifacts.

This module centralizes the artifact mechanics shared by notebooks while
leaving batch loops, progress reporting, and Neuroglancer visualization logic in
the notebooks.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


ARTIFACT_SCHEMA_VERSION = 1
SYNAPSE_TABLE_NAME = "synapses_ca3_v1"
DIRECTION_AFFERENT = "afferent"
DIRECTION_EFFERENT = "efferent"
DIRECTIONS = (DIRECTION_AFFERENT, DIRECTION_EFFERENT)
REQUIRED_SYNAPSE_COLUMNS = (
    "id",
    "pre_pt_root_id",
    "post_pt_root_id",
    "pre_pt_position",
    "post_pt_position",
    "direction",
)


def _json_safe(value: Any) -> Any:
    """Convert common NumPy/path values to JSON-compatible Python objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _normalized_resolution(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        value = value.tolist()
    return list(value)


def artifact_paths(
    output_dir: str | Path,
    root_id: int | str,
    materialization_version: int | str,
) -> dict[str, Path | str]:
    """Return canonical Parquet/metadata paths and filenames for one root."""
    output_path = Path(output_dir)
    parquet_filename = f"synapses_{root_id}_mat{materialization_version}.parquet"
    metadata_filename = f"synapses_{root_id}_mat{materialization_version}_metadata.json"
    return {
        "output_dir": output_path,
        "parquet_filename": parquet_filename,
        "metadata_filename": metadata_filename,
        "parquet_path": output_path / parquet_filename,
        "metadata_path": output_path / metadata_filename,
    }


def query_synapses_for_root(
    client: Any,
    root_id: int | str,
    materialization_version: int | str,
    desired_resolution: list[int] | tuple[int, int, int],
) -> dict[str, Any]:
    """Query one root's synapses and remove autapses explicitly."""
    afferent_df = client.materialize.synapse_query(
        post_ids=root_id,
        materialization_version=materialization_version,
        desired_resolution=desired_resolution,
        remove_autapses=False,
    )
    efferent_df = client.materialize.synapse_query(
        pre_ids=root_id,
        materialization_version=materialization_version,
        desired_resolution=desired_resolution,
        remove_autapses=False,
    )

    afferent_autapse_mask = afferent_df["pre_pt_root_id"] == afferent_df["post_pt_root_id"]
    efferent_autapse_mask = efferent_df["pre_pt_root_id"] == efferent_df["post_pt_root_id"]
    afferent_autapse_count = int(afferent_autapse_mask.sum())
    efferent_autapse_count = int(efferent_autapse_mask.sum())

    return {
        "afferent_df": afferent_df.loc[~afferent_autapse_mask].copy(),
        "efferent_df": efferent_df.loc[~efferent_autapse_mask].copy(),
        "afferent_autapse_count": afferent_autapse_count,
        "efferent_autapse_count": efferent_autapse_count,
    }


def combine_synapse_tables(afferent_df: pd.DataFrame, efferent_df: pd.DataFrame) -> pd.DataFrame:
    """Combine afferent/efferent rows and add the canonical direction column."""
    afferent_synapses = afferent_df.copy()
    efferent_synapses = efferent_df.copy()
    afferent_synapses["direction"] = DIRECTION_AFFERENT
    efferent_synapses["direction"] = DIRECTION_EFFERENT
    return pd.concat([afferent_synapses, efferent_synapses], ignore_index=True, sort=False)


def split_synapse_table(synapses_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a combined synapse table into afferent and efferent frames."""
    if "direction" not in synapses_df.columns:
        raise ValueError("Cannot split synapse table without a direction column.")
    afferent_df = synapses_df.loc[synapses_df["direction"] == DIRECTION_AFFERENT].copy()
    efferent_df = synapses_df.loc[synapses_df["direction"] == DIRECTION_EFFERENT].copy()
    return afferent_df, efferent_df


def parquet_safe_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Convert NumPy array object values to lists before Parquet writing."""
    safe_df = df.copy()
    object_columns = safe_df.select_dtypes(include="object").columns
    for column in object_columns:
        if safe_df[column].map(lambda value: isinstance(value, np.ndarray)).any():
            safe_df[column] = safe_df[column].map(
                lambda value: value.tolist() if isinstance(value, np.ndarray) else value
            )
    return safe_df


def build_metadata(
    root_id: int | str,
    datastack: str,
    materialization_version: int | str,
    materialization_timestamp: Any,
    desired_resolution: list[int] | tuple[int, int, int],
    synapses_df: pd.DataFrame,
    parquet_filename: str,
    synapse_table: str = SYNAPSE_TABLE_NAME,
    retrieval_timestamp: str | None = None,
) -> dict[str, Any]:
    """Build canonical JSON metadata for a one-root synapse artifact."""
    if retrieval_timestamp is None:
        retrieval_timestamp = datetime.now(timezone.utc).isoformat()
    if hasattr(materialization_timestamp, "isoformat"):
        materialization_timestamp = materialization_timestamp.isoformat()

    direction_counts = (
        synapses_df["direction"].value_counts().to_dict()
        if "direction" in synapses_df.columns
        else {}
    )
    metadata = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "root_id": str(root_id),
        "datastack": datastack,
        "materialization_version": int(materialization_version),
        "materialization_timestamp": materialization_timestamp,
        "synapse_table": synapse_table,
        "desired_resolution": _normalized_resolution(desired_resolution),
        "afferent_count": int(direction_counts.get(DIRECTION_AFFERENT, 0)),
        "efferent_count": int(direction_counts.get(DIRECTION_EFFERENT, 0)),
        "total_synapse_rows": int(len(synapses_df)),
        "retrieval_timestamp": retrieval_timestamp,
        "parquet_filename": parquet_filename,
    }
    return _json_safe(metadata)


def save_synapse_artifact(
    synapses_df: pd.DataFrame,
    metadata: Mapping[str, Any],
    parquet_path: str | Path,
    metadata_path: str | Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save one Parquet artifact and metadata JSON."""
    parquet_path = Path(parquet_path)
    metadata_path = Path(metadata_path)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "parquet_path": parquet_path,
        "metadata_path": metadata_path,
        "parquet_written": False,
        "metadata_written": False,
        "parquet_skipped_existing": False,
        "metadata_skipped_existing": False,
    }

    if parquet_path.exists() and not overwrite:
        result["parquet_skipped_existing"] = True
    else:
        try:
            synapses_df.to_parquet(parquet_path, index=False)
        except Exception:
            parquet_safe_dataframe(synapses_df).to_parquet(parquet_path, index=False)
        result["parquet_written"] = True

    if metadata_path.exists() and not overwrite:
        result["metadata_skipped_existing"] = True
    else:
        metadata_path.write_text(json.dumps(_json_safe(metadata), indent=2), encoding="utf-8")
        result["metadata_written"] = True

    return result


def load_metadata(metadata_path: str | Path) -> dict[str, Any]:
    """Load synapse artifact metadata JSON."""
    return json.loads(Path(metadata_path).read_text(encoding="utf-8"))


def validate_metadata(
    metadata: Mapping[str, Any],
    root_id: int | str,
    datastack: str,
    materialization_version: int | str,
    desired_resolution: list[int] | tuple[int, int, int],
    parquet_filename: str,
    synapse_table: str = SYNAPSE_TABLE_NAME,
) -> list[str]:
    """Return metadata compatibility problems; an empty list means compatible."""
    problems = []
    schema_version = metadata.get("artifact_schema_version", ARTIFACT_SCHEMA_VERSION)

    if schema_version != ARTIFACT_SCHEMA_VERSION:
        problems.append(
            f"artifact_schema_version mismatch: expected {ARTIFACT_SCHEMA_VERSION}, got {schema_version!r}"
        )
    if str(metadata.get("root_id")) != str(root_id):
        problems.append(f"root_id mismatch: expected {root_id}, got {metadata.get('root_id')!r}")
    if metadata.get("datastack") != datastack:
        problems.append(f"datastack mismatch: expected {datastack!r}, got {metadata.get('datastack')!r}")
    if metadata.get("materialization_version") != int(materialization_version):
        problems.append(
            "materialization_version mismatch: "
            f"expected {int(materialization_version)}, got {metadata.get('materialization_version')!r}"
        )
    if _normalized_resolution(metadata.get("desired_resolution")) != _normalized_resolution(desired_resolution):
        problems.append(
            "desired_resolution mismatch: "
            f"expected {_normalized_resolution(desired_resolution)!r}, got {metadata.get('desired_resolution')!r}"
        )
    if metadata.get("synapse_table") != synapse_table:
        problems.append(
            f"synapse_table mismatch: expected {synapse_table!r}, got {metadata.get('synapse_table')!r}"
        )
    if metadata.get("parquet_filename") != parquet_filename:
        problems.append(
            f"parquet_filename mismatch: expected {parquet_filename!r}, got {metadata.get('parquet_filename')!r}"
        )
    return problems


def validate_synapse_table(
    synapses_df: pd.DataFrame,
    metadata: Mapping[str, Any],
    root_id: int | str,
) -> list[str]:
    """Return table validation problems; an empty list means valid."""
    problems = []
    missing_columns = [column for column in REQUIRED_SYNAPSE_COLUMNS if column not in synapses_df.columns]
    if missing_columns:
        problems.append(f"missing required columns: {missing_columns}")
        return problems

    directions = set(synapses_df["direction"].dropna().unique().tolist())
    invalid_directions = sorted(directions - set(DIRECTIONS))
    if invalid_directions:
        problems.append(f"invalid direction values: {invalid_directions}")

    direction_counts = synapses_df["direction"].value_counts().to_dict()
    afferent_count = int(direction_counts.get(DIRECTION_AFFERENT, 0))
    efferent_count = int(direction_counts.get(DIRECTION_EFFERENT, 0))
    total_rows = int(len(synapses_df))

    if metadata.get("afferent_count") != afferent_count:
        problems.append(
            f"afferent_count mismatch: metadata {metadata.get('afferent_count')!r}, table {afferent_count}"
        )
    if metadata.get("efferent_count") != efferent_count:
        problems.append(
            f"efferent_count mismatch: metadata {metadata.get('efferent_count')!r}, table {efferent_count}"
        )
    if metadata.get("total_synapse_rows") != total_rows:
        problems.append(
            f"total_synapse_rows mismatch: metadata {metadata.get('total_synapse_rows')!r}, table {total_rows}"
        )

    root_id_int = int(root_id)
    afferent_rows = synapses_df["direction"] == DIRECTION_AFFERENT
    efferent_rows = synapses_df["direction"] == DIRECTION_EFFERENT
    if not (synapses_df.loc[afferent_rows, "post_pt_root_id"] == root_id_int).all():
        problems.append("one or more afferent rows do not have post_pt_root_id equal to root_id")
    if not (synapses_df.loc[efferent_rows, "pre_pt_root_id"] == root_id_int).all():
        problems.append("one or more efferent rows do not have pre_pt_root_id equal to root_id")
    if (synapses_df["pre_pt_root_id"] == synapses_df["post_pt_root_id"]).any():
        problems.append("one or more autapse rows remain")

    return problems


def load_valid_synapse_artifact(
    output_dir: str | Path,
    root_id: int | str,
    datastack: str,
    materialization_version: int | str,
    desired_resolution: list[int] | tuple[int, int, int],
) -> dict[str, Any]:
    """Load and validate a cached one-root artifact."""
    paths = artifact_paths(output_dir, root_id, materialization_version)
    parquet_path = paths["parquet_path"]
    metadata_path = paths["metadata_path"]
    problems = []

    if not Path(parquet_path).exists():
        problems.append(f"missing Parquet artifact: {parquet_path}")
    if not Path(metadata_path).exists():
        problems.append(f"missing metadata artifact: {metadata_path}")
    if problems:
        return {"status": "missing", "ok": False, "problems": problems, **paths}

    try:
        metadata = load_metadata(metadata_path)
    except Exception as exc:
        return {
            "status": "invalid_metadata",
            "ok": False,
            "problems": [f"could not load metadata: {type(exc).__name__}: {exc}"],
            **paths,
        }

    metadata_problems = validate_metadata(
        metadata=metadata,
        root_id=root_id,
        datastack=datastack,
        materialization_version=materialization_version,
        desired_resolution=desired_resolution,
        parquet_filename=str(paths["parquet_filename"]),
    )
    if metadata_problems:
        return {
            "status": "incompatible_metadata",
            "ok": False,
            "metadata": metadata,
            "problems": metadata_problems,
            **paths,
        }

    try:
        synapses_df = pd.read_parquet(parquet_path)
    except Exception as exc:
        return {
            "status": "invalid_parquet",
            "ok": False,
            "metadata": metadata,
            "problems": [f"could not load Parquet: {type(exc).__name__}: {exc}"],
            **paths,
        }

    table_problems = validate_synapse_table(synapses_df, metadata, root_id)
    if table_problems:
        return {
            "status": "invalid_table",
            "ok": False,
            "metadata": metadata,
            "synapses_df": synapses_df,
            "problems": table_problems,
            **paths,
        }

    afferent_df, efferent_df = split_synapse_table(synapses_df)
    return {
        "status": "loaded_cache",
        "ok": True,
        "metadata": metadata,
        "synapses_df": synapses_df,
        "afferent_df": afferent_df,
        "efferent_df": efferent_df,
        "problems": [],
        **paths,
    }


def load_or_query_synapses(
    client: Any,
    output_dir: str | Path,
    root_id: int | str,
    datastack: str,
    materialization_version: int | str,
    desired_resolution: list[int] | tuple[int, int, int],
    overwrite: bool = False,
) -> dict[str, Any]:
    """Load a compatible cache, or query one root and save a compatible artifact."""
    if not overwrite:
        cached = load_valid_synapse_artifact(
            output_dir=output_dir,
            root_id=root_id,
            datastack=datastack,
            materialization_version=materialization_version,
            desired_resolution=desired_resolution,
        )
        if cached["ok"]:
            return cached
    else:
        cached = None

    paths = artifact_paths(output_dir, root_id, materialization_version)
    query_result = query_synapses_for_root(
        client=client,
        root_id=root_id,
        materialization_version=materialization_version,
        desired_resolution=desired_resolution,
    )
    synapses_df = combine_synapse_tables(query_result["afferent_df"], query_result["efferent_df"])

    try:
        materialization_timestamp = client.materialize.get_timestamp(materialization_version)
    except Exception:
        materialization_timestamp = None

    metadata = build_metadata(
        root_id=root_id,
        datastack=datastack,
        materialization_version=materialization_version,
        materialization_timestamp=materialization_timestamp,
        desired_resolution=desired_resolution,
        synapses_df=synapses_df,
        parquet_filename=str(paths["parquet_filename"]),
    )
    save_result = save_synapse_artifact(
        synapses_df=synapses_df,
        metadata=metadata,
        parquet_path=paths["parquet_path"],
        metadata_path=paths["metadata_path"],
        overwrite=overwrite,
    )

    validation = load_valid_synapse_artifact(
        output_dir=output_dir,
        root_id=root_id,
        datastack=datastack,
        materialization_version=materialization_version,
        desired_resolution=desired_resolution,
    )
    if validation["ok"]:
        validation.update(
            {
                "status": "queried_and_saved",
                "save_result": save_result,
                "afferent_autapse_count": query_result["afferent_autapse_count"],
                "efferent_autapse_count": query_result["efferent_autapse_count"],
                "cache_attempt": cached,
            }
        )
    return validation
