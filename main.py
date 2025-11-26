# main.py
import sys
import ctypes
import time
import logging
import glob
import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QTranslator, QLibraryInfo, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QMessageBox, QDialog, QVBoxLayout,
    QLabel, QTextBrowser, QDialogButtonBox
)
from main_window import MainWindow
from PIL import Image
from config import (
    WARNING_TEXT, DISCLAIMER_TEXT, APP_INTERNAL_NAME, APP_VERSION,
    PROJECT_FILE_EXTENSION, MAX_IMAGE_PIXELS
)
from debug_stream import StdStreamHandler
from logger_setup import setup_logging
from log_handler import QtLogHandler

def cleanup_stale_temp_dirs():
    try:
        temp_root = tempfile.gettempdir()
        stale_dirs = glob.glob(os.path.join(temp_root, "sbv_*"))
        
        cleaned_count = 0
        for d in stale_dirs:
            path = Path(d)
            if path.is_dir():
                try:
                    shutil.rmtree(path, ignore_errors=True)
                    cleaned_count += 1
                except OSError:
                    pass
        if cleaned_count > 0:
            logging.info(f"Cleaned up {cleaned_count} stale temporary directories from previous sessions.")
    except Exception as e:
        logging.warning(f"Failed to cleanup stale temp dirs: {e}")

def main():
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

    app = QApplication(sys.argv)
    
    setup_logging()
    
    cleanup_stale_temp_dirs()
    
    win = MainWindow()
    
    qt_log_handler = QtLogHandler()
    qt_log_handler.new_record.connect(
        win.debug_console.append_text,
        Qt.ConnectionType.QueuedConnection
    )
    logging.getLogger().addHandler(qt_log_handler)
    
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    stdout_handler = StdStreamHandler(original_stream=original_stdout, is_stderr=False)
    stderr_handler = StdStreamHandler(original_stream=original_stderr, is_stderr=True)
    
    sys.stdout = stdout_handler
    sys.stderr = stderr_handler
    stdout_handler.messageWritten.connect(win.debug_console.append_text, Qt.ConnectionType.QueuedConnection)
    stderr_handler.messageWritten.connect(win.debug_console.append_text, Qt.ConnectionType.QueuedConnection)
    
    try:
        logging.info(f"--- {APP_INTERNAL_NAME} v{APP_VERSION} Log Start ---")

        if sys.platform == "win32":
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_INTERNAL_NAME)
            except AttributeError:
                logging.warning("Could not set AppUserModelID. Taskbar features might not work correctly.")

        server_name = f"{APP_INTERNAL_NAME}_SingleInstance_Lock"
        socket = QLocalSocket()
        
        is_another_instance_running = False
        max_retries = 5
        retry_delay_ms = 300

        for i in range(max_retries):
            socket.connectToServer(server_name)
            if socket.waitForConnected(500):
                is_another_instance_running = True
                break
            else:
                socket.disconnectFromServer()
                time.sleep(retry_delay_ms / 1000.0)

        if is_another_instance_running:
            logging.info("Application already running. Sending file path (if any) to primary instance.")
            if len(sys.argv) > 1:
                file_path_to_open = sys.argv[1]
                if file_path_to_open.lower().endswith(PROJECT_FILE_EXTENSION):
                    logging.info(f"Sending file path: {file_path_to_open}")

                    payload = file_path_to_open.encode('utf-8')
                    header = len(payload).to_bytes(8, 'big', signed=False)
                    socket.write(header + payload)
                    
                    if not socket.waitForBytesWritten(2000):
                        logging.error("Failed to write data to the primary instance's socket.")
                    else:
                        if not socket.waitForReadyRead(2000):
                            logging.warning("Primary instance did not acknowledge file open within the timeout period.")
                        else:
                            response = socket.readAll().data()
                            if response == b'ack':
                                logging.info("Primary instance acknowledged the file open.")
                            else:
                                logging.warning(f"Received unexpected response from primary instance: {response!r}")
                else:
                    logging.warning(
                        f"Ignoring command line argument: '{file_path_to_open}' is not a valid project file ({PROJECT_FILE_EXTENSION})."
                    )

            socket.disconnectFromServer()
            logging.info("Secondary instance exiting.")
            sys.exit(0)
        else:
            QLocalServer.removeServer(server_name)
            
            server = QLocalServer()
            if not server.listen(server_name):
                QMessageBox.critical(None, "エラー", f"サーバーの起動に失敗しました: {server.errorString()}")
                logging.critical(f"Could not listen on local server socket: {server.errorString()}")
                sys.exit(1)
            
            logging.info("Primary instance: Local server socket established.")

            app.setApplicationName(APP_INTERNAL_NAME)

            translator = QTranslator()
            path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
            if translator.load("qt_ja", path):
                app.installTranslator(translator)
                logging.info("Loaded Qt Japanese translations.")

            
            if not show_agreement_dialog():
                logging.info("User did not accept agreement. Exiting.")
                sys.exit(0)
            
            MAX_PAYLOAD_SIZE = 4096
            
            def handle_new_connection():
                client_connection = server.nextPendingConnection()
                
                if client_connection:
                    logging.info("New client connection detected (secondary instance startup).")

                    client_connection.disconnected.connect(client_connection.deleteLater)

                    expected_payload_size = 0
                    received_payload_buffer = b''

                    def read_from_socket():
                        nonlocal expected_payload_size, received_payload_buffer
                        
                        received_payload_buffer += client_connection.readAll()

                        while True:
                            if expected_payload_size == 0:
                                if len(received_payload_buffer) < 8:
                                    return
                                
                                header = received_payload_buffer[:8]
                                received_payload_buffer = received_payload_buffer[8:]
                                expected_payload_size = int.from_bytes(header, 'big', signed=False)

                                if expected_payload_size <= 0 or expected_payload_size > MAX_PAYLOAD_SIZE:
                                    reason = "0以下です" if expected_payload_size <= 0 else "上限を超えています"
                                    logging.error(
                                        f"不正なペイロードサイズを受信しました: {expected_payload_size} ({reason})。 "
                                        f"ヘッダー (16進): {header.hex()}。接続を閉じます。"
                                    )
                                    client_connection.close()
                                    return

                            if len(received_payload_buffer) < expected_payload_size:
                                return

                            actual_payload = received_payload_buffer[:expected_payload_size]
                            received_payload_buffer = received_payload_buffer[expected_payload_size:]
                            
                            try:
                                file_path = actual_payload.decode('utf-8')
                                if file_path:
                                    logging.info(f"ファイルパスを受信しました: {file_path}")
                                    win.open_project_from_path(file_path)
                                
                                win.bring_to_front()
                                
                                client_connection.write(b'ack')
                                
                                if not client_connection.waitForBytesWritten(1000):
                                    logging.warning("ACKの書き込み待機中にタイムアウトしました。")
                            
                            except UnicodeDecodeError:
                                logging.error(f"受信データのデコードに失敗しました。データ(16進): {actual_payload.hex()}")
                            
                            finally:
                                client_connection.close()
                                return
                    
                    client_connection.readyRead.connect(read_from_socket)

            server.newConnection.connect(handle_new_connection)
            
            if len(sys.argv) > 1:
                file_path_to_open = sys.argv[1]
                if file_path_to_open.lower().endswith(PROJECT_FILE_EXTENSION):
                    logging.info(f"Opening file from command line argument: {file_path_to_open}")
                    win.open_project_from_path(file_path_to_open)

            win.show()
            logging.info("Application startup complete. Running event loop.")
            
            exit_code = app.exec()
            sys.exit(exit_code)
            
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        logging.info("Restored standard output streams.")

