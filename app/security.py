import ipaddress
import re
import socket
from pathlib import Path, PurePath
from urllib.parse import urlsplit, urlunsplit

import tldextract

ALLOWED_HEADERS = {"user-agent", "referer", "cookie", "authorization"}
TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def validate_headers(headers: dict[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in (headers or {}).items():
        normalized = name.strip().lower()
        if normalized not in ALLOWED_HEADERS:
            raise ValueError(f"Header is not allowed: {name}")
        if "\r" in value or "\n" in value:
            raise ValueError(f"Header contains a newline: {name}")
        result[name.strip()] = value.strip()
    return result


def infer_referer(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP and HTTPS URLs are supported")
    extracted = TLD_EXTRACTOR(parsed.hostname)
    host = extracted.top_domain_under_public_suffix or parsed.hostname
    return urlunsplit((parsed.scheme, host, "/", "", ""))


def validate_remote_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP and HTTPS URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in URLs are not allowed")

    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(
                parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
            )
        }
    except socket.gaierror as exc:
        raise ValueError("The URL host could not be resolved") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private, loopback, link-local, and reserved hosts are not allowed")
    return url.strip()


def sanitize_output_name(value: str) -> str:
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value.strip()).strip(" .")
    if name.lower().endswith(".mp4"):
        name = name[:-4].rstrip(" .")
    if not name or name.upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError("Invalid output filename")
    return name[:180]


def sanitize_subdir(value: str | None) -> str:
    if not value:
        return ""
    candidate = value.replace("\\", "/").strip("/")
    if not candidate:
        return ""
    if ":" in candidate or PurePath(candidate).is_absolute():
        raise ValueError("Output directory must be relative")
    parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Output directory contains an unsafe path component")
    return "/".join(sanitize_output_name(part) for part in parts)


def safe_output_path(root: Path, subdir: str, filename: str) -> Path:
    root = root.resolve()
    target = (root / subdir / filename).resolve()
    if root != target and root not in target.parents:
        raise ValueError("Output path escapes the download directory")
    return target


def redact_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "<redacted>" if parsed.query else "", "")
    )
