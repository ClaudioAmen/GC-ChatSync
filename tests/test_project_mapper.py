"""Unit tests for cross-platform project path and URI translation."""

import json
import unittest
from pathlib import Path

from gc_chatsync.project_mapper import (
    extract_path_from_file_uri,
    path_to_file_uri,
    translate_file_uri,
    translate_project_json,
    translate_workspace_uris_string,
)


class TestProjectMapper(unittest.TestCase):

    def test_extract_path_from_file_uri(self):
        self.assertEqual(
            extract_path_from_file_uri("file:///home/claudio/Documents/progetti"),
            "/home/claudio/Documents/progetti"
        )
        self.assertEqual(
            extract_path_from_file_uri("file:///C:/Users/Claudio/Documents/progetti"),
            "C:/Users/Claudio/Documents/progetti"
        )
        self.assertEqual(
            extract_path_from_file_uri("file:///c:/Users/Claudio/Documents/progetti"),
            "c:/Users/Claudio/Documents/progetti"
        )

    def test_path_to_file_uri(self):
        self.assertEqual(
            path_to_file_uri("/home/claudio/Documents", "linux"),
            "file:///home/claudio/Documents"
        )
        self.assertEqual(
            path_to_file_uri("C:/Users/Claudio/Documents", "windows"),
            "file:///C:/Users/Claudio/Documents"
        )

    def test_translate_linux_to_windows(self):
        src_uri = "file:///home/claudio/Documents/progetti/my-project"
        win_home = Path("C:/Users/ClaudioAmen")

        translated = translate_file_uri(
            src_uri,
            target_os="windows",
            target_home=win_home
        )
        self.assertEqual(
            translated,
            "file:///C:/Users/ClaudioAmen/Documents/progetti/my-project"
        )

    def test_translate_windows_to_linux(self):
        src_uri = "file:///C:/Users/ClaudioAmen/Documents/progetti/my-project"
        linux_home = Path("/home/claudio")

        translated = translate_file_uri(
            src_uri,
            target_os="linux",
            target_home=linux_home
        )
        self.assertEqual(
            translated,
            "file:///home/claudio/Documents/progetti/my-project"
        )

    def test_translate_with_custom_mapping(self):
        src_uri = "file:///mnt/storage/code/app"
        custom = {"/mnt/storage/code": "D:/Development"}

        translated = translate_file_uri(
            src_uri,
            target_os="windows",
            custom_mappings=custom
        )
        self.assertEqual(translated, "file:///D:/Development/app")

    def test_translate_project_json(self):
        sample_project = {
            "id": "4777bba5-4413-4c0f-b059-3321e1755397",
            "name": "progetti",
            "projectResources": {
                "resources": [
                    {
                        "folderUri": "file:///home/claudio/Documents/progetti"
                    }
                ]
            },
            "isWorkspaceOnly": False
        }

        translated_project = translate_project_json(
            sample_project,
            target_os="windows",
            target_home=Path("C:/Users/Claudio")
        )

        res_uri = translated_project["projectResources"]["resources"][0]["folderUri"]
        self.assertEqual(res_uri, "file:///C:/Users/Claudio/Documents/progetti")
        self.assertEqual(translated_project["id"], "4777bba5-4413-4c0f-b059-3321e1755397")

    def test_translate_workspace_uris_string(self):
        raw_json = '["file:///home/claudio/Documents/progetti"]'
        translated = translate_workspace_uris_string(
            raw_json,
            target_os="windows",
            target_home=Path("C:/Users/Claudio")
        )
        self.assertEqual(translated, '["file:///C:/Users/Claudio/Documents/progetti"]')


if __name__ == "__main__":
    unittest.main()