def show_agreement_dialog() -> bool:
    dialog = QDialog()
    dialog.setWindowTitle("ご利用前の確認事項")
    dialog.setMinimumSize(600, 550)

    layout = QVBoxLayout(dialog)

    title_label = QLabel("<b>アプリケーションのご利用にあたって</b>")
    title_label.setStyleSheet("font-size: 16px;")
    layout.addWidget(title_label)
    
    intro_label = QLabel("このアプリケーションをご利用になる前に、以下の内容をすべてお読みいただき、同意いただく必要があります。")
    layout.addWidget(intro_label)

    text_browser = QTextBrowser()
    text_browser.setOpenExternalLinks(True)

    warning_html = WARNING_TEXT.replace('\n\n', '</p><p>').replace('\n', '<br>')
    disclaimer_html = DISCLAIMER_TEXT.replace('\n\n', '</p><p>').replace('\n', '<br>')
    
    content_html = f"""
    <html><head><style>
        body {{ font-family: "Meiryo", sans-serif; line-height: 1.5; }}
        h4 {{ 
            background-color: #f0f0f0; padding: 4px; border-radius: 3px; 
            margin-top: 10px; margin-bottom: 5px; border-left: 5px solid #ccc; padding-left: 8px;
        }}
    </style></head><body>
        <h4>動画作成上の注意</h4>
        <p>{warning_html}</p>
        <hr>
        <h4>免責事項</h4>
        <p>{disclaimer_html}</p>
    </body></html>
    """
    text_browser.setHtml(content_html)
    layout.addWidget(text_browser)

    question_label = QLabel("<b>上記の内容をすべて理解し、同意した上でアプリケーションを使用しますか？</b>")
    layout.addWidget(question_label)

    button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
    button_box.button(QDialogButtonBox.StandardButton.Yes).setText("同意して利用を開始する")
    button_box.button(QDialogButtonBox.StandardButton.No).setText("同意しない")
    button_box.accepted.connect(dialog.accept)
    button_box.rejected.connect(dialog.reject)
    
    no_button = button_box.button(QDialogButtonBox.StandardButton.No)
    no_button.setDefault(True)

    layout.addWidget(button_box)

    return dialog.exec() == QDialog.Accepted

if __name__ == "__main__":
    main()