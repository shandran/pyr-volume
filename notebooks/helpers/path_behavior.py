from pathlib import Path


def format_path(path, project_root, show_full_path=False):
    """Return an absolute path or a project-relative path for display."""
    path = Path(path).expanduser().resolve()
    project_root = Path(project_root).expanduser().resolve()

    if show_full_path:
        return str(path)

    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def print_path(label, path, project_root, show_full_path=False):
    """Print a label with a consistently formatted display path."""
    print(f"{label}: {format_path(path, project_root, show_full_path)}")
