import socket
import pickle
import select
import sys
import threading
import psutil
import time

from pystray import Icon, Menu, MenuItem
from pystray._win32 import *
from win32api import GetMonitorInfo, MonitorFromPoint
from PIL import Image, ImageDraw, ImageFont

class IconEx(Icon):
    """Extended Icon class with custom tooltip positioning."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _on_notify(self, wparam, lparam):
        """Handle notification events for the tray icon."""
        if self._menu_handle and lparam == win32.WM_LBUTTONUP or lparam == win32.WM_RBUTTONUP:
            self.monitor_info = GetMonitorInfo(MonitorFromPoint((0, 0)))
            self.tooltip_offset = (
                self.monitor_info.get("Monitor")[3] - 
                self.monitor_info.get("Work")[3]
            )
            
            win32.SetForegroundWindow(self._hwnd)
            
            point = wintypes.POINT()
            win32.GetCursorPos(ctypes.byref(point))

            hmenu, descriptors = self._menu_handle
            index = win32.TrackPopupMenuEx(
                hmenu,
                win32.TPM_LEFTALIGN | win32.TPM_TOPALIGN | win32.TPM_RETURNCMD,
                point.x,
                self.monitor_info.get("Monitor")[3] - self.tooltip_offset - 4,
                self._menu_hwnd,
                None
            )
            if index > 0:
                descriptors[index - 1](self)

def create_text_icon(character: str, size: int, color: tuple) -> Image.Image:
    """Create an icon image from a text character."""
    font = ImageFont.truetype('C:\\Windows\\Fonts\\segoeicons.ttf', size + 32)
    length = int(font.getlength(character)) - 16
    img = Image.new('RGBA', (length, length), color=(0, 0, 0, 0))
    
    draw = ImageDraw.Draw(img)
    draw.text((0, 0), character, font=font, fill=color, spacing=16)
    return img

def handle_client(conn, address):
    global status_text

    """Handle individual client connection."""
    try:
        # Set socket to non-blocking temporarily to check if it's still connected
        conn.setblocking(False)
        
        # Wait a moment and check if connection is still alive
        time.sleep(0.3)
        
        # Check if socket is readable (which would mean it's closed or has data)
        readable, _, exceptional = select.select([conn], [], [conn], 0)
        
        if readable or exceptional:
            # Socket is readable with no data sent = closed connection (ping)
            try:
                data = conn.recv(1, socket.MSG_PEEK)
                if not data:
                    print(f"Ping check from: {address}")
                    return
            except (socket.error, BlockingIOError):
                print(f"Ping check from: {address}")
                return
        
        # Set back to blocking mode with timeout
        conn.setblocking(True)
        conn.settimeout(1.0)
        
        # Try to send a small keepalive first to verify connection is still alive
        try:
            conn.sendall(b'keepalive')
        except (socket.error, BrokenPipeError, ConnectionResetError, OSError):
            print(f"Ping check from: {address}")
            return
        
        print(f"Client connected: {address}")
        status_text = f"Status: {address[0]} connected"
        tray.icon = create_text_icon(icons[1], 512, (255, 255, 255))
        tray._update_icon()
        tray._update_menu()
        
        cache = None
        
        while not stopped:
            try:
                battery = psutil.sensors_battery()
                if cache != battery:
                    print("Sending new data.")
                    cache = battery
                    conn.sendall(pickle.dumps(cache))
                else:
                    conn.sendall(b'keepalive')
                
                time.sleep(1)
                
            except socket.timeout:
                # Client might have disconnected, try to send to verify
                try:
                    conn.sendall(b'keepalive')
                except (socket.error, BrokenPipeError, ConnectionResetError, OSError):
                    print(f"Client timeout: {address}")
                    break
            except (socket.error, BrokenPipeError, ConnectionResetError, OSError):
                print(f"Client disconnected: {address}")
                break
            except Exception as err:
                print(f"Error handling client: {err}")
                break
                
    finally:
        try:
            conn.close()
        except Exception:
            pass
        print(f"Connection closed: {address}")
        status_text = f"Status: Idle"
        tray.icon = create_text_icon(icons[0], 512, (255, 255, 255))
        tray._update_icon()
        tray._update_menu()


def server():
    global status_text

    """Main server loop."""
    try:
        server_socket.bind((HOST, PORT))
        server_socket.listen(5)
        server_socket.settimeout(1.0)
        print(f"Listening on {HOST}:{PORT}")
    except socket.error as err:
        print(f"Failed to start server: {err}")
        return
    
    client_threads = []
    
    while not stopped:
        try:
            conn, address = server_socket.accept()
            
            # Start a new thread for each client
            client_thread = threading.Thread(
                target=handle_client,
                args=(conn, address),
                daemon=True
            )
            client_thread.start()
            client_threads.append(client_thread)
            
            # Clean up finished threads
            client_threads = [t for t in client_threads if t.is_alive()]
            
        except socket.timeout:
            continue
        except socket.error as err:
            if not stopped:
                print(f"Accept error: {err}")
                time.sleep(1)
    
    # Cleanup
    try:
        server_socket.close()
    except Exception:
        pass
    
    # Wait for client threads to finish
    for thread in client_threads:
        thread.join(timeout=2.0)
    
    print("Server stopped")

def exit():
    global stopped
    stopped = True

# Configuration
HOST = "0.0.0.0"
PORT = 8473
status_text = "Status: Idle"

icons = (u"\uF608", u"\uF5FD")
tray = IconEx(
    "remote_battery",
    icon=create_text_icon(icons[0], 512, (255, 255, 255)),
    title=f"RemoteBattery Server",
    menu=Menu(MenuItem(lambda _: status_text, lambda _: None), MenuItem("Exit", exit))
)

# Global state
stopped = False

# Initialize server socket
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

# Start server thread
main_thread = threading.Thread(target=server, daemon=True)
tray_thread = threading.Thread(target=tray.run, daemon=True)

if __name__ == "__main__":
    try:
        tray_thread.start()
        main_thread.start()
        print("Server started. Press Ctrl+C to stop.")
        while main_thread.is_alive():
            tray_thread.join(1)
            main_thread.join(1)
    except (KeyboardInterrupt, SystemExit):
        print("\nShutdown requested.")
    finally:
        print("\nShutting down...")
        stopped = True
        try:
            server_socket.close()
        except Exception:
            pass
        tray.stop()
        tray_thread.join(timeout=3.0)
        main_thread.join(timeout=3.0)
        sys.exit(0)
