#!/usr/bin/env python3

import grpc
import subprocess
import json
import sys
import os
import socket
import ipaddress
import time
import traceback

sys.path.append('/shared')
import bgp_segments_pb2
import bgp_segments_pb2_grpc

CERT_DIR = '/shared/certs'
CA_CERT = os.path.join(CERT_DIR, "ca.crt")
CONTROLLER_PORT = 50052
RETRY_INTERVAL = 5

def get_asn_from_frr():
    try:
        result = subprocess.run(
            ['vtysh', '-c', 'show running-config'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        for line in result.stdout.split('\n'):
            line = line.strip()
            if line.startswith('router bgp'):
                parts = line.split()
                if len(parts) >= 3 and parts[2].isdigit():
                    return int(parts[2])
        
        print("No ASN in the FRR configuration")
        return None
        
    except Exception as e:
        print(f"Error from FRR: {e}")
        return None

def extract_bgp_paths():
    try:
        result = subprocess.run(
            ['vtysh', '-c', 'show ip bgp json'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            print(f"Error vtysh: {result.stderr}")
            return []
        
        bgp_data = json.loads(result.stdout)
        routes = bgp_data.get("routes", {})
        paths = []
        
        for prefix, route_info in routes.items():
            for entry in route_info:
                path = entry.get("path") or ''
                best = entry.get("bestpath") or False
                if path:
                    as_sequence = [
                        int(asn) for asn in path.split() 
                        if asn.isdigit()
                    ]
                    if as_sequence and as_sequence not in paths:
                        paths.append(as_sequence)
                    if best:
                        print(path, best)
        return paths
        
    except subprocess.TimeoutExpired:
        print("Timeout vtysh")
        return []
    except json.JSONDecodeError as e:
        print(f"Error JSON: {e}")
        return []
    except Exception as e:
        print(f"Error: {e}")
        return []

def calculate_segments_from_paths(paths, local_asn):
    segments = set()

    for path in paths:
        if path and path[0] != local_asn:
            full_path = [local_asn] + path
        else:
            full_path = path
            
        for i in range(len(full_path) - 1):
            as_a = full_path[i]
            as_b = full_path[i + 1]
            
            #evita duplicati
            segment = (min(as_a, as_b), max(as_a, as_b))
            segments.add(segment)
    
    return list(segments)

def get_all_networks():
    networks = []
    
    try:
        result = subprocess.run(
            ["ip", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        
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
                network = ipaddress.ip_interface(addr_with_prefix).network
                
                net_info = {
                    'network': str(network),
                    'interface': interface,
                    'is_ipv6': ':' in addr_with_prefix
                }
                
                #evita duplicati
                if not any(n['network'] == net_info['network'] for n in networks):
                    networks.append(net_info)
                    
            except ValueError:
                continue
                
    except Exception as e:
        print(f"Error on the gathering networks: {e}")
    
    return networks

def get_lan_address(interface):
    try:
        cmd = ["ip", "-4", "-o", "addr", "show", "dev", interface]
        out = subprocess.check_output(cmd, text=True, encoding='utf-8', errors='ignore').strip()
        return out.split()[3]
    except Exception:
        return None

def is_grpc_service(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=1):
            return True
    except Exception:
        return False

def find_controller():
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
                    if is_grpc_service(ip, CONTROLLER_PORT):
                        return ip
            except Exception:
                continue
                
    except Exception as e:
        print(f"Error in the controller search: {e}")
    
    return None

def send_bgp_data(controller_ip, local_asn):
    """Invia dati BGP (con segmenti calcolati) al controller"""
    try:
        # Raccogli dati BGP
        bgp_paths = extract_bgp_paths()
        
        # Calcola segmenti dai path
        segments = calculate_segments_from_paths(bgp_paths, local_asn)
        
        # Raccogli reti
        networks = get_all_networks()
        
        print(f"My data:")
        print(f"   • AS Number: {local_asn}")
        print(f"   • BGP Paths: {len(bgp_paths)}")
        print(f"   • Segments calculated: {len(segments)}")
        print(f"   • Networks: {len(networks)}")
        
        # Connessione gRPC sicura
        address = f"{controller_ip}:{CONTROLLER_PORT}"
        
        try:
            with open(CA_CERT, 'rb') as f:
                ca_cert = f.read()
        except FileNotFoundError:
            print(f"[client] Errore: Certificato CA non trovato in {CA_CERT}")
            return False
        
        credentials = grpc.ssl_channel_credentials(root_certificates=ca_cert)
        
        channel = grpc.secure_channel(
            address,
            credentials,
            options=[
                ('grpc.ssl_target_name_override', 'ctrl'),
                ('grpc.keepalive_time_ms', 30000),
                ('grpc.keepalive_timeout_ms', 10000),
            ]
        )
        
        stub = bgp_segments_pb2_grpc.BgpPathServiceStub(channel)
        
        # Prepara messaggi protobuf
        segments_msg = [
            bgp_segments_pb2.Segment(as_a=seg[0], as_b=seg[1])
            for seg in segments
        ]
        
        paths_msg = [
            bgp_segments_pb2.AsPath(as_sequence=path)
            for path in bgp_paths
        ]
        
        networks_msg = [
            bgp_segments_pb2.Network(
                network=net['network'],
                interface=net['interface'],
                is_ipv6=net['is_ipv6']
            )
            for net in networks
        ]
        
        # Crea richiesta
        request = bgp_segments_pb2.BgpDataRequest(
            local_asn=local_asn,
            segments=segments_msg,
            paths=paths_msg,
            networks=networks_msg
        )
        
        # Invia al controller
        response = stub.ReportBgpData(request, timeout=10)
        
        channel.close()
        
        if response.success:
            print(f"{response.message}")
            print(f"   • Total segments in controller: {response.total_segments_stored}")
            return True
        else:
            print(f"{response.message}")
            return False
            
    except grpc.RpcError as e:
        print(f"Error gRPC: {e.code()}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False

def run_client():
    local_asn = get_asn_from_frr()
    if local_asn is None:
        print("\nNo ASN in the config")
        sys.exit(1)
    
    hostname = socket.gethostname()
    
    print("=" * 60)
    print("PHASE 2 - SEGMENT COLLECTION: AS Node Client")
    print("=" * 60)
    print(f"ASN: {local_asn}")
    print(f"Hostname: {hostname}")
    print("-" * 60)
    
    controller_ip = find_controller()
    
    if not controller_ip:
        print("No controller in the net")
        sys.exit(1)
    
    print(f"Controller found: {controller_ip}")
    print("-" * 60)
    
    attempt = 0
    while True:
        attempt += 1
        print(f"\nAttempt #{attempt}")
        
        if send_bgp_data(controller_ip, local_asn):
            print("\n" + "=" * 60)
            print("Success! Data sent to controller")
            print("=" * 60)
            sys.exit(0)
        
        print(f"\nNew attempt in {RETRY_INTERVAL} seconds...")
        time.sleep(RETRY_INTERVAL)

if __name__ == '__main__':
    try:
        run_client()
    except KeyboardInterrupt:
        print("\nClient on closure...")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}")
        traceback.print_exc()
        sys.exit(1)
