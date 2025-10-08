# main.py
import sys
import json
import ctypes
from PySide6.QtCore import QTranslator, QLibraryInfo, QUrl, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QMessageBox, QDialog, QVBoxLayout,
    QLabel, QTextBrowser, QDialogButtonBox
)
from PySide6.QtGui import QDesktopServices
from main_window import MainWindow
from PIL import Image
from config import (
    WARNING_TEXT, DISCLAIMER_TEXT, APP_INTERNAL_NAME, APP_VERSION,
    GITHUB_REPO_ID, PROJECT_FILE_EXTENSION, MAX_IMAGE_PIXELS
)
from ffmpeg_downloader import check_ffmpeg_exists, FFmpegDownloaderDialog
from debug_stream import StdStreamHandler
from logger_setup import setup_logging


def main():
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

    app = QApplication(sys.argv)
    
    setup_logging()
    
    stdout_handler = StdStreamHandler(original_stream=sys.stdout, is_stderr=False)
    stderr_handler = StdStreamHandler(original_stream=sys.stderr, is_stderr=True)
    
    sys.stdout = stdout_handler
    sys.stderr = stderr_handler
    
    print(f"--- {APP_INTERNAL_NAME} v{APP_VERSION} Log Start ---")

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_INTERNAL_NAME)
        except AttributeError:
            print("WARNING: Could not set AppUserModelID. Taskbar features might not work correctly.")

    server_name = f"{APP_INTERNAL_NAME}_SingleInstance_Lock"
    socket = QLocalSocket()
    
    socket.connectToServer(server_name)

    if socket.waitForConnected(500):
        print("Application already running. Sending file path (if any) to primary instance.")
        if len(sys.argv) > 1:
            file_path_to_open = sys.argv[1]
            if file_path_to_open.lower().endswith(PROJECT_FILE_EXTENSION):
                print(f"Sending file path: {file_path_to_open}")

                payload = file_path_to_open.encode('utf-8')
                header = len(payload).to_bytes(8, 'big', signed=False)
                socket.write(header + payload)

                socket.flush()

                if not socket.waitForReadyRead(2000):
                    print("Primary instance did not acknowledge file open.", file=sys.stderr)

        socket.disconnectFromServer()
        print("Secondary instance exiting.")
        sys.exit(0)
    else:
        QLocalServer.removeServer(server_name)
        
        server = QLocalServer()
        if not server.listen(server_name):
            QMessageBox.critical(None, "エラー", f"サーバーの起動に失敗しました: {server.errorString()}")
            print(f"FATAL: Could not listen on local server socket: {server.errorString()}", file=sys.stderr)
            sys.exit(1)
        
        print("Primary instance: Local server socket established.")

        app.setApplicationName(APP_INTERNAL_NAME)

        translator = QTranslator()
        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if translator.load("qt_ja", path):
            app.installTranslator(translator)
            print("Loaded Qt Japanese translations.")

        win = MainWindow()
        
        stdout_handler.messageWritten.connect(
            win.debug_console.append_text,
            Qt.ConnectionType.QueuedConnection
        )
        stderr_handler.messageWritten.connect(
            win.debug_console.append_text,
            Qt.ConnectionType.QueuedConnection
        )
        
        if not show_agreement_dialog():
            print("User did not accept agreement. Exiting.")
            sys.exit(0)
        
        def handle_new_connection():
            client_connection = server.nextPendingConnection()
            print("New client connection detected (secondary instance startup).")

            client_connection.disconnected.connect(client_connection.deleteLater)

            expected_payload_size = 0
            received_payload = b''

            def read_from_socket():
                nonlocal expected_payload_size, received_payload
                
                if expected_payload_size == 0:
                    if client_connection.bytesAvailable() < 8:
                        return
                    header = client_connection.read(8)
                    expected_payload_size = int.from_bytes(header, 'big', signed=False)
                
                if client_connection.bytesAvailable() > 0:
                    received_payload += client_connection.readAll()

                if len(received_payload) >= expected_payload_size:
                    actual_payload = received_payload[:expected_payload_size]
                    
                    try:
                        file_path = actual_payload.decode('utf-8')
                        if file_path:
                            print(f"Received complete file path: {file_path}")
                            win.open_project_from_path(file_path)
                    except UnicodeDecodeError:
                        print(f"ERROR: Failed to decode received path bytes.")

                    win.bring_to_front()
                    client_connection.write(b'ack')
                    client_connection.flush()
                    client_connection.disconnectFromServer()

            client_connection.readyRead.connect(read_from_socket)

        server.newConnection.connect(handle_new_connection)
        
        if len(sys.argv) > 1:
            file_path_to_open = sys.argv[1]
            if file_path_to_open.lower().endswith(PROJECT_FILE_EXTENSION):
                print(f"Opening file from command line argument: {file_path_to_open}")
                win.open_project_from_path(file_path_to_open)
        
        win.show()
        print("Application startup complete. Running event loop.")
        sys.exit(app.exec())


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