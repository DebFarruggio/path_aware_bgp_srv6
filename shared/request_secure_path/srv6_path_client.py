#!/usr/bin/env python3

import grpc
import subprocess
import sys
import os
import socket
import ipaddress
import argparse

sys.path.append('/shared')
import srv6_path_pb2
import srv6_path_pb2_grpc

CONTROLLER_PORT = 50053
CA_CERT = '/shared/certs/ca.crt'

class SRv6PathClient:
    def __init__(self):
        self.my_asn = self.get_my_asn()
        self.hostname = socket.gethostname()
        self.controller_ip = None
        
        if not self.my_asn:
            print("ASN not found")
            sys.exit(1)
        
        print(f"AS Number: {self.my_asn}")
        print(f"Hostname: {self.hostname}")
    
    def get_my_asn(self):
        try:
            result = subprocess.run(
                ['vtysh', '-c', 'show running-config'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split('\n'):
                if line.strip().startswith('router bgp'):
                    parts = line.split()
                    if len(parts) >= 3 and parts[2].isdigit():
                        return int(parts[2])
        except Exception as e:
            print(f"Error getting ASN: {e}")
        return None
    
    def find_controller(self):
        if self.controller_ip:
            return self.controller_ip
        
        try:
            result = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True)
            interfaces = [line.split(':')[1].strip() for line in result.stdout.split('\n') 
                         if 'eth' in line and 'state UP' in line]
            
            for iface in interfaces:
                addr = self.get_interface_address(iface)
                if not addr:
                    continue
                
                network = ipaddress.ip_interface(addr).network
                my_ip = addr.split('/')[0]
                
                for host in network.hosts():
                    ip = str(host)
                    if ip == my_ip:
                        continue
                    
                    try:
                        with socket.create_connection((ip, CONTROLLER_PORT), timeout=0.5):
                            self.controller_ip = ip
                            return ip
                    except:
                        continue
        except Exception as e:
            print(f"Error finding controller: {e}")
        
        return None
    
    def get_interface_address(self, interface):
        try:
            cmd = ["ip", "-4", "-o", "addr", "show", "dev", interface]
            out = subprocess.check_output(cmd, text=True).strip()
            return out.split()[3]
        except:
            return None
    
    def get_grpc_channel(self):
        controller_ip = self.find_controller()
        if not controller_ip:
            raise Exception("Controller not found")
        
        try:
            with open(CA_CERT, 'rb') as f:
                ca_cert = f.read()
        except FileNotFoundError:
            raise Exception(f"CA certificate not found at {CA_CERT}")
        
        credentials = grpc.ssl_channel_credentials(root_certificates=ca_cert)
        return grpc.secure_channel(
            f"{controller_ip}:{CONTROLLER_PORT}",
            credentials,
            options=[
                ('grpc.ssl_target_name_override', 'ctrl'),
                ('grpc.keepalive_time_ms', 30000),
                ('grpc.keepalive_timeout_ms', 10000),
            ]
        )
    
    def request_paths(self, dest_asn):
        try:
            channel = self.get_grpc_channel()
            stub = srv6_path_pb2_grpc.SRv6PathServiceStub(channel)
            
            print(f"\nRequesting paths: AS{self.my_asn} → AS{dest_asn}")
            request = srv6_path_pb2.PathRequest(
                source_asn=self.my_asn,
                destination_asn=dest_asn,only_trusted=True
            )
            
            response = stub.RequestPath(request, timeout=10)
            channel.close()
            return response
        except grpc.RpcError as e:
            print(f"gRPC Error: {e.code()}: {e.details()}")
        except Exception as e:
            print(f"Error: {e}")
        return None
    
    def install_path(self, dest_asn, path_index):
        try:
            channel = self.get_grpc_channel()
            stub = srv6_path_pb2_grpc.SRv6PathServiceStub(channel)
            
            print(f"\nRequesting installation of path #{path_index + 1}...")
            request = srv6_path_pb2.InstallPathRequest(
                source_asn=self.my_asn,
                destination_asn=dest_asn,
                path_index=path_index,
                only_trusted=True
            )
            
            response = stub.InstallPath(request, timeout=15)
            channel.close()
            
            if response and response.success:
                return self.install_locally(response)
            else:
                print(f"Installation failed: {response.error_message if response else 'No response'}")
                return False
        except Exception as e:
            print(f"Error: {e}")
            return False
    
    def install_locally(self, response):
        command = response.install_command
        dest_net = response.destination_network
        
        if command.strip().startswith('#'):
            print("\nDirect connection - no installation needed")
            return True
        
        try:
            print("\nInstalling secure path locally...")

            subprocess.run(f"ip -6 route del {dest_net}", shell=True, capture_output=True, timeout=5)

            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=5)
            
            if result.returncode == 0:
                print("SUCCESS! Secure path installed")

                verify = subprocess.run(
                    f"ip -6 route show {dest_net}",
                    shell=True, capture_output=True, text=True
                )
                
                if "encap seg6" in verify.stdout:
                    print("[CHECK] Route verified")
                else:
                    print("[CHECK] Warning: Route may not be properly installed")
                
                self.send_confirmation(response.as_path[-1], True, "")
                return True
            else:
                error = result.stderr.strip()
                print(f"Installation failed: {error}")
                self._send_confirmation(response.as_path[-1], False, error)
                return False
        except Exception as e:
            print(f"Error: {e}")
            return False
    
    def send_confirmation(self, dest_asn, success, error_msg):
        try:
            channel = self.get_grpc_channel()
            stub = srv6_path_pb2_grpc.SRv6PathServiceStub(channel)
            
            confirm = srv6_path_pb2.InstallConfirm(
                source_asn=self.my_asn,
                destination_asn=dest_asn,
                installed=success,
                error_message=error_msg
            )
            
            stub.ConfirmInstallation(confirm, timeout=5)
            channel.close()
        except:
            pass
    
    def display_path(self, path_response, number):
        print(f"\nPath #{number}:")
        print(f"  Route: {path_response.path_string}")
        print(f"  Hops: {path_response.hops}")
        print(f"  Destination: {path_response.destination_network}")
        print(f"  Trusted nodes:")
        for node in path_response.nodes:
            print(f"    AS{node.asn} - {node.hostname}")
    
    def handle_single_path(self, response, dest_asn):
        print(f"\n✓ Found 1 secure path:")
        self.display_path(response.paths[0], 1)
        
        if input("\nInstall this path? (y/n): ").strip().lower() == 'y':
            self.install_path(dest_asn, 0)
        else:
            print("Installation cancelled")
    
    def handle_multiple_paths(self, response, dest_asn):
        while True:
            print(f"\n{'='*60}")
            print(f"Found {response.total_paths} secure paths:")
            print(f"{'='*60}")
            
            for i, path_resp in enumerate(response.paths, 1):
                print(f"{i}. {path_resp.path_string} ({path_resp.hops} hops)")
            print(f"{response.total_paths + 1}. Cancel and return")
            
            try:
                choice = int(input(f"\nSelect path (1-{response.total_paths + 1}): ").strip())
            except ValueError:
                print("Invalid input")
                continue
            
            if choice == response.total_paths + 1:
                print("Selection cancelled")
                break
            
            if 1 <= choice <= response.total_paths:
                self.display_path(response.paths[choice - 1], choice)
                
                if input("\nInstall this path? (y/n): ").strip().lower() == 'y':
                    self.install_path(dest_asn, choice - 1)
                    break
                else:
                    print("\nReturning to path selection...")
            else:
                print(f"Invalid choice. Enter 1-{response.total_paths + 1}")
    
    def interactive_mode(self):
        print("=" * 60)
        print("PHASE 3 - CALCULATE THE SECURE PATH")
        print("=" * 60)
        
        while True:
            try:
                print("\nCommands:")
                print("  <ASN> - Request secure path")
                print("  quit  - Exit")
                
                cmd = input("\n> ").strip()
                if not cmd:
                    continue
                if cmd.lower() in ['quit', 'exit', 'q']:
                    break
                
                try:
                    dest_asn = int(cmd)
                except ValueError:
                    print("Invalid ASN")
                    continue
                
                if dest_asn == self.my_asn:
                    print("Cannot route to yourself")
                    continue
                
                response = self.request_paths(dest_asn)
                if not response or not response.success:
                    print(f"Error: {response.error_message if response else 'No response'}")
                    continue
                
                if response.total_paths == 1:
                    self.handle_single_path(response, dest_asn)
                else:
                    self.handle_multiple_paths(response, dest_asn)
            
            except KeyboardInterrupt:
                print("\nExiting...")
                break
            except Exception as e:
                print(f"Error: {e}")

def main():
    parser = argparse.ArgumentParser(description='SRv6 Secure Path Client')
    parser.add_argument('--dest', type=int, help='Destination ASN (non-interactive)')
    args = parser.parse_args()
    
    try:
        client = SRv6PathClient()
        
        if args.dest:
            response = client.request_paths(args.dest)
            if response and response.success:
                if response.total_paths == 1:
                    client.display_path(response.paths[0], 1)
                    client.install_path(args.dest, 0)
                else:
                    print(f"Found {response.total_paths} paths. Use interactive mode to select.")
                    for i, path in enumerate(response.paths, 1):
                        print(f"{i}. {path.path_string}")
            else:
                sys.exit(1)
        else:
            client.interactive_mode()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
