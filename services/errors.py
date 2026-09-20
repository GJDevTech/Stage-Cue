class CloudError(RuntimeError):
    pass


class CloudConfigurationError(CloudError):
    pass


class CloudUnavailable(CloudError):
    pass


class CloudConflict(CloudError):
    pass


class AccountNotRegistered(CloudError):
    pass


class AuthorizationError(CloudError):
    pass


class MembershipRequired(AuthorizationError):
    pass


class AdminRequired(AuthorizationError):
    pass
