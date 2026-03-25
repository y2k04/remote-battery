import socket
import pickle
import subprocess
import sys
import threading
import traceback
import time
from typing import Optional

from psutil import POWER_TIME_UNLIMITED
from pystray import Icon, Menu, MenuItem
from pystray._win32 import *
from win32api import GetMonitorInfo, MonitorFromPoint
from PIL import Image, ImageDraw, ImageFont


class IconEx(Icon):
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
                self.monitor_info.get("Monitor")[3] - self.tooltip_offset - 8,
                self._menu_hwnd,
                None
            )
            if index > 0:
                descriptors[index - 1](self)


def get_remote_ip() -> Optional[str]:
    try:
        result = subprocess.run(
            'netstat -n | find ":3389" | find "ESTABLISHED"',
            shell=True,
            capture_output=True,
            text=True,
            timeout=5
        )
        parts = result.stdout.strip().split()
        if len(parts) >= 4:
            return parts[2].split(':')[0]
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"Error getting remote IP: {e}")
    return None


def ping_port(ip: str, port: int, timeout: float = 0.5) -> bool:
    test_socket = None
    try:
        test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_socket.settimeout(timeout)
        result = test_socket.connect_ex((ip, port))
        return result == 0
    except Exception:
        return False
    finally:
        if test_socket:
            try:
                test_socket.close()
            except Exception:
                pass


def wait_for_port(ip: str, port: int, check_ip_change_interval: int = 5) -> Optional[str]:
    attempt = 0
    current_ip = ip
    
    while not stopped:
        attempt += 1
      
        if attempt % check_ip_change_interval == 0:
            print("Checking for IP change...")
            new_ip = get_remote_ip()
            if new_ip and new_ip != current_ip:
                print(f"Remote IP changed from {current_ip} to {new_ip}")
                current_ip = new_ip
                attempt = 0
        
        if not current_ip:
            print("No remote IP found, checking again...")
            time.sleep(1)
            current_ip = get_remote_ip()
            continue
        
        print(f"Ping attempt {attempt} to {current_ip}:{port}")
        if ping_port(current_ip, port, timeout=0.5):
            print(f"Port {port} is open on {current_ip}")
            return current_ip
        
        time.sleep(1)
    
    return None


def try_connect(target_ip: str) -> bool:
    global client
    
    try:
        if client:
            try:
                client.close()
            except Exception:
                pass
        
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(3.0)
        
        print(f"Connecting to {target_ip}:{PORT}")
        client.connect((target_ip, PORT))
        
        print("Connected to server")
        return True
        
    except socket.timeout:
        print("Connection timeout")
        return False
    except socket.error as err:
        print(f"Connection failed: {err}")
        return False
    except Exception as err:
        print(f"Unexpected error during connection: {err}")
        return False


def convert_time(seconds: int) -> str:
    if seconds < 0:
        return "N/A"

    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours > 48:
        return "Calculating..."
    elif hours == 0:
        return f"{minutes} min"
    return f"{hours} hr {minutes:02d} min"


def create_text_icon(character: str, size: int, color: tuple) -> Image.Image:
    font = ImageFont.truetype('C:\\Windows\\Fonts\\segoeicons.ttf', size + 32)
    length = int(font.getlength(character)) - 16
    img = Image.new('RGBA', (length, length), color=(0, 0, 0, 0))
    
    draw = ImageDraw.Draw(img)
    draw.text((0, 0), character, font=font, fill=color, spacing=16)
    return img


def update_tray_disconnected():
    global status_text
    status_text = "Disconnected - Connecting..."
    try:
        tray.icon = create_text_icon(ICON_SET['discharging'][0], 512, (255, 100, 100))
        tray.title = f"Battery status: {status_text}"
        tray._update_icon()
        tray.update_menu()
    except Exception as e:
        print(f"Error updating tray icon: {e}")


