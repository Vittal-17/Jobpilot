class ProviderError(Exception):
    pass

class ProviderConfigurationError(ProviderError):
    pass

class ProviderTimeout(ProviderError):
    pass

class ProviderHTTPError(ProviderError):
    pass

class ProviderNetworkError(ProviderError):
    pass

class ProviderPayloadError(ProviderError):
    pass
