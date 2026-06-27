class ProcessingError(Exception):
    def __init__(self, error_type: str, error_message: str):
        self.error_type = error_type
        self.error_message = error_message
        super().__init__(error_message)


class RetryableProcessingError(ProcessingError):
    pass


class TerminalProcessingError(ProcessingError):
    pass
