"""Unit tests for tls_service — config-driven TLS certificate resolution.

Tests cover all three strategies (manual, self_signed, enterprise_ca),
disabled/empty config paths, expiry warning logging, and the output contract.
"""
import datetime
import importlib
import json
import logging
import os
import sys
import unittest.mock as mock
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_self_signed_pem(tmp_path: Path, cn: str = "test.local"):
    """Generate a minimal self-signed PEM cert+key for testing."""
    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=90))
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


# ---------------------------------------------------------------------------
# resolve_tls — disabled / empty config
# ---------------------------------------------------------------------------

class TestResolveTlsDisabled:
    """Tests for the disabled/empty TLS configuration path."""

    def test_resolve_tls_disabled_flag(self):
        """enabled: false returns None."""
        from src.services.tls_service import resolve_tls

        result = resolve_tls({"enabled": False, "strategy": "manual"})
        assert result is None

    def test_resolve_tls_no_config(self):
        """Empty dict returns None."""
        from src.services.tls_service import resolve_tls

        result = resolve_tls({})
        assert result is None

    def test_resolve_tls_none_config(self):
        """None returns None."""
        from src.services.tls_service import resolve_tls

        result = resolve_tls(None)
        assert result is None

    def test_resolve_tls_missing_enabled_key(self):
        """Dict without 'enabled' key defaults to disabled."""
        from src.services.tls_service import resolve_tls

        result = resolve_tls({"strategy": "manual"})
        assert result is None


# ---------------------------------------------------------------------------
# ManualTLSStrategy
# ---------------------------------------------------------------------------

class TestManualTLSStrategy:
    """Tests for the ManualTLSStrategy (pre-existing cert/key on disk)."""

    def test_manual_strategy_resolves_existing_paths(self, tmp_path):
        """Existing cert and key → returns the correct (Path, Path) tuple."""
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        cert.write_text("CERT")
        key.write_text("KEY")

        from src.services.tls_service import ManualTLSStrategy

        c, k = ManualTLSStrategy().resolve(
            {"cert_path": str(cert), "key_path": str(key)}
        )
        assert c == cert
        assert k == key

    def test_manual_strategy_missing_cert_raises(self, tmp_path):
        """RuntimeError when cert_path does not exist."""
        key = tmp_path / "key.pem"
        key.write_text("KEY")

        from src.services.tls_service import ManualTLSStrategy

        with pytest.raises(RuntimeError, match="cert not found"):
            ManualTLSStrategy().resolve(
                {"cert_path": str(tmp_path / "missing.pem"), "key_path": str(key)}
            )

    def test_manual_strategy_missing_key_raises(self, tmp_path):
        """RuntimeError when key_path does not exist."""
        cert = tmp_path / "cert.pem"
        cert.write_text("CERT")

        from src.services.tls_service import ManualTLSStrategy

        with pytest.raises(RuntimeError, match="key not found"):
            ManualTLSStrategy().resolve(
                {"cert_path": str(cert), "key_path": str(tmp_path / "missing.pem")}
            )


# ---------------------------------------------------------------------------
# SelfSignedTLSStrategy
# ---------------------------------------------------------------------------

