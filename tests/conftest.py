import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["YESPM_MOCK"] = "1"

from src.config import load_config  # noqa: E402
from src.tools.template_loader import load_default_template  # noqa: E402
from src.tools.value_tree import compute_template_paths, instantiate_value_tree  # noqa: E402


@pytest.fixture()
def cfg(tmp_path):
    os.environ["YESPM_SESSION_DIR"] = str(tmp_path)
    return load_config()


@pytest.fixture()
def template():
    tpl = load_default_template()
    compute_template_paths(tpl)
    return tpl


@pytest.fixture()
def value_tree(template):
    return instantiate_value_tree(template)
