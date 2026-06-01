#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "plugin.audio.sendspin"
ADDON_XML = ADDON_DIR / "addon.xml"
SETTINGS_XML = ADDON_DIR / "resources" / "settings.xml"
PYPROJECT = ROOT / "pyproject.toml"
ASSET_NAME = "plugin.audio.sendspin.zip"
ZIP_PATH = ROOT / ASSET_NAME
DOCKER_PACKAGE_NAME = "sendspin-cli-for-sendspin-kodi"
API_ROOT = "https://api.github.com"


class ReleaseError(Exception):
    pass


def print_status(test_name: str, success: bool, detail: str = "") -> None:
    """Prints a structured status line with ANSI colors for visibility."""
    use_color = sys.stdout.isatty() or os.environ.get("GITHUB_ACTIONS") == "true"
    green = "\033[1;32m" if use_color else ""
    red = "\033[1;31m" if use_color else ""
    reset = "\033[0m" if use_color else ""

    status = f"{green}Success{reset}" if success else f"{red}FAILED{reset}"
    print(f"{test_name}: {status}{f' ({detail})' if detail else ''}")


def run_git(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ReleaseError(f"git {' '.join(args)} failed") from exc


def addon_metadata() -> tuple[str, str]:
    try:
        root = ET.parse(ADDON_XML).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ReleaseError(f"Could not read {ADDON_XML.relative_to(ROOT)}: {exc}") from exc

    addon_id = root.attrib.get("id", "").strip()
    version = root.attrib.get("version", "").strip()
    if not addon_id:
        raise ReleaseError("addon.xml is missing addon id")
    if not version:
        raise ReleaseError("addon.xml is missing addon version")
    return addon_id, version


def pyproject_version() -> str:
    try:
        with PYPROJECT.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseError(f"Could not read {PYPROJECT.relative_to(ROOT)}: {exc}") from exc

    version = str(data.get("project", {}).get("version", "")).strip()
    if not version:
        raise ReleaseError("pyproject.toml is missing project.version")
    return version


def docker_image_versions() -> dict[str, str]:
    try:
        root = ET.parse(SETTINGS_XML).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ReleaseError(f"Could not read {SETTINGS_XML.relative_to(ROOT)}: {exc}") from exc

    versions = {}
    for setting in root.findall(".//setting"):
        if setting.attrib.get("id") != "docker_image_version":
            continue

        version = setting.attrib.get("default", "").strip()
        if not version:
            raise ReleaseError("docker_image_version setting is missing a default version")
        versions[f"{SETTINGS_XML.relative_to(ROOT)} docker_image_version default"] = version

    if not versions:
        raise ReleaseError(f"{SETTINGS_XML.relative_to(ROOT)} is missing docker_image_version setting")

    return versions


def validate_versions() -> tuple[str, str, str, list[str]]:
    errors: list[str] = []
    addon_id, addon_version = "unknown", "unknown"
    package_version, image_version = "unknown", "unknown"

    try:
        addon_id, addon_version = addon_metadata()
        if addon_id != ADDON_DIR.name:
            errors.append(f"addon id {addon_id!r} does not match addon folder {ADDON_DIR.name!r}")
    except ReleaseError as exc:
        errors.append(str(exc))

    try:
        package_version = pyproject_version()
    except ReleaseError as exc:
        errors.append(str(exc))

    try:
        image_versions = docker_image_versions()
        distinct_versions = set(image_versions.values())
        if len(distinct_versions) > 1:
            details = ", ".join(f"{source} has {version}" for source, version in image_versions.items())
            errors.append(f"Version mismatch: Docker image versions do not align: {details}")
        if distinct_versions:
            image_version = next(iter(distinct_versions))
    except ReleaseError as exc:
        errors.append(str(exc))

    if addon_version != "unknown" and package_version != "unknown":
        if addon_version != package_version:
            errors.append(
                f"Version mismatch: {ADDON_XML.relative_to(ROOT)} has {addon_version}, "
                f"{PYPROJECT.relative_to(ROOT)} has {package_version}"
            )

    return addon_id, addon_version, image_version, errors


def tag_for(version: str, tag_override: str | None = None) -> str:
    return tag_override or f"v{version}"


def repository() -> tuple[str, str]:
    github_repository = os.environ.get("GITHUB_REPOSITORY")
    if github_repository:
        owner_repo = github_repository.strip()
    else:
        remote = run_git(["remote", "get-url", "origin"])
        match = re.search(r"github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?$", remote)
        if not match:
            raise ReleaseError("Could not infer GitHub repository from origin remote")
        owner_repo = f"{match.group(1)}/{match.group(2)}"

    if "/" not in owner_repo:
        raise ReleaseError(f"Invalid GitHub repository {owner_repo!r}")
    owner, repo = owner_repo.split("/", 1)
    return owner, repo


def github_request(
    method: str,
    path_or_url: str,
    token: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, dict[str, str]]:
    url = path_or_url if path_or_url.startswith("https://") else f"{API_ROOT}{path_or_url}"
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        request.add_header("Content-Type", content_type)

    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)
    except urllib.error.URLError as exc:
        raise ReleaseError(f"GitHub request failed: {exc}") from exc


