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
            extract_path_from_file_uri("file:///home/developer/Documents/projects"),
            "/home/developer/Documents/projects"
        )
        self.assertEqual(
            extract_path_from_file_uri("file:///C:/Users/Developer/Documents/projects"),
            "C:/Users/Developer/Documents/projects"
        )
        self.assertEqual(
            extract_path_from_file_uri("file:///c:/Users/Developer/Documents/projects"),
            "c:/Users/Developer/Documents/projects"
        )

    def test_path_to_file_uri(self):
        self.assertEqual(
            path_to_file_uri("/home/developer/Documents", "linux"),
            "file:///home/developer/Documents"
        )
        self.assertEqual(
            path_to_file_uri("C:/Users/Developer/Documents", "windows"),
            "file:///C:/Users/Developer/Documents"
        )

    def test_translate_linux_to_windows(self):
        src_uri = "file:///home/developer/Documents/projects/my-project"
        win_home = Path("C:/Users/Developer")

        translated = translate_file_uri(
            src_uri,
            target_os="windows",
            target_home=win_home
        )
        self.assertEqual(
            translated,
            "file:///C:/Users/Developer/Documents/projects/my-project"
        )

    def test_translate_windows_to_linux(self):
        src_uri = "file:///C:/Users/Developer/Documents/projects/my-project"
        linux_home = Path("/home/developer")

        translated = translate_file_uri(
            src_uri,
            target_os="linux",
            target_home=linux_home
        )
        self.assertEqual(
            translated,
            "file:///home/developer/Documents/projects/my-project"
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

    def test_translate_with_custom_mapping_bidirectional(self):
        """A single custom mapping entry should work in both directions (Linux <-> Windows)."""
        custom = {"D:/Progetti": "/home/user/Documents/progetti"}

        # Windows -> Linux (matching key)
        win_uri = "file:///D:/Progetti/gc-chatsync"
        to_linux = translate_file_uri(win_uri, target_os="linux", custom_mappings=custom)
        self.assertEqual(to_linux, "file:///home/user/Documents/progetti/gc-chatsync")

        # Linux -> Windows (matching value in reverse)
        linux_uri = "file:///home/user/Documents/progetti/gc-chatsync"
        to_win = translate_file_uri(linux_uri, target_os="windows", custom_mappings=custom)
        self.assertEqual(to_win, "file:///D:/Progetti/gc-chatsync")

    def test_translate_project_json(self):
        sample_project = {
            "id": "4777bba5-4413-4c0f-b059-3321e1755397",
            "name": "projects",
            "projectResources": {
                "resources": [
                    {
                        "folderUri": "file:///home/developer/Documents/projects"
                    }
                ]
            },
            "isWorkspaceOnly": False
        }

        translated_project = translate_project_json(
            sample_project,
            target_os="windows",
            target_home=Path("C:/Users/Developer")
        )

        res_uri = translated_project["projectResources"]["resources"][0]["folderUri"]
        self.assertEqual(res_uri, "file:///C:/Users/Developer/Documents/projects")
        self.assertEqual(translated_project["id"], "4777bba5-4413-4c0f-b059-3321e1755397")

    def test_translate_workspace_uris_string(self):
        raw_json = '["file:///home/developer/Documents/projects"]'
        translated = translate_workspace_uris_string(
            raw_json,
            target_os="windows",
            target_home=Path("C:/Users/Developer")
        )
        self.assertEqual(translated, '["file:///C:/Users/Developer/Documents/projects"]')


if __name__ == "__main__":
    unittest.main()
