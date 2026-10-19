class LabError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def __str__(self):
        return f"ERROR {self.code}: {self.message}"