def release_exists(owner: str, repo: str, tag: str, token: str) -> bool:
    status, body, _headers = github_request(
        "GET",
        f"/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(tag, safe='')}",
        token,
    )
    if status == 200:
        return True
    if status == 404:
        return False
    raise ReleaseError(f"Could not check release {tag}: HTTP {status} {body.decode('utf-8', errors='replace')}")


def validate_release_tag_available(
    version: str,
    tag_override: str | None = None,
    *,
    require_token: bool = True,
    token_override: str | None = None,
) -> tuple[str | None, str | None, str]:
    tag = tag_for(version, tag_override)
    token = token_override or os.environ.get("GITHUB_TOKEN")
    if not token:
        if require_token:
            raise ReleaseError("GITHUB_TOKEN is required to check whether the release tag already exists")
        return None, None, tag

    owner, repo = repository()
    if release_exists(owner, repo, tag, token):
        raise ReleaseError(f"Release {tag} already exists in {owner}/{repo}")

    return owner, repo, tag


def check_docker_image_exists(owner: str, version: str, token: str) -> tuple[bool, str]:
    """Verifies that the required Docker image tag exists on GHCR using the GitHub API."""
    last_error = "Package not found"
    # GitHub distinguishes between users and orgs in the package API path
    for type_prefix in ("users", "orgs"):
        status, body, headers = github_request(
            "GET",
            f"/{type_prefix}/{owner}/packages/container/{DOCKER_PACKAGE_NAME}/versions?per_page=100",
            token,
        )
        if status == 200:
            try:
                versions = json.loads(body.decode("utf-8"))
                if not isinstance(versions, list):
                    continue
                for v in versions:
                    tags = v.get("metadata", {}).get("container", {}).get("tags", [])
                    if version in tags:
                        return True, ""
                return False, f"Tag '{version}' not found in the last 100 versions of {DOCKER_PACKAGE_NAME}"
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        elif status != 404:
            # If we get a 403 or other error, record it
            last_error = f"HTTP {status}: {body.decode('utf-8', errors='replace').strip()}"
            # Check if it's a permission issue often seen with tokens
            if status == 403:
                last_error += " (Check if token has 'read:packages' scope)"

    return False, last_error


def create_zip() -> Path:
    if not ADDON_DIR.is_dir():
        raise ReleaseError(f"Missing addon directory {ADDON_DIR.relative_to(ROOT)}")

    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(ADDON_DIR.rglob("*")):
            if path.is_dir():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            archive.write(path, path.relative_to(ROOT).as_posix())

    return ZIP_PATH


def create_release(owner: str, repo: str, tag: str, version: str, token: str) -> dict:
    payload = {
        "tag_name": tag,
        "target_commitish": os.environ.get("GITHUB_SHA") or run_git(["rev-parse", "HEAD"]),
        "name": f"Sendspin Kodi {version}",
        "body": f"Release {version} for plugin.audio.sendspin.",
        "draft": False,
        "prerelease": False,
    }
    body = json.dumps(payload).encode("utf-8")
    status, response_body, _headers = github_request("POST", f"/repos/{owner}/{repo}/releases", token, body=body)
    if status not in {200, 201}:
        raise ReleaseError(
            f"Could not create release {tag}: HTTP {status} {response_body.decode('utf-8', errors='replace')}"
        )
    return json.loads(response_body.decode("utf-8"))


