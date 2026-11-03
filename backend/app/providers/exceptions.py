class ProviderError(Exception):
    """Normalized provider failure with preserved HTTP/billing metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response=None,
        billing_ambiguous: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response = response
        self.billing_ambiguous = billing_ambiguous

class ProviderConfigurationError(ProviderError):
    pass

class ProviderTimeout(ProviderError):
    def __init__(self, message: str, *, status_code: int | None = 408, response=None):
        super().__init__(message, status_code=status_code, response=response, billing_ambiguous=True)

class ProviderHTTPError(ProviderError):
    def __init__(self, message: str, *, status_code: int | None = None, response=None, billing_ambiguous: bool = False):
        super().__init__(message, status_code=status_code, response=response, billing_ambiguous=billing_ambiguous)

class ProviderNetworkError(ProviderError):
    def __init__(self, message: str, *, status_code: int | None = None, response=None):
        super().__init__(message, status_code=status_code, response=response, billing_ambiguous=True)

class ProviderPayloadError(ProviderError):
    def __init__(self, message: str, *, status_code: int | None = None, response=None):
        super().__init__(message, status_code=status_code, response=response, billing_ambiguous=False)


class BillingContractUnverified(ProviderError):
    """The request shape cannot prove the provider's advertised fixed cost."""

    def __init__(self, message: str = "billing_contract_unverified"):
        super().__init__(message, billing_ambiguous=True)