def monitor():
    global status_text, client, connected, current_remote_ip
    
    print("Waiting for tray to initialize...")
    time.sleep(1)
    
    print("Showing disconnected state")
    update_tray_disconnected()
    
    current_remote_ip = get_remote_ip()
    if not current_remote_ip:
        print("No remote IP found initially, waiting...")
    
    while not stopped:
        if not connected:
            print("Waiting for server to be available...")
            
            available_ip = wait_for_port(current_remote_ip or "0.0.0.0", PORT, check_ip_change_interval=5)
            
            if not available_ip or stopped:
                continue
            
            current_remote_ip = available_ip
            
            print(f"Port is available, attempting connection to {current_remote_ip}")
            if try_connect(current_remote_ip):
                connected = True
                print("Connected successfully")
                continue
            else:
                print("Connection failed despite port being open, retrying...")
                time.sleep(2)
                continue
        
        try:
            data = client.recv(4096)
            
            if not data:
                print("Connection closed by server")
                connected = False
                update_tray_disconnected()
                continue
                
            if data == b'keepalive':
                continue
            
            print("New data received.")
            battery = pickle.loads(data)
            print(f"Battery: {battery.percent}%, Plugged: {battery.power_plugged}")
            
            if battery.power_plugged:
                if battery.percent == 100:
                    status_text = f"Fully charged ({battery.percent}%)"
                else:
                    status_text = f"{battery.percent}% available (plugged in)"
                
                status_tooltip = f"{battery.percent}% available"
            else:
                if battery.secsleft != POWER_TIME_UNLIMITED:
                    status_text = f"{convert_time(battery.secsleft)} ({battery.percent}%) remaining"
                else:
                    status_text = f"{battery.percent}% remaining"
                
                status_tooltip = f"{battery.percent}% remaining"

            status = 'charging' if battery.power_plugged else 'discharging'
            level = min(battery.percent // 10, 10)
            
            print(f"Status: {status}, Level index: {level}")
            
            tray.icon = create_text_icon(ICON_SET[status][level], 512, (255, 255, 255))
            tray.title = f"Battery status: {status_tooltip}"
            tray._update_icon()
            tray.update_menu()
            
        except socket.timeout:
            print("Socket timeout, checking connection...")
            try:
                client.sendall(b'keepalive')
            except (socket.error, BrokenPipeError, ConnectionResetError):
                print("Connection lost")
                connected = False
                update_tray_disconnected()
        except (socket.error, BrokenPipeError, ConnectionResetError) as err:
            print(f"Socket error: {err}")
            connected = False
            update_tray_disconnected()
        except Exception as err:
            print(f"Error in monitor: {err}")
            traceback.print_exc()
            connected = False
            update_tray_disconnected()

def exit():
    global stopped
    stopped = True

PORT = 8473

ICON_SET = {
    "discharging": (
        '\uEBA0', '\uEBA1', '\uEBA2', '\uEBA3', '\uEBA4',
        '\uEBA5', '\uEBA6', '\uEBA7', '\uEBA8', '\uEBA9', '\uEBAA'
    ),
    "charging": (
        '\uEBAB', '\uEBAC', '\uEBAD', '\uEBAE', '\uEBAF',
        '\uEBB0', '\uEBB1', '\uEBB2', '\uEBB3', '\uEBB4', '\uEBB5'
    )
}

status_tooltip = 'Disconnected - Connecting...'
status_text = 'Disconnected - Connecting...'
stopped = False
connected = False
client = None
current_remote_ip = None

# Initialize tray icon with disconnected state
tray = IconEx(
    "remote_battery",
    icon=create_text_icon(ICON_SET['discharging'][0], 512, (255, 100, 100)),
    title=f"Battery status: {status_tooltip}",
    menu=Menu(MenuItem(lambda _: status_text, lambda _: None), MenuItem("Exit", exit))
)


def main():
    global stopped
    
    print("Starting Remote Battery Monitor...")
    
    monitor_thread = threading.Thread(target=monitor, daemon=True)
    tray_thread = threading.Thread(target=tray.run, daemon=True)
    
    try:
        print("Starting tray thread...")
        tray_thread.start()
        
        print("Starting monitor thread...")
        monitor_thread.start()
        
        while monitor_thread.is_alive():
            monitor_thread.join(1)
            tray_thread.join(1)
            
    except (KeyboardInterrupt, SystemExit, OSError):
        print('\n! Received keyboard interrupt, quitting threads.\n')
    finally:
        print("Shutting down...")
        stopped = True
        if client:
            try:
                client.close()
            except Exception:
                pass
        tray.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
