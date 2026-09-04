class GwenError(Exception):
    """Base exception safe to translate at the user boundary."""


class DailyUsageLimitReached(GwenError):
    pass


class ProviderUnavailable(GwenError):
    pass


class InputTooLong(GwenError):
    pass
