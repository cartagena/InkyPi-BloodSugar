"""Test setup shared by the unit and integration suites.

The unit tests are written to run in **two** environments:

* **Standalone** — a bare checkout of this repo, no InkyPi anywhere. The one
  host module `blood_sugar.py` imports (``BasePlugin``) doesn't exist, so this
  file registers a minimal stand-in for it in ``sys.modules`` before the plugin
  is imported.
* **Inside a real InkyPi checkout** — set ``INKYPI_PATH`` (or put
  ``<inkypi>/src`` on ``PYTHONPATH``). The real host module imports fine, so
  nothing is stubbed and the exact same tests run against the real
  ``BasePlugin``.

That dual mode is the point. Stubs that only ever run against themselves drift
away from the host silently; because these tests also run unstubbed in CI's
integration job, a change to InkyPi's ``BasePlugin`` contract shows up as a
failure rather than as a stub that quietly still passes.

Note that `blood_sugar.py` already guards its *other* host import — the
settings-schema DSL — with a try/except so the plugin stays importable on
upstream InkyPi (see the comment at the top of that file). Standalone, that
guard takes its ImportError branch exactly as it would on upstream, so nothing
needs stubbing for it here.
"""

import os
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def inkypi_src() -> Path | None:
    """``<inkypi>/src`` from ``$INKYPI_PATH``, if it points at a real checkout."""
    raw = os.environ.get("INKYPI_PATH")
    if not raw:
        return None
    src = Path(raw).expanduser().resolve() / "src"
    return src if src.is_dir() else None


def _add_inkypi_to_path() -> None:
    """Put a configured InkyPi checkout on sys.path before anything imports.

    This has to happen here, in the *root* conftest, rather than in
    ``tests/integration/conftest.py``: pytest loads the root one first, and by
    the time the integration conftest ran, this file would already have decided
    InkyPi was absent and stubbed ``plugins`` into ``sys.modules`` — after
    which the real package can never be imported.
    """
    src = inkypi_src()
    if src is not None and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def inkypi_available() -> bool:
    """True when a real InkyPi checkout is importable (its src/ is on sys.path)."""
    try:
        import plugins.base_plugin.base_plugin  # noqa: F401
    except ImportError:
        return False
    return True


class _StubBasePlugin:
    """The subset of InkyPi's BasePlugin that BloodSugar actually uses.

    ``render_image`` raises rather than returning a fake image: it is the
    Jinja + headless-Chromium pipeline, and pretending to provide it would let
    a rendering regression pass the unit suite. Exercising it for real is what
    ``tests/integration/`` is for.
    """

    def __init__(self, config, **dependencies):
        self.config = dict(config)
        self.dependencies = dependencies

    def get_plugin_id(self) -> str:
        plugin_id = self.config.get("id")
        return plugin_id if isinstance(plugin_id, str) else ""

    def get_plugin_dir(self, path=None) -> str:
        base = str(REPO_ROOT / self.get_plugin_id())
        return f"{base}/{path}" if path else base

    def validate_settings(self, settings):
        return None

    def build_settings_schema(self):
        return None

    def generate_settings_template(self) -> dict:
        template_params: dict = {"style_settings": True}
        settings_schema = self.build_settings_schema()
        if settings_schema:
            template_params["settings_schema"] = settings_schema
        else:
            template_params["settings_template"] = f"{self.get_plugin_id()}/settings.html"
        template_params["frame_styles"] = []
        return template_params

    def render_image(self, dimensions, html_file, css_file=None, template_params=None):
        raise NotImplementedError(
            "render_image needs a real InkyPi host (Jinja + headless Chromium). "
            "Cover it in tests/integration/, not here."
        )


def _install_host_stubs() -> None:
    # `plugins` gets this repo's root as its search path, so
    # `plugins.blood_sugar` resolves to the real ./blood_sugar/ package
    # directory (a namespace package — it has no __init__.py, which is also how
    # InkyPi ships it). Importing the plugin under its real dotted path, rather
    # than as a bare top-level `blood_sugar`, is what lets these same tests run
    # unmodified against a real InkyPi checkout.
    plugins_pkg = types.ModuleType("plugins")
    plugins_pkg.__path__ = [str(REPO_ROOT)]

    base_plugin_pkg = types.ModuleType("plugins.base_plugin")
    base_plugin_pkg.__path__ = []
    base_plugin_module = types.ModuleType("plugins.base_plugin.base_plugin")
    base_plugin_module.BasePlugin = _StubBasePlugin

    sys.modules.update(
        {
            "plugins": plugins_pkg,
            "plugins.base_plugin": base_plugin_pkg,
            "plugins.base_plugin.base_plugin": base_plugin_module,
        }
    )


_add_inkypi_to_path()

if inkypi_available():
    pass
elif inkypi_src() is not None:
    # INKYPI_PATH points at a real checkout but the host still won't import —
    # almost always a missing InkyPi dependency. Fail loudly: silently falling
    # back to stubs here would let CI's integration job report green while
    # having quietly run the unit suite twice.
    import plugins.base_plugin.base_plugin as _probe  # noqa: F401
else:
    _install_host_stubs()