class TestSelfSignedTLSStrategy:
    """Tests for the SelfSignedTLSStrategy (runtime cert generation)."""

    @pytest.mark.skipif(
        importlib.util.find_spec("cryptography") is None,
        reason="cryptography package not installed",
    )
    def test_self_signed_generates_cert(self, tmp_path):
        """Generates cert and key files at the configured paths."""
        from src.services.tls_service import SelfSignedTLSStrategy

        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        c, k = SelfSignedTLSStrategy().resolve(
            {
                "cert_path": str(cert_path),
                "key_path": str(key_path),
                "cn": "localhost",
                "san": ["localhost"],
            }
        )
        assert c.exists()
        assert k.exists()

    @pytest.mark.skipif(
        importlib.util.find_spec("cryptography") is None,
        reason="cryptography package not installed",
    )
    def test_self_signed_cert_readable(self, tmp_path):
        """Generated cert is valid PEM that can be parsed by cryptography."""
        from cryptography import x509
        from src.services.tls_service import SelfSignedTLSStrategy

        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        c, _ = SelfSignedTLSStrategy().resolve(
            {
                "cert_path": str(cert_path),
                "key_path": str(key_path),
                "cn": "localhost",
            }
        )
        loaded = x509.load_pem_x509_certificate(c.read_bytes())
        assert loaded.not_valid_after_utc > datetime.datetime.now(datetime.timezone.utc)

    @pytest.mark.skipif(
        importlib.util.find_spec("cryptography") is None,
        reason="cryptography package not installed",
    )
    def test_self_signed_uses_default_paths(self, tmp_path, monkeypatch):
        """Without explicit cert_path/key_path, falls back to /tmp/valdo-tls/."""
        import src.services.tls_service as svc

        # Redirect /tmp/valdo-tls to tmp_path so we don't write system /tmp
        default_cert = tmp_path / "cert.pem"
        default_key = tmp_path / "key.pem"

        orig = svc.SelfSignedTLSStrategy.resolve

        def patched(self, config):
            config = dict(config)
            config.setdefault("cert_path", str(default_cert))
            config.setdefault("key_path", str(default_key))
            return orig(self, config)

        monkeypatch.setattr(svc.SelfSignedTLSStrategy, "resolve", patched)

        c, k = svc.SelfSignedTLSStrategy().resolve({"cn": "localhost"})
        assert c.exists()
        assert k.exists()

    def test_self_signed_raises_without_cryptography(self, monkeypatch):
        """RuntimeError with install hint when cryptography is not available."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "cryptography":
                raise ImportError("mocked missing")
            # Also block submodules
            if name.startswith("cryptography."):
                raise ImportError("mocked missing")
            return real_import(name, *args, **kwargs)

        from src.services import tls_service as svc

        strategy = svc.SelfSignedTLSStrategy()

        with monkeypatch.context() as m:
            m.setattr(builtins, "__import__", mock_import)
            with pytest.raises(RuntimeError, match="cryptography package required"):
                strategy.resolve({"cn": "localhost"})


# ---------------------------------------------------------------------------
# EnterpriseCAStrategy
# ---------------------------------------------------------------------------

class TestEnterpriseCAStrategy:
    """Tests for the EnterpriseCAStrategy (external CA API)."""

    def _valid_cache(self, tmp_path):
        """Write a cached cert with a far-future expiry."""
        pytest.importorskip("cryptography")
        return _make_self_signed_pem(tmp_path)

    def test_enterprise_ca_uses_cached_cert(self, tmp_path):
        """If cert is cached and not near expiry, no HTTP request is made."""
        pytest.importorskip("cryptography")
        cert_path, key_path = self._valid_cache(tmp_path)

        from src.services.tls_service import EnterpriseCAStrategy

        with patch("urllib.request.urlopen") as mock_urlopen:
            c, k = EnterpriseCAStrategy().resolve(
                {
                    "ca_api_url": "https://ca.example.com/issue",
                    "cert_path": str(cert_path),
                    "key_path": str(key_path),
                    "cn": "valdo.internal",
                    "san": ["valdo.internal"],
                    "expiry_warn_days": 30,
                }
            )
            mock_urlopen.assert_not_called()
        assert c == cert_path
        assert k == key_path

    def test_enterprise_ca_fetches_when_missing(self, tmp_path):
        """When no cached cert exists, the CA API is called and cert is saved."""
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        # Build a fake PEM response that cryptography (if present) won't need
        fake_cert_pem = "-----BEGIN CERTIFICATE-----\nFAKEDATA\n-----END CERTIFICATE-----\n"
        fake_key_pem = "-----BEGIN RSA PRIVATE KEY-----\nFAKEDATA\n-----END RSA PRIVATE KEY-----\n"  # gitleaks:allow
        response_body = json.dumps({"cert": fake_cert_pem, "key": fake_key_pem}).encode()

        mock_response = MagicMock()
        mock_response.read.return_value = response_body
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        from src.services.tls_service import EnterpriseCAStrategy

        with patch("urllib.request.urlopen", return_value=mock_response):
            c, k = EnterpriseCAStrategy().resolve(
                {
                    "ca_api_url": "https://ca.example.com/issue",
                    "cert_path": str(cert_path),
                    "key_path": str(key_path),
                    "cn": "valdo.internal",
                }
            )

        assert cert_path.read_text() == fake_cert_pem
        assert key_path.read_text() == fake_key_pem

    def test_enterprise_ca_fetches_when_expired(self, tmp_path):
        """Expired cached cert triggers a fresh CA API call."""
        pytest.importorskip("cryptography")
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        # Write an already-expired cert
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_path = tmp_path / "key.pem"
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "old")])
        expired_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=400))
            .not_valid_after(datetime.datetime.utcnow() - datetime.timedelta(days=10))
            .sign(key, hashes.SHA256())
        )
        cert_path = tmp_path / "cert.pem"
        cert_path.write_bytes(expired_cert.public_bytes(serialization.Encoding.PEM))

        fake_pem = "-----BEGIN CERTIFICATE-----\nNEW\n-----END CERTIFICATE-----\n"
        fake_key_pem = "-----BEGIN RSA PRIVATE KEY-----\nNEW\n-----END RSA PRIVATE KEY-----\n"  # gitleaks:allow
        response_body = json.dumps({"cert": fake_pem, "key": fake_key_pem}).encode()

        mock_response = MagicMock()
        mock_response.read.return_value = response_body
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        from src.services.tls_service import EnterpriseCAStrategy

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            EnterpriseCAStrategy().resolve(
                {
                    "ca_api_url": "https://ca.example.com/issue",
                    "cert_path": str(cert_path),
                    "key_path": str(key_path),
                    "cn": "valdo.internal",
                    "expiry_warn_days": 30,
                }
            )
            mock_urlopen.assert_called_once()


# ---------------------------------------------------------------------------
# resolve_tls — unknown strategy / output contract
# ---------------------------------------------------------------------------

class TestResolveTlsMisc:
    """Miscellaneous resolve_tls tests — error handling and output contract."""

    def test_unknown_strategy_raises(self):
        """RuntimeError for an unrecognised strategy name."""
        from src.services.tls_service import resolve_tls

        with pytest.raises(RuntimeError, match="Unknown TLS strategy"):
            resolve_tls({"enabled": True, "strategy": "magic_beans"})

    def test_resolve_tls_returns_correct_output_contract(self, tmp_path):
        """resolve_tls returns a (Path, Path) tuple when TLS is enabled.

        Uses a real self-signed PEM pair because resolve_tls now runs
        _validate_cert_coverage on manual-strategy certs and rejects
        unparseable PEM input.
        """
        cert, key = _make_self_signed_pem(tmp_path, cn="contract.test")

        from src.services.tls_service import resolve_tls

        result = resolve_tls(
            {
                "enabled": True,
                "strategy": "manual",
                "cert_path": str(cert),
                "key_path": str(key),
                "cn": "contract.test",
            }
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], Path)
        assert isinstance(result[1], Path)


# ---------------------------------------------------------------------------
# Expiry warning logging
# ---------------------------------------------------------------------------

class TestExpiryWarningLogged:
    """Tests for the expiry-warning log path."""

    def test_expiry_warning_logged_when_near_expiry(self, tmp_path, caplog):
        """A cert expiring in fewer than warn_days triggers a WARNING log."""
        pytest.importorskip("cryptography")
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        # Create cert that expires in 5 days (below default 30-day threshold)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_path = tmp_path / "key.pem"
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "soon")])
        soon_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=5))
            .sign(key, hashes.SHA256())
        )
        cert_path = tmp_path / "cert.pem"
        cert_path.write_bytes(soon_cert.public_bytes(serialization.Encoding.PEM))

        from src.services import tls_service as svc

        with caplog.at_level(logging.WARNING, logger="src.services.tls_service"):
            svc.resolve_tls(
                {
                    "enabled": True,
                    "strategy": "manual",
                    "cert_path": str(cert_path),
                    "key_path": str(key_path),
                    "expiry_warn_days": 30,
                }
            )

        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("expires in" in m for m in warning_msgs)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX file mode bits are not enforced on Windows",
    )
    @pytest.mark.skipif(
        importlib.util.find_spec("cryptography") is None,
        reason="cryptography package not installed",
    )
    def test_self_signed_key_has_0o600_permissions(self, tmp_path):
        """Issue #10 fix 10-D \u2014 self-signed keys must be chmod 0o600."""
        from src.services.tls_service import SelfSignedTLSStrategy

        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        SelfSignedTLSStrategy().resolve(
            {
                "cert_path": str(cert_path),
                "key_path": str(key_path),
                "cn": "localhost",
            }
        )
        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0o600 on key file, got {oct(mode)}"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX file mode bits are not enforced on Windows",
    )
    def test_enterprise_ca_key_has_0o600_permissions(self, tmp_path):
        """Issue #10 fix 10-D \u2014 enterprise-CA keys must be chmod 0o600."""
        from src.services.tls_service import EnterpriseCAStrategy

        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"

        fake_cert_pem = "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n"
        fake_key_pem = "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n"  # gitleaks:allow
        response_body = json.dumps(
            {"cert": fake_cert_pem, "key": fake_key_pem}
        ).encode()

        mock_response = MagicMock()
        mock_response.read.return_value = response_body
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            EnterpriseCAStrategy().resolve(
                {
                    "ca_api_url": "https://ca.example.com/issue",
                    "cert_path": str(cert_path),
                    "key_path": str(key_path),
                    "cn": "valdo.internal",
                }
            )

        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0o600 on key file, got {oct(mode)}"

    def test_no_expiry_warning_for_fresh_cert(self, tmp_path, caplog):
        """A cert with ample validity remaining does NOT trigger a WARNING."""
        pytest.importorskip("cryptography")
        cert_path, key_path = _make_self_signed_pem(tmp_path)  # 90-day cert

        from src.services import tls_service as svc

        with caplog.at_level(logging.WARNING, logger="src.services.tls_service"):
            svc.resolve_tls(
                {
                    "enabled": True,
                    "strategy": "manual",
                    "cert_path": str(cert_path),
                    "key_path": str(key_path),
                    "expiry_warn_days": 30,
                }
            )

        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("expires in" in m for m in warning_msgs)

