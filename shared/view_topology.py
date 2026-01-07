import sqlite3
from datetime import datetime

DB_TOPOLOGY = '/shared/network_topology.db'

def view_topology():
    conn = sqlite3.connect(DB_TOPOLOGY)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("=" * 60)
    print("NETWORK TOPOLOGY DATABASE")
    print("=" * 60)
    
    # Segmenti
    print("\nSEGMENTS:")
    print("-" * 60)
    cursor.execute('''
        SELECT as_a, as_b, trusted, discovered_by, discovered_at 
        FROM segments 
        ORDER BY as_a, as_b
    ''')
    
    segments = cursor.fetchall()
    if segments:
        print(f"{'Segment':<18} {'Trusted':<8} {'Discovered By':<15} {'Time'}")
        print("-" * 60)
        for row in segments:
            seg = f"AS{row['as_a']} - AS{row['as_b']}"
            trusted = f"{row['trusted']}"
            discovered = f"AS{row['discovered_by']}"
            time = row['discovered_at']
            print(f"{seg:<18} {trusted:<8} {discovered:<15} {time}")
    else:
        print("No segments found")
    
    print("\nPATHS:")
    print("-" * 60)
    cursor.execute('''
        SELECT id, path, discovered_by, discovered_at
        FROM as_paths
        ORDER BY path
    ''')
    
    paths = cursor.fetchall()
    if paths:
        print("1")
        print(f"{'Path':<25} {'Discovered by'} {'Discovered at:'}")
        print("-" * 60)
        for row in paths:
            print("ok")
            path = row['path']
            by = row['discovered_by']
            at = row['discovered_at']
            print(f"{path:<25} {by} {at}")
    else:
        print("No networks found")
    
    # Reti
    print("\nNETWORKS:")
    print("-" * 60)
    cursor.execute('''
        SELECT asn, network, interface, is_ipv6 
        FROM as_networks 
        ORDER BY asn, network
    ''')
    
    networks = cursor.fetchall()
    if networks:
        print(f"{'AS':<8} {'Network':<25} {'Interface':<12} {'Type'}")
        print("-" * 60)
        for row in networks:
            asn = f"AS{row['asn']}"
            net = row['network']
            iface = row['interface']
            net_type = 'IPv6' if row['is_ipv6'] else 'IPv4'
            print(f"{asn:<8} {net:<25} {iface:<12} {net_type}")
    else:
        print("No networks found")
    
    conn.close()

if __name__ == '__main__':
    try:
        view_topology()
    except Exception as e:
        print(f"Error: {e}")
