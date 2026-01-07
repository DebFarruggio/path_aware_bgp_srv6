#!/usr/bin/env python3
"""Server gRPC su Controller per calcolare i path sicuri"""

import grpc
from concurrent import futures
import sqlite3
from collections import defaultdict, deque
import sys
import os
import subprocess

sys.path.append('/shared')
import srv6_path_pb2
import srv6_path_pb2_grpc

DB_TRUSTED = '/shared/trusted_nodes.db'
DB_TOPOLOGY = '/shared/network_topology.db'
CERT_DIR = '/shared/certs'
SERVER_CERT = os.path.join(CERT_DIR, "server.crt")
SERVER_KEY = os.path.join(CERT_DIR, "server.key")

class SRv6PathCalculator:
    def __init__(self):
        self.trusted_nodes = {}
        self.neighbors = {}
        self.segments = []
        self.load_data()
    
    def load_data(self):
        self.load_trusted_nodes()
        self.load_neighbors()
        self.load_segments()
    
    def load_trusted_nodes(self):
        try:
            with sqlite3.connect(DB_TRUSTED) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute('SELECT * FROM nodes')
                self.trusted_nodes = {int(row['router_bgp']): dict(row) for row in cursor}
            print("[1/3] Data loaded")
        except Exception as e:
            print(f"Error loading trusted nodes: {e}")
    
    def load_neighbors(self):
        try:
            with sqlite3.connect(DB_TRUSTED) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute('SELECT * FROM bgp_neighbors')
                for row in cursor:
                    asn = row['local_asn']
                    self.neighbors.setdefault(asn, []).append({
                        'neighbor_asn': row['neighbor_asn'],
                        'neighbor_ip': row['neighbor_ip'],
                        'interface': row['interface']
                    })
            print("[2/3] Data loaded")
        except Exception as e:
            print(f"Error loading neighbors: {e}")
    
    def load_segments(self):
        try:
            with sqlite3.connect(DB_TOPOLOGY) as conn:
                cursor = conn.execute('SELECT as_a, as_b FROM segments')
                self.segments = [(row[0], row[1]) for row in cursor]
            print("[3/3] Data loaded")
        except Exception as e:
            print(f"Error loading segments: {e}")
    
    def build_graph(self, only_trusted=True):
        graph = defaultdict(set)
        for as_a, as_b in self.segments:
            if not only_trusted or (as_a in self.trusted_nodes and as_b in self.trusted_nodes):
                graph[as_a].add(as_b)
                graph[as_b].add(as_a)
        return graph
    
    def find_all_paths(self, graph, start, end, max_paths=5):
        if start not in graph or end not in graph:
            return []
        
        all_paths = []
        queue = deque([[start]])
        
        while queue and len(all_paths) < max_paths:
            path = queue.popleft()
            node = path[-1]
            
            if node == end:
                all_paths.append(path)
                continue
            
            for neighbor in sorted(graph[node]):
                if neighbor not in path:
                    queue.append(path + [neighbor])
        
        return sorted(all_paths, key=len)
    
    def get_locator_address(self, asn):
        if asn in self.trusted_nodes:
            locator = self.trusted_nodes[asn]['locator']
            if locator and locator != 'N/A':
                prefix = locator.split('/')[0]
                return f"{prefix}1" if prefix.endswith('::') else f"{prefix}::1"
        return None
    
    def find_next_hop_to(self, current_asn, target_asn):
        for nbr in self.neighbors.get(current_asn, []):
            if nbr['neighbor_asn'] == target_asn:
                return nbr
        return None
    
    def generate_transit_commands(self, path):
        if len(path) < 2:
            return {}
        
        dest_locator = self.trusted_nodes[path[-1]]['locator']
        transit_commands = {}
        
        for i in range(1, len(path) - 1):
            next_hop = self.find_next_hop_to(path[i], path[i + 1])
            if next_hop:
                commands = []
                
                #rimuove tutte le route esistenti una alla volta per quella destinazione
                commands.append(
                    f"while ip -6 route del {dest_locator} 2>/dev/null; do :; done"
                )
                
                commands.append(
                    f"ip -6 route add {dest_locator} "
                    f"via {next_hop['neighbor_ip']} "
                    f"dev {next_hop['interface']} metric 1"
                )
                
                transit_commands.setdefault(path[i], []).extend(commands)
        
        return transit_commands
    
    def install_command_on_node(self, asn, command):
        if asn not in self.trusted_nodes:
            return False, "Node not trusted"
        
        node_ipv4 = self.trusted_nodes[asn].get('ipv4', '')
        if not node_ipv4 or node_ipv4 == 'N/A':
            return False, "No IPv4 address for node"
        
        try:
            #ssh
            ssh_cmd = f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@{node_ipv4} '{command}'"
            result = subprocess.run(
                ssh_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return True, "OK"
            else:
                error = result.stderr.strip() or result.stdout.strip()
                return False, f"SSH failed: {error}"
                
        except subprocess.TimeoutExpired:
            return False, "Timeout"
        except Exception as e:
            return False, str(e)
    
    def build_path_response(self, path, source_asn, dest_asn):
        sid_list = [self.get_locator_address(asn) for asn in path]
        sid_list = [sid for sid in sid_list if sid]
        
        dest_locator = self.get_locator_address(dest_asn)
        if not dest_locator:
            return None
        
        dest_full = self.trusted_nodes[dest_asn]['locator']
        dest_network = dest_full if '/' in dest_full else f"{dest_locator}/128"
        
        #trova interfaccia output
        if len(path) > 1:
            next_hop = self.find_next_hop_to(source_asn, path[1])
            output_interface = next_hop['interface'] if next_hop else "eth2"
        else:
            output_interface = "eth1"
        
        #commando per il source
        intermediate_sids = sid_list[1:-1] if len(sid_list) > 2 else []
        if intermediate_sids:
            install_command = (f"ip -6 route add {dest_network} encap seg6 mode encap "
                             f"segs {','.join(sid_list)} dev {output_interface} metric 1")
            print(install_command)
        else:
            install_command = "# Direct connection, no SRv6 needed"
        
        nodes_info = [
            srv6_path_pb2.NodeInfo(
                asn=asn,
                hostname=self.trusted_nodes[asn]['hostname'],
                locator=self.trusted_nodes[asn]['locator'],
                is_trusted=True,
                ipv4=self.trusted_nodes[asn].get('ipv4', 'N/A'),
                ipv6=self.trusted_nodes[asn].get('ipv6', 'N/A')
            )
            for asn in path if asn in self.trusted_nodes
        ]
        
        return srv6_path_pb2.PathResponse(
            success=True,as_path=path,path_string=" → ".join(f"AS{asn}" for asn in path),
            hops=len(path) - 1,
            sid_list=sid_list,
            destination_network=dest_network,
            install_command=install_command,
            output_interface=output_interface,
            metric=1,
            nodes=nodes_info
        )

class SRv6PathServicer(srv6_path_pb2_grpc.SRv6PathServiceServicer):
    def __init__(self):
        self.calculator = SRv6PathCalculator()
    
    def RequestPath(self, request, context):
        print(f"\n[RequestPath] AS{request.source_asn} → AS{request.destination_asn}")
        
        self.calculator.load_data()
        graph = self.calculator.build_graph(request.only_trusted or True)
        paths = self.calculator.find_all_paths(graph, request.source_asn, request.destination_asn)
        
        if not paths:
            print("No path found")
            return srv6_path_pb2.MultiplePathsResponse(
                success=False,
                error_message=f"No path found between AS{request.source_asn} and AS{request.destination_asn}",
                total_paths=0
            )
        
        print(f"Found {len(paths)} secure path(s)")
        for i, path in enumerate(paths, 1):
            print(f"  {i}. {' → '.join(f'AS{asn}' for asn in path)} ({len(path)-1} hops)")
        
        path_responses = [
            self.calculator.build_path_response(path, request.source_asn, request.destination_asn)
            for path in paths
        ]
        path_responses = [pr for pr in path_responses if pr]
        
        return srv6_path_pb2.MultiplePathsResponse(
            success=True,
            paths=path_responses,
            total_paths=len(path_responses)
        )
    
    def InstallPath(self, request, context):
        print(f"\n[InstallPath] AS{request.source_asn} → AS{request.destination_asn} (index: {request.path_index})")
        
        self.calculator.load_data()
        graph = self.calculator.build_graph(request.only_trusted or True)
        paths = self.calculator.find_all_paths(graph, request.source_asn, request.destination_asn)
        
        if not paths or request.path_index >= len(paths):
            return srv6_path_pb2.PathResponse(
                success=False,
                error_message=f"Invalid path index {request.path_index}"
            )
        
        path = paths[request.path_index]
        print(f"Installing: {' → '.join(f'AS{asn}' for asn in path)}")
        
        #installa commandi per i nodi di transito
        transit_commands = self.calculator.generate_transit_commands(path)
        if transit_commands:
            print("\nInstalling route - removing conflicts...")
            for asn, commands in transit_commands.items():
                hostname = self.calculator.trusted_nodes[asn]['hostname']
                print(f"  AS{asn} ({hostname}):")
                for cmd in commands:
                    success, msg = self.calculator.install_command_on_node(asn, cmd)
                    status = "✓" if success else "✗"
                    print(cmd)
                    if success: 
                        print(f"    {status} Transit node ready")
                    if not success:
                        print(f"      Error: {msg}")
        
        response = self.calculator.build_path_response(path, request.source_asn, request.destination_asn)
        print("\nPath ready for source installation")
        return response
    
    def ConfirmInstallation(self, request, context):
        status = "✓ installed" if request.installed else "✗ failed"
        print(f"\n[Confirm] AS{request.source_asn} → AS{request.destination_asn}: {status}")
        if not request.installed:
            print(f"  Error: {request.error_message}")
        
        return srv6_path_pb2.InstallResponse(success=True, message="Confirmation received")

def serve():
    with open(SERVER_CERT, "rb") as f:
        server_cert = f.read()
    with open(SERVER_KEY, "rb") as f:
        server_key = f.read()
    
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
    
    srv6_path_pb2_grpc.add_SRv6PathServiceServicer_to_server(SRv6PathServicer(), server)
    server.add_secure_port('[::]:50053', grpc.ssl_server_credentials([(server_key, server_cert)]))
    server.start()
    
    print("=" * 60)
    print("PHASE 3 - CALCULATE THE SECURE PATH")
    print("=" * 60)
    print("Controller on port 50053")
    print("Waiting for nodes...")
    print("=" * 60)
    sys.stdout.flush()
    
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\nController closing...")
        server.stop(0)

if __name__ == "__main__":
    serve()
