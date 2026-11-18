"""Exceptions of the GRDF client.

Messages never contain credentials, cookies, tokens, PCE numbers or e-mail addresses.
"""

from __future__ import annotations


class GrdfError(Exception):
    """Base class."""


class GrdfRouteNotAllowed(GrdfError):
    """The path is not in the read-only allow-list (a bug, never a runtime condition)."""


class GrdfTemporaryError(GrdfError):
    """GRDF or the network misbehaved; nothing is wrong with the credentials. Back off and try
    again at the next scheduled fetch — never in a loop."""


class GrdfAuthError(GrdfError):
    """Authentication was refused, or could not be completed with a password alone.

    Terminal until the user acts (reauth flow): the client makes no further HTTP call.

    reason:
      - "refused": the password step did not succeed (wrong password, locked account… or an
        unknown outcome: the request failed after the password had left);
      - "identify_refused": Okta rejected the identifier step (password not sent);
      - "password_not_offered": Okta asked for something else — MFA, another authenticator
        (password not sent);
      - "blocked": a previous refusal is still pending; nothing was sent.
    okta_keys: Okta i18n message keys / error codes of the refusal, for diagnostics.
    """

    def __init__(self, reason: str, okta_keys: tuple[str, ...] = (), status: int | None = None):
        self.reason = reason
        self.okta_keys = okta_keys
        self.status = status
        detail = f" (HTTP {status})" if status is not None else ""
        keys = f" [{', '.join(okta_keys)}]" if okta_keys else ""
        super().__init__(f"GRDF authentication failed: {reason}{detail}{keys}")
