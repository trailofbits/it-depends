"""Functions for logging."""

import logging


class ConditionalNewlineHandler(logging.StreamHandler):
    """Custom handler that suppresses newlines and INFO prefix for messages with [!n] marker."""

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a record."""
        try:
            # Check if the special marker is in the message
            if "[!n]" in record.msg:
                # Remove the marker from the message
                record.msg = record.msg.replace("[!n]", "")
                # Temporarily set the terminator to an empty string
                self.terminator = ""
                # Create a custom formatter that omits the level name
                formatter = logging.Formatter("%(message)s")
            else:
                # Use the default terminator
                self.terminator = "\n"
                # Use the standard formatter with level name
                formatter = logging.Formatter("%(levelname)s: %(message)s")

            # Format and output the record
            msg = formatter.format(record)
            stream = self.stream
            stream.write(msg + self.terminator)
            self.flush()
        except Exception:  # noqa: BLE001
            self.handleError(record)


def setup_logger(level: str) -> None:
    """Configure root logger for the application so all modules log to stdout."""
    level_name = level.upper()
    level_value = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level_value)
    # Remove all handlers associated with the root logger (avoid duplicate logs)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    handler = ConditionalNewlineHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root_logger.addHandler(handler)
