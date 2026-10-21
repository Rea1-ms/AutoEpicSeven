"""Scoped registry substitutes for Windows-only offline test imports."""

import importlib
import sys
from contextlib import contextmanager
from types import ModuleType
from unittest.mock import patch


def _reject_registry(*_args, **_kwargs):
    raise AssertionError('Offline Windows tests attempted registry access')


@contextmanager
def windows_registry_imports():
    """Scope a strict registry stub to imports when the native module is absent."""
    try:
        importlib.import_module('winreg')
    except ModuleNotFoundError as exc:
        if exc.name != 'winreg':
            raise
        registry = ModuleType('winreg')
        registry.HKEY_CURRENT_USER = object()
        registry.HKEY_LOCAL_MACHINE = object()
        for name in ('OpenKey', 'QueryValueEx', 'EnumKey', 'EnumValue'):
            setattr(registry, name, _reject_registry)
        # Production modules remain Windows-specific. Only their pure logic
        # is imported for offline tests; an unexpected registry read must fail
        # instead of returning plausible values from a permissive mock. The
        # temporary module must never survive this import scope in sys.modules.
        with patch.dict(sys.modules, {'winreg': registry}):
            yield
    else:
        yield
