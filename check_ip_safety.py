import ipaddress
import sys

# Copying the function from src/utils/http.py
def is_ip_safe(ip_addr: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address is globally reachable and safe."""
    try:
        if isinstance(ip_addr, str):
            # Handle IPv6 scope ids if present
            ip = ipaddress.ip_address(ip_addr.split("%")[0])
        else:
            ip = ip_addr

        # Ensure the IP is globally reachable (excludes private, loopback, link-local, reserved)
        # We also explicitly block multicast, as is_global can be True for multicast in some versions/contexts
        if not ip.is_global or ip.is_multicast:
            return False
        return True
    except ValueError:
        return False

test_ips = [
    "127.0.0.1",
    "10.0.0.1",
    "192.168.1.1",
    "8.8.8.8",
    "0.0.0.0",
    "::1",
    "2001:4860:4860::8888",
    "::ffff:127.0.0.1",  # IPv4-mapped loopback
    "::ffff:192.168.1.1", # IPv4-mapped private
    "::ffff:8.8.8.8",     # IPv4-mapped public
]

print(f"{'IP Address':<25} | {'Safe?':<6} | {'is_global'}")
print("-" * 45)

for ip_str in test_ips:
    try:
        ip_obj = ipaddress.ip_address(ip_str)
        safe = is_ip_safe(ip_str)
        print(f"{ip_str:<25} | {str(safe):<6} | {ip_obj.is_global}")
    except ValueError as e:
        print(f"{ip_str:<25} | Error  | {e}")
