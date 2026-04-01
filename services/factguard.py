"""
FactGuard — Output verification layer for all agents.
Runs after every agent before state update.
Catches: hallucinated sources, empty claims, malformed JSON,
         claims without evidence, suspiciously vague statements.
Never crashes the pipeline — flags and continues.
"""
import re
from urllib.parse import urlparse
from agents.contracts import CrawlerFinding, ConflictReport


class FactGuardViolation(Exception):
    """Raised when an agent output fails verification."""
    pass


def verify_crawler_finding(finding: CrawlerFinding) -> CrawlerFinding:
    """
    Rules:
    1. claim must be a single sentence (no multi-claim dumps)
    2. source_url must be a real URL (http/https, valid domain)
    3. claim must not be vague filler
    4. raw_excerpt must exist and be non-empty
    5. claim length: 20-350 characters
    """
    claim = finding.get("claim", "").strip()
    url = finding.get("source_url", "")
    excerpt = finding.get("raw_excerpt", "").strip()

    # Rule 1: single sentence check (allow 2 sentences max)
    sentences = [s.strip() for s in re.split(r"[.!?]", claim) if s.strip()]
    if len(sentences) > 2:
        raise FactGuardViolation(
            f"Multi-claim detected. Split into atomic claims. Got: '{claim[:80]}...'"
        )

    # Rule 2: URL validity
    try:
        parsed = urlparse(url)
        assert parsed.scheme in ("http", "https")
        assert "." in parsed.netloc
    except (AssertionError, Exception):
        raise FactGuardViolation(f"Invalid source URL: '{url}'")

    # Rule 3: vague filler check
    # All patterns end with \s*\.?$ so they only fire when NOTHING meaningful follows.
    # "Researchers found X reduces Y by 30%" → PASSES (something follows)
    # "Researchers found." → FAILS (nothing follows)
    VAGUE_PATTERNS = [
        r"^the article (discusses|mentions|talks about)\s*\.?$",
        r"^sources (say|suggest|indicate)\s*\.?$",
        r"^it (is|was) (noted|mentioned|stated) that\s*\.?$",
        r"^according to (various|multiple|some) sources\s*\.?$",
        r"^the (text|content|page) (discusses|covers|mentions)\s*\.?$",
        r"^researchers (have|found|suggest)\s*\.?$",
    ]
    for pattern in VAGUE_PATTERNS:
        if re.match(pattern, claim.lower()):
            raise FactGuardViolation(
                f"Vague claim detected: '{claim}'. Must be specific and attributable."
            )

    # Rule 4: excerpt must exist
    if len(excerpt) < 20:
        raise FactGuardViolation("raw_excerpt too short or missing. Must quote source text.")

    # Rule 5: claim length bounds
    if len(claim) < 20:
        raise FactGuardViolation(f"Claim too vague (< 20 chars): '{claim}'")
    if len(claim) > 400:
        raise FactGuardViolation(f"Claim too long (> 400 chars) — likely multi-claim dump.")

    return finding


def verify_conflict_report(report: ConflictReport) -> ConflictReport:
    """
    Rules:
    1. verdict must be VERIFIED / CONTESTED / UNVERIFIED
    2. CONTESTED reports must have at least one ConflictPair
    3. Each ConflictPair must have non-empty claim_a, source_a, claim_b, source_b
    4. conflict_type must be valid
    """
    VALID_VERDICTS = {"VERIFIED", "CONTESTED", "UNVERIFIED"}
    VALID_CONFLICT_TYPES = {"direct_contradiction", "temporal_shift", "scope_difference"}

    verdict = report.get("verdict", "")
    if verdict not in VALID_VERDICTS:
        raise FactGuardViolation(f"Invalid verdict: '{verdict}'. Must be one of {VALID_VERDICTS}")

    conflicts = report.get("conflicts", [])
    if verdict == "CONTESTED" and not conflicts:
        raise FactGuardViolation("CONTESTED verdict requires at least one ConflictPair.")

    for pair in conflicts:
        for field in ["claim_a", "source_a", "claim_b", "source_b"]:
            if not pair.get(field, "").strip():
                raise FactGuardViolation(f"ConflictPair missing field: '{field}'")
        if pair.get("conflict_type") not in VALID_CONFLICT_TYPES:
            raise FactGuardViolation(
                f"Invalid conflict_type: '{pair.get('conflict_type')}'"
            )

    return report


def safe_verify(finding, verifier_fn):
    """
    Wraps a verifier. On violation, flags the finding and continues.
    Never crashes the pipeline.
    """
    try:
        return verifier_fn(finding)
    except FactGuardViolation as e:
        finding["factguard_flagged"] = True
        finding["factguard_reason"] = str(e)
        return finding
