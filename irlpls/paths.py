"""Where irlpls finds its JSON Schemas: $IRLPLS_SCHEMA_DIR, else the repo's schema/ (a checkout), else the copy
bundled in the package (irlpls/data/schema, kept identical by tools/sync_package_data.py)."""
import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
REPO_SCHEMA = HERE.parent / "schema"
BUNDLED_SCHEMA = HERE / "data" / "schema"


def schema_dir():
    env = os.environ.get("IRLPLS_SCHEMA_DIR")
    if env:
        return pathlib.Path(env)
    return REPO_SCHEMA if (REPO_SCHEMA / "capture-card.schema.json").is_file() else BUNDLED_SCHEMA
