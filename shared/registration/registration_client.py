#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import grpc
import socket
import subprocess
import time
from datetime import datetime
import sys
import os
import ipaddress
import json
import locale
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nodeinfo_pb2
import nodeinfo_pb2_grpc

GRPC_PORT = 50051
RETRY_INTERVAL = 5

CERT_DIR = '/shared/certs'
CA_CERT = os.path.join(CERT_DIR, "ca.crt")

def get_hostname():
    return socket.gethostname()

def get_as_number():
    try:
        result = subprocess.run(['vtysh', '-c', 'show running-config'],
                              capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            if line.strip().startswith('router bgp'):
                parts = line.split()
                if len(parts) >= 3 and parts[2].isdigit():
                    return int(parts[2])
    except:
        pass
    return None
    
def get_locator():
    try:
        result = subprocess.run(
            ['vtysh', '-c', 'show segment-routing srv6 locator json'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=5
        )
        
        if result.returncode != 0:
            return "N/A"
            
        if not result.stdout.strip():
            return "N/A"
    
        data = json.loads(result.stdout)
        
        if "locators" in data and len(data["locators"]) > 0:
            for locator in data["locators"]:
                if "prefix" in locator:
                    return locator["prefix"]
        
        return "N/A"
        
    except Exception:
        return "N/A"

def find_interface_for_neighbor(neighbor_ip):
    try:
        result = subprocess.run(
            ["ip", "-6", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        
        neighbor_addr = ipaddress.ip_address(neighbor_ip)
        
        for line in result.stdout.split('\n'):
            if not line.strip():
                continue
            
            parts = line.split()
            if len(parts) < 4:
                continue
            
            interface = parts[1]
            addr_with_prefix = parts[3]
            
            # Salta loopback e link-local
            if interface == 'lo' or addr_with_prefix.startswith('fe80:'):
                continue
            
            try:
                my_network = ipaddress.ip_interface(addr_with_prefix).network
                
                # Se il neighbor è nella stessa rete, questa è l'interfaccia giusta
                if neighbor_addr in my_network:
                    return interface
                    
            except ValueError:
                continue
        
        return None
        
    except Exception as e:
        return None

def get_bgp_neighbors():
    neighbors = []
    
    try:
        result = subprocess.run(
            ['vtysh', '-c', 'show bgp ipv6 summary json'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=5
        )
        
        if result.returncode != 0:
            print(f"vtysh failed: {result.stderr}")
            return []
        
        bgp_data = json.loads(result.stdout)
        peers = {}
        
        if "ipv6Unicast" in bgp_data and "peers" in bgp_data["ipv6Unicast"]:
            peers = bgp_data["ipv6Unicast"]["peers"]
        elif "peers" in bgp_data:
            peers = bgp_data["peers"]
        
        #trova interfacce neighbor
        for neighbor_ip, neighbor_info in peers.items():
            # Salta link-local
            if neighbor_ip.startswith('fe80'):
                continue
            
            neighbor_asn = neighbor_info.get('remoteAs', 0)
            interface = find_interface_for_neighbor(neighbor_ip)
            
            if interface:
                neighbor_data = {
                    'neighbor_ip': neighbor_ip,
                    'neighbor_asn': neighbor_asn,
                    'interface': interface
                }
                
                neighbors.append(neighbor_data)
        
        return neighbors
        
    except json.JSONDecodeError as e:
        print(f"SON decode error: {e}")
        return []
    except Exception as e:
        print(f"Error gathering BGP neighbors: {e}")
        return []

def get_ipv4_from_interface(interface):
    try:
        cmd = ["ip", "-4", "-o", "addr", "show", "dev", interface]
        out = subprocess.check_output(cmd, text=True, encoding='utf-8', errors='ignore').strip()
        if out:
            return out.split()[3].split('/')[0]
    except Exception:
        pass
    return "0.0.0.0"

def get_ipv6_from_interface(interface):
    try:
        cmd = ["ip", "-6", "-o", "addr", "show", "dev", interface, "scope", "global"]
        out = subprocess.check_output(cmd, text=True, encoding='utf-8', errors='ignore').strip()
        if out:
            return out.split()[3].split('/')[0]
    except Exception:
        pass
    return "::"

def get_lan_address(interface):
    try:
        cmd = ["ip", "-4", "-o", "addr", "show", "dev", interface]
        out = subprocess.check_output(cmd, text=True, encoding='utf-8',errors='ignore').strip()
        return out.split()[3]
    except Exception:
        return None

def is_grpc_service(ip):
    try:
        with socket.create_connection((ip, GRPC_PORT), timeout=1):
            return True
    except Exception:
        return False

def find_controller_interface():
    try:
        result = subprocess.run(
            ["ip", "-o", "link", "show"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        interfaces = []
        for line in result.stdout.split('\n'):
            if 'eth' in line and 'state UP' in line:
                iface = line.split(':')[1].strip()
                interfaces.append(iface)
        
        sys.stdout.flush()
        
        for iface in interfaces:
            addr = get_lan_address(iface)
            if addr is None:
                continue
            try:
                net = ipaddress.ip_interface(addr).network
                for host in net.hosts():
                    ip = str(host)
                    my_ip = addr.split('/')[0]
                    if ip == my_ip:
                        continue
                    if is_grpc_service(ip):
                        sys.stdout.flush()
                        return iface, ip
            except Exception:
                continue
    except Exception as e:
        print(f"Error search: {e}")
        sys.stdout.flush()
    return None, None

def try_register(stub, hostname, interface, attempt):
    try:
        ipv4 = get_ipv4_from_interface(interface)
        ipv6 = get_ipv6_from_interface(interface)
        router_bgp = get_as_number()
        locator = get_locator()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        neighbors = get_bgp_neighbors()
        neighbors_json = json.dumps(neighbors)
        
        node_info = nodeinfo_pb2.NodeInfoMessage(
            hostname=hostname,
            ipv4=ipv4,
            ipv6=ipv6,
            router_bgp=router_bgp,
            locator=locator,
            timestamp=timestamp,
            networks=neighbors_json
        )
        
        print(f"Attempt #{attempt}")
        print(f"   IPv4: {ipv4} | IPv6: {ipv6}")
        print(f"   AS: {router_bgp} | Locator: {locator}")
        print(f"   Networks found: {len(neighbors)}")
        for net in neighbors:
            print(f"      • {net['neighbor_ip']} (dev {net['interface']})")
        sys.stdout.flush()
        
        response = stub.RegisterNode(node_info, timeout=10)
        
        if response.success:
            print(f"{response.message}")
            print("=" * 60)
            sys.stdout.flush()
            return True
        else:
            print(f"{response.message}")
            sys.stdout.flush()
            return False
            
    except grpc.RpcError as e:
        print(f"Error RPC: {e.code()}")
        sys.stdout.flush()
        return False
    except Exception as e:
        print(f"Error: {e}")
        sys.stdout.flush()
        return False

def run_client():
    sys.stdout.flush()
    
    print("=" * 60)
    print("PHASE 1 - REGISTRATION TRUSTED NODE FROM A CLIENT")
    print("=" * 60)
    
    try:
        with open(CA_CERT, 'rb') as f:
            ca_cert = f.read()
    except FileNotFoundError:
        print(f"[client] Errore: Certificato CA non trovato in {CA_CERT}")
        return

    credentials = grpc.ssl_channel_credentials(root_certificates=ca_cert)
    
    #trova prima il controller
    if len(sys.argv) > 1:
        interface = sys.argv[1]
        sys.stdout.flush()
        
        addr = get_lan_address(interface)
        if not addr:
            print(f"Impossible to read address on {interface}")
            sys.exit(1)
        
        net = ipaddress.ip_interface(addr).network
        ctrl_ip = None
        for host in net.hosts():
            ip = str(host)
            if ip != addr.split('/')[0] and is_grpc_service(ip):
                ctrl_ip = ip
                break
        
        if not ctrl_ip:
            print("No controller in this network")
            sys.exit(1)
    else:
        sys.stdout.flush()
        interface, ctrl_ip = find_controller_interface()
        if ctrl_ip is None:
            print("Controller not found")
            sys.exit(1)
    
    target = f"{ctrl_ip}:{GRPC_PORT}"
    hostname = get_hostname()
    
    print(f"Controller: {target}")
    print(f"Hostname: {hostname}")
    print("=" * 60)
    sys.stdout.flush()
    
    attempt = 0
    
    while True:
        attempt += 1
        
        try:
            channel = grpc.secure_channel(
                target,
                credentials,
                options=[
                    ('grpc.ssl_target_name_override', 'ctrl'),
                    ('grpc.keepalive_time_ms', 30000),
                    ('grpc.keepalive_timeout_ms', 10000),
                    ]
                )
            
            stub = nodeinfo_pb2_grpc.NodeInfoServiceStub(channel)
            
            if try_register(stub, hostname, interface, attempt):
                channel.close()
                print("SUCCESS! You're in as TRUSTED node")
                sys.exit(0)
            
            channel.close()
            
        except Exception as e:
            print(f"Error connection: {e}")
            sys.stdout.flush()
        
        print(f"New attempt in {RETRY_INTERVAL} seconds...\n")
        sys.stdout.flush()
        time.sleep(RETRY_INTERVAL)

if __name__ == "__main__":
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except:
        pass
    
    try:
        run_client()
    except KeyboardInterrupt:
        print("\nClient on closure...")
        sys.exit(0)
    except Exception as e:
        print(f"\nError fatal {e}")
        traceback.print_exc()
        sys.exit(1)
