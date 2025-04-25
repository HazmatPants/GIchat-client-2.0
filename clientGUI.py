import sys
import asyncio
import threading
import os
import json
import time
import random
import base64
import traceback
import markdown
from datetime import datetime

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QPushButton, QTextEdit, QLabel,
                             QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox, QLineEdit, QDialog,
                             QProgressBar, QMenuBar, QAction)
from PyQt5.QtGui import QIcon, QPixmap, QTextCursor, QFont
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer, QCoreApplication, QEventLoop, QMetaObject

from PIL import Image
from ping3 import ping
from pygame import mixer
import websockets
import toml

mixer.init()

# === Config and Logging ===
CLI_VERSION = "2.0.0"
CLI_DIR = os.path.dirname(__file__)
os.chdir(CLI_DIR)

LOG_FILE = os.path.join(CLI_DIR, "latest.log")
CONFIG_FILE = os.path.join(CLI_DIR, "config.toml")

with open(LOG_FILE, "w") as f:
    f.write("")

def log(msg):
    print(msg)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        toml.dump(data, f)

def load_config():
    try:
        data = toml.load(CONFIG_FILE)
        log("Config Loaded")
        return data
    except toml.TomlDecodeError:
        log("Invalid Config TOML")
        QMessageBox.critical(None, "TOML Decode Error",
                             "Config file contains invalid TOML. Fix the issues or delete it to generate a new one.")
        sys.exit()
    except FileNotFoundError:
        log("Config file missing")
        data = {
            "client": {
                "username": f"NewUser_{random.randint(1, 10000)}",
                "font": {"name": "Helvetica", "size": 10},
                "admin_key": ""
            },
            "server": {"host": "grigga-industries.ydns.eu", "port": 8765}
        }
        save_config(data)
        sys.exit()

# === Utility ===
def playsound(path):
    mixer.Sound(path).play()

def playeventsound(event):
    playsound(os.path.join("assets", "sounds", f"{event}.wav"))

