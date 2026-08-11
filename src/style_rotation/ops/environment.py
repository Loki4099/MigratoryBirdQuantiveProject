from __future__ import annotations

import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from style_rotation.core.canonical import sha256_hexdigest

NUMERICAL_PACKAGES = ("numpy", "pandas", "scipy")


def capture_numerical_environment(
    package_names: tuple[str, ...] = NUMERICAL_PACKAGES,
) -> dict[str, Any]:
    """Capture only stable inputs that can influence numerical reproduction."""

    packages: dict[str, str | None] = {}
    for package_name in sorted(set(package_names)):
        try:
            packages[package_name] = version(package_name)
        except PackageNotFoundError:
            packages[package_name] = None
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "byteorder": sys.byteorder,
        "packages": packages,
    }


def numerical_environment_hash(environment: dict[str, Any] | None = None) -> str:
    return sha256_hexdigest(environment or capture_numerical_environment())
