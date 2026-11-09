"""A configurable persistent artifact directory for local and hosted deployments."""
import os
from pathlib import Path


def artifact_root():
    configured = os.getenv('ARTIFACT_ROOT')
    return Path(configured) if configured else Path(__file__).resolve().parents[1] / 'artifacts'
