_DEV_SIGNATURE = "AB2025"
import asyncio
import logging
import re
from typing import Optional, Dict, Any
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright, TimeoutError as PlaywrightTimeoutError

from app.settings import settings

logger = logging.getLogger(__name__)

BASE_URL = settings.elibra_base_url
ISSUANCE_URL = f"{BASE_URL}/workspace/issuance"
LOGIN_URL = f"{BASE_URL}/auth/login"
USER_DATA_DIR = Path("pw_profile").absolute()


class ElibraRPA:
    """
    RPA client for eLibra using Playwright.
    Uses persistent browser context to maintain login session.
    Thread-safe: uses asyncio.Lock() to serialize all RPA operations.
    """
    
    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._lock = asyncio.Lock()
        self._initialized = False
        self._logging_in = False
        
    async def initialize(self, headless: bool = False):
        """
        Initialize Playwright and launch persistent browser context.
        Creates pw_profile directory to persist login session.
        Thread-safe: uses lock to prevent concurrent initialization.
        """
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            import platform
            if platform.system() == "Windows":
                try:
                    loop = asyncio.get_running_loop()
                    loop_type = type(loop).__name__
                    logger.info(f"Current event loop type: {loop_type}")
                    
                    if "Selector" in loop_type:
                        policy = asyncio.get_event_loop_policy()
                        error_msg = (
                            f"ERROR: Event loop is {loop_type}, but Playwright requires ProactorEventLoop on Windows.\n"
                            f"Current loop policy: {type(policy).__name__}\n"
                            f"\n"
                            f"This usually happens when:\n"
                            f"  1. Running uvicorn directly (not via run_windows.py)\n"
                            f"  2. Using --reload flag on Windows (uvicorn reloader creates child processes)\n"
                            f"\n"
                            f"Solution:\n"
                            f"  - Use 'python run_windows.py' (reload is disabled by default on Windows)\n"
                            f"  - If you need reload, restart manually after code changes\n"
                            f"  - Or use 'python run_windows.py --reload' and accept potential issues\n"
                        )
                        logger.error(error_msg)
                        raise RuntimeError(error_msg)
                except RuntimeError:
                    raise  # Re-raise our custom error
                except Exception as e:
                    logger.warning(f"Could not verify event loop type: {e}")
                
            try:
                logger.info("Initializing Playwright RPA...")
                self.playwright = await async_playwright().start()
                USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=str(USER_DATA_DIR),
                    headless=headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                
                # Get or create a page
                if self.context.pages:
                    self.page = self.context.pages[0]
                else:
                    self.page = await self.context.new_page()
                    
                self._initialized = True
                logger.info("Playwright RPA initialized")
            except Exception as e:
                logger.error(f"Failed to initialize RPA: {e}", exc_info=True)
                # Clean up on failure
                if self.context:
                    try:
                        await self.context.close()
                    except:
                        pass
                    self.context = None
                if self.playwright:
                    try:
                        await self.playwright.stop()
                    except:
                        pass
                    self.playwright = None
                self.page = None
                raise
        
    async def close(self):
        """Close browser context and playwright."""
        async with self._lock:
            if self.context:
                await self.context.close()
                self.context = None
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
            self.page = None
            self._initialized = False
            logger.info("Playwright RPA closed")
    
    async def _ensure_initialized(self):
        """
        Ensure RPA is initialized, try to initialize if not.
        Note: This should be called BEFORE acquiring self._lock to avoid deadlock.
        """
        if not self._initialized:
            logger.warning("RPA not initialized, attempting to initialize now...")
            await self.initialize(headless=False)
            if not self._initialized:
                raise RuntimeError("Failed to initialize RPA. Please check logs for errors.")
    
    async def _ensure_page(self):
        """Ensure we have a valid page."""
        await self._ensure_initialized()
        if not self.page or self.page.is_closed():
            if self.context:
                self.page = await self.context.new_page()
            else:
                raise RuntimeError("Browser context lost")
    
    async def _auto_login_if_needed(self) -> None:
        """
        If we are on /auth/login and credentials are configured, perform auto-login.
        This is best-effort: on failure we raise a clear error so caller can surface it to the user.
        """
        await self._ensure_page()
        url = self.page.url or ""

        # Quick check: only attempt if we're clearly on login page
        if "/auth/login" not in url:
            return

        if self._logging_in:
            logger.info("Auto-login already in progress, waiting for it to finish...")
            for i in range(40):  # wait up to ~40 seconds
                await asyncio.sleep(1)
                current = self.page.url or ""
                if "/auth/login" not in current:
                    logger.info("Existing auto-login finished, continuing")
                    return
            raise RuntimeError(
                "Auto-login is still in progress but did not complete in time. "
                "Please try the operation again or use /rpa/manual-login."
            )

        if not settings.elibra_user_email or not settings.elibra_password:
            raise RuntimeError(
                "eLibra session expired and auto-login is not configured. "
                "Set ELIBRA_USER_EMAIL / ELIBRA_PASSWORD (or user_email/password) in .env "
                "or use /rpa/manual-login to log in manually."
            )

        self._logging_in = True
        try:
            logger.info("Attempting auto-login on eLibra /auth/login...")

            if LOGIN_URL not in url:
                logger.info(f"Navigating to login page: {LOGIN_URL} (current: {url})")
                await self.page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)

            email_value = settings.elibra_user_email
            password_value = settings.elibra_password
            email_field = None
            email_selectors = [
                ("get_by_placeholder", "E-mail", {}),
                ("get_by_placeholder", "Email", {}),
                ("get_by_placeholder", "E-mail address", {}),
                ("get_by_label", "E-mail", {}),
                ("get_by_label", "Email", {}),
                ("locator", "input[type='email']", {}),
                ("locator", "input[name='email']", {}),
                ("locator", "input[name*='username']", {}),
            ]
            for method, selector, kwargs in email_selectors:
                try:
                    if method == "get_by_placeholder":
                        candidate = self.page.get_by_placeholder(selector).first
                    elif method == "get_by_label":
                        candidate = self.page.get_by_label(selector).first
                    else:
                        candidate = self.page.locator(selector).first
                    if await candidate.is_visible(timeout=1500):
                        email_field = candidate
                        logger.info(f"Found email field using {method}: {selector}")
                        break
                except Exception:
                    continue

            if not email_field:
                raise RuntimeError("Could not find email/username field on eLibra login page.")

            await email_field.fill(email_value)

            password_field = None
            password_selectors = [
                ("get_by_placeholder", "Password", {}),
                ("get_by_label", "Password", {}),
                ("locator", "input[type='password']", {}),
                ("locator", "input[name='password']", {}),
            ]
            for method, selector, kwargs in password_selectors:
                try:
                    if method == "get_by_placeholder":
                        candidate = self.page.get_by_placeholder(selector).first
                    elif method == "get_by_label":
                        candidate = self.page.get_by_label(selector).first
                    else:
                        candidate = self.page.locator(selector).first
                    if await candidate.is_visible(timeout=1500):
                        password_field = candidate
                        logger.info(f"Found password field using {method}: {selector}")
                        break
                except Exception:
                    continue

            if not password_field:
                raise RuntimeError("Could not find password field on eLibra login page.")

            await password_field.fill(password_value)

            # CLICK LOGIN BUTTON
            login_clicked = False
            button_selectors = [
                ("get_by_role", "button", {"name": re.compile("Sign in|Log in|Login|Войти", re.I)}),
                ("get_by_text", "Sign in", {}),
                ("get_by_text", "Log in", {}),
                ("get_by_text", "Login", {}),
                ("get_by_text", "Войти", {}),
                ("locator", "button[type='submit']", {}),
            ]
            for method, selector, kwargs in button_selectors:
                try:
                    if method == "get_by_role":
                        btn = self.page.get_by_role(selector, **kwargs).first
                    elif method == "get_by_text":
                        btn = self.page.get_by_text(selector).first
                    else:
                        btn = self.page.locator(selector).first
                    if await btn.is_visible(timeout=1500):
                        await btn.click()
                        login_clicked = True
                        logger.info(f"Clicked login button using {method}: {selector}")
                        break
                except Exception:
                    continue

            if not login_clicked:
                # Fallback: press Enter in password field
                try:
                    await password_field.press("Enter")
                    login_clicked = True
                    logger.info("Pressed Enter in password field as login fallback")
                except Exception:
                    pass

            if not login_clicked:
                raise RuntimeError("Could not find or click login button on eLibra login page.")

            try:
                await self.page.wait_for_url(
                    lambda u: "/auth/login" not in u,
                    timeout=30000,
                )
                logger.info(f"Auto-login successful, current URL: {self.page.url}")

                # После успешного логина eLibra часто редиректит на корень (`{BASE_URL}/`).
                # Нам же нужно рабочее место выдачи — сразу переходим на ISSUANCE_URL.
                try:
                    logger.info(f"Navigating to issuance workspace after login: {ISSUANCE_URL}")
                    await self.page.goto(ISSUANCE_URL, wait_until="networkidle", timeout=30000)
                except Exception as nav_e:
                    logger.warning(f"Navigation to issuance after auto-login failed: {nav_e}")
            except PlaywrightTimeoutError:
                raise RuntimeError("Auto-login timed out: still on /auth/login after submit.")

        finally:
            self._logging_in = False

    async def _ensure_issuance_page(self):
        """
        Navigate to issuance page and ensure we are logged in.
        If redirected to /auth/login, perform auto-login (if configured) once.
        """
        await self._ensure_page()

        # Try at most two cycles: load issuance -> maybe login -> load issuance again
        for attempt in range(2):
            current_url = self.page.url or ""

            # If on login page, auto-login (if credentials are present)
            if "/auth/login" in current_url:
                logger.warning("Detected eLibra login page while ensuring issuance page. Trying auto-login...")
                await self._auto_login_if_needed()

            current_url = self.page.url or ""
            if ISSUANCE_URL not in current_url:
                logger.info(f"Navigating to issuance page (current: {current_url})")
                await self.page.goto(ISSUANCE_URL, wait_until="networkidle", timeout=30000)
            else:
                logger.debug("Already on issuance page")

            # Verify that we actually see the issuance UI (Search user input)
            try:
                search_input = self.page.get_by_placeholder("Search user").first
                await search_input.wait_for(state="visible", timeout=5000)
                # If we got here, we're on the issuance workspace and logged in
                return
            except Exception:
                logger.warning("Could not find 'Search user' input on issuance page, maybe session expired?")
                # Next iteration will try auto-login if needed

        # If we exit the loop, we failed to get issuance UI
        raise RuntimeError(
            "Не удалось открыть рабочее место выдачи в eLibra. "
            "Проверь, что логин/пароль корректны, или попробуй /rpa/manual-login."
        )
    
    async def health(self) -> Dict[str, Any]:
        """
        Check RPA health status.
        Returns dict with page_open, url, logged_in (best-effort).
        """
        try:
            # Try to initialize if not already initialized
            if not self._initialized:
                try:
                    await self.initialize(headless=False)
                except Exception as e:
                    return {
                        "ok": False,
                        "page_open": False,
                        "url": None,
                        "logged_in": False,
                        "message": f"RPA initialization failed: {str(e)}"
                    }
            
            async with self._lock:
                if not self._initialized or not self.page or self.page.is_closed():
                    return {
                        "ok": False,
                        "page_open": False,
                        "url": None,
                        "logged_in": False,
                        "message": "RPA not initialized or page closed"
                    }
                
                url = self.page.url
                # Best-effort check: if we're on issuance page and can see search input, likely logged in
                logged_in = False
                try:
                    # Quick check: see if we can find the search input (would fail if not logged in)
                    await self._ensure_issuance_page()
                    search_input = self.page.get_by_placeholder("Search user").first
                    await search_input.wait_for(state="attached", timeout=3000)
                    logged_in = True
                except:
                    logged_in = False
                
                return {
                    "ok": True,
                    "page_open": True,
                    "url": url,
                    "logged_in": logged_in,
                    "message": "RPA is healthy"
                }
        except Exception as e:
            return {
                "ok": False,
                "page_open": False,
                "url": None,
                "logged_in": False,
                "message": f"Health check error: {str(e)}"
            }
    
    async def manual_login(self) -> Dict[str, Any]:
        """
        Open browser to issuance page for manual login.
        Returns message instructing user to log in.
        """
        try:
            # Initialize before acquiring lock to avoid deadlock
            await self._ensure_initialized()
            async with self._lock:
                await self._ensure_page()
                await self.page.goto(ISSUANCE_URL, wait_until="networkidle", timeout=30000)
                
                return {
                    "ok": True,
                    "message": "Browser opened. Please log in in the opened browser tab, then come back.",
                    "url": self.page.url
                }
        except Exception as e:
            logger.error(f"Manual login error: {e}", exc_info=True)
            return {
                "ok": False,
                "message": f"Failed to open browser: {str(e)}"
            }
    
    async def search_readers(self, query: str, n: int = 4) -> Dict[str, Any]:
        """
        Search for readers using the UI search input.
        Returns list of reader objects (compatible with API format).
        
        Strategy:
        1. Intercept the search API response to get initial results
        2. For each result, try to find reader_id by:
           - Checking data attributes on result elements
           - Intercepting requests when clicking on results
           - Parsing the search response which may contain reader_id
        """
        try:
            # Ensure initialized before acquiring lock to avoid deadlock
            await self._ensure_initialized()
            async with self._lock:
                await self._ensure_issuance_page()
                
                # Find search input (left top "Search user")
                search_input = self.page.get_by_placeholder("Search user").first
                await search_input.wait_for(state="visible", timeout=10000)
                
                # Intercept search API response and requests BEFORE triggering search
                results = []
                reader_id_by_index = {}  # Map index -> reader_id
                
                async def handle_request(request):
                    """Intercept requests to extract reader_id from payload."""
                    url = request.url
                    # Log all API requests for debugging
                    if "/api/" in url:
                        logger.debug(f"API request: {request.method} {url}")
                    
                    # Intercept reader profile requests (happens when clicking on result)
                    if "/api/interface-service/issuance/action/reader/profile/" in url:
                        try:
                            # Check URL params
                            if "readerId=" in url:
                                reader_id = int(url.split("readerId=")[1].split("&")[0])
                                # Try to match with result index (we'll need to track which result was clicked)
                                # For now, store it - we'll match later
                                reader_id_by_index["last_clicked"] = reader_id
                            
                            # Check request payload
                            try:
                                post_data = request.post_data
                                if post_data:
                                    import json
                                    try:
                                        payload = json.loads(post_data) if isinstance(post_data, str) else post_data
                                        if isinstance(payload, dict) and "readerId" in payload:
                                            reader_id = int(payload["readerId"])
                                            reader_id_by_index["last_clicked"] = reader_id
                                    except:
                                        # Try URL-encoded format
                                        try:
                                            if "readerId=" in post_data:
                                                reader_id = int(post_data.split("readerId=")[1].split("&")[0])
                                                reader_id_by_index["last_clicked"] = reader_id
                                        except:
                                            pass
                            except:
                                pass
                        except:
                            pass
                
                async def handle_response(response):
                    """Intercept responses to get search results and reader_id."""
                    url = response.url
                    # Log all API responses for debugging
                    if "/api/" in url:
                        logger.debug(f"API response: {response.status} {url}")
                    
                    # Intercept search results - check multiple possible URL patterns
                    # Old API: POST /api/interface-service/issuance/action/reader/profile/list/4?searchValue=...
                    search_patterns = [
                        "/api/interface-service/issuance/action/reader/profile/list",
                        "reader/profile/list",
                        "/search",
                        "reader/search"
                    ]
                    
                    if any(pattern in url for pattern in search_patterns):
                        try:
                            # Check if response is successful
                            if response.status >= 200 and response.status < 300:
                                data = await response.json()
                                nonlocal results
                                logger.info(f"Intercepted search response from {url}: type={type(data).__name__}")
                                
                                # Extract list from response
                                if isinstance(data, list):
                                    results = data
                                    logger.info(f"Got {len(results)} results as direct list")
                                elif isinstance(data, dict):
                                    # Try various possible keys
                                    for key in ["result", "results", "data", "items", "list"]:
                                        if key in data and isinstance(data[key], list):
                                            results = data[key]
                                            logger.info(f"Got {len(results)} results from data.{key}")
                                            break
                                
                                # Log first result structure for debugging
                                if results and len(results) > 0:
                                    logger.debug(f"First result keys: {list(results[0].keys()) if isinstance(results[0], dict) else 'not dict'}")
                                
                                # Check if results already have parentId/readerId/id
                                for result in results:
                                    if isinstance(result, dict):
                                        # Try different field names for reader_id
                                        if "parentId" not in result or not result.get("parentId"):
                                            if "readerId" in result:
                                                result["parentId"] = result["readerId"]
                                            elif "id" in result:
                                                result["parentId"] = result["id"]
                                            elif "reader_id" in result:
                                                result["parentId"] = result["reader_id"]
                        except Exception as e:
                            logger.debug(f"Error parsing search response from {url}: {e}")
                            pass
                    
                    # Also check response for reader_id
                    if "/api/interface-service/issuance/action/reader/profile/" in url:
                        try:
                            resp_data = await response.json()
                            if isinstance(resp_data, dict):
                                if "readerId" in resp_data:
                                    reader_id_by_index["last_clicked"] = int(resp_data["readerId"])
                                elif "id" in resp_data:
                                    reader_id_by_index["last_clicked"] = int(resp_data["id"])
                        except:
                            pass
                
                # Set up handlers BEFORE triggering search
                self.page.on("request", handle_request)
                self.page.on("response", handle_response)
                
                # Clear and type search query
                await search_input.clear()
                await search_input.fill(query)
                await asyncio.sleep(0.2)  # Small delay before pressing Enter
                
                # Trigger search - try multiple methods
                logger.info(f"Triggering search for query: {query}")
                
                # Method 1: Press Enter (most common)
                await search_input.press("Enter")
                
                # Also try typing a space and backspace to trigger autocomplete if Enter doesn't work
                # Some UIs trigger search on input change
                await asyncio.sleep(0.1)
                
                # Wait for search API call with timeout
                max_wait = 5  # Wait up to 5 seconds for results
                waited = 0
                while waited < max_wait and len(results) == 0:
                    await asyncio.sleep(0.3)
                    waited += 0.3
                
                if len(results) == 0:
                    logger.warning(f"No results intercepted after {waited}s, waiting a bit more...")
                    await asyncio.sleep(1)
                
                # Additional wait for UI to render
                await asyncio.sleep(0.3)
                
                # Try to extract reader_id from DOM elements
                # Results might have data attributes or IDs containing reader_id
                try:
                    # Look for result elements - they might have data-reader-id, data-id, or similar
                    result_elements = await self.page.locator("[data-reader-id], [data-id], .reader-item, .search-result-item").all()
                    
                    for idx, elem in enumerate(result_elements[:len(results)]):
                        if idx < len(results):
                            # Try to get reader_id from data attributes
                            reader_id_attr = await elem.get_attribute("data-reader-id")
                            if not reader_id_attr:
                                reader_id_attr = await elem.get_attribute("data-id")
                            if not reader_id_attr:
                                # Try to get from onclick or other attributes
                                onclick = await elem.get_attribute("onclick")
                                if onclick and "readerId" in onclick:
                                    import re
                                    match = re.search(r'readerId[=:](\d+)', onclick)
                                    if match:
                                        reader_id_attr = match.group(1)
                            
                            if reader_id_attr:
                                try:
                                    reader_id = int(reader_id_attr)
                                    # Update result with reader_id if not present
                                    if "parentId" not in results[idx] or not results[idx].get("parentId"):
                                        results[idx]["parentId"] = reader_id
                                except:
                                    pass
                except Exception as e:
                    logger.debug(f"Could not extract reader_id from DOM: {e}")
                
                # If results still don't have parentId, try clicking on results to get reader_id
                # This will trigger profile requests that contain reader_id in payload
                missing_ids = [idx for idx, r in enumerate(results) if not r.get("parentId")]
                
                if missing_ids:
                    try:
                        # Find clickable result elements
                        result_selectors = [
                            ".result", "[role='option']", ".reader-item", 
                            ".search-result", "[data-reader-id]", "[data-id]",
                            "div[class*='result']", "div[class*='item']", "li[class*='result']"
                        ]
                        
                        result_elements = []
                        for selector in result_selectors:
                            try:
                                elems = await self.page.locator(selector).all()
                                if elems and len(elems) >= len(results):
                                    result_elements = elems
                                    break
                            except:
                                continue
                        
                        # Click on results that are missing parentId
                        for idx in missing_ids[:5]:  # Limit to first 5 to avoid being too slow
                            if idx >= len(result_elements):
                                continue
                            
                            try:
                                # Clear the last_clicked before clicking
                                reader_id_by_index.pop("last_clicked", None)
                                
                                elem = result_elements[idx]
                                # Scroll element into view and click
                                await elem.scroll_into_view_if_needed()
                                await asyncio.sleep(0.1)
                                await elem.click()
                                await asyncio.sleep(0.4)  # Wait for request
                                
                                # Check if we got reader_id
                                if "last_clicked" in reader_id_by_index:
                                    reader_id = reader_id_by_index["last_clicked"]
                                    results[idx]["parentId"] = reader_id
                                    logger.info(f"Got reader_id {reader_id} for result {idx} by clicking")
                                
                                # Close any popup/modal with Escape
                                try:
                                    await self.page.keyboard.press("Escape")
                                    await asyncio.sleep(0.1)
                                except:
                                    pass
                            except Exception as e:
                                logger.debug(f"Could not click result {idx} to get reader_id: {e}")
                                continue
                    except Exception as e:
                        logger.debug(f"Error clicking results to get reader_id: {e}")
                
                # Remove handlers
                self.page.remove_listener("request", handle_request)
                self.page.remove_listener("response", handle_response)
                
                # If we still don't have parentId in results, try to get from the search response structure
                # Sometimes the API response already contains parentId
                for result in results:
                    if "parentId" not in result or not result.get("parentId"):
                        # Try to find in nested structures
                        if "id" in result:
                            result["parentId"] = result["id"]
                        elif "readerId" in result:
                            result["parentId"] = result["readerId"]
                
                logger.info(f"Search complete: found {len(results)} results")
                
                # If no results, log available network activity for debugging
                if len(results) == 0:
                    logger.warning("No results found. This might mean:")
                    logger.warning("1. Search API call was not intercepted")
                    logger.warning("2. Response format is different than expected")
                    logger.warning("3. No results match the query")
                
                return {
                    "ok": True,
                    "results": results,
                    "count": len(results)
                }
        except Exception as e:
            logger.error(f"Search readers error: {e}", exc_info=True)
            return {
                "ok": False,
                "results": [],
                "count": 0,
                "error": str(e)
            }
    
    async def _verify_reader_selected(self) -> bool:
        """
        Verify that a reader is selected by checking if Ant Design card with reader details is visible.
        Returns True if reader card appears to be selected and has data.
        """
        try:
            # Method 1: Check for warning message "Select a reader" - if it's visible, reader is NOT selected
            warning_select = self.page.locator("text=/Select a reader/i").first
            try:
                if await warning_select.is_visible(timeout=300):
                    logger.debug("'Select a reader' warning is visible - reader NOT selected")
                    return False
            except:
                pass  # Warning not visible is good
            
            # Method 2: Check for Ant Design card with reader details
            # The card has class "ant-card" and contains reader information in "ant-descriptions"
            try:
                # Look for the card that contains reader profile details
                reader_card = self.page.locator(".ant-card:has(.ant-descriptions)").first
                if await reader_card.is_visible(timeout=500):
                    logger.debug("Found Ant Design card with descriptions")
                    
                    # Check for "Card barcode" label - if it exists, reader is likely selected
                    card_barcode_label = reader_card.locator("text=/Card barcode/i").first
                    if await card_barcode_label.is_visible(timeout=300):
                        logger.debug("Reader selected: found Ant Design card with Card barcode label")
                        return True
                    
                    # Alternative: check for "First Name" or "Last Name" label
                    first_name_label = reader_card.locator("text=/First Name/i").first
                    if await first_name_label.is_visible(timeout=300):
                        logger.debug("Reader selected: found Ant Design card with First Name label")
                        return True
            except Exception as e:
                logger.debug(f"Error checking Ant Design card: {e}")
            
            # Method 3: Check for "ant-card-head-title" with reader name (simpler check)
            try:
                card_title = self.page.locator(".ant-card-head-title h4").first
                if await card_title.is_visible(timeout=500):
                    title_text = await card_title.inner_text()
                    if title_text and title_text.strip() and len(title_text.strip()) > 2:
                        logger.debug(f"Reader selected: found card title: {title_text[:30]}")
                        return True
            except Exception as e:
                logger.debug(f"Error checking card title: {e}")
            
            # Method 4: Check for ant-descriptions table with reader data
            try:
                descriptions_table = self.page.locator(".ant-descriptions table").first
                if await descriptions_table.is_visible(timeout=500):
                    # Check if table has multiple rows (reader data)
                    rows = await descriptions_table.locator("tbody tr").all()
                    if len(rows) >= 3:  # At least 3 rows means we have reader data
                        logger.debug(f"Reader selected: found {len(rows)} rows in descriptions table")
                        return True
            except Exception as e:
                logger.debug(f"Error checking descriptions table: {e}")
                
        except Exception as e:
            logger.debug(f"Error verifying reader selection: {e}")
        
        logger.debug("Reader NOT selected - verification failed all checks")
        return False
    
    async def issue_item(self, barcode: str, reader_id: int, loan_days: int = 14, reader_query: Optional[str] = None) -> Dict[str, Any]:
        """
        Issue a book item via UI.
        
        Note: The UI requires a reader to be selected before issuing. This method assumes
        the reader is already selected in the browser, or you can provide reader_query
        to search for the reader first. The reader_id is used for logging/reference only.
        
        Flow:
        1. Ensure on issuance page
        2. Optionally search and select reader if reader_query provided
        3. Click Issuance tab if needed
        4. Fill barcode input
        5. Submit/confirm
        6. Detect success/failure via network interception
        
        Args:
            barcode: Book barcode to issue
            reader_id: Reader ID (for logging/reference, not used for UI selection)
            loan_days: Number of days for loan (used if UI supports it, otherwise ignored)
            reader_query: Optional query string to search for reader (name/email/card barcode)
        
        Returns: {ok: bool, message: str, barcode: str, reader_id: int, loan_days: int, api_response: dict}
        """
        try:
            # Ensure initialized before acquiring lock to avoid deadlock
            await self._ensure_initialized()
            async with self._lock:
                await self._ensure_issuance_page()
                
                # Step 1: Click Issuance tab - CRITICAL: find Issuance in the SAME container as Return
                # There are TWO "Issuance" elements: one in navigation (left menu) and one in radio buttons (with Return)
                # We need the one in the radio button group, NOT the navigation one!
                logger.info("Clicking Issuance tab (must be in same container as Return, not in navigation)...")
                issuance_tab_clicked = False
                
                # Strategy: Find the container that has BOTH "Return" and "Issuance" (radio button group)
                # Then find Issuance inside that container
                selectors_to_try = [
                    # Method 1: Find radio group container, then Issuance inside it
                    ("locator", ".ant-radio-group label.ant-radio-button-wrapper:has-text('Issuance')", {}),
                    ("locator", "[class*='ant-radio-group'] label:has-text('Issuance')", {}),
                    # Method 2: Find container that has Return, then Issuance in same container
                    ("locator", ":has-text('Return'):has-text('Issuance') label:has-text('Issuance')", {}),
                    # Method 3: Direct input[value='issuance'] in radio group
                    ("locator", ".ant-radio-group input.ant-radio-button-input[value='issuance']", {}),
                    # Method 4: Find Return's parent, then find Issuance in same parent
                    ("locator", "label.ant-radio-button-wrapper:has-text('Return') ~ label.ant-radio-button-wrapper:has-text('Issuance')", {}),
                    # Method 5: Fallback - just label with Issuance (but prefer ones in radio group)
                    ("locator", "label.ant-radio-button-wrapper:has-text('Issuance')", {}),
                ]
                
                for method, selector, kwargs in selectors_to_try:
                    try:
                        element = self.page.locator(selector).first
                        
                        if await element.is_visible(timeout=2000):
                            await element.click()
                            await asyncio.sleep(0.5)  # Wait for tab switch
                            issuance_tab_clicked = True
                            logger.info(f"✓ Clicked Issuance tab using selector: {selector}")
                            break
                    except Exception as e:
                        logger.debug(f"Could not click Issuance tab with selector {selector}: {e}")
                        continue
                
                if not issuance_tab_clicked:
                    logger.warning("Could not click Issuance tab with any selector, continuing anyway...")
                
                # Step 2: Search and select reader
                # CRITICAL: Even if a reader appears to be selected, we MUST search and select
                # the correct reader using reader_query (card_barcode) to ensure we have the right one.
                # We cannot trust that the previously selected reader is the one we need.
                # 
                # Exception: If reader_query is NOT provided, we check if reader is already selected
                # and proceed (for backward compatibility, though this is not recommended).
                
                reader_selected = False
                
                # If reader_query is provided, ALWAYS search and select (don't trust previous selection)
                if reader_query:
                    logger.info(f"reader_query provided ({reader_query[:20]}...), will ALWAYS search and select reader (ignoring previous selection)")
                    reader_selected = False  # Force search even if reader appears selected
                else:
                    # No reader_query - check if reader is already selected (backward compatibility)
                    reader_already_selected = await self._verify_reader_selected()
                    if reader_already_selected:
                        logger.info("Reader is already selected in UI - verified by card presence (no reader_query provided)")
                        reader_selected = True
                    else:
                        logger.info("Reader is NOT selected - will search and select")
                        reader_selected = False
                
                # If reader is not selected (or we need to re-select), search for it
                if not reader_selected:
                    # Determine search query - prefer name/email/card barcode over reader_id
                    search_query = None
                    
                    if reader_query:
                        # Use provided query (name/email/card barcode - works best)
                        # IMPORTANT: reader_query should be card_barcode/name, NOT book barcode!
                        search_query = reader_query
                        logger.info(f"Using provided reader_query for reader search: {search_query[:50]}")  # Log first 50 chars to avoid logging full barcode
                    else:
                        # No reader_query provided - CANNOT search effectively
                        # We can't call search_readers here because we're already in a lock
                        # Searching by reader_id in UI doesn't work
                        logger.error(f"CRITICAL: reader_query (card_barcode/name) not provided for reader_id {reader_id}. Cannot search for reader in UI! Issue will likely fail.")
                        # Don't try to search - it will fail anyway
                        # Just return error instead of trying to proceed
                        return {
                            "ok": False,
                            "message": f"Reader query (card_barcode/name) not provided. Cannot search for reader {reader_id} in UI. Please ensure card_barcode is passed as reader_query.",
                            "barcode": barcode,
                            "reader_id": reader_id
                        }
                    
                    try:
                        search_input = self.page.get_by_placeholder("Search user").first
                        await search_input.wait_for(state="visible", timeout=10000)
                        
                        # IMPORTANT: Click on the field FIRST to activate it (user says dropdown only appears if field is clicked)
                        logger.info("Clicking on search input field to activate it...")
                        await search_input.click()
                        await asyncio.sleep(0.5)  # Wait for field to be fully focused/activated
                        
                        # CRITICAL: Clear the field completely (even if it has old text like "Aidar")
                        # We MUST clear it to avoid confusion - old text doesn't mean reader is selected!
                        logger.info("Clearing search field (removing any old text)...")
                        await search_input.clear()
                        await asyncio.sleep(0.3)
                        
                        # Verify field is actually empty
                        current_value = await search_input.input_value()
                        if current_value and current_value.strip():
                            # Field still has value, try more aggressive clearing
                            await search_input.press("Control+a")  # Select all
                            await asyncio.sleep(0.1)
                            await search_input.press("Delete")  # Delete
                            await asyncio.sleep(0.2)
                            logger.info(f"Field had value '{current_value}', cleared it")
                        
                        # Click again to ensure field is active and ready for input
                        await search_input.click()
                        await asyncio.sleep(0.3)
                        
                        # Type the query character by character (more realistic, triggers autocomplete better)
                        logger.info(f"Typing search query: {search_query}")
                        await search_input.type(search_query, delay=50)  # 50ms delay between characters
                        await asyncio.sleep(0.5)  # Wait a bit after typing
                        
                        # Wait for Ant Design Select dropdown to appear
                        # Ant Design uses: .ant-select-dropdown:not(.ant-select-dropdown-hidden)
                        logger.info("Waiting for Ant Design dropdown to appear...")
                        dropdown_visible = False
                        dropdown_container = None
                        
                        # Wait up to 5 seconds for dropdown to appear (user says it takes 1-2 seconds, maybe more)
                        for attempt in range(20):  # 20 * 0.25s = 5 seconds
                            try:
                                # Look for Ant Design dropdown that is NOT hidden
                                dropdown_selector = ".ant-select-dropdown:not(.ant-select-dropdown-hidden)"
                                dropdown_container = self.page.locator(dropdown_selector).first
                                
                                if await dropdown_container.is_visible(timeout=250):
                                    logger.info(f"✓ Ant Design dropdown appeared after ~{attempt * 0.25:.1f}s")
                                    dropdown_visible = True
                                    break
                            except:
                                pass
                            
                            await asyncio.sleep(0.25)
                        
                        if not dropdown_visible:
                            logger.warning("Ant Design dropdown did not appear, trying to find options directly...")
                            await asyncio.sleep(1.0)  # Extra wait
                        
                        # Find Ant Design option elements
                        reader_selected = False

                        # ВАЖНО: eLibra сначала может показать "общий" список пользователей,
                        # а уже потом – отфильтрованный по нашему запросу.
                        # Поэтому мы ждем, пока в тексте опций появится совпадение с нашим search_query
                        # (card_barcode / имя), а не кликаем первый элемент сразу.

                        option_to_click = None
                        normalized_query = (search_query or "").strip().lower()

                        for attempt in range(10):  # до ~5 секунд (10 * 0.5s)
                            options = []
                            try:
                                # Method 1: Find options in visible dropdown
                                options = await self.page.locator(
                                    ".ant-select-dropdown:not(.ant-select-dropdown-hidden) [role='option']"
                                ).all()
                                logger.info(f"Attempt {attempt+1}: found {len(options)} options in dropdown")
                            except Exception as e:
                                logger.warning(f"Error finding options: {e}")

                            # If no options, try fallback
                            if not options:
                                try:
                                    all_options = await self.page.locator("[role='option']").all()
                                    for opt in all_options:
                                        try:
                                            if await opt.is_visible(timeout=100):
                                                options.append(opt)
                                        except:
                                            continue
                                    logger.info(f"Attempt {attempt+1}: found {len(options)} options via fallback")
                                except:
                                    pass

                            # Пытаемся найти опцию, в которой ТЕКСТ реально соответствует нашему запросу
                            if options:
                                for opt in options:
                                    try:
                                        title = await opt.get_attribute("title") or ""
                                        text = await opt.inner_text() or ""
                                        combined = f"{title} {text}".lower()

                                        # Для card_barcode ожидаем точное вхождение строки
                                        # Для имени – тоже ищем подстроку
                                        if normalized_query and normalized_query in combined:
                                            option_to_click = opt
                                            logger.info(f"Matched option by query '{normalized_query}': {(title or text)[:80]}")
                                            break
                                    except Exception as e:
                                        logger.debug(f"Error inspecting option: {e}")

                            if option_to_click:
                                break

                            # Если список еще "старый" (нет совпадений), подождем и попробуем еще раз
                            await asyncio.sleep(0.5)

                        # Если так и не нашли совпадающую опцию — лучше зафейлить, чем кликнуть первого Шона
                        if not option_to_click:
                            logger.error(
                                f"No option matched search_query='{normalized_query}'. "
                                "Probably dropdown still shows generic list. Aborting reader selection."
                            )
                            return {
                                "ok": False,
                                "message": (
                                    "Не удалось найти читателя по введенному коду/имени. "
                                    "Убедись, что такой читатель существует, или попробуй еще раз."
                                ),
                                "barcode": barcode,
                                "reader_id": reader_id,
                            }

                        logger.info("=== CLICKING MATCHED OPTION NOW ===")

                        # NOW CLICK IT!
                        # Scroll into view
                        await option_to_click.scroll_into_view_if_needed()
                        await asyncio.sleep(0.2)

                        # CLICK THE CONTENT DIV (most reliable for Ant Design)
                        logger.info("=== ATTEMPTING CLICK ===")
                        clicked = False

                        try:
                            content = option_to_click.locator(".ant-select-item-option-content").first
                            if await content.is_visible(timeout=1000):
                                logger.info("Clicking content div...")
                                await content.click(timeout=3000)
                                clicked = True
                                logger.info("✓✓✓ CLICKED CONTENT DIV!")
                            else:
                                raise Exception("Content not visible")
                        except Exception as e1:
                            logger.warning(f"Content click failed: {e1}, trying direct click...")
                            try:
                                await option_to_click.click(timeout=3000)
                                clicked = True
                                logger.info("✓✓✓ CLICKED OPTION DIRECTLY!")
                            except Exception as e2:
                                logger.error(f"Direct click also failed: {e2}")
                                # Last resort: JavaScript
                                try:
                                    await self.page.evaluate("el => el.click()", option_to_click)
                                    clicked = True
                                    logger.info("✓✓✓ CLICKED VIA JAVASCRIPT!")
                                except Exception as e3:
                                    logger.error(f"All clicks failed: {e3}")
                        
                        if clicked:
                            # Wait for reader card
                            logger.info("Waiting for reader card...")
                            await asyncio.sleep(2.5)
                            
                            if await self._verify_reader_selected():
                                reader_selected = True
                                logger.info("✓✓✓ READER SELECTED!")
                            else:
                                logger.warning("Card not visible yet, waiting more...")
                                await asyncio.sleep(2.0)
                                if await self._verify_reader_selected():
                                    reader_selected = True
                                    logger.info("✓✓✓ READER SELECTED (after wait)!")
                                else:
                                    logger.error("✗✗✗ READER CARD STILL NOT VISIBLE!")
                        else:
                            logger.error("✗✗✗ CLICK FAILED - READER NOT SELECTED!")
                        
                        # Final check
                        if not reader_selected:
                            logger.warning(f"Reader {reader_id} was not selected after dropdown click")
                            # One last verification attempt
                            await asyncio.sleep(0.5)
                            if await self._verify_reader_selected():
                                reader_selected = True
                                logger.info("Reader card appeared on final check")
                    except Exception as e:
                        logger.warning(f"Error searching/selecting reader: {e}")
                        # Don't continue - we need reader to be selected!
                
                # CRITICAL: Verify reader is selected before proceeding
                # Even if we tried to select, we must verify it worked
                if not await self._verify_reader_selected():
                    logger.error(f"Reader is NOT selected after search. Cannot proceed with issue.")
                    return {
                        "ok": False,
                        "message": f"Reader is NOT selected in the UI. Please ensure reader exists and is selectable.",
                        "barcode": barcode,
                        "reader_id": reader_id
                    }
                
                logger.info("✓ Reader verified as selected - proceeding with barcode input")
                
                # Step 3: Fill barcode input and press Enter to open modal
                barcode_input = self.page.get_by_placeholder("Enter barcode").first
                await barcode_input.wait_for(state="visible", timeout=10000)
                await barcode_input.clear()
                await barcode_input.fill(barcode)
                await barcode_input.press("Enter")
                
                # Step 4: Wait for modal dialog to appear
                await asyncio.sleep(0.5)  # Wait for modal animation
                
                # Find the modal (try multiple selectors)
                modal_selectors = [
                    "[role='dialog']",
                    ".modal",
                    ".dialog",
                    "[class*='modal']",
                    "[class*='dialog']",
                    "div:has-text('issuance-book')",
                ]
                
                modal_visible = False
                for selector in modal_selectors:
                    try:
                        modal = self.page.locator(selector).first
                        if await modal.is_visible(timeout=2000):
                            modal_visible = True
                            logger.debug(f"Found modal with selector: {selector}")
                            break
                    except:
                        continue
                
                if not modal_visible:
                    logger.warning("Modal dialog not found, continuing anyway...")
                
                # Step 5: Fill return-date in modal
                # Calculate return date (loan_days from today)
                from datetime import datetime, timedelta
                return_date = datetime.now() + timedelta(days=loan_days)
                
                # Use YYYY-MM-DD format as specified by user (e.g., 2025-12-31)
                date_formats = [
                    "%Y-%m-%d",  # YYYY-MM-DD (2025-12-31) - primary format
                ]
                
                # Find return-date input field
                date_input_selectors = [
                    "input[placeholder*='дату']",
                    "input[placeholder*='date']",
                    "input[placeholder*='Выберите']",
                    "input[label*='return-date']",
                    "input[label*='return date']",
                    "input[name*='return']",
                    "input[name*='date']",
                    "input[type='date']",
                    "input[type='text']",  # Some date pickers use text input
                ]
                
                date_filled = False
                date_input_element = None
                
                # First, find the date input element
                for selector in date_input_selectors:
                    try:
                        date_input = self.page.locator(selector).first
                        if await date_input.is_visible(timeout=2000):
                            placeholder = await date_input.get_attribute("placeholder") or ""
                            input_type = await date_input.get_attribute("type") or ""
                            
                            # Check if this looks like a date field
                            if ("дату" in placeholder.lower() or 
                                "date" in placeholder.lower() or 
                                "выберите" in placeholder.lower() or
                                input_type == "date"):
                                date_input_element = date_input
                                logger.info(f"Found date input with selector: {selector}, placeholder: {placeholder}")
                                break
                    except:
                        continue
                
                # If not found by specific selectors, try to find by label text "return-date"
                if not date_input_element:
                    try:
                        # Look for label containing "return-date" and find associated input
                        labels = await self.page.locator("label").all()
                        for label in labels:
                            label_text = await label.inner_text()
                            if "return-date" in label_text.lower() or "return date" in label_text.lower():
                                # Try to find input associated with this label
                                for_attr = await label.get_attribute("for")
                                if for_attr:
                                    date_input_element = self.page.locator(f"#{for_attr}").first
                                    if await date_input_element.is_visible(timeout=1000):
                                        break
                                # Or try to find input near the label
                                parent = label.locator("..")
                                date_input_element = parent.locator("input").first
                                if await date_input_element.is_visible(timeout=1000):
                                    break
                    except:
                        pass
                
                # Fill date using YYYY-MM-DD format (2025-12-31)
                if date_input_element:
                    try:
                        date_str = return_date.strftime("%Y-%m-%d")  # Format: 2025-12-31
                        
                        # Click to focus the input
                        await date_input_element.click()
                        await asyncio.sleep(0.1)
                        
                        # Select all and replace
                        await date_input_element.press("Control+a")
                        await asyncio.sleep(0.1)
                        await date_input_element.fill(date_str)
                        await asyncio.sleep(0.2)
                        
                        # Press Tab to move to next field (triggers validation/acceptance)
                        await date_input_element.press("Tab")
                        await asyncio.sleep(0.3)
                        
                        # Verify value was accepted
                        value = await date_input_element.input_value()
                        if value and len(value) > 0:
                            date_filled = True
                            logger.info(f"Filled return date: {date_str}, stored value: {value}")
                        else:
                            # Try pressing Enter as alternative
                            await date_input_element.click()
                            await date_input_element.press("Control+a")
                            await date_input_element.fill(date_str)
                            await date_input_element.press("Enter")
                            await asyncio.sleep(0.2)
                            value = await date_input_element.input_value()
                            if value:
                                date_filled = True
                                logger.info(f"Filled return date (with Enter): {date_str}")
                    except Exception as e:
                        logger.warning(f"Error filling date: {e}")
                
                if not date_filled:
                    logger.warning(f"Could not fill return date automatically. Calculated date: {return_date.strftime('%Y-%m-%d')}")
                
                # Step 6: Intercept network request to detect success/failure
                issue_response_data = None
                
                async def handle_issue_response(response):
                    if "/api/interface-service/issuance/action/issue/book/item" in response.url:
                        try:
                            nonlocal issue_response_data
                            issue_response_data = await response.json()
                        except:
                            pass
                
                self.page.on("response", handle_issue_response)
                
                # Step 7: Click "Issuance" button in modal
                # Wait a bit for date to be processed
                await asyncio.sleep(0.3)
                
                # Find Issuance button in modal (not the tab)
                issuance_button_found = False
                issuance_button_selectors = [
                    "button:has-text('Issuance')",
                    "button[type='submit']",
                    "[role='button']:has-text('Issuance')",
                ]
                
                for selector in issuance_button_selectors:
                    try:
                        buttons = await self.page.locator(selector).all()
                        for btn in buttons:
                            # Check if button is in modal and not a tab
                            aria_selected = await btn.get_attribute("aria-selected")
                            if aria_selected is None:  # Not a tab
                                # Check if button is visible
                                if await btn.is_visible(timeout=500):
                                    await btn.click()
                                    issuance_button_found = True
                                    logger.info("Clicked Issuance button in modal")
                                    break
                        if issuance_button_found:
                            break
                    except:
                        continue
                
                if not issuance_button_found:
                    logger.warning("Could not find Issuance button in modal, trying Enter key")
                    await self.page.keyboard.press("Enter")
                
                # Step 5: Wait for response (max 5 seconds)
                for _ in range(10):  # Check every 0.5s for 5s
                    await asyncio.sleep(0.5)
                    if issue_response_data is not None:
                        break
                
                self.page.remove_listener("response", handle_issue_response)
                
                # Determine success/failure from API response
                ok = False
                message = "Issue action completed"
                
                if issue_response_data:
                    # Check API response format (typically {status: 0} means success)
                    if isinstance(issue_response_data, dict):
                        status = issue_response_data.get("status")
                        if status == 0:
                            ok = True
                            message = issue_response_data.get("message", "Issue successful")
                        else:
                            ok = False
                            message = issue_response_data.get("message", "Issue failed")
                    else:
                        # Unexpected format, assume success for now
                        ok = True
                        message = "Issue completed (unexpected response format)"
                else:
                    # No API response caught, try UI-based detection
                    await asyncio.sleep(0.5)
                    error_indicators = [
                        self.page.get_by_text("error", exact=False),
                        self.page.get_by_text("failed", exact=False),
                        self.page.locator(".error"),
                        self.page.locator(".toast-error"),
                    ]
                    
                    for indicator in error_indicators:
                        try:
                            element = indicator.first
                            if await element.is_visible(timeout=500):
                                message = await element.inner_text()
                                ok = False
                                break
                        except:
                            pass
                    
                    if ok:
                        # Try success indicators
                        success_indicators = [
                            self.page.get_by_text("success", exact=False),
                            self.page.get_by_text("issued", exact=False),
                        ]
                        for indicator in success_indicators:
                            try:
                                element = indicator.first
                                if await element.is_visible(timeout=500):
                                    message = await element.inner_text()
                                    ok = True
                                    break
                            except:
                                pass
                        
                        # If still no clear indication, assume success (barcode input might have cleared)
                        if message == "Issue action completed":
                            ok = True
                
                # If error occurred and modal is still open, close it
                if not ok:
                    try:
                        # Try to find and close the modal dialog
                        modal_close_selectors = [
                            "[role='dialog'] button[aria-label*='close' i]",
                            "[role='dialog'] button[aria-label*='Close' i]",
                            "[role='dialog'] .ant-modal-close",
                            "[role='dialog'] button:has-text('Close')",
                            "[role='dialog'] button:has-text('×')",
                            ".ant-modal-close",
                            ".ant-modal button[aria-label='Close']",
                        ]
                        
                        for selector in modal_close_selectors:
                            try:
                                close_btn = self.page.locator(selector).first
                                if await close_btn.is_visible(timeout=500):
                                    await close_btn.click()
                                    logger.info("Closed error modal dialog after issue failure")
                                    await asyncio.sleep(0.3)
                                    break
                            except:
                                continue
                        
                        # Fallback: press Escape to close modal
                        try:
                            await self.page.keyboard.press("Escape")
                            await asyncio.sleep(0.2)
                            logger.info("Pressed Escape to close modal after issue failure")
                        except:
                            pass
                    except Exception as e:
                        logger.debug(f"Error closing modal after issue failure: {e}")
                
                return {
                    "ok": ok,
                    "message": message,
                    "barcode": barcode,
                    "reader_id": reader_id,
                    "loan_days": loan_days,
                    "api_response": issue_response_data
                }
                
        except PlaywrightTimeoutError as e:
            logger.error(f"Issue item timeout: {e}")
            return {
                "ok": False,
                "message": f"Timeout: {str(e)}",
                "barcode": barcode,
                "reader_id": reader_id
            }
        except Exception as e:
            logger.error(f"Issue item error: {e}", exc_info=True)
            return {
                "ok": False,
                "message": f"Error: {str(e)}",
                "barcode": barcode,
                "reader_id": reader_id
            }
    
    async def return_item(self, barcode: str, reader_id: Optional[int] = None, reader_query: Optional[str] = None) -> Dict[str, Any]:
        """
        Return a book item via UI.
        
        Note: The UI REQUIRES a reader to be selected before returning. Provide reader_id or reader_query.
        
        Flow:
        1. Ensure on issuance page
        2. Click Return tab
        3. Search and select reader (if reader_id or reader_query provided)
        4. Fill barcode input
        5. Submit/confirm
        6. Detect success/failure
        
        Args:
            barcode: Book barcode to return
            reader_id: Optional reader ID (used for search/selection)
            reader_query: Optional query string to search for reader (name/email/card barcode)
        
        Returns: {ok: bool, message: str, ...}
        """
        try:
            # Ensure initialized before acquiring lock to avoid deadlock
            await self._ensure_initialized()
            async with self._lock:
                await self._ensure_issuance_page()
                
                # Step 1: Click Return tab - try multiple selectors
                return_tab_clicked = False
                
                # Try different selectors for Return tab
                selectors_to_try = [
                    ("get_by_role", "button", {"name": "Return"}),
                    ("get_by_text", "Return", {}),
                    ("locator", "button:has-text('Return')", {}),
                    ("locator", "[aria-label*='Return']", {}),
                    ("locator", "button[class*='tab'][class*='return']", {}),
                ]
                
                for method, selector, kwargs in selectors_to_try:
                    try:
                        if method == "get_by_role":
                            element = self.page.get_by_role(selector, **kwargs).first
                        elif method == "get_by_text":
                            element = self.page.get_by_text(selector).first
                        else:  # locator
                            element = self.page.locator(selector).first
                        
                        if await element.is_visible(timeout=2000):
                            await element.click()
                            await asyncio.sleep(0.5)  # Wait for tab switch
                            return_tab_clicked = True
                            logger.debug(f"Successfully clicked Return tab using {method}: {selector}")
                            break
                    except Exception as e:
                        logger.debug(f"Could not click Return tab with {method} {selector}: {e}")
                        continue
                
                if not return_tab_clicked:
                    logger.warning("Could not click Return tab with any selector, continuing anyway...")
                
                # Step 2: Search and select reader if reader_query provided (REQUIRED for return)
                # IMPORTANT: Use reader_query (card_barcode/name), NOT reader_id (reader_id doesn't work in search)
                if reader_query:
                    search_query = reader_query
                    
                    try:
                        # Use the same approach as issue_item - click field, type, wait for dropdown, click option
                        search_input = self.page.get_by_placeholder("Search user").first
                        await search_input.wait_for(state="visible", timeout=10000)
                        
                        # Click to activate field
                        await search_input.click()
                        await asyncio.sleep(0.5)
                        await search_input.clear()
                        await asyncio.sleep(0.3)
                        await search_input.click()
                        await asyncio.sleep(0.3)
                        
                        # Type the query
                        logger.info(f"Typing search query for return: {search_query}")
                        await search_input.type(search_query, delay=50)
                        await asyncio.sleep(1.0)
                        
                        # Wait for Ant Design dropdown
                        dropdown_ready = False
                        for attempt in range(20):
                            try:
                                dropdown = self.page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").first
                                if await dropdown.is_visible(timeout=200):
                                    options = await dropdown.locator("[role='option']").all()
                                    if options and len(options) > 0:
                                        logger.info(f"Dropdown ready with {len(options)} options")
                                        dropdown_ready = True
                                        break
                            except:
                                pass
                            await asyncio.sleep(0.25)
                        
                        # Find and click matching option (wait for filtered results, don't click first generic item)
                        if dropdown_ready:
                            option_to_click = None
                            normalized_query = (reader_query or "").strip().lower()
                            
                            # Wait for dropdown to be filtered by search query (same logic as issue_item)
                            for attempt in range(10):  # до ~5 секунд (10 * 0.5s)
                                options = []
                                try:
                                    options = await self.page.locator(
                                        ".ant-select-dropdown:not(.ant-select-dropdown-hidden) [role='option']"
                                    ).all()
                                    logger.info(f"Return: Attempt {attempt+1}: found {len(options)} options in dropdown")
                                except Exception as e:
                                    logger.warning(f"Error finding options: {e}")
                                
                                # Try to find option that matches our search query
                                if options:
                                    for opt in options:
                                        try:
                                            title = await opt.get_attribute("title") or ""
                                            text = await opt.inner_text() or ""
                                            combined = f"{title} {text}".lower()
                                            
                                            # For card_barcode expect exact match or substring
                                            if normalized_query and normalized_query in combined:
                                                option_to_click = opt
                                                logger.info(f"Return: Matched option by query '{normalized_query}': {(title or text)[:80]}")
                                                break
                                        except Exception as e:
                                            logger.debug(f"Error inspecting option: {e}")
                                
                                if option_to_click:
                                    break
                                
                                # If list still shows generic items, wait and try again
                                await asyncio.sleep(0.5)
                            
                            # If no matching option found, abort
                            if not option_to_click:
                                logger.error(
                                    f"Return: No option matched search_query='{normalized_query}'. "
                                    "Probably dropdown still shows generic list. Aborting reader selection."
                                )
                                return {
                                    "ok": False,
                                    "message": (
                                        "Не удалось найти читателя по введенному коду/имени. "
                                        "Убедись, что такой читатель существует, или попробуй еще раз."
                                    ),
                                    "barcode": barcode,
                                }
                            
                            # Click the matched option
                            try:
                                await option_to_click.scroll_into_view_if_needed()
                                await asyncio.sleep(0.2)
                                
                                # Click content div (Ant Design)
                                try:
                                    content = option_to_click.locator(".ant-select-item-option-content").first
                                    if await content.is_visible(timeout=1000):
                                        await content.click(timeout=3000)
                                        logger.info("✓ Clicked option content div for return")
                                    else:
                                        raise Exception("Content not visible")
                                except Exception as e1:
                                    logger.warning(f"Content click failed: {e1}, trying direct click...")
                                    await option_to_click.click(timeout=3000)
                                    logger.info("✓ Clicked option directly for return")
                                
                                await asyncio.sleep(1.5)  # Wait for reader card
                                
                                # Verify reader is selected
                                if await self._verify_reader_selected():
                                    logger.info("✓ Reader selected and verified for return")
                                else:
                                    logger.warning("Reader card not visible after click, waiting more...")
                                    await asyncio.sleep(1.0)
                                    if not await self._verify_reader_selected():
                                        logger.error("✗ Reader card still not visible after return selection")
                            except Exception as e:
                                logger.error(f"Error clicking option for return: {e}")
                                return {
                                    "ok": False,
                                    "message": f"Ошибка при выборе читателя: {str(e)}",
                                    "barcode": barcode,
                                }
                    except Exception as e:
                        logger.warning(f"Could not search/select reader for return: {e}")
                else:
                    logger.warning("No reader_query provided for return - reader selection will be skipped")
                
                # Step 3: Fill barcode input
                barcode_input = self.page.get_by_placeholder("Enter barcode").first
                await barcode_input.wait_for(state="visible", timeout=10000)
                await barcode_input.clear()
                await barcode_input.fill(barcode)
                
                # Step 3: Intercept network request to detect success/failure
                return_response_data = None
                
                async def handle_return_response(response):
                    if "/api/interface-service/issuance/action/return/book/item" in response.url:
                        try:
                            nonlocal return_response_data
                            return_response_data = await response.json()
                        except:
                            pass
                
                self.page.on("response", handle_return_response)
                
                # Click Return button or press Enter
                buttons = await self.page.locator("button:has-text('Return')").all()
                action_button = None
                for btn in buttons:
                    aria_selected = await btn.get_attribute("aria-selected")
                    if aria_selected is None:
                        action_button = btn
                        break
                
                if action_button:
                    await action_button.click()
                else:
                    await barcode_input.press("Enter")
                
                # Step 3.5: Check for security warning modal (check multiple times)
                # eLibra shows: "The book is given to another reader. Are you sure you want to return the book?"
                # SECURITY: We should NOT automatically confirm this - reject the return immediately
                
                def check_warning_modal():
                    """Helper function to check for warning modal and handle it."""
                    warning_modal_selectors = [
                        ".ant-modal-confirm:has-text('Warning')",
                        ".ant-modal-confirm:has-text('The book is given to another reader')",
                        ".ant-modal-confirm:has-text('given to another reader')",
                        "[role='dialog'].ant-modal-confirm",
                        ".ant-modal.ant-modal-confirm",
                        "[role='dialog'][aria-modal='true'].ant-modal-confirm",
                    ]
                    
                    for selector in warning_modal_selectors:
                        try:
                            modal = self.page.locator(selector).first
                            if modal and modal.is_visible():
                                # Verify it's the right modal by checking content
                                modal_text = modal.inner_text()
                                if "given to another reader" in modal_text.lower() or ("warning" in modal_text.lower() and "book" in modal_text.lower()):
                                    return modal
                        except:
                            continue
                    return None
                
                # Step 4: Wait for response OR warning modal (max 5 seconds)
                warning_handled = False
                for wait_attempt in range(10):  # 10 * 0.5s = 5 seconds
                    await asyncio.sleep(0.5)
                    
                    # Check for warning modal on each iteration
                    try:
                        modal = self.page.locator(".ant-modal-confirm").first
                        if await modal.is_visible(timeout=200):
                            modal_text = await modal.inner_text()
                            if "given to another reader" in modal_text.lower() or ("warning" in modal_text.lower() and "book" in modal_text.lower()):
                                logger.warning("SECURITY WARNING: Book is given to another reader - rejecting return")
                                
                                # Click Cancel button
                                cancel_clicked = False
                                
                                # Method 1: Find by text "Отмена"
                                try:
                                    cancel_btn = modal.locator(".ant-modal-confirm-btns button:has-text('Отмена')").first
                                    if await cancel_btn.is_visible(timeout=500):
                                        await cancel_btn.click()
                                        logger.info("✓ Clicked Cancel (found by text 'Отмена')")
                                        cancel_clicked = True
                                        await asyncio.sleep(0.5)
                                except:
                                    pass
                                
                                # Method 2: Find by class ant-btn-default
                                if not cancel_clicked:
                                    try:
                                        cancel_btn = modal.locator(".ant-modal-confirm-btns .ant-btn-default").first
                                        if await cancel_btn.is_visible(timeout=500):
                                            btn_text = await cancel_btn.inner_text()
                                            if "Отмена" in btn_text or "Cancel" in btn_text:
                                                await cancel_btn.click()
                                                logger.info(f"✓ Clicked Cancel (found by class, text: {btn_text})")
                                                cancel_clicked = True
                                                await asyncio.sleep(0.5)
                                    except:
                                        pass
                                
                                # Method 3: First button (usually Cancel)
                                if not cancel_clicked:
                                    try:
                                        buttons = await modal.locator(".ant-modal-confirm-btns button").all()
                                        if len(buttons) >= 2:
                                            cancel_btn = buttons[0]
                                            btn_text = await cancel_btn.inner_text()
                                            if "Отмена" in btn_text or "Cancel" in btn_text:
                                                await cancel_btn.click()
                                                logger.info(f"✓ Clicked Cancel (first button, text: {btn_text})")
                                                cancel_clicked = True
                                                await asyncio.sleep(0.5)
                                    except:
                                        pass
                                
                                # If modal detected, return error immediately (don't wait for API response)
                                if cancel_clicked:
                                    self.page.remove_listener("response", handle_return_response)
                                    await asyncio.sleep(0.3)
                                    return {
                                        "ok": False,
                                        "message": "Книга выдана другому читателю. Возврат отклонен по соображениям безопасности.",
                                        "barcode": barcode,
                                        "security_warning": True
                                    }
                                else:
                                    # Try Escape as fallback
                                    await self.page.keyboard.press("Escape")
                                    await asyncio.sleep(0.3)
                                    self.page.remove_listener("response", handle_return_response)
                                    return {
                                        "ok": False,
                                        "message": "Книга выдана другому читателю. Возврат отклонен по соображениям безопасности.",
                                        "barcode": barcode,
                                        "security_warning": True
                                    }
                    except:
                        pass
                    
                    # Check if API response received
                    if return_response_data is not None:
                        break
                
                self.page.remove_listener("response", handle_return_response)
                
                # Determine success/failure from API response and UI
                ok = False
                message = "Return action completed"
                
                # Wait a bit for UI to update
                await asyncio.sleep(0.5)
                
                # Check API response first
                if return_response_data:
                    if isinstance(return_response_data, dict):
                        status = return_response_data.get("status")
                        # Status 0 usually means success
                        if status == 0 or status == "0" or return_response_data.get("success") is True:
                            ok = True
                            message = return_response_data.get("message", "Return successful")
                        elif "error" in str(return_response_data).lower() or "fail" in str(return_response_data).lower():
                            ok = False
                            message = return_response_data.get("message", "Return failed")
                        else:
                            # If no clear error, check if status exists but is not 0
                            if status is not None and status != 0:
                                ok = False
                                message = return_response_data.get("message", f"Return failed (status: {status})")
                            else:
                                # Ambiguous - check UI
                                ok = True
                                message = return_response_data.get("message", "Return completed")
                    else:
                        # Non-dict response - likely success if we got here
                        ok = True
                        message = "Return completed"
                
                # Fallback to UI detection if API response doesn't clearly indicate success
                if not ok or return_response_data is None:
                    # Check for error indicators
                    error_indicators = [
                        self.page.get_by_text("error", exact=False),
                        self.page.get_by_text("failed", exact=False),
                        self.page.locator(".error"),
                        self.page.locator(".toast-error"),
                    ]
                    
                    error_found = False
                    for indicator in error_indicators:
                        try:
                            element = indicator.first
                            if await element.is_visible(timeout=500):
                                error_message = await element.inner_text()
                                message = error_message
                                ok = False
                                error_found = True
                                logger.info(f"Found error indicator: {error_message}")
                                break
                        except:
                            pass
                    
                    # If no error found, check for success indicators
                    if not error_found:
                        success_indicators = [
                            self.page.get_by_text("success", exact=False),
                            self.page.get_by_text("completed", exact=False),
                            self.page.get_by_text("Return action completed", exact=False),
                            self.page.locator(".success"),
                            self.page.locator(".toast-success"),
                        ]
                        
                        for indicator in success_indicators:
                            try:
                                element = indicator.first
                                if await element.is_visible(timeout=500):
                                    success_message = await element.inner_text()
                                    message = success_message
                                    ok = True
                                    logger.info(f"Found success indicator: {success_message}")
                                    break
                            except:
                                pass
                        
                        # If we see "Return action completed" message, it's always a success
                        if "Return action completed" in message or "completed" in message.lower():
                            ok = True
                        
                        # If still unsure and no API error, assume success (barcode input might have cleared)
                        if not error_found and return_response_data is None:
                            ok = True
                            if "Return action completed" in message:
                                ok = True
                            else:
                                message = "Return action completed"
                                ok = True
                
                # If error occurred and modal/dialog is still open, close it
                if not ok:
                    try:
                        # Try to find and close any error modal/dialog
                        modal_close_selectors = [
                            "[role='dialog'] button[aria-label*='close' i]",
                            "[role='dialog'] button[aria-label*='Close' i]",
                            "[role='dialog'] .ant-modal-close",
                            "[role='dialog'] button:has-text('Close')",
                            "[role='dialog'] button:has-text('×')",
                            ".ant-modal-close",
                            ".ant-modal button[aria-label='Close']",
                        ]
                        
                        for selector in modal_close_selectors:
                            try:
                                close_btn = self.page.locator(selector).first
                                if await close_btn.is_visible(timeout=500):
                                    await close_btn.click()
                                    logger.info("Closed error modal/dialog after return failure")
                                    await asyncio.sleep(0.3)
                                    break
                            except:
                                continue
                        
                        # Fallback: press Escape to close modal
                        try:
                            await self.page.keyboard.press("Escape")
                            await asyncio.sleep(0.2)
                            logger.info("Pressed Escape to close modal after return failure")
                        except:
                            pass
                    except Exception as e:
                        logger.debug(f"Error closing modal after return failure: {e}")

                return {
                    "ok": ok,
                    "message": message,
                    "barcode": barcode,
                    "api_response": return_response_data
                }
                
        except PlaywrightTimeoutError as e:
            logger.error(f"Return item timeout: {e}")
            return {
                "ok": False,
                "message": f"Timeout: {str(e)}",
                "barcode": barcode
            }
        except Exception as e:
            logger.error(f"Return item error: {e}", exc_info=True)
            return {
                "ok": False,
                "message": f"Error: {str(e)}",
                "barcode": barcode
            }


# Global instance (singleton pattern)
_rpa_instance: Optional[ElibraRPA] = None


def get_rpa() -> ElibraRPA:
    """Get or create global RPA instance."""
    global _rpa_instance
    if _rpa_instance is None:
        _rpa_instance = ElibraRPA()
    return _rpa_instance