def b64encode(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def b64decode(b64string):
    return base64.b64decode(b64string)

def markdown_to_html(markdown_text):
    html_output = markdown.markdown(markdown_text)
    return html_output

# === Async Communication Handler ===
class Communicator(QObject):
    print_to_console = pyqtSignal(str, object)
    load_messages = pyqtSignal(list)

class LoadingWindow(QMainWindow):
    def __init__(self, messages, chat_client):
        super().__init__()
        log("LoadingWindow initialized")

        self.chat = chat_client
        self.messages = messages
        self.setWindowTitle("Loading...")
        self.setGeometry(100, 100, 350, 150)
        self.setStyleSheet("background-color: black;")
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.setFixedSize(350, 150)

        # Layout setup
        layout = QVBoxLayout()

        self.loading_label = QLabel("Loading Messages", self)
        self.loading_label.setStyleSheet("color: white;")
        layout.addWidget(self.loading_label)
        
        self.progress = QProgressBar(self)
        self.progress.setStyleSheet("QProgressBar {color: white; background-color: #333;} QProgressBar::chunk {background-color: #05B8CC;}")
        self.progress.setMaximum(len(self.messages))
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        container = QWidget(self)
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.idx = 0
        
        self.chat.clear_console()

        QTimer.singleShot(0, self.process_messages)

    def process_messages(self):
        for self.idx, message in enumerate(self.messages):
            message = self.messages[self.idx]
            username, content, timestamp = message
            html = markdown_to_html(content.strip())
            self.chat.comm.print_to_console.emit(f"[{timestamp}] &lt;{username}&gt;", None)
            self.chat.comm.print_to_console.emit(html, None)
            self.progress.setValue(self.idx + 1)
            log(f"loaded message {self.idx}")
            QCoreApplication.processEvents()

        self.close()


class ChatClient(QMainWindow):
    def __init__(self):
        super().__init__()

        self.websocket = None
        self.loop = asyncio.new_event_loop()
        self.shutdown_flag = False

        self.comm = Communicator()
        self.comm.print_to_console.connect(self.print_to_console)
        self.comm.load_messages.connect(self.show_loading_window)

        self.init_ui()
        threading.Thread(target=self.start_asyncio_loop, daemon=True).start()

    def show_loading_window(self, messages):
        log("Creating LoadingWindow on GUI thread")
        self.loading_window = LoadingWindow(messages, self)
        self.loading_window.show()

    def init_ui(self):
        self.setWindowTitle(f"GI.chat Client {CLI_VERSION}")
        self.setStyleSheet("background-color: #000000; color: white")
        self.setGeometry(100, 100, 900, 500)
        self.setWindowIcon(QIcon("assets/images/GIchat_Icon.ico"))

        self.console = QTextEdit(self)
        self.console.setReadOnly(True)
        self.console.setFont(QFont(CLI_CONFIG["client"]["font"]["name"], CLI_CONFIG["client"]["font"]["size"]))
        self.console.setStyleSheet("background-color: #232323; color: white")

        self.message_input = QTextEdit(self)
        self.message_input.setFixedHeight(50)
        self.message_input.setStyleSheet("background-color: #232323; color: white")

        send_button = QPushButton(">", self)
        send_button.clicked.connect(self.send_message)
        send_button.setFixedWidth(50)
        send_button.setStyleSheet("background-color: #232323; color: white")

        file_button = QPushButton("Send\nImage", self)
        file_button.clicked.connect(self.send_file)
        file_button.setStyleSheet("background-color: #232323; color: white")

        ping_button = QPushButton("Ping", self)
        ping_button.clicked.connect(self.ping_server)
        ping_button.setStyleSheet("background-color: #232323; color: white")

        clear_button = QPushButton("Clear", self)
        clear_button.clicked.connect(lambda: self.console.clear())
        clear_button.setStyleSheet("background-color: #232323; color: white")

        disconnect_button = QPushButton("Disconnect", self)
        disconnect_button.clicked.connect(lambda: asyncio.run_coroutine_threadsafe(self.disconnect(), self.loop))
        disconnect_button.setStyleSheet("background-color: #232323; color: white")

        reconnect_button = QPushButton("Reconnect", self)
        reconnect_button.clicked.connect(lambda: asyncio.run_coroutine_threadsafe(self.reconnect(), self.loop))
        reconnect_button.setStyleSheet("background-color: #232323; color: white")

        # Layouts
        button_layout = QVBoxLayout()
        button_layout.addWidget(ping_button)
        button_layout.addWidget(disconnect_button)
        button_layout.addWidget(reconnect_button)
        button_layout.addWidget(clear_button)
        button_layout.addWidget(file_button)

        message_layout = QHBoxLayout()
        message_layout.addWidget(self.message_input)
        message_layout.addWidget(send_button)

        main_layout = QHBoxLayout()
        main_layout.addLayout(button_layout)
        main_layout.addWidget(self.console)

        central_widget = QWidget()
        central_layout = QVBoxLayout()
        central_layout.addLayout(main_layout)
        central_layout.addLayout(message_layout)
        central_widget.setLayout(central_layout)

        self.setCentralWidget(central_widget)

        # Menu bar
        menubar = QMenuBar(self)
        options_menu = menubar.addMenu("Options")

        credits_action = QAction("Credits", self)
        credits_action.triggered.connect(lambda: QMessageBox.information(self, "Credits",
                                                                         "Made by GI\nWritten in Python 3.10\nSound effects from AIM and Valve"))
        options_menu.addAction(credits_action)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(lambda: asyncio.run_coroutine_threadsafe(self.client_exit(), self.loop))
        options_menu.addAction(exit_action)

        self.setMenuBar(menubar)

    def print_to_console(self, text, image=None):
        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.console.setTextCursor(cursor)
        if image:
            self.console.insertHtml(f'<img src="{image}" width="200">')
        self.console.insertHtml(text + "<br>")
        self.console.moveCursor(QTextCursor.End)

    def clear_console(self):
        self.console.clear()
    
    async def retrieve_messages(self):
        log("retrieve_messages() called")

        data = {
            "username": username,
            "message": "RAW:MSGDB",
            "event": "request",
            "type": "msg"
        }
        await self.websocket.send(json.dumps(data))
        log("Requesting message DB from server...")

        messages = await self.websocket.recv()
        messages = json.loads(messages)
        log(f"Retrieved {len(messages)} messages from server")

        self.comm.load_messages.emit(messages)

    def ping_server(self):
        responsetime = ping(host)
        if responsetime:
            QMessageBox.information(self, "Ping Successful",
                                    f"Response Time: {round(responsetime * 1000, 2)}ms")
        else:
            QMessageBox.critical(self, "Ping Failed", "Host unreachable")

    def start_asyncio_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.connect())
        except ConnectionRefusedError:
            QMessageBox.critical(self, "Connection Error", "Connection Refused")
        while not self.shutdown_flag:
            self.loop.run_forever()

    async def connect(self):
        uri = f"ws://{host}:{port}"
        try:
            self.websocket = await websockets.connect(uri)
            await self.websocket.send(username)
            server_info = await self.websocket.recv()
            server_info = json.loads(server_info)
            print(server_info)
            self.comm.print_to_console.emit(f"Connected to {server_info['name']} ({uri})", None)
            playeventsound("connect")
            data = {
                "username": username,
                "message": "RAW:USERLIST",
                "event": "request",
                "type": "msg"
            }
            await self.websocket.send(json.dumps(data))
            log(f"Requesting user list from server...")
            online_users = await self.websocket.recv()
            online_users = json.loads(online_users)
            log(f"Retrieved user list")
            if type(online_users) == list:
                users = ", ".join(online_users)
                self.comm.print_to_console.emit("Online Users: " + users, None)
            await self.retrieve_messages()
            await self.receive_messages()
        except Exception as e:
            self.comm.print_to_console.emit(f"Connection failed: {e}", None)
            traceback.print_exc()

    async def disconnect(self):
        if self.websocket:
            try:
                await self.websocket.close(reason="Client Disconnect")
            except Exception as e:
                log(f"WebSocket close error: {e}")
            self.websocket = None
            playeventsound("disconnect")
            self.comm.print_to_console.emit("Disconnected.", None)

    async def reconnect(self):
        self.comm.print_to_console.emit("Reconnecting...", None)
        await self.disconnect()
        await self.connect()

    async def client_exit(self):
        self.shutdown_flag = True
        await self.disconnect()
        log("Client exited")
        self.close()
        os._exit(0)

    def send_message(self):
        msg = self.message_input.toPlainText().strip()
        if msg:
            self.message_input.clear()
            asyncio.run_coroutine_threadsafe(self._send_message(msg), self.loop)

    async def _send_message(self, msg):
        data = {
            "type": "msg",
            "username": username,
            "message": msg,
            "event": "send_message",
            "admin_key": CLI_CONFIG["client"].get("admin_key", " ")
        }
        if self.websocket and self.websocket.open:
            await self.websocket.send(json.dumps(data))
            playeventsound("send_message")
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            msg_html = markdown_to_html(msg)
            self.comm.print_to_console.emit(f"[{timestamp}] &lt;{username}&gt;", None)
            self.comm.print_to_console.emit(msg_html, None)

    def send_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select a file", "", "Image files (*.png *.jpg *.jpeg)")
        if file_path:
            asyncio.run_coroutine_threadsafe(self._send_file(file_path), self.loop)

    async def _send_file(self, path):
        encoded = b64encode(path)
        data = {
            "type": "file",
            "username": username,
            "data": encoded,
            "filename": os.path.basename(path),
            "event": "send_message"
        }
        if self.websocket and self.websocket.open:
            await self.websocket.send(json.dumps(data))
            playeventsound("send_message")
            self.comm.print_to_console.emit(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] &lt;{username}&gt; sent an image", None)
            self.comm.print_to_console.emit("", path)

    async def receive_messages(self):
        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    if data["event"] == "srv_message":
                        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        self.comm.print_to_console.emit(f"[{timestamp}] &lt;{data['username']}&gt;", None)
                        self.comm.print_to_console.emit(data['message'], None)
                    else:
                        if data["type"] == "msg" and not data["event"] == "request":
                            msg_html = markdown_to_html(data['message'])
                            self.comm.print_to_console.emit(f"[{timestamp}] &lt;{data['username']}&gt;", None)
                            self.comm.print_to_console.emit(msg_html, None)
                            playeventsound("rcv_message")
                        elif data["type"] == "file":
                            with open(data["filename"], "wb") as f:
                                f.write(b64decode(data["data"]))
                            pixmap = QPixmap(data["filename"])
                            self.comm.print_to_console.emit(f"[{timestamp}] &lt;{data['username']}&gt; sent an image", pixmap)
                            os.remove(data["filename"])
                except json.JSONDecodeError:
                    self.comm.print_to_console.emit("Received invalid JSON", None)
                except Exception as e:
                    log(f"Error occurred when receiving a message: {e}")
        except websockets.exceptions.ConnectionClosed:
            self.comm.print_to_console.emit("Connection closed.", None)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    try:
        CLI_CONFIG = load_config()
        username = CLI_CONFIG["client"]["username"]
        host = CLI_CONFIG["server"]["host"]
        port = CLI_CONFIG["server"]["port"]
    except RuntimeError:
        QMessageBox.critical(None, "Config Error", str(e))
        sys.exit(1)
    window = ChatClient()
    window.show()
    sys.exit(app.exec_())
