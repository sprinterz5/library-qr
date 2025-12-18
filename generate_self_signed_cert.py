#!/usr/bin/env python3
"""
Generate self-signed SSL certificate for local HTTPS development.

This script creates a self-signed certificate that can be used for HTTPS
to avoid "not secure" warnings in Chrome on iPhone.

Usage:
    python generate_self_signed_cert.py

This will create:
    - server.key (private key)
    - server.crt (certificate)

Requirements:
    pip install cryptography

Then use with uvicorn:
    python run_windows.py --https
"""

import os
import sys
import ipaddress
from datetime import datetime, timedelta

def generate_cert():
    """Generate self-signed certificate using cryptography library."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        print("❌ cryptography library not found.")
        print("   Install it with: pip install cryptography")
        return False
    
    key_file = "server.key"
    cert_file = "server.crt"
    
    # Check if cert already exists
    if os.path.exists(key_file) and os.path.exists(cert_file):
        print(f"✓ Certificate files already exist: {key_file}, {cert_file}")
        print("   Delete them first if you want to regenerate.")
        return True
    
    print("Generating self-signed certificate...")
    
    try:
        # Generate private key
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        
        # Create certificate
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "KZ"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Almaty"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Almaty"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Library"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ])
        
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            private_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.utcnow()
        ).not_valid_after(
            datetime.utcnow() + timedelta(days=365)
        ).add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.IPAddress(ipaddress.IPv6Address("::1")),
            ]),
            critical=False,
        ).sign(private_key, hashes.SHA256())
        
        # Write private key
        with open(key_file, "wb") as f:
            f.write(private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ))
        
        # Write certificate
        with open(cert_file, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        
        print(f"✓ Certificate generated: {key_file}, {cert_file}")
        print("⚠ This is a self-signed certificate. Browsers will show a warning.")
        print("   On iPhone Chrome: Tap 'Advanced' → 'Proceed to [IP] (unsafe)'")
        print("   Or ignore the warning - it's safe for local network.")
        return True
        
    except Exception as e:
        print(f"❌ Error generating certificate: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    generate_cert()

