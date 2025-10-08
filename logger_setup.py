# logger_setup.py
import logging
import logging.handlers
import sys
from pathlib import Path
from PySide6.QtCore import QStandardPaths

LOG_FILENAME = "error.log"

def setup_logging():
    # 1. Define log file path in a user-writable directory
    try:
        data_path_str = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        if not data_path_str:
            data_path_str = "."
        
        log_dir = Path(data_path_str) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / LOG_FILENAME
    except Exception as e:
        print(f"FATAL: Could not create log file directory. Reason: {e}", file=sys.stderr)
        return

    # 2. Configure a rotating file handler for the log
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8'
    )
    log_format = (
        "--- %(asctime)s ---\n"
        "Level: %(levelname)s\n"
        "Message: %(message)s\n"
        "Location: %(pathname)s:%(lineno)d\n"
        "Traceback:\n%(exc_text)s\n"
    )
    formatter = logging.Formatter(log_format)
    handler.setFormatter(formatter)
    
    # 3. Create a dedicated logger for uncaught exceptions
    exception_logger = logging.getLogger('unhandled_exception_logger')
    exception_logger.setLevel(logging.ERROR)
    exception_logger.addHandler(handler)

    # 4. Define the hook function that will be called on an exception
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        exception_logger.error(
            "An unhandled exception occurred.", 
            exc_info=(exc_type, exc_value, exc_traceback)
        )

    # 5. Set the custom hook as the global exception handler
    sys.excepthook = handle_exception
    
    print(f"Global exception logging is active. Log file: {log_file}")