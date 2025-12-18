#!/usr/bin/env python3
"""
Windows launcher for uvicorn with correct event loop policy.

This script MUST set WindowsProactorEventLoopPolicy BEFORE importing uvicorn,
because uvicorn creates an event loop during import, and we need the correct
policy to be set before that happens.

Usage:
    python run_windows.py

Or with custom host/port:
    python run_windows.py --host 127.0.0.1 --port 8000  # localhost only
    python run_windows.py --host 0.0.0.0 --port 8000    # accessible from network (default)
"""
import os
import sys
import platform

# CRITICAL: Set event loop policy FIRST, before ANY other async imports
# Import the policy setter module (it will set the policy)
try:
    import _set_event_loop_policy  # noqa: F401
except ImportError:
    # Fallback if file doesn't exist
    import asyncio
    if platform.system() == "Windows":
        policy = asyncio.get_event_loop_policy()
        if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            print("✓ [run_windows.py] Set Windows event loop policy to ProactorEventLoop (fallback)")

# Now safe to import uvicorn
import uvicorn

if __name__ == "__main__":
    # Parse command line arguments if provided
    host = "0.0.0.0"  # Listen on all interfaces (accessible from network)
    port = 8000
    
    # CRITICAL: On Windows, reload=True causes issues because uvicorn's reloader
    # creates child processes that create event loops BEFORE app.main is imported.
    # This means the event loop policy can't be set in time.
    # Solution: Disable reload by default on Windows, enable with --reload flag
    if platform.system() == "Windows":
        reload = False  # Disable by default on Windows
        if "--reload" in sys.argv:
            reload = True
            print("⚠ [run_windows.py] WARNING: --reload on Windows may cause event loop issues.")
            print("   If you see errors, remove --reload flag.")
    else:
        reload = True  # Enable by default on Linux/Mac
        if "--no-reload" in sys.argv:
            reload = False
    
    if "--host" in sys.argv:
        idx = sys.argv.index("--host")
        if idx + 1 < len(sys.argv):
            host = sys.argv[idx + 1]
    
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
    
    # Check for SSL certificate files - use HTTPS if available, HTTP otherwise
    ssl_keyfile = None
    ssl_certfile = None
    use_https = False
    
    # Priority: --http flag > --https flag > auto-detect (cert files)
    if "--http" in sys.argv:
        # User explicitly requested HTTP (force HTTP even if cert exists)
        print("🌐 Using HTTP (--http flag - forced)")
        use_https = False
    elif "--https" in sys.argv:
        # User explicitly requested HTTPS
        if os.path.exists("server.key") and os.path.exists("server.crt"):
            ssl_keyfile = "server.key"
            ssl_certfile = "server.crt"
            use_https = True
            print("🔒 Using HTTPS (--https flag)")
        else:
            print("⚠ --https flag used but certificate files not found.")
            print("   Run: python generate_self_signed_cert.py")
            print("   Continuing with HTTP...")
            use_https = False
    elif os.path.exists("server.key") and os.path.exists("server.crt"):
        # Auto-detect: certificate files exist, use HTTPS
        ssl_keyfile = "server.key"
        ssl_certfile = "server.crt"
        use_https = True
        print("🔒 Certificate files found - using HTTPS automatically")
    else:
        # No flag, no cert - use HTTP
        print("🌐 Using HTTP (no certificate found)")
        print("   To use HTTPS, run: python generate_self_signed_cert.py")
        use_https = False
    
    protocol = "https" if use_https else "http"
    print(f"🚀 Starting uvicorn on {protocol}://{host}:{port} (reload={reload})")
    print("📝 Press CTRL+C to stop")
    
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        reload_includes=["_set_event_loop_policy.py", "app/main.py", "app/rpa_elibra.py"] if reload else None,
        loop="asyncio",  # Ensure uvicorn uses the asyncio loop (with our policy)
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile
    )