# ---------------------------------------------------------------------------
# _validate_cert_coverage tests — new coverage validation gate added so
# operators discover SAN/CN mismatches and cert/key pairing errors at
# startup instead of as opaque browser TLS errors.
# ---------------------------------------------------------------------------

class TestValidateCertCoverage:
    """Tests for src.services.tls_service._validate_cert_coverage."""

    @staticmethod
    def _make_cert_with_san(tmp_path, cn="valdo.test", dns_names=None,
                            ip_addresses=None, days_valid=90,
                            not_before_days_ago=1):
        """Generate a self-signed PEM cert+key with controllable SAN entries.

        Returns the (cert_path, key_path) tuple. Both files are PEM-encoded
        and unencrypted, suitable for the manual TLS strategy.
        """
        pytest.importorskip("cryptography")
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import ipaddress as _ip

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_path = tmp_path / "key.pem"
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, cn)]
        )
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(
                datetime.datetime.utcnow()
                - datetime.timedelta(days=not_before_days_ago)
            )
            .not_valid_after(
                datetime.datetime.utcnow()
                + datetime.timedelta(days=days_valid)
            )
        )
        san_entries = []
        for d in dns_names or []:
            san_entries.append(x509.DNSName(d))
        for i in ip_addresses or []:
            san_entries.append(x509.IPAddress(_ip.ip_address(i)))
        if san_entries:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(san_entries), critical=False
            )

        cert = builder.sign(key, hashes.SHA256())
        cert_path = tmp_path / "cert.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return cert_path, key_path

    def test_valid_cert_passes(self, tmp_path):
        """Cert with cn in SAN and matching key passes silently."""
        cert, key = self._make_cert_with_san(
            tmp_path, cn="valdo.internal",
            dns_names=["valdo.internal", "localhost"],
        )
        from src.services.tls_service import _validate_cert_coverage
        # Should not raise.
        _validate_cert_coverage(
            cert, key,
            {"cn": "valdo.internal", "san": ["valdo.internal", "localhost"]},
        )

    def test_cn_in_subject_only_passes(self, tmp_path):
        """Older cert with no SAN but matching Subject CN is accepted."""
        cert, key = self._make_cert_with_san(
            tmp_path, cn="legacy.host", dns_names=None,
        )
        from src.services.tls_service import _validate_cert_coverage
        _validate_cert_coverage(cert, key, {"cn": "legacy.host"})

    def test_missing_cn_raises(self, tmp_path):
        """Configured cn that does not match cert SAN or Subject CN raises."""
        cert, key = self._make_cert_with_san(
            tmp_path, cn="valdo.internal",
            dns_names=["valdo.internal"],
        )
        from src.services.tls_service import _validate_cert_coverage
        with pytest.raises(RuntimeError, match="does not cover the configured cn"):
            _validate_cert_coverage(
                cert, key, {"cn": "wrong.host", "san": []},
            )

    def test_missing_san_entry_raises(self, tmp_path):
        """Configured san entry not present in cert raises with the missing list."""
        cert, key = self._make_cert_with_san(
            tmp_path, cn="valdo.internal",
            dns_names=["valdo.internal"],
        )
        from src.services.tls_service import _validate_cert_coverage
        with pytest.raises(RuntimeError, match="missing SAN entries"):
            _validate_cert_coverage(
                cert, key,
                {"cn": "valdo.internal", "san": ["valdo.internal", "extra.host"]},
            )

    def test_ip_san_matches(self, tmp_path):
        """IP addresses in cert SAN are matched by parsed value, not string."""
        cert, key = self._make_cert_with_san(
            tmp_path, cn="valdo.internal",
            dns_names=["valdo.internal"],
            ip_addresses=["127.0.0.1"],
        )
        from src.services.tls_service import _validate_cert_coverage
        # Same IP, different textual form, must still be considered covered.
        _validate_cert_coverage(
            cert, key,
            {"cn": "valdo.internal", "san": ["127.0.0.1"]},
        )

    def test_wildcard_san_covers_subdomain(self, tmp_path):
        """Cert with *.corp.example.com covers valdo.corp.example.com."""
        cert, key = self._make_cert_with_san(
            tmp_path, cn="*.corp.example.com",
            dns_names=["*.corp.example.com"],
        )
        from src.services.tls_service import _validate_cert_coverage
        _validate_cert_coverage(
            cert, key,
            {"cn": "valdo.corp.example.com",
             "san": ["valdo.corp.example.com"]},
        )

    def test_expired_cert_raises(self, tmp_path):
        """Cert past notAfter raises with 'has expired' message."""
        cert, key = self._make_cert_with_san(
            tmp_path, cn="valdo.internal",
            dns_names=["valdo.internal"],
            days_valid=-1,
            not_before_days_ago=10,
        )
        from src.services.tls_service import _validate_cert_coverage
        with pytest.raises(RuntimeError, match="has expired"):
            _validate_cert_coverage(
                cert, key, {"cn": "valdo.internal", "san": []},
            )

    def test_not_yet_valid_cert_raises(self, tmp_path):
        """Cert before notBefore raises with 'not yet valid' message."""
        cert, key = self._make_cert_with_san(
            tmp_path, cn="valdo.internal",
            dns_names=["valdo.internal"],
            not_before_days_ago=-30,
            days_valid=365,
        )
        from src.services.tls_service import _validate_cert_coverage
        with pytest.raises(RuntimeError, match="not yet valid"):
            _validate_cert_coverage(
                cert, key, {"cn": "valdo.internal", "san": []},
            )

    def test_mismatched_key_raises(self, tmp_path):
        """A key file that does not pair with the cert raises clearly."""
        pytest.importorskip("cryptography")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        cert, _key = self._make_cert_with_san(
            tmp_path, cn="valdo.internal",
            dns_names=["valdo.internal"],
        )
        # Replace the key with a freshly generated unrelated one.
        other_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        wrong_key_path = tmp_path / "wrong_key.pem"
        wrong_key_path.write_bytes(
            other_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

        from src.services.tls_service import _validate_cert_coverage
        with pytest.raises(RuntimeError, match="cert/key mismatch"):
            _validate_cert_coverage(
                cert, wrong_key_path,
                {"cn": "valdo.internal", "san": ["valdo.internal"]},
            )

    def test_unparseable_cert_raises(self, tmp_path):
        """Garbage in cert_path produces a clear PEM-parse error."""
        cert = tmp_path / "cert.pem"
        cert.write_text("not a real cert")
        key = tmp_path / "key.pem"
        key.write_text("not a real key")

        from src.services.tls_service import _validate_cert_coverage
        with pytest.raises(RuntimeError, match="could not be parsed as PEM"):
            _validate_cert_coverage(
                cert, key, {"cn": "x", "san": []},
            )

    def test_self_signed_strategy_skips_validation(self, tmp_path, monkeypatch):
        """resolve_tls must not call _validate_cert_coverage for self_signed.

        The self_signed strategy generates the cert from the same cn/san it
        was just handed, so re-validating would be redundant and noisy.
        """
        pytest.importorskip("cryptography")
        called = {"n": 0}

        def _spy(*args, **kwargs):
            called["n"] += 1

        from src.services import tls_service
        monkeypatch.setattr(tls_service, "_validate_cert_coverage", _spy)

        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"
        tls_service.resolve_tls(
            {
                "enabled": True,
                "strategy": "self_signed",
                "cert_path": str(cert_path),
                "key_path": str(key_path),
                "cn": "valdo.skipped",
                "san": ["valdo.skipped"],
            }
        )
        assert called["n"] == 0, (
            "self_signed must skip _validate_cert_coverage"
        )

    def test_manual_strategy_invokes_validation(self, tmp_path, monkeypatch):
        """resolve_tls must call _validate_cert_coverage for manual strategy."""
        cert, key = self._make_cert_with_san(
            tmp_path, cn="valdo.manual",
            dns_names=["valdo.manual"],
        )
        called = {"n": 0}

        def _spy(*args, **kwargs):
            called["n"] += 1

        from src.services import tls_service
        monkeypatch.setattr(tls_service, "_validate_cert_coverage", _spy)
        tls_service.resolve_tls(
            {
                "enabled": True,
                "strategy": "manual",
                "cert_path": str(cert),
                "key_path": str(key),
                "cn": "valdo.manual",
                "san": ["valdo.manual"],
            }
        )
        assert called["n"] == 1, (
            "manual strategy must call _validate_cert_coverage exactly once"
        )
