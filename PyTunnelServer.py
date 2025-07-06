"""
内网穿透服务器
开发者：MuXiPixel(罗佳煊)
版本：1.0.0
更新日期：2025.7.7
python版本：Python 3.13.3
"""

import socket
import configparser
import threading
import sys
import time

def get_server_config():
    config = configparser.ConfigParser()
    config.read("server_config.ini", encoding="utf-8")
    port = config.getint("SERVER", "port", fallback=8888)
    host = config.get("SERVER", "host", fallback="")
    token = config.get("SERVER", "token", fallback="")
    NCP = config.get("SERVER", "NCP", fallback="TCP")
    backlog = config.getint("SERVER", "backlog", fallback=50)
    exit_time = config.getint("SERVER", "exit_time", fallback=5)
    heartbeat_timeout = config.getint("SERVER", "heartbeat_timeout", fallback=1800)
    server_config = {
        "port": port,
        "token": token,
        "NCP": NCP,
        "host": host,
        "backlog": backlog,
        "exit_time": exit_time,
        "heartbeat_timeout": heartbeat_timeout
    }
    return server_config

def forward_data(source, target):
    try:
        while True:
            data = source.recv(4096)
            if not data:
                break
            target.sendall(data)
    except Exception:
        pass
    finally:
        try:
            source.close()
        except:
            pass
        try:
            target.close()
        except:
            pass

def handle_tcp_client(client_socket, addr, server_token):
    try:
        auth_data = client_socket.recv(1024).decode(errors='ignore').strip()
        if not auth_data.startswith("TOKEN:"):
            print(f"[TCP][{addr}] 未发送TOKEN，断开连接")
            client_socket.close()
            return
        client_token = auth_data[6:]
        if client_token != server_token:
            print(f"[TCP][{addr}] TOKEN验证失败")
            client_socket.close()
            return
        print(f"[TCP][{addr}] TOKEN验证成功")

        target_info = client_socket.recv(1024).decode()
        try:
            target_host, target_port_str = target_info.split(":")
            target_port = int(target_port_str)
        except Exception as e:
            print(f"[TCP][{addr}] 无法解析目标地址: {e}")
            client_socket.close()
            return

        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            remote.connect((target_host, target_port))
        except Exception as e:
            print(f"[TCP][{addr}] 连接远端失败: {e}")
            client_socket.close()
            remote.close()
            return

        print(f"[转发][TCP] {addr} <-> {target_host}:{target_port}")

        threading.Thread(target=forward_data, args=(client_socket, remote), daemon=True).start()
        threading.Thread(target=forward_data, args=(remote, client_socket), daemon=True).start()
    except Exception as e:
        print(f"[TCP][{addr}] 处理失败: {e}")
        client_socket.close()

def handle_tcp(server_socket, server_token):
    while True:
        try:
            client_socket, addr = server_socket.accept()
            print(f"[TCP] 接入连接: {addr}")
            threading.Thread(target=handle_tcp_client, args=(client_socket, addr, server_token), daemon=True).start()
        except Exception as e:
            print(f"[错误] TCP监听失败: {e}")

session_map = {}
session_last_active = {}
session_heartbeat = {}
session_lock = threading.Lock()
SESSION_TIMEOUT = 300
HEARTBEAT_TIMEOUT = 1800

def cleanup_sessions():
    while True:
        now = time.time()
        with session_lock:
            expired = []
            for client, last_time in session_last_active.items():
                timeout = HEARTBEAT_TIMEOUT if session_heartbeat.get(client, False) else SESSION_TIMEOUT
                if now - last_time > timeout:
                    expired.append(client)
            
            for client in expired:
                print(f"[UDP] 清理超时会话: {client} -> {session_map.get(client)}")
                session_map.pop(client, None)
                session_last_active.pop(client, None)
                session_heartbeat.pop(client, None)
        time.sleep(60)

