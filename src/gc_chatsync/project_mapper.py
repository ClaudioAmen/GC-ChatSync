"""Cross-platform path and URI translation between Linux and Windows.

Translates Antigravity project definitions (~/.gemini/config/projects/*.json)
and workspace URIs in conversation_summaries.db across operating systems.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

logger = logging.getLogger("gc_chatsync.project_mapper")

# Deduplicate debug messages for outside-home URIs during a session
_LOGGED_UNTRANSLATED_URIS: set[str] = set()


def _is_windows_path(path_str: str) -> bool:
    """Return True if path_str looks like a Windows drive path (e.g. C:/...)."""
    return bool(re.match(r"^[a-zA-Z]:(?:/|$)", path_str))


def _matches_prefix(raw_path: str, prefix: str, case_insensitive: bool = False) -> Optional[str]:
    """If raw_path equals prefix or starts with prefix + '/', return the relative remainder."""
    if case_insensitive:
        p_cmp = raw_path.lower()
        pref_cmp = prefix.lower()
    else:
        p_cmp = raw_path
        pref_cmp = prefix

    if p_cmp == pref_cmp:
        return ""
    if p_cmp.startswith(pref_cmp + "/"):
        return raw_path[len(prefix) + 1:]
    return None


def normalize_to_forward_slashes(path_str: str) -> str:
    """Ensure path uses forward slashes."""
    return path_str.replace("\\", "/")


def extract_path_from_file_uri(uri: str) -> str:
    """Extract filesystem path from a file:// URI.

    Example:
    file:///home/user/Documents -> /home/user/Documents
    file:///C:/Users/Username/Documents -> C:/Users/Username/Documents
    """
    if not uri.startswith("file://"):
        return uri

    parsed = urlparse(uri)
    raw_path = unquote(parsed.path)

    # Windows drive letter URI fix: /C:/Users -> C:/Users
    if re.match(r"^/[a-zA-Z]:", raw_path):
        raw_path = raw_path[1:]

    return normalize_to_forward_slashes(raw_path)


def path_to_file_uri(path_str: str, target_os: str) -> str:
    """Format a filesystem path as an Antigravity-compliant file:// URI."""
    clean = normalize_to_forward_slashes(path_str).strip()
    if target_os == "windows":
        # Ensure leading slash before drive letter: file:///C:/...
        if re.match(r"^[a-zA-Z]:", clean):
            return f"file:///{clean}"
        return f"file://{clean}"
    else:
        # Linux / Unix: file:///home/...
        if not clean.startswith("/"):
            clean = f"/{clean}"
        return f"file://{clean}"


