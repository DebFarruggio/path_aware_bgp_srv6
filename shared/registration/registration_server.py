#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import grpc
from concurrent import futures
import time
import sqlite3
from datetime import datetime
import sys
import os
import threading
import json
import locale
import nodeinfo_pb2
import nodeinfo_pb2_grpc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append('/shared')

DB_PATH = '/shared/trusted_nodes.db'
db_lock = threading.Lock()

CERT_DIR = '/shared/certs'
SERVER_CERT = os.path.join(CERT_DIR, "server.crt")
SERVER_KEY = os.path.join(CERT_DIR, "server.key")

def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nodes (
            hostname TEXT PRIMARY KEY,
            ipv4 TEXT NOT NULL,
            ipv6 TEXT NOT NULL,
            router_bgp INTEGER NOT NULL,
            locator TEXT NOT NULL,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    #mi serve per estrapolare i vicini bgp
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bgp_neighbors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_asn INTEGER NOT NULL,
            neighbor_ip TEXT NOT NULL,
            neighbor_asn INTEGER NOT NULL,
            interface TEXT NOT NULL,
            FOREIGN KEY (local_asn) REFERENCES nodes(router_bgp),
            UNIQUE(local_asn, neighbor_ip)
        )
    ''')
    
    conn.commit()
    conn.close()
    print(f"Database ready to go\n")
    sys.stdout.flush()

def save_trusted_node(hostname, ipv4, ipv6, router_bgp, locator, neighbors_json):
    """Salva nodo e neighbor BGP"""
    try:
        with db_lock:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cursor = conn.cursor()
            
            # 1. Salva nodo
            cursor.execute('''
                INSERT INTO nodes (hostname, ipv4, ipv6, router_bgp, locator, last_update)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(hostname) 
                DO UPDATE SET 
                    ipv4 = excluded.ipv4,
                    ipv6 = excluded.ipv6,
                    router_bgp = excluded.router_bgp,
                    locator = excluded.locator,
                    last_update = CURRENT_TIMESTAMP
            ''', (hostname, ipv4, ipv6, router_bgp, locator))
            
            # 2. Rimuovi vecchi neighbor
            cursor.execute('DELETE FROM bgp_neighbors WHERE local_asn = ?', (router_bgp,))
            
            # 3. Salva nuovi neighbor
            try:
                neighbors = json.loads(neighbors_json)
                for nbr in neighbors:
                    cursor.execute('''
                        INSERT INTO bgp_neighbors (local_asn, neighbor_ip, neighbor_asn, interface)
                        VALUES (?, ?, ?, ?)
                    ''', (
                        router_bgp,
                        nbr['neighbor_ip'],
                        nbr['neighbor_asn'],
                        nbr['interface']
                    ))
                
                print(f"   ✓ Saved {len(neighbors)} BGP neighbors")
                
            except json.JSONDecodeError:
                print(f"Invalid neighbors JSON")
            
            conn.commit()
            conn.close()
            return True
            
    except Exception as e:
        print(f"Error: {e}")
        return False

class NodeInfoServicer(nodeinfo_pb2_grpc.NodeInfoServiceServicer):
    def __init__(self):
        self.registered_nodes = set()
    
    def RegisterNode(self, request, context):
        client_peer = context.peer()
        hostname = request.hostname
        
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Registration request from {hostname} ({client_peer})")
        print(f"   • IPv4: {request.ipv4}")
        print(f"   • IPv6: {request.ipv6}")
        print(f"   • AS number: {request.router_bgp}")
        print(f"   • Locator: {request.locator}")
        
        try:
            networks = json.loads(request.networks)
            print(f"   • Networks: {len(networks)}")
            for net in networks[:5]:  # Mostra max 5 reti
                net_type = "IPv6" if net['is_ipv6'] else "IPv4"
                print(f"      - {net['network']} ({net_type}, dev {net['interface']})")
        except:
            print(f"   • Networks not found")
        
        sys.stdout.flush()
        
        success = save_trusted_node(
            hostname,
            request.ipv4,
            request.ipv6,
            request.router_bgp,
            request.locator,
            request.networks
        )
        
        if success:
            self.registered_nodes.add(hostname)
            message = f"{hostname} registrated with success!"
            print(f"{message}")
            print(f"Nodes registrated: {len(self.registered_nodes)}")
        else:
            message = f"Error during the registration of {hostname}"
            print(f"{message}")
        
        print("-" * 60)
        sys.stdout.flush()
        
        return nodeinfo_pb2.NodeInfoResponse(
            success=success,
            message=message
        )

def serve():
    with open(SERVER_CERT, "rb") as f:
        server_cert = f.read()
    with open(SERVER_KEY, "rb") as f:
        server_key = f.read()

    server_creds = grpc.ssl_server_credentials(
        [(server_key, server_cert)]
    )
    
    init_database()
    
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=20),
        options=[
            ('grpc.max_send_message_length', 50 * 1024 * 1024),
            ('grpc.max_receive_message_length', 50 * 1024 * 1024),
            ('grpc.keepalive_time_ms', 30000),
            ('grpc.keepalive_timeout_ms', 10000),
            ('grpc.keepalive_permit_without_calls', True),
        ]
    )
    
    nodeinfo_pb2_grpc.add_NodeInfoServiceServicer_to_server(
        NodeInfoServicer(), server
    )
    
    server.add_secure_port('[::]:50051',server_creds)
    server.start()
    
    print("=" * 60)
    print("Controller gRPC ready on the port 50051")
    print("Waiting for some nodes...")
    print("=" * 60)
    sys.stdout.flush()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nController on closure...")
        server.stop(0)

if __name__ == '__main__':
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except:
        pass
    serve()
