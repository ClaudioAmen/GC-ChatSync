"""Cross-Python compatibility and code invariant tests."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


class TestPythonCompatibility(unittest.TestCase):
    def test_all_modules_have_future_annotations(self):
        """Verify all library modules import annotations from __future__ for Python 3.10 compatibility."""
        src_dir = Path(__file__).resolve().parent.parent / "src" / "gc_chatsync"
        py_files = list(src_dir.glob("*.py"))
        self.assertTrue(len(py_files) > 0, "No python files found in src/gc_chatsync")

        missing = []
        for py_file in py_files:
            if py_file.name == "__init__.py":
                continue
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))
            
            has_future_annotations = False
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    for alias in node.names:
                        if alias.name == "annotations":
                            has_future_annotations = True
                            break
            if not has_future_annotations:
                missing.append(py_file.name)

        self.assertEqual(
            missing,
            [],
            f"The following modules are missing 'from __future__ import annotations' (breaks Python 3.10-3.13): {missing}",
        )


if __name__ == "__main__":
    unittest.main()
