"""TLS certificate resolution service with pluggable strategy support.

Implements a strategy pattern so that switching TLS mode requires only a
``config/ui.yml`` change — no code modifications needed.

Supported strategies
--------------------
- ``manual``       — use pre-existing cert/key files at configured paths
- ``self_signed``  — generate a self-signed cert at server startup
- ``enterprise_ca``— fetch cert/key from an enterprise CA REST API

Usage::

    from src.services.tls_service import resolve_tls

    tls_result = resolve_tls(ui_yml_tls_section)
    if tls_result:
        cert_path, key_path = tls_result
        uvicorn.run(..., ssl_certfile=str(cert_path), ssl_keyfile=str(key_path))
"""
import datetime
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


def _restrict_key_permissions(key_path: Path) -> None:
    """Restrict permissions on a TLS private key file to ``0o600``.

    SECURITY: Private key files must not be world- or group-readable
    (CWE-732). On POSIX this enforces owner-only read/write. On Windows,
    ``os.chmod`` does not enforce POSIX semantics \u2014 the call is logged
    but otherwise has no effect. Operators on Windows should rely on
    file-system ACLs instead.

    Args:
        key_path: Path to the TLS private key file just written to disk.
    """
    try:
        os.chmod(key_path, 0o600)
    except OSError as exc:
        logger.warning(
            "Could not chmod TLS key %s to 0o600: %s", key_path, exc
        )


class TLSStrategy(ABC):
    """Abstract base for TLS certificate resolution strategies.

    Subclasses must implement :meth:`resolve` and return a
    ``(cert_path, key_path)`` tuple.
    """

    @abstractmethod
    def resolve(self, config: dict) -> tuple:
        """Resolve cert and key paths, generating or fetching as needed.

        Args:
            config: The ``tls`` section dict from ``ui.yml``.

        Returns:
            Tuple of ``(cert_path, key_path)`` as :class:`pathlib.Path` objects.

        Raises:
            RuntimeError: If the cert cannot be resolved.
        """


class ManualTLSStrategy(TLSStrategy):
    """Use pre-existing cert/key files at paths specified in ``ui.yml``.

    The paths must exist on disk before the server starts.  No generation
    or network calls are made.

    Required config keys:
        ``cert_path`` — absolute path to the PEM certificate.
        ``key_path``  — absolute path to the PEM private key.
    """

    def resolve(self, config: dict) -> tuple:
        """Return the configured cert/key paths after verifying they exist.

        Args:
            config: The ``tls`` section dict from ``ui.yml``.

        Returns:
            ``(cert_path, key_path)`` as :class:`pathlib.Path` objects.

        Raises:
            RuntimeError: If either file does not exist.
        """
        cert = Path(config["cert_path"])
        key = Path(config["key_path"])
        if not cert.exists():
            raise RuntimeError(f"TLS cert not found: {cert}")
        if not key.exists():
            raise RuntimeError(f"TLS key not found: {key}")
        return cert, key