def translate_file_uri(
    uri: str,
    target_os: str,
    target_home: Optional[Path] = None,
    custom_mappings: Optional[Dict[str, str]] = None,
) -> str:
    """Translate a file URI from one OS to another.

    Maps user-home-relative subdirectories (e.g. ~/Documents/progetti)
    between Linux (/home/username/...) and Windows (C:/Users/Username/...).
    """
    if not uri.startswith("file://"):
        return uri

    raw_path = extract_path_from_file_uri(uri)

    # 1. Check user-defined custom root mappings first (bidirectional: key -> value or value -> key)
    if custom_mappings:
        for src_prefix, dst_prefix in custom_mappings.items():
            norm_src = normalize_to_forward_slashes(src_prefix).rstrip("/")
            norm_dst = normalize_to_forward_slashes(dst_prefix).rstrip("/")
            if not norm_src or not norm_dst:
                continue

            # Direction 1: raw_path starts with norm_src -> map to norm_dst
            rel_fwd = _matches_prefix(raw_path, norm_src, case_insensitive=_is_windows_path(norm_src))
            if rel_fwd is not None:
                dst_is_win = _is_windows_path(norm_dst)
                if (target_os == "windows" and dst_is_win) or (target_os != "windows" and not dst_is_win):
                    new_path = f"{norm_dst}/{rel_fwd}" if rel_fwd else norm_dst
                    return path_to_file_uri(new_path, target_os)

            # Direction 2: raw_path starts with norm_dst -> map to norm_src
            rel_rev = _matches_prefix(raw_path, norm_dst, case_insensitive=_is_windows_path(norm_dst))
            if rel_rev is not None:
                src_is_win = _is_windows_path(norm_src)
                if (target_os == "windows" and src_is_win) or (target_os != "windows" and not src_is_win):
                    new_path = f"{norm_src}/{rel_rev}" if rel_rev else norm_src
                    return path_to_file_uri(new_path, target_os)

    # 2. Determine target home directory
    if target_home is None:
        if target_os == "windows":
            u_prof = os.environ.get("USERPROFILE")
            target_home_str = normalize_to_forward_slashes(u_prof) if u_prof else "C:/Users/Default"
        else:
            target_home_str = normalize_to_forward_slashes(str(Path.home().resolve()))
    else:
        raw_str = normalize_to_forward_slashes(str(target_home))
        # If it's a Windows path on POSIX, avoid .resolve() which prepends POSIX cwd
        if target_os == "windows" and re.match(r"^[a-zA-Z]:", raw_str):
            target_home_str = raw_str
        elif target_os != "windows" and raw_str.startswith("/"):
            target_home_str = raw_str
        else:
            target_home_str = normalize_to_forward_slashes(str(target_home.resolve()))

    target_home_str = target_home_str.rstrip("/")

    # Pattern A: Source is Linux home (/home/<username>/<rel_path>)
    linux_match = re.match(r"^/home/[^/]+(?:/(.*))?$", raw_path)
    if linux_match:
        rel_subpath = linux_match.group(1) or ""
        if target_os == "windows":
            new_path = f"{target_home_str}/{rel_subpath}" if rel_subpath else target_home_str
            return path_to_file_uri(new_path, "windows")
        else:
            # Linux to Linux (e.g. different username)
            new_path = f"{target_home_str}/{rel_subpath}" if rel_subpath else target_home_str
            return path_to_file_uri(new_path, "linux")

    # Pattern B: Source is Windows home (e.g. C:/Users/<username>/<rel_path>)
    win_match = re.match(r"^[a-zA-Z]:/[Uu]sers/[^/]+(?:/(.*))?$", raw_path)
    if win_match:
        rel_subpath = win_match.group(1) or ""
        if target_os == "linux":
            new_path = f"{target_home_str}/{rel_subpath}" if rel_subpath else target_home_str
            return path_to_file_uri(new_path, "linux")
        else:
            # Windows to Windows (e.g. different username)
            new_path = f"{target_home_str}/{rel_subpath}" if rel_subpath else target_home_str
            return path_to_file_uri(new_path, "windows")

    # If it cannot be relativized to home and no custom mapping applies, preserve original
    if uri not in _LOGGED_UNTRANSLATED_URIS:
        _LOGGED_UNTRANSLATED_URIS.add(uri)
        logger.debug(f"URI outside standard home directories, preserving: {uri}")
    return uri


def translate_project_json(
    project_data: Dict[str, Any],
    target_os: str,
    target_home: Optional[Path] = None,
    custom_mappings: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Translate all folderUri occurrences in an Antigravity project configuration."""
    data = dict(project_data)

    project_resources = data.get("projectResources")
    if isinstance(project_resources, dict):
        resources = project_resources.get("resources")
        if isinstance(resources, list):
            new_resources: List[Dict[str, Any]] = []
            for res in resources:
                if isinstance(res, dict) and "folderUri" in res:
                    new_res = dict(res)
                    new_res["folderUri"] = translate_file_uri(
                        res["folderUri"],
                        target_os=target_os,
                        target_home=target_home,
                        custom_mappings=custom_mappings,
                    )
                    new_resources.append(new_res)
                else:
                    new_resources.append(res)
            data["projectResources"]["resources"] = new_resources

    return data


def translate_workspace_uris_string(
    workspace_uris_json: str,
    target_os: str,
    target_home: Optional[Path] = None,
    custom_mappings: Optional[Dict[str, str]] = None,
) -> str:
    """Translate serialized workspace_uris JSON array from conversation_summaries table."""
    if not workspace_uris_json or not workspace_uris_json.strip():
        return workspace_uris_json

    try:
        uris = json.loads(workspace_uris_json)
        if isinstance(uris, list):
            translated = [
                translate_file_uri(u, target_os=target_os, target_home=target_home, custom_mappings=custom_mappings)
                for u in uris
                if isinstance(u, str)
            ]
            return json.dumps(translated)
    except Exception as e:
        logger.warning(f"Could not parse workspace_uris JSON '{workspace_uris_json}': {e}")

    return workspace_uris_json
