import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)

os.environ.setdefault("YESPM_MOCK", "1")

from yespm_backend.config import load_config  # noqa: E402
from yespm_backend.engine.backend import Backend  # noqa: E402
from yespm_backend.protocol.transport import InProcessTransport  # noqa: E402
from yespm_backend.template import load_default_template  # noqa: E402
from yespm_backend.tree.value_tree import (  # noqa: E402
    compute_template_paths,
    instantiate_value_tree,
)


@pytest.fixture()
def cfg(tmp_path):
    os.environ["YESPM_SESSION_DIR"] = str(tmp_path)
    os.environ["YESPM_DB_PATH"] = str(tmp_path / "yespm.sqlite")
    return load_config()


@pytest.fixture()
def template():
    tpl = load_default_template()
    compute_template_paths(tpl)
    return tpl


@pytest.fixture()
def value_tree(template):
    return instantiate_value_tree(template)


@pytest.fixture()
def backend(cfg):
    b = Backend(cfg)
    yield b
    b.close()


@pytest.fixture()
def transport(backend):
    return InProcessTransport(backend)
