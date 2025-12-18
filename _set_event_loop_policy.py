"""
This module MUST be imported FIRST in any process to set the correct event loop policy on Windows.

This is critical for uvicorn's reloader, which creates child processes.
The policy must be set before any event loop is created.

This module is imported at the very beginning of app/main.py to ensure
the policy is set before uvicorn creates an event loop in child processes.
"""
import sys
import platform
import os
import asyncio

if platform.system() == "Windows":
    # Check if we're in a uvicorn child process (via environment variable)
    is_uvicorn_child = os.environ.get("_UVICORN_WINDOWS_EVENT_LOOP_POLICY") == "ProactorEventLoop"
    
    # Force ProactorEventLoop policy for Windows (required for Playwright subprocess support)
    # This MUST happen before any event loop is created
    try:
        # Try to get current policy
        policy = asyncio.get_event_loop_policy()
        if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            print("✓ [_set_event_loop_policy.py] Set Windows event loop policy to ProactorEventLoop")
        else:
            if is_uvicorn_child:
                print("✓ [_set_event_loop_policy.py] Event loop policy already correct (uvicorn child process)")
    except Exception as e:
        # If we can't get the policy, try to set it anyway
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            print(f"✓ [_set_event_loop_policy.py] Set Windows event loop policy (forced, error was: {e})")
        except Exception as e2:
            print(f"⚠ [_set_event_loop_policy.py] Could not set event loop policy: {e2}")