def upload_asset(release: dict, asset_path: Path, token: str) -> None:
    upload_url = str(release.get("upload_url", "")).split("{", 1)[0]
    if not upload_url:
        raise ReleaseError("GitHub release response did not include upload_url")

    query = urllib.parse.urlencode({"name": ASSET_NAME})
    body = asset_path.read_bytes()
    status, response_body, _headers = github_request(
        "POST",
        f"{upload_url}?{query}",
        token,
        body=body,
        content_type="application/zip",
    )
    if status not in {200, 201}:
        raise ReleaseError(
            f"Could not upload {ASSET_NAME}: HTTP {status} {response_body.decode('utf-8', errors='replace')}"
        )


def publish(tag_override: str | None = None, token_override: str | None = None) -> None:
    addon_id, version, image_version, errors = validate_versions()

    if not errors:
        print(f"Validated {addon_id} {version} with Docker image {image_version}")

    token = token_override or os.environ.get("GITHUB_TOKEN")

    owner: str | None = None
    repo: str | None = None
    tag = tag_for(version, tag_override)

    if not token:
        errors.append("GITHUB_TOKEN is required to publish a release")
    else:
        try:
            owner, repo, tag = validate_release_tag_available(version, tag_override, token_override=token)
            if owner and image_version != "unknown":
                if not check_docker_image_exists(owner, image_version, token):
                    errors.append(
                        f"Docker image ghcr.io/{owner}/{DOCKER_PACKAGE_NAME}:{image_version} not found on GHCR"
                    )
        except ReleaseError as exc:
            errors.append(str(exc))

    if errors:
        raise ReleaseError("\n" + "\n".join(f"  - {e}" for e in errors))

    # At this point, token, owner, and repo are guaranteed to be set if errors is empty
    assert token and owner and repo

    zip_path = create_zip()
    release = create_release(owner, repo, tag, version, token)
    upload_asset(release, zip_path, token)
    print(f"Published {addon_id} {version} with Docker image {image_version} as {tag} with asset {ASSET_NAME}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate, package, and publish plugin.audio.sendspin releases.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate versions, check the GitHub release tag is unused, and build the release zip",
    )
    parser.add_argument("--publish", action="store_true", help="create the GitHub release and upload the release zip")
    parser.add_argument("--tag", help="override the release tag; defaults to v<version>")
    parser.add_argument("--token", help="GitHub API token (overrides GITHUB_TOKEN env var)")
    args = parser.parse_args()

    if args.check == args.publish:
        parser.error("choose exactly one of --check or --publish")

    try:
        if args.publish:
            publish(args.tag, args.token)
        else:
            addon_id, version, image_version, local_errors = validate_versions()
            metadata_ok = not local_errors

            print_status("Metadata Validation", metadata_ok, f"{addon_id} {version}" if metadata_ok else "")
            if not metadata_ok:
                for e in local_errors:
                    print_status("  > Error", False, e)

            token = args.token or os.environ.get("GITHUB_TOKEN")
            tag = tag_for(version, args.tag)
            remote_ok = True

            if token:
                try:
                    owner, repo = repository()
                    if release_exists(owner, repo, tag, token):
                        print_status("GitHub Release Check", False, f"{tag} already exists")
                        remote_ok = False
                    else:
                        print_status("GitHub Release Check", True, f"{tag} available")

                    if image_version != "unknown":
                        exists, detail = check_docker_image_exists(owner, image_version, token)
                        if exists:
                            print_status("Docker Image Check", True, f"{image_version} found")
                        else:
                            print_status("Docker Image Check", False, detail)
                            remote_ok = False
                except ReleaseError as exc:
                    print_status("GitHub/Docker API", False, str(exc))
                    remote_ok = False
            else:
                print_status("GitHub Release Check", True, f"Skipped (no token, tag {tag})")
                print_status("Docker Image Check", True, "Skipped (no token)")

            if metadata_ok and remote_ok:
                zip_path = create_zip()
                print_status("Zip Creation", True, zip_path.name)

            if not (metadata_ok and remote_ok):
                return 1

    except ReleaseError as exc:
        # Only print unexpected errors that weren't already caught by the test output
        if not str(exc).startswith("\n"):
            print_status("Unexpected Error", False, str(exc))
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
