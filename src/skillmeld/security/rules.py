# SPDX-License-Identifier: Apache-2.0
"""Core deterministic rule set for the security gate.

Rule ids are stable and auditable (``core:<slug>``); categories follow Snyk's ToxicSkills
8-category taxonomy. Severity drives the tri-state verdict: critical -> BLOCK, high or
medium -> REVIEW, low/info -> recorded only. BLOCK-class rules are deliberately rare and
high-precision (reverse shells, credential exfiltration, decoded hostile payloads); anything
probabilistic sits at REVIEW so the one security prompt the user sees stays trustworthy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

RULESET_VERSION = "2026.06.09"

# Snyk ToxicSkills taxonomy.
PROMPT_INJECTION = "prompt-injection"
MALICIOUS_CODE = "malicious-code"
SUSPICIOUS_DOWNLOAD = "suspicious-download"
CREDENTIAL_HANDLING = "credential-handling"
SECRET_EXPOSURE = "secret-exposure"
THIRD_PARTY_CONTENT = "third-party-content"
UNVERIFIABLE_DEPENDENCY = "unverifiable-dependency"
MONEY_ACCESS = "money-access"
# Engine notices (caps, scanner errors, stale hosted verdicts) — not part of the taxonomy.
META = "meta"
# License findings (detection, conflicts, incompatible combinations) — ours, not Snyk's.
LICENSE = "license"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FileKind(StrEnum):
    MARKDOWN = "markdown"
    SCRIPT = "script"
    MANIFEST = "manifest"
    OTHER = "other"


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    severity: Severity
    kinds: frozenset[FileKind]
    pattern: re.Pattern[str]
    message: str


ALL_KINDS = frozenset(FileKind)
_SCRIPTISH = frozenset({FileKind.SCRIPT, FileKind.MANIFEST})


def _rule(
    slug: str,
    category: str,
    severity: Severity,
    pattern: str,
    message: str,
    *,
    kinds: frozenset[FileKind] = ALL_KINDS,
    flags: int = re.IGNORECASE,
) -> Rule:
    return Rule(
        id=f"core:{slug}",
        category=category,
        severity=severity,
        kinds=kinds,
        pattern=re.compile(pattern, flags),
        message=message,
    )


LINE_RULES: tuple[Rule, ...] = (
    # --- prompt injection ---------------------------------------------------------------
    _rule(
        "instruction-override",
        PROMPT_INJECTION,
        Severity.HIGH,
        r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|earlier|above)\s+"
        r"(?:instructions|rules|prompts|directives)",
        "Attempts to override the agent's prior instructions.",
    ),
    _rule(
        "instruction-disregard",
        PROMPT_INJECTION,
        Severity.HIGH,
        r"disregard\s+(?:the\s+)?(?:system|previous|prior|safety)\s+"
        r"(?:prompt|prompts|instructions|rules|guidelines)",
        "Tells the agent to disregard system or safety instructions.",
    ),
    _rule(
        "covert-action",
        PROMPT_INJECTION,
        Severity.HIGH,
        r"(?:without|don'?t|do\s+not)\s+(?:telling|informing|asking|alerting|notifying)\s+"
        r"the\s+user",
        "Instructs the agent to act behind the user's back.",
    ),
    _rule(
        "conceal-from-user",
        PROMPT_INJECTION,
        Severity.HIGH,
        r"(?:hide|conceal)\s+(?:this|it|these|the\s+following)\s+from\s+the\s+user",
        "Instructs the agent to conceal content from the user.",
    ),
    _rule(
        "system-prompt-extraction",
        PROMPT_INJECTION,
        Severity.MEDIUM,
        r"(?:reveal|print|show|output|repeat)\s+(?:your\s+)?system\s+prompt",
        "Asks the agent to expose its system prompt.",
    ),
    # --- malicious code (shell-level) ---------------------------------------------------
    _rule(
        "reverse-shell",
        MALICIOUS_CODE,
        Severity.CRITICAL,
        r"(?:/dev/tcp/|bash\s+-i\s+>&|\bnc(?:at)?\s+(?:-e|--exec)\b|"
        r"mkfifo\s+\S+.{0,40}\|\s*(?:nc|netcat)\b)",
        "Reverse-shell construction.",
    ),
    _rule(
        "fork-bomb",
        MALICIOUS_CODE,
        Severity.CRITICAL,
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;",
        "Shell fork bomb.",
        flags=0,
    ),
    _rule(
        "destructive-rm",
        MALICIOUS_CODE,
        Severity.CRITICAL,
        r"rm\s+-[a-z]*[rR][a-z]*\s+(?:--no-preserve-root\s+)?(?:/|~|\"?\$HOME\"?)(?=[\s\"'`;]|$)",
        "Recursive delete aimed at the filesystem root or home.",
        flags=0,
    ),
    _rule(
        "no-preserve-root",
        MALICIOUS_CODE,
        Severity.CRITICAL,
        r"--no-preserve-root",
        "Explicitly disables the root-deletion safety guard.",
    ),
    _rule(
        "disk-overwrite",
        MALICIOUS_CODE,
        Severity.CRITICAL,
        r"\bdd\s+.{0,40}\bof=/dev/(?:sd[a-z]|nvme|disk)",
        "Raw write to a block device.",
    ),
    _rule(
        "eval-remote",
        MALICIOUS_CODE,
        Severity.CRITICAL,
        r"(?:\beval\b|\bexec\b)\s*[(\s].{0,60}(?:curl|wget|fetch\(|urlopen|requests\.get)",
        "Evaluates code fetched from the network.",
    ),
    _rule(
        "eval-encoded",
        MALICIOUS_CODE,
        Severity.CRITICAL,
        r"(?:\beval\b|\bexec\b)\s*\(.{0,60}b(?:ase)?64\s*[._]?\s*(?:decode|b64decode)",
        "Evaluates base64-decoded code.",
    ),
    _rule(
        "history-tamper",
        MALICIOUS_CODE,
        Severity.MEDIUM,
        r"(?:history\s+-c\b|unset\s+HISTFILE|shred\s+.{0,30}history)",
        "Tampers with shell history.",
        flags=0,
    ),
    _rule(
        "install-hook",
        MALICIOUS_CODE,
        Severity.HIGH,
        r"\"(?:pre|post)install\"\s*:",
        "npm lifecycle install hook runs code at install time.",
        kinds=frozenset({FileKind.MANIFEST}),
        flags=0,
    ),
    # --- suspicious download ------------------------------------------------------------
    _rule(
        "raw-ip-url",
        SUSPICIOUS_DOWNLOAD,
        Severity.HIGH,
        r"https?://(?:\d{1,3}\.){3}\d{1,3}",
        "URL addresses a raw IP instead of a named host.",
    ),
    _rule(
        "password-archive",
        SUSPICIOUS_DOWNLOAD,
        Severity.MEDIUM,
        r"(?:unzip|7z[az]?)\s+.{0,40}\s-[pP]\S+",
        "Extracts a password-protected archive (scanner-evasion pattern).",
        flags=0,
    ),
    # --- unverifiable dependency --------------------------------------------------------
    _rule(
        "pipe-to-shell",
        UNVERIFIABLE_DEPENDENCY,
        Severity.HIGH,
        r"(?:curl|wget)\b[^|;\n]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b",
        "Pipes a downloaded script straight into a shell.",
    ),
    _rule(
        "remote-script-exec",
        UNVERIFIABLE_DEPENDENCY,
        Severity.HIGH,
        r"(?:\bsh\b|\bbash\b)\s+<\(\s*(?:curl|wget)",
        "Executes a remote script via process substitution.",
    ),
    _rule(
        "git-http-dependency",
        UNVERIFIABLE_DEPENDENCY,
        Severity.MEDIUM,
        r"git\+http://",
        "Dependency fetched over unencrypted HTTP.",
    ),
    # --- third-party content ------------------------------------------------------------
    _rule(
        "remote-instructions",
        THIRD_PARTY_CONTENT,
        Severity.MEDIUM,
        r"(?:fetch|read|load|download|retrieve|get)\b.{0,50}\b(?:instructions?|prompts?)\s+"
        r"from\s+https?://",
        "Pulls agent instructions from an external URL at runtime.",
    ),
    # --- secret exposure (hardcoded) ----------------------------------------------------
    _rule(
        "aws-access-key",
        SECRET_EXPOSURE,
        Severity.HIGH,
        r"\bAKIA[0-9A-Z]{16}\b",
        "Hardcoded AWS access key id.",
        flags=0,
    ),
    _rule(
        "github-token",
        SECRET_EXPOSURE,
        Severity.HIGH,
        r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
        "Hardcoded GitHub token.",
        flags=0,
    ),
    _rule(
        "slack-token",
        SECRET_EXPOSURE,
        Severity.HIGH,
        r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",
        "Hardcoded Slack token.",
        flags=0,
    ),
    _rule(
        "anthropic-key",
        SECRET_EXPOSURE,
        Severity.HIGH,
        r"\bsk-ant-[A-Za-z0-9-]{20,}\b",
        "Hardcoded Anthropic API key.",
        flags=0,
    ),
    _rule(
        "private-key-block",
        SECRET_EXPOSURE,
        Severity.HIGH,
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        "Embedded private key material.",
        flags=0,
    ),
    _rule(
        "generic-api-key",
        SECRET_EXPOSURE,
        Severity.MEDIUM,
        r"(?:api[_-]?key|secret|token|password)\s*[:=]\s*"
        r"['\"](?!(?:your|example|placeholder|change|xxxx|test|dummy|sample|insert))"
        r"[A-Za-z0-9+/_-]{20,}['\"]",
        "Possible hardcoded credential.",
    ),
    # --- money access -------------------------------------------------------------------
    _rule(
        "wallet-material",
        MONEY_ACCESS,
        Severity.MEDIUM,
        r"(?:seed\s+phrase|mnemonic\s+(?:phrase|words)|wallet\s+private\s+key|"
        r"recovery\s+phrase)",
        "Handles cryptocurrency wallet recovery material.",
    ),
)

# Credential-handling co-occurrence inputs (special check in scan.py, not plain line rules).
SECRET_PATH_RE = re.compile(
    r"(?:~/\.aws/credentials|~/\.ssh/|\bid_rsa\b|\bid_ed25519\b|\.netrc\b|\.npmrc\b|"
    r"\.pypirc\b|/etc/(?:passwd|shadow)\b|\.env\b|\bkeychain\b|\.kube/config\b|"
    r"\.docker/config\.json\b|\bauthorized_keys\b|\.git-credentials\b)",
    re.IGNORECASE,
)
NETWORK_RE = re.compile(
    r"(?:\bcurl\b|\bwget\b|\bnc\b|\bncat\b|requests\.(?:post|put|get)|urllib|urlopen|"
    r"\bfetch\s*\(|httpx|http\.client|\bscp\b|\brsync\b.{0,40}@|Invoke-WebRequest)",
    re.IGNORECASE,
)
CREDENTIAL_WINDOW = 10

# Network-egress allowlist: suffix-matched against URL hosts found in skill content.
ALLOWED_DOMAINS = frozenset(
    {
        "github.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
        "gist.github.com",
        "pypi.org",
        "files.pythonhosted.org",
        "npmjs.com",
        "registry.npmjs.org",
        "python.org",
        "nodejs.org",
        "anthropic.com",
        "claude.ai",
        "claude.com",
        "modelcontextprotocol.io",
        "ifylab.dev",
    }
)
URL_HOST_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")

# Typosquat targets: popular package names; a manifest dependency at edit distance one of
# these (and not itself a known name) is flagged.
POPULAR_PACKAGES = frozenset(
    {
        "requests",
        "numpy",
        "pandas",
        "scipy",
        "matplotlib",
        "flask",
        "django",
        "pytest",
        "httpx",
        "pydantic",
        "boto3",
        "botocore",
        "urllib3",
        "setuptools",
        "pillow",
        "cryptography",
        "sqlalchemy",
        "fastapi",
        "aiohttp",
        "beautifulsoup4",
        "lxml",
        "openpyxl",
        "rich",
        "typer",
        "click",
        "jinja2",
        "pyyaml",
        "react",
        "lodash",
        "express",
        "axios",
        "typescript",
        "webpack",
        "vite",
        "next",
        "vue",
        "jest",
        "eslint",
        "prettier",
    }
)
# Real packages that sit at edit distance one of a popular name; never flag these.
TYPOSQUAT_EXCEPTIONS = frozenset({"request", "vitest"})

BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff")