class SelfSignedTLSStrategy(TLSStrategy):
    """Generate a self-signed TLS certificate at server startup.

    Requires the ``cryptography`` package (``pip install cryptography``).
    The certificate is written to the configured paths and is valid for
    365 days.  The ``cn`` and ``san`` config keys control the certificate
    subject / SANs.

    Optional config keys:
        ``cert_path`` — destination PEM cert file (default:
            ``/tmp/valdo-tls/cert.pem``).
        ``key_path``  — destination PEM key file (default:
            ``/tmp/valdo-tls/key.pem``).
        ``cn``        — common name (default: ``localhost``).
        ``san``       — list of DNS names or IP addresses for the SAN
            extension (default: ``[cn]``).
    """

    def resolve(self, config: dict) -> tuple:
        """Generate a self-signed cert/key pair and return their paths.

        Args:
            config: The ``tls`` section dict from ``ui.yml``.

        Returns:
            ``(cert_path, key_path)`` as :class:`pathlib.Path` objects.

        Raises:
            RuntimeError: If the ``cryptography`` package is not installed.
        """
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID
        except ImportError as exc:
            raise RuntimeError(
                "cryptography package required for self_signed TLS. "
                "Install with: pip install cryptography"
            ) from exc

        cert_path = Path(config.get("cert_path", "/tmp/valdo-tls/cert.pem"))
        key_path = Path(config.get("key_path", "/tmp/valdo-tls/key.pem"))
        cn = config.get("cn", "localhost")
        san_names = config.get("san", [cn])

        cert_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.parent.mkdir(parents=True, exist_ok=True)

        # Generate private key
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_path.write_bytes(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        # Lock down the key file before doing anything else with it.
        _restrict_key_permissions(key_path)

        # Build SAN list — split IP addresses from DNS names
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        san_list = []
        for name in san_names:
            try:
                import ipaddress as _ip
                san_list.append(x509.IPAddress(_ip.ip_address(name)))
            except ValueError:
                san_list.append(x509.DNSName(name))

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
            .sign(private_key, hashes.SHA256())
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        logger.info("Self-signed TLS cert generated: %s (CN=%s)", cert_path, cn)
        return cert_path, key_path


class EnterpriseCAStrategy(TLSStrategy):
    """Fetch a TLS certificate from an enterprise certificate authority API.

    On first call the strategy POSTs a JSON request to ``ca_api_url`` and
    caches the returned cert/key to disk.  On subsequent calls it returns
    the cached cert if its remaining validity exceeds ``expiry_warn_days``.

    Required config keys:
        ``ca_api_url``       — URL of the CA issuance endpoint.

    Optional config keys:
        ``cert_path``        — destination PEM cert (default:
            ``/tmp/valdo-tls/cert.pem``).
        ``key_path``         — destination PEM key (default:
            ``/tmp/valdo-tls/key.pem``).
        ``cn``               — common name sent in the API request
            (default: ``localhost``).
        ``san``              — SAN list sent in the API request
            (default: ``[cn]``).
        ``ca_api_token_env`` — name of the env var holding the bearer token
            (default: ``CA_API_TOKEN``).
        ``expiry_warn_days`` — minimum days of validity required to use the
            cached cert (default: ``30``).
    """

    def resolve(self, config: dict) -> tuple:
        """Return cert/key paths, using cache if valid or fetching from CA.

        Args:
            config: The ``tls`` section dict from ``ui.yml``.

        Returns:
            ``(cert_path, key_path)`` as :class:`pathlib.Path` objects.

        Raises:
            RuntimeError: If the CA API returns an error or the response
                cannot be parsed.
        """
        import json
        import urllib.request

        ca_api_url = config["ca_api_url"]
        token_env = config.get("ca_api_token_env", "CA_API_TOKEN")
        token = os.getenv(token_env, "")
        cert_path = Path(config.get("cert_path", "/tmp/valdo-tls/cert.pem"))
        key_path = Path(config.get("key_path", "/tmp/valdo-tls/key.pem"))
        cn = config.get("cn", "localhost")
        san_names = config.get("san", [cn])
        warn_days = config.get("expiry_warn_days", 30)

        # Return cached cert if still sufficiently valid
        if cert_path.exists() and key_path.exists():
            cached_expiry = _cert_expiry(cert_path)
            if cached_expiry and cached_expiry > datetime.datetime.utcnow() + datetime.timedelta(
                days=warn_days
            ):
                logger.info(
                    "Using cached enterprise cert (expires %s)", cached_expiry.date()
                )
                return cert_path, key_path

        # Fetch from CA API
        payload = json.dumps({"cn": cn, "san": san_names}).encode()
        req = urllib.request.Request(
            ca_api_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        cert_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        cert_path.write_text(data["cert"])
        key_path.write_text(data["key"])
        # Lock down the key file as soon as it lands on disk.
        _restrict_key_permissions(key_path)
        logger.info("Enterprise CA cert fetched and cached to %s", cert_path)
        return cert_path, key_path


def _validate_cert_coverage(cert_path: Path, key_path: Path, config: dict) -> None:
    """Validate that the loaded cert covers the configured ``cn`` and ``san``.

    Fails fast at startup so misconfigured certs surface here instead of as
    opaque browser-side TLS errors. Checks performed:

    1. The certificate is currently valid (``notBefore`` <= now <= ``notAfter``).
    2. The configured ``cn`` appears in the cert's Subject CN **or** in its
       Subject Alternative Names (modern browsers ignore Subject CN, so SAN
       coverage is preferred — a missing CN with present SAN is allowed).
    3. Every entry in the configured ``san`` list is present in the cert's
       SAN extension. DNS names are matched case-insensitively; IP addresses
       are matched by parsed value so ``127.0.0.1`` and ``127.000.000.001``
       compare equal.
    4. The private key in ``key_path`` mathematically matches the public key
       in ``cert_path``. This catches the common mistake of pairing a fresh
       key file with a stale cert (or vice versa) after a renewal.

    Args:
        cert_path: Path to the PEM-encoded certificate (may include chain).
        key_path: Path to the PEM-encoded unencrypted private key.
        config: The ``tls`` section dict from ``ui.yml``.

    Raises:
        RuntimeError: If any of the above checks fails, with a message
            describing exactly what is missing or mismatched.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.x509.oid import ExtensionOID, NameOID
    except ImportError:
        # cryptography is required for self_signed; for manual/enterprise_ca
        # it is best-effort. Skip validation rather than crash on a minimal
        # install that uses pre-issued cert files.
        logger.warning(
            "cryptography package not installed; skipping TLS cert coverage "
            "validation. Install with: pip install cryptography"
        )
        return

    import ipaddress

    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except Exception as exc:
        raise RuntimeError(
            f"TLS cert at {cert_path} could not be parsed as PEM X.509: {exc}"
        ) from exc

    # ----- 1) Validity window --------------------------------------------
    # Use the *_utc accessors when available (cryptography >= 42); fall
    # back to the naive accessors for older installs and normalise to
    # naive UTC for comparison.
    now = datetime.datetime.utcnow()
    try:
        not_before = cert.not_valid_before_utc.replace(tzinfo=None)
        not_after = cert.not_valid_after_utc.replace(tzinfo=None)
    except AttributeError:
        not_before = cert.not_valid_before  # type: ignore[attr-defined]
        not_after = cert.not_valid_after  # type: ignore[attr-defined]
    if now < not_before:
        raise RuntimeError(
            f"TLS cert at {cert_path} is not yet valid "
            f"(notBefore={not_before.isoformat()}, now={now.isoformat()})"
        )
    if now > not_after:
        raise RuntimeError(
            f"TLS cert at {cert_path} has expired "
            f"(notAfter={not_after.isoformat()}, now={now.isoformat()})"
        )

    # ----- 2/3) Build cert SAN inventory ---------------------------------
    cert_dns: set = set()
    cert_ips: set = set()
    try:
        san_ext = cert.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        ).value
        for entry in san_ext:
            if isinstance(entry, x509.DNSName):
                cert_dns.add(entry.value.lower())
            elif isinstance(entry, x509.IPAddress):
                cert_ips.add(entry.value)
    except x509.ExtensionNotFound:
        pass  # cert has no SAN — older cert; CN-only fallback below

    cert_cn = None
    try:
        cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if cn_attrs:
            cert_cn = cn_attrs[0].value.lower()
    except Exception:
        cert_cn = None

    def _covered(name: str) -> bool:
        """Return True if ``name`` is covered by SAN or matches Subject CN."""
        name_l = name.lower()
        if name_l in cert_dns:
            return True
        if cert_cn and cert_cn == name_l:
            return True
        try:
            ip = ipaddress.ip_address(name)
            if ip in cert_ips:
                return True
        except ValueError:
            pass
        # Wildcard match: cert SAN "*.corp.example.com" covers
        # "valdo.corp.example.com" but not "corp.example.com" itself,
        # mirroring RFC 6125 §6.4.3 semantics for the leftmost label.
        for dns in cert_dns:
            if dns.startswith("*.") and "." in name_l:
                if name_l.split(".", 1)[1] == dns[2:]:
                    return True
        return False

    configured_cn = config.get("cn")
    if configured_cn and not _covered(configured_cn):
        raise RuntimeError(
            f"TLS cert at {cert_path} does not cover the configured cn "
            f"{configured_cn!r}. Cert SAN DNS={sorted(cert_dns)}, "
            f"SAN IP={sorted(str(i) for i in cert_ips)}, "
            f"Subject CN={cert_cn!r}. Reissue the cert with the missing "
            f"name in its SAN list, or update tls.cn / tls.san in ui.yml."
        )

    missing = [n for n in (config.get("san") or []) if not _covered(n)]
    if missing:
        raise RuntimeError(
            f"TLS cert at {cert_path} is missing SAN entries required by "
            f"ui.yml tls.san: {missing}. Cert SAN DNS={sorted(cert_dns)}, "
            f"SAN IP={sorted(str(i) for i in cert_ips)}. Reissue the cert "
            f"with these names in its SAN list, or remove them from ui.yml."
        )

    # ----- 4) Cert/key pairing -------------------------------------------
    try:
        private_key = serialization.load_pem_private_key(
            key_path.read_bytes(), password=None
        )
    except Exception as exc:
        raise RuntimeError(
            f"TLS key at {key_path} could not be loaded as an unencrypted "
            f"PEM private key: {exc}. If your key is in PKCS#12 format, "
            f"extract it with: openssl pkcs12 -in cert.pfx -nocerts -nodes "
            f"-out key.pem"
        ) from exc

    cert_pub = cert.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_pub = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if cert_pub != key_pub:
        raise RuntimeError(
            f"TLS cert/key mismatch: the public key in {cert_path} does not "
            f"match the private key in {key_path}. This usually means a cert "
            f"renewal updated only one of the two files. Re-pair them and "
            f"restart."
        )

    logger.info(
        "TLS cert coverage validated: cn=%s, san=%s, expires=%s",
        configured_cn,
        config.get("san") or [],
        not_after.date(),
    )


def _cert_expiry(cert_path: Path):
    """Return the expiry :class:`datetime.datetime` of a PEM cert, or ``None``.

    Args:
        cert_path: Path to the PEM-encoded X.509 certificate.

    Returns:
        A naive UTC :class:`datetime.datetime` representing the
        ``notAfter`` field, or ``None`` if the cert cannot be read or
        the ``cryptography`` package is unavailable.
    """
    try:
        from cryptography import x509 as _x509

        cert = _x509.load_pem_x509_certificate(cert_path.read_bytes())
        # not_valid_after_utc is available in cryptography >= 42; fall back
        # to not_valid_after for older installs.
        try:
            return cert.not_valid_after_utc.replace(tzinfo=None)
        except AttributeError:
            return cert.not_valid_after  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        return None


_STRATEGIES: dict = {
    "manual": ManualTLSStrategy,
    "self_signed": SelfSignedTLSStrategy,
    "enterprise_ca": EnterpriseCAStrategy,
}


def resolve_tls(tls_config: dict):
    """Resolve TLS cert and key from the ``tls`` section of ``ui.yml``.

    This is the primary public entry-point used by the ``valdo serve``
    command.  If TLS is disabled (or the config is empty) it returns
    ``None`` and the server starts in plain HTTP mode.

    Args:
        tls_config: The ``tls`` dict parsed from ``config/ui.yml``.
            Pass ``{}`` or ``None`` for no TLS.

    Returns:
        A ``(cert_path, key_path)`` tuple of :class:`pathlib.Path` objects,
        or ``None`` when TLS is disabled.

    Raises:
        RuntimeError: If the strategy name is unrecognised or cert
            resolution fails.

    Example::

        result = resolve_tls({"enabled": True, "strategy": "self_signed",
                               "cn": "valdo.internal"})
        if result:
            cert_path, key_path = result
    """
    if not tls_config or not tls_config.get("enabled", False):
        return None

    strategy_name = tls_config.get("strategy", "manual")
    strategy_cls = _STRATEGIES.get(strategy_name)
    if not strategy_cls:
        raise RuntimeError(
            f"Unknown TLS strategy: {strategy_name!r}. Valid: {list(_STRATEGIES)}"
        )

    strategy = strategy_cls()
    cert_path, key_path = strategy.resolve(tls_config)

    # Validate that the resolved cert actually covers the configured cn/san
    # and pairs with the key. Fails fast with a descriptive RuntimeError so
    # misconfiguration surfaces here rather than as an opaque browser TLS
    # error. Skipped for self_signed because that strategy just generated
    # the cert from the same cn/san values; re-checking would only add log
    # noise without catching a real failure mode.
    if strategy_name != "self_signed":
        _validate_cert_coverage(cert_path, key_path, tls_config)

    # Log cert info and warn if near expiry
    expiry = _cert_expiry(cert_path)
    warn_days = tls_config.get("expiry_warn_days", 30)
    logger.info(
        "TLS strategy=%s cert=%s expires=%s",
        strategy_name,
        cert_path,
        expiry.date() if expiry else "unknown",
    )
    if expiry:
        days_left = (expiry - datetime.datetime.utcnow()).days
        if days_left < warn_days:
            logger.warning(
                "TLS cert expires in %d days (threshold: %d)", days_left, warn_days
            )

    return cert_path, key_path
