#!/usr/bin/env python3

import grpc
from concurrent import futures
import sqlite3
import sys
import os
import time
import threading
from datetime import datetime

sys.path.append('/shared')
import bgp_segments_pb2
import bgp_segments_pb2_grpc

DB_TRUSTED = '/shared/trusted_nodes.db'
DB_TOPOLOGY = '/shared/network_topology.db'
CERT_DIR = '/shared/certs'
SERVER_CERT = os.path.join(CERT_DIR, "server.crt")
SERVER_KEY = os.path.join(CERT_DIR, "server.key")

db_lock = threading.Lock()

def init_topology_database():
    conn = sqlite3.connect(DB_TOPOLOGY)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            as_a INTEGER,
            as_b INTEGER,
            trusted INTEGER,
            discovered_by INTEGER,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(as_a, as_b)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS as_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT,
            discovered_by INTEGER,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS as_networks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asn INTEGER,
            network TEXT,
            interface TEXT,
            is_ipv6 INTEGER,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(asn, network)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✓ Topology database initialized")

def load_trusted_nodes():
    try:
        conn = sqlite3.connect(DB_TRUSTED)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('SELECT hostname, ipv4, ipv6, router_bgp, locator FROM nodes')
        
        nodes = {}
        for row in cursor.fetchall():
            asn = int(row['router_bgp'])
            nodes[asn] = {
                'hostname': row['hostname'],
                'locator': row['locator'],
                'ipv4': row['ipv4'],
                'ipv6': row['ipv6']
            }
        
        conn.close()
        print(f"✓ Loaded {len(nodes)} trusted nodes from database")
        return nodes
    except Exception as e:
        print(f"Error loading trusted nodes: {e}")
        return {}

class BgpDataServicer(bgp_segments_pb2_grpc.BgpPathServiceServicer):
    def __init__(self):
        self.trusted_nodes = load_trusted_nodes()
        self.total_segments = 0
        self.received_from = set()
    
    def ReportBgpData(self, request, context):
    
        client_asn = request.local_asn
        client_peer = context.peer()
        
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Data from AS{client_asn}")
        
        is_trusted = client_asn in self.trusted_nodes
        if is_trusted:
            node_info = self.trusted_nodes[client_asn]
            print(f"  ✓ Trusted node: {node_info['hostname']}")
        else:
            print(f" Untrusted node: error")
        
        segments_count = len(request.segments)
        paths_count = len(request.paths)
        networks_count = len(request.networks)
        
        print(f"  • Segments: {segments_count}")
        print(f"  • Paths: {paths_count}")
        print(f"  • Networks: {networks_count}")
        

        new_segments = self.save_data(client_asn, request)
        
        print(f"  • New segments stored: {new_segments}")
        print("-" * 60)
        sys.stdout.flush()
        
        self.received_from.add(client_asn)
        
        return bgp_segments_pb2.BgpDataResponse(
            success=True,
            message=f"Data from AS{client_asn} stored successfully",
            total_segments_stored=self.total_segments
        )
    
    def save_data(self, source_asn, request):
        new_segments_count = 0
        
        try:
            with db_lock:
                conn = sqlite3.connect(DB_TOPOLOGY, timeout=5)
                cursor = conn.cursor()
                
                for seg in request.segments:
                    trust = (
                        seg.as_a in self.trusted_nodes and 
                        seg.as_b in self.trusted_nodes
                    )
                    
                    try:
                        cursor.execute('''
                            INSERT INTO segments (as_a, as_b, trusted, discovered_by)
                            VALUES (?, ?, ?, ?)
                        ''', (seg.as_a, seg.as_b, trust, source_asn))
                        new_segments_count += 1
                    except sqlite3.IntegrityError:
                        #segmento già esistente
                        pass
                
                for path_msg in request.paths:
                    path_str = ' → '.join(str(asn) for asn in path_msg.as_sequence)
                    cursor.execute('''
                        INSERT INTO as_paths (path, discovered_by)
                        VALUES (?, ?)
                    ''', (path_str, source_asn))
                
                #rimuovi vecchie per questo ASN
                cursor.execute('DELETE FROM as_networks WHERE asn = ?', (source_asn,))
                
                for net in request.networks:
                    cursor.execute('''
                        INSERT INTO as_networks (asn, network, interface, is_ipv6)
                        VALUES (?, ?, ?, ?)
                    ''', (source_asn, net.network, net.interface, 1 if net.is_ipv6 else 0))
                
                cursor.execute('SELECT COUNT(*) FROM segments')
                self.total_segments = cursor.fetchone()[0]
                
                conn.commit()
                conn.close()
                
        except Exception as e:
            print(f"  ✗ Database error: {e}")
        
        return new_segments_count
    
    def print_summary(self):
        try:
            with db_lock:
                conn = sqlite3.connect(DB_TOPOLOGY)
                cursor = conn.cursor()
                
                cursor.execute('SELECT as_a, as_b, trusted FROM segments')
                segments = cursor.fetchall()
                
                trusted_segments = sum(
                    1 for seg in segments
                    if seg[2] == 1
                )
                untrusted_segments = len(segments) - trusted_segments
                
                cursor.execute('SELECT COUNT(*) FROM as_paths')
                total_paths = cursor.fetchone()[0]
                
                conn.close()
                
                print("\n" + "=" * 60)
                print("TOPOLOGY SUMMARY")
                print("=" * 60)
                print(f"Total segments: {len(segments)}")
                print(f"  • Trusted: {trusted_segments}")
                print(f"  • Untrusted: {untrusted_segments}")
                print(f"Total paths: {total_paths}")
                print(f"Reporting nodes: {len(self.received_from)}")
                print("=" * 60)
                sys.stdout.flush()
                
        except Exception as e:
            print(f"Error generating summary: {e}")

def serve():
    with open(SERVER_CERT, "rb") as f:
        server_cert = f.read()
    with open(SERVER_KEY, "rb") as f:
        server_key = f.read()
    
    server_creds = grpc.ssl_server_credentials([(server_key, server_cert)])
    
    init_topology_database()
    servicer = BgpDataServicer()
    
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=20),
        options=[
            ('grpc.max_send_message_length', 50 * 1024 * 1024),
            ('grpc.max_receive_message_length', 50 * 1024 * 1024),
            ('grpc.keepalive_time_ms', 30000),
            ('grpc.keepalive_timeout_ms', 10000),
        ]
    )
    
    bgp_segments_pb2_grpc.add_BgpPathServiceServicer_to_server(servicer, server)
    server.add_secure_port('[::]:50052', server_creds)
    server.start()
    
    print("=" * 60)
    print("PHASE 2 - SEGMENT COLLECTION: Controller Server")
    print("=" * 60)
    print("Listening on port 50052")
    print("Waiting for nodes...")
    print("=" * 60)
    sys.stdout.flush()
    
    try:
        while True:
            time.sleep(10)
            
            if len(servicer.received_from) > 0:
                servicer.print_summary()
    
    except KeyboardInterrupt:
        print("\nController stopping...")
        servicer.print_summary()
        server.stop(0)

if __name__ == '__main__':
    serve()
