# logger_setup.py
import logging
import logging.handlers
import sys
from pathlib import Path
from PySide6.QtCore import QStandardPaths

LOG_FILENAME = "error.log"

def setup_logging():
    # 1. Define log file path
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

    # 2. Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 3. Configure a rotating file handler for detailed error logging
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8'
    )
    file_format = (
        "--- %(asctime)s ---\n"
        "Level: %(levelname)s\n"
        "Logger: %(name)s\n"
        "Location: %(pathname)s:%(lineno)d\n"
        "Message: %(message)s\n"
        "Traceback:\n%(exc_text)s\n"
    )
    file_formatter = logging.Formatter(file_format)
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.WARNING)
    root_logger.addHandler(file_handler)

    # 4. Configure a stream handler for console output only if stdout is valid.
    if sys.stdout and hasattr(sys.stdout, 'write'):
        console_handler = logging.StreamHandler(sys.stdout)
        console_formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)-8s] [%(name)s:%(lineno)d] %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.INFO)
        root_logger.addHandler(console_handler)

    # 5. Define the hook for uncaught exceptions
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        root_logger.critical(
            "An unhandled exception occurred.", 
            exc_info=(exc_type, exc_value, exc_traceback)
        )

    # 6. Set the custom hook
    sys.excepthook = handle_exception
    
    root_logger.info(f"Global exception logging is active. Log file: {log_file}")