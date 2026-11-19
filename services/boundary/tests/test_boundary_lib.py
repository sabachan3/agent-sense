import argparse
from pathlib import Path

import boundary_lib


def project(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def check(root: Path, config: dict | None = None, **options) -> list[dict]:
    return boundary_lib.run(argparse.Namespace(root=root, config=config or {}, **options))["findings"]


def found(findings: list[dict]) -> list[tuple]:
    return [(finding["rule"], finding["file"], finding["line"], finding["detail"]["to"]) for finding in findings]


BASE = {"services/a/a_lib.py": "import json\n", "services/b/b_lib.py": "X = 1\n", "services/b/helpers/__init__.py": "",
        "services/b/tests/test_b_lib.py": "import b_lib\n", "contracts/schema.py": "", "services/c/lib.py": "",
        "services/b/lib.py": ""}


def test_interface_matches_the_contract():
    assert boundary_lib.NAME == "boundary" and boundary_lib.SUMMARY
    assert set(boundary_lib.RULES) == {"cross-service-import", "cross-service-syspath", "cross-service-path"}
    boundary_lib.add_arguments(argparse.ArgumentParser())


def test_clean_services_and_top_level_glue_pass(tmp_path):
    root = project(tmp_path, {**BASE, "cli.py": "import sys\nsys.path.insert(0, 'services/a')\nimport a_lib, b_lib\n",
                              "services/a/run.py": "import a_lib\nfrom contracts import schema\nfrom contracts.schema import x\n"
                                                   "import os.path\nimport requests\n",
                              "services/c/main.py": "import lib\n"})
    result = boundary_lib.run(argparse.Namespace(root=root, config={}))
    assert result["findings"] == [] and result["stats"]["services"] == 3 and result["stats"]["service_pairs"] == []


def test_import_of_a_module_owned_by_another_service_fails(tmp_path):
    root = project(tmp_path, {**BASE, "services/a/run.py": (
        "import a_lib\nimport b_lib\nfrom b_lib import X\nfrom helpers import thing\n"
        "import services.b.b_lib\nfrom services.b import b_lib as other\nfrom services import b\n"
        "import importlib\nmodule = importlib.import_module('b_lib')\n"),
        "services/a/tests/test_a_lib.py": "def test():\n    import b_lib\n"})
    findings = check(root)
    assert found(findings) == [("cross-service-import", "services/a/run.py", line, "b") for line in (2, 3, 4, 5, 6, 7, 9)] + [
        ("cross-service-import", "services/a/tests/test_a_lib.py", 2, "b")]
    assert findings[0]["severity"] == "fail" and "`b_lib`" in findings[0]["message"] and "`a`" in findings[0]["message"]


def test_a_module_name_each_service_has_its_own_copy_of_is_not_a_reach(tmp_path):
    root = project(tmp_path, {**BASE, "services/c/main.py": "import lib\n", "services/a/run.py": "import lib\n",
                              "services/a/json.py": "", "services/c/other.py": "import json\nimport a\n"})
    assert found(check(root)) == [("cross-service-import", "services/a/run.py", 1, "b"),
                                  ("cross-service-import", "services/c/other.py", 2, "a")]


def test_relative_imports_that_leave_the_service(tmp_path):
    root = project(tmp_path, {**BASE, "services/a/pkg/__init__.py": "", "services/a/pkg/x.py": (
        "from . import y\nfrom .. import a_lib\nfrom ...b import b_lib\nfrom ...b.helpers import z\n")})
    assert found(check(root)) == [("cross-service-import", "services/a/pkg/x.py", 3, "b"),
                                  ("cross-service-import", "services/a/pkg/x.py", 4, "b")]


def test_sys_path_edits_that_add_another_service(tmp_path):
    root = project(tmp_path, {**BASE, "services/a/run.py": (
        "import sys\nfrom pathlib import Path\nHERE = Path(__file__).resolve().parent\n"
        "sys.path.insert(0, str(HERE))\nsys.path.insert(0, str(HERE.parent / 'b'))\n"
        "sys.path.append('../b')\nsys.path.insert(0, str(HERE.parents[1] / 'services' / 'c'))\n"
        "sys.path.insert(0, str(HERE.parents[1] / 'contracts'))\nsys.path.append(str(HERE / 'tests'))\n")})
    assert found(check(root)) == [("cross-service-syspath", "services/a/run.py", 5, "b"),
                                  ("cross-service-syspath", "services/a/run.py", 6, "b"),
                                  ("cross-service-syspath", "services/a/run.py", 7, "c")]
    assert all(finding["severity"] == "fail" for finding in check(root))


def test_path_literals_into_another_service_warn(tmp_path):
    root = project(tmp_path, {**BASE, "services/a/data/x.json": "", "services/data/d_lib.py": "", "services/a/run.py": (
        '"""Reads services/b/out.json in the docstring, which is prose."""\nfrom pathlib import Path\n'
        "HERE = Path(__file__).resolve().parent\nLLM = HERE.parent / 'b' / 'b_lib.py'\n"
        "OUT = open('services/b/out.json')\nOTHER = '../c/lib.py'\nOWN = HERE / 'data' / 'x.json'\n"
        "SELF = 'services/a/x.json'\nSHARED = 'services/contracts/x'\nWORD = 'b'\nROOT = HERE.parents[1] / 'runs' / 'b'\n"),
        "services/a/go.sh": "# services/b/ in a comment\npython services/b/b_lib.py --x\ncd ../c && ls\n"})
    findings = check(root)
    assert found(findings) == [("cross-service-path", "services/a/go.sh", 2, "b"),
                               ("cross-service-path", "services/a/go.sh", 3, "c"),
                               ("cross-service-path", "services/a/run.py", 4, "b"),
                               ("cross-service-path", "services/a/run.py", 5, "b"),
                               ("cross-service-path", "services/a/run.py", 6, "c")]
    assert all(finding["severity"] == "warn" for finding in findings)


def test_javascript_imports_that_resolve_into_another_service(tmp_path):
    root = project(tmp_path, {"services/web/src/a.ts": (
        "import x from './local';\nimport y from '../../api/src/db';\nimport z from '../../../contracts/types';\n"
        "const w = require('../../api/util');\nexport * from '@/services/api/x';\nimport r from 'react';\n"
        "// import q from '../../api/commented'\n"), "services/api/src/db.ts": "", "contracts/types.ts": ""})
    assert found(check(root)) == [("cross-service-import", "services/web/src/a.ts", 2, "api"),
                                  ("cross-service-import", "services/web/src/a.ts", 4, "api"),
                                  ("cross-service-import", "services/web/src/a.ts", 5, "api")]


def test_allow_list_services_dir_and_severity(tmp_path):
    files = {**BASE, "services/a/run.py": "import b_lib\nP = 'services/c/lib.py'\n", "apps/x/x.py": "import y_lib\n",
             "apps/y/y_lib.py": ""}
    root = project(tmp_path, files)
    assert len(check(root)) == 2
    assert found(check(root, allow=["b"])) == [("cross-service-path", "services/a/run.py", 2, "c")]
    assert check(root, allow=["a->b", "a->c"]) == []
    assert found(check(root, config={"allow": ["z->b"]})) == found(check(root))
    assert found(check(root, services_dir="apps")) == [("cross-service-import", "apps/x/x.py", 1, "y")]
    assert check(root, config={"services-dir": "apps", "severity": {"cross-service-import": "off"}}) == []
    assert check(root, config={"severity": {"cross-service-path": "fail"}})[1]["severity"] == "fail"


def test_findings_satisfy_the_contract_and_stats_name_the_pairs(tmp_path):
    import pytest
    from contracts import schema

    root = project(tmp_path, {**BASE, "services/a/run.py": "import b_lib\n", "services/a/bad.py": "def (:\n"})
    result = boundary_lib.run(argparse.Namespace(root=root, config={}))
    schema.validate_result(result, boundary_lib.RULES)
    assert result["stats"]["service_pairs"] == ["a->b"]
    with pytest.raises(RuntimeError):
        boundary_lib.run(argparse.Namespace(root=tmp_path / "nope", config={}))
    assert boundary_lib.run(argparse.Namespace(root=project(tmp_path / "empty", {"x.py": ""}), config={}))["findings"] == []
