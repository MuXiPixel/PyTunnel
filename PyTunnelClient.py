"""
内网穿透客户端
开发者：MuXiPixel（罗佳煊）
版本：1.0.0
更新日期：2025.7.7
"""

import socket
import threading
import sys
import time
import argparse
import configparser
from pathlib import Path

class ConfigLoader:
    @staticmethod
    def load():
        """智能配置加载：支持文件+命令行+环境变量"""
        defaults = {
            'server_ip': None,
            'server_port': None,
            'token': None,
            'protocol': None,
            'local_ip': '127.0.0.1',
            'local_port': None,
            'remote_port': None,
            'heartbeat': 30,
            'timeout': 60
        }

        file_config = ConfigLoader._load_file()
        cli_config = ConfigLoader._parse_cli()
        config = {**defaults, **file_config, **cli_config}
        
        required = ['server_ip', 'server_port', 'token', 'protocol', 'local_port', 'remote_port']
        if any(config[k] is None for k in required):
            print("错误：缺少必要配置参数！")
            print("必须提供：", ", ".join(required))
            sys.exit(1)
            
        config['protocol'] = config['protocol'].upper()
        if config['protocol'] not in ['TCP', 'UDP']:
            print("错误：协议必须是TCP或UDP")
            sys.exit(1)
            
        return config

    @staticmethod
    def _load_file():
        """从client_config.ini加载配置"""
        config_path = Path("client_config.ini")
        if not config_path.exists():
            return {}

        try:
            parser = configparser.ConfigParser()
            parser.read(config_path, encoding='utf-8')
            if not parser.has_section('CLIENT'):
                return {}

            return {
                'server_ip': parser.get('CLIENT', 'server_ip', fallback=None),
                'server_port': parser.getint('CLIENT', 'server_port', fallback=None),
                'token': parser.get('CLIENT', 'token', fallback=None),
                'protocol': parser.get('CLIENT', 'protocol', fallback=None),
                'local_ip': parser.get('CLIENT', 'local_ip', fallback='127.0.0.1'),
                'local_port': parser.getint('CLIENT', 'local_port', fallback=None),
                'remote_port': parser.getint('CLIENT', 'remote_port', fallback=None),
                'heartbeat': parser.getint('CLIENT', 'heartbeat_interval', fallback=30),
                'timeout': parser.getint('CLIENT', 'timeout', fallback=60)
            }
        except Exception as e:
            print(f"配置文件解析警告: {e}")
            return {}

    @staticmethod
    def _parse_cli():
        """解析命令行参数"""
        parser = argparse.ArgumentParser(
            description="内网穿透客户端 (兼容server1.py协议)",
            formatter_class=argparse.RawTextHelpFormatter
        )
        
        parser.add_argument('-s', '--server', help='服务器IP地址')
        parser.add_argument('-p', '--port', type=int, help='服务器端口')
        parser.add_argument('-t', '--token', help='认证令牌')
        parser.add_argument('-P', '--protocol', choices=['tcp', 'udp', 'TCP', 'UDP'], help='协议类型')
        parser.add_argument('-l', '--local-ip', help='本地绑定IP (默认:127.0.0.1)')
        parser.add_argument('--local-port', type=int, help='本地监听端口')
        parser.add_argument('--remote-port', type=int, help='远程目标端口')
        parser.add_argument('--heartbeat', type=int, help='UDP心跳间隔(秒)')
        parser.add_argument('--timeout', type=int, help='连接超时(秒)')
        
        return {k: v for k, v in vars(parser.parse_args()).items() if v is not None}

class BaseClient:
    def __init__(self, config):
        self.config = config
        self.running = True
        self._print_banner()

    def _print_banner(self):
        info = [
            f"{'='*50}",
            f"内网穿透客户端 v2.0 [协议: {self.config['protocol']}]",
            f"服务器: {self.config['server_ip']}:{self.config['server_port']}",
            f"本地绑定: {self.config['local_ip']}:{self.config['local_port']}",
            f"远程端口: {self.config['remote_port']}",
            f"超时设置: {self.config['timeout']}秒"
        ]
        
        if self.config['protocol'] == 'UDP':
            info.append(f"心跳间隔: {self.config['heartbeat']}秒")
            
        info.append(f"{'='*50}")
        print("\n".join(info))

class TCPClient(BaseClient):
    def start(self):
        try:
            with socket.create_connection(
                (self.config['server_ip'], self.config['server_port']),
                timeout=self.config['timeout']
            ) as server_sock:
                server_sock.sendall(f"TOKEN:{self.config['token']}".encode())
                server_sock.sendall(f"127.0.0.1:{self.config['remote_port']}".encode())
                
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as local_sock:
                    local_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    local_sock.bind((self.config['local_ip'], self.config['local_port']))
                    local_sock.listen(5)
                    
                    while self.running:
                        try:
                            client_sock, addr = local_sock.accept()
                            print(f"[TCP] 接受本地连接: {addr}")
                            threading.Thread(
                                target=self._forward,
                                args=(client_sock, server_sock),
                                daemon=True
                            ).start()
                        except socket.timeout:
                            continue
        except Exception as e:
            print(f"[TCP] 错误: {e}")
            self.running = False

    def _forward(self, source, target):
        try:
            while self.running:
                data = source.recv(4096)
                if not data:
                    break
                target.sendall(data)
        except Exception as e:
            print(f"[TCP] 转发错误: {e}")
        finally:
            source.close()

class UDPClient(BaseClient):
    def start(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind((self.config['local_ip'], self.config['local_port']))
                sock.settimeout(1)
                
                recv_thread = threading.Thread(
                    target=self._recv_loop,
                    args=(sock,),
                    daemon=True
                )
                recv_thread.start()
                
                while self.running:
                    self._send_heartbeat(sock)
                    for _ in range(self.config['heartbeat']):
                        if not self.running:
                            break
                        time.sleep(1)
                        
        except Exception as e:
            print(f"[UDP] 错误: {e}")
            self.running = False

    def _recv_loop(self, sock):
        while self.running:
            try:
                data, addr = sock.recvfrom(4096)
                if addr[0] == self.config['server_ip']:
                    self._send_to_local(sock, data)
                else:
                    self._send_to_server(sock, data, addr)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[UDP] 接收错误: {e}")

    def _send_to_local(self, sock, data):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as local_sock:
                local_sock.sendto(
                    data, 
                    (self.config['local_ip'], self.config['remote_port'])
                )
        except Exception as e:
            print(f"[UDP] 本地转发失败: {e}")

    def _send_to_server(self, sock, data, addr):
        try:
            packet = (
                f"TOKEN:{self.config['token']}\n"
                f"{addr[0]}:{addr[1]}\n".encode() + data
            )
            sock.sendto(
                packet,
                (self.config['server_ip'], self.config['server_port'])
            )
        except Exception as e:
            print(f"[UDP] 服务器转发失败: {e}")

    def _send_heartbeat(self, sock):
        try:
            packet = (
                f"TOKEN:{self.config['token']}\n"
                f"{self.config['local_ip']}:{self.config['local_port']}\n"
                "HEARTBEAT".encode()
            )
            sock.sendto(
                packet,
                (self.config['server_ip'], self.config['server_port'])
            )
        except Exception as e:
            print(f"[UDP] 心跳发送失败: {e}")

def main():
    config = ConfigLoader.load()
    client_class = TCPClient if config['protocol'] == 'TCP' else UDPClient
    client = client_class(config)
    
    try:
        client.start()
    except KeyboardInterrupt:
        print("\n客户端安全关闭...")
        client.running = False
        sys.exit(0)

if __name__ == "__main__":
    main()