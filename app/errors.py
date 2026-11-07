class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str, errors: dict | None = None, *, title: str | None = None,
                 back_url: str | None = None, back_label: str | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or {}
        self.title = title
        self.back_url = back_url
        self.back_label = back_label


class ValidationFailed(ServiceError):
    status_code = 422


class Forbidden(ServiceError):
    status_code = 403


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409
