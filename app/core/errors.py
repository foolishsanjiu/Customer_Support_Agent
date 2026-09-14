class BusinessError(Exception):
    status_code = 400
    code = "business_error"


class ResourceNotFound(BusinessError):
    status_code = 404
    code = "not_found"


class ObjectAccessDenied(BusinessError):
    status_code = 403
    code = "object_access_denied"


class BusinessConflict(BusinessError):
    status_code = 409
    code = "business_conflict"
