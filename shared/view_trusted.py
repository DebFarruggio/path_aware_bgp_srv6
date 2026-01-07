import sqlite3
from datetime import datetime

DB_TRUSTED = '/shared/trusted_nodes.db'

def view_trusted():
    conn = sqlite3.connect(DB_TRUSTED)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("=" * 60)
    print("TRUSTED NODES DATABASE")
    print("=" * 60)
    
    print("\nTRUSTED NODES:")
    print("-" * 60)
    cursor.execute('''
        SELECT hostname, ipv4, ipv6, router_bgp, locator, last_update
        FROM nodes 
        ORDER BY hostname
    ''')
    
    nodes = cursor.fetchall()
    
    if nodes:
        print(f"{'Nodes':<5} {'IPv4':<10} {'IPv6':<15} {'ASN':<5} {'Locator':<18} {'Time'}")
        print("-" * 60)
        for row in nodes:
            node = f"{row['hostname']}"
            ipv4 = f"{row['ipv4']}"
            ipv6 = f"{row['ipv6']}"
            asn = f"{row['router_bgp']}"
            locator = f"{row['locator']}"
            time = f"{row['last_update']}"
            print(f"{node:<5} {ipv4:<10} {ipv6:<15} {asn:<5} {locator:<18} {time}")
            
    else:
        print("No nodes found")
    
    print("-" * 60)
    print("BGP NEIGHBORS OF TRUSTED NODES:")
    print("-" * 60)
    cursor.execute('''
        SELECT local_asn, neighbor_ip, neighbor_asn, interface
        FROM bgp_neighbors 
        ORDER BY local_asn
    ''')
    
    neigh = cursor.fetchall()
    
    if neigh:
        print(f"{'ASN':<10} {'Neighbor IP':<20} {'Neighbor ASN':<15} {'Interface':<5}")
        print("-" * 60)
        for row in neigh:
            asn = f"{row['local_asn']}"
            ip = f"{row['neighbor_ip']}"
            neigh_asn = f"{row['neighbor_asn']}"
            interface = f"{row['interface']}"
            print(f"{asn:<10} {ip:<20} {neigh_asn:<15} {interface:<5}")
            
    else:
        print("No nodes found")

if __name__ == '__main__':
    try:
        view_trusted()
    except Exception as e:
        print(f"Error: {e}")
