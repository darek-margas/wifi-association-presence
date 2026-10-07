"""SSH connection policy shared by SSH-console drivers.

Each AP model declares its own SshPolicy (key exchange, host key and cipher
algorithms, and login method), because embedded SSH servers vary widely and old
firmware only speaks deprecated algorithms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import warnings

# Old APs force finite-field Diffie-Hellman; cryptography warns about it on every
# key exchange. The policy opts into it deliberately, so silence that one warning.
warnings.filterwarnings("ignore", message="Diffie-Hellman over finite fields")


@dataclass(frozen=True, slots=True)
class SshPolicy:
    """How to talk SSH to one AP model."""

    kex_algs: tuple[str, ...]
    server_host_key_algs: tuple[str, ...]
    encryption_algs: tuple[str, ...]
    preferred_auth: tuple[str, ...] = ("password",)
    """Login methods, in order. Many APs list publickey but have no way to install keys."""
    connect_timeout: int = 10

    def connect_options(self) -> dict[str, Any]:
        """Keyword arguments for asyncssh.connect()."""
        return {
            "kex_algs": list(self.kex_algs),
            "server_host_key_algs": list(self.server_host_key_algs),
            "encryption_algs": list(self.encryption_algs),
            "preferred_auth": list(self.preferred_auth),
            # Never offer the local user's keys or agent: the AP can't accept them,
            # and failed attempts count against the AP's login attempt limit.
            "client_keys": None,
            "agent_path": None,
            # Embedded APs regenerate host keys on factory reset; not verified.
            "known_hosts": None,
            "connect_timeout": self.connect_timeout,
        }