def handle_udp(server_socket, server_token):
    while True:
        try:
            data, addr = server_socket.recvfrom(4096)
            with session_lock:
                try:
                    parts = data.split(b'\n', 2)
                    if len(parts) == 3 and parts[2] == b'HEARTBEAT':
                        token_line = parts[0].decode(errors='ignore').strip()
                        if token_line.startswith("TOKEN:") and token_line[6:] == server_token:
                            session_last_active[addr] = time.time()
                            session_heartbeat[addr] = True
                            print(f"[UDP] 收到心跳来自 {addr}")
                            continue
                except:
                    pass
                
                if addr in session_map:
                    target_addr = session_map[addr]
                    server_socket.sendto(data, target_addr)
                    session_last_active[addr] = time.time()
                    session_heartbeat[addr] = False
                    continue

                reversed_clients = [client for client, target in session_map.items() if target == addr]
                if reversed_clients:
                    for client in reversed_clients:
                        server_socket.sendto(data, client)
                        session_last_active[client] = time.time()
                        session_heartbeat[client] = False
                    continue

                try:
                    parts = data.split(b'\n', 2)
                    if len(parts) < 3:
                        print(f"[UDP][{addr}] 数据格式错误，不足3段")
                        continue
                    token_line = parts[0].decode(errors='ignore').strip()
                    target_line = parts[1].decode(errors='ignore').strip()
                    payload = parts[2]

                    if not token_line.startswith("TOKEN:"):
                        print(f"[UDP][{addr}] 缺少TOKEN字段，丢弃")
                        continue
                    client_token = token_line[6:]
                    if client_token != server_token:
                        print(f"[UDP][{addr}] TOKEN验证失败")
                        continue

                    target_ip, target_port_str = target_line.split(":")
                    target_port = int(target_port_str)
                    target_addr = (target_ip, target_port)

                    session_map[addr] = target_addr
                    session_last_active[addr] = time.time()
                    session_heartbeat[addr] = False

                    server_socket.sendto(payload, target_addr)
                    print(f"[UDP] 新建会话 {addr} -> {target_addr}")

                except Exception as e:
                    print(f"[UDP][{addr}] 解析失败: {e}")

        except Exception as e:
            print(f"[错误] UDP处理失败: {e}")

def open_socket():
    server_socket_config = get_server_config()
    protocol = server_socket_config["NCP"].upper()
    server_token = server_socket_config["token"]
    global HEARTBEAT_TIMEOUT
    HEARTBEAT_TIMEOUT = server_socket_config["heartbeat_timeout"]

    if protocol == "UDP":
        print("服务器传输协议: UDP")
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            server_socket.bind((server_socket_config["host"], server_socket_config["port"]))
            print(f"服务器访问端口: {server_socket_config['port']}")
        except Exception:
            print(f"服务器地址或端口绑定失败\n程序将在{server_socket_config['exit_time']}秒后退出...")
            time.sleep(server_socket_config["exit_time"])
            sys.exit(1)

        threading.Thread(target=cleanup_sessions, daemon=True).start()
        handle_udp(server_socket, server_token)

    elif protocol == "TCP":
        print("服务器传输协议: TCP")
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server_socket.bind((server_socket_config["host"], server_socket_config["port"]))
        except Exception:
            print(f"服务器地址或端口绑定失败\n程序将在{server_socket_config['exit_time']}秒后退出...")
            time.sleep(server_socket_config["exit_time"])
            sys.exit(1)
        try:
            server_socket.listen(server_socket_config["backlog"])
            print(f"服务器监听已开启，监听端口: {server_socket_config['port']}")
        except Exception:
            print(f"服务器{server_socket_config['port']}端口监听失败, 可能该端口已被占用或不合法\n程序将在{server_socket_config['exit_time']}秒后退出")
            time.sleep(server_socket_config["exit_time"])
            sys.exit(1)

        handle_tcp(server_socket, server_token)

    else:
        print(f"协议不受支持或该协议不存在\n程序将在{server_socket_config['exit_time']}秒后退出...")
        time.sleep(server_socket_config["exit_time"])
        sys.exit(1)

if __name__ == "__main__":
    main_thread = threading.Thread(target=open_socket, daemon=False)
    main_thread.start()