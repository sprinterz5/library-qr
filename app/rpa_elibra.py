_DEV_SIGNATURE = "AB2025"
import asyncio
import logging
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
                            f"Solution:\n"
                            f"  - Use 'python run_windows.py' (reload is disabled by default on Windows)\n"
                        )
                        logger.error(error_msg)
                        raise RuntimeError(error_msg)
                except RuntimeError:
                    raise
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
                
                if self.context.pages:
                    self.page = self.context.pages[0]
                else:
                    self.page = await self.context.new_page()
                    
                self._initialized = True
                logger.info("Playwright RPA initialized")
            except Exception as e:
                logger.error(f"Failed to initialize RPA: {e}", exc_info=True)
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
        Best-effort: on failure raises a clear error.
        """
        await self._ensure_page()
        url = self.page.url or ""

        if "/auth/login" not in url:
            return

        if self._logging_in:
            logger.info("Auto-login already in progress, waiting...")
            try:
                await self.page.wait_for_url(
                    lambda u: "/auth/login" not in u,
                    timeout=40000,
                )
                logger.info("Existing auto-login finished")
                return
            except PlaywrightTimeoutError:
                raise RuntimeError(
                    "Auto-login is still in progress but did not complete in time. "
                    "Please try the operation again or use /rpa/manual-login."
                )

        if not settings.elibra_user_email or not settings.elibra_password:
            raise RuntimeError(
                "eLibra session expired and auto-login is not configured. "
                "Set ELIBRA_USER_EMAIL / ELIBRA_PASSWORD in .env "
                "or use /rpa/manual-login."
            )

        self._logging_in = True
        try:
            logger.info("Attempting auto-login on eLibra...")

            if LOGIN_URL not in url:
                await self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

            # Exact selectors from real eLibra login page HTML
            email_field = self.page.locator("#login_email").first
            await email_field.wait_for(state="visible", timeout=10000)
            await email_field.fill(settings.elibra_user_email)

            password_field = self.page.locator("#login_password").first
            await password_field.wait_for(state="visible", timeout=3000)
            await password_field.fill(settings.elibra_password)

            # form#login button[type='submit'] > span:text('Log in')
            login_btn = self.page.locator("form#login button[type='submit']").first
            try:
                await login_btn.wait_for(state="visible", timeout=3000)
                await login_btn.click()
            except PlaywrightTimeoutError:
                await password_field.press("Enter")

            try:
                await self.page.wait_for_url(
                    lambda u: "/auth/login" not in u,
                    timeout=30000,
                )
                logger.info(f"Auto-login successful, current URL: {self.page.url}")
                try:
                    await self.page.goto(ISSUANCE_URL, wait_until="domcontentloaded", timeout=30000)
                except Exception as nav_e:
                    logger.warning(f"Navigation to issuance after login failed: {nav_e}")
            except PlaywrightTimeoutError:
                raise RuntimeError("Auto-login timed out: still on /auth/login after submit.")

        finally:
            self._logging_in = False

    async def _find_first_visible(self, selectors: list, timeout: int = 1500):
        """
        Try a list of (method, selector) tuples and return the first visible element.
        Methods: 'locator', 'get_by_placeholder', 'get_by_label', 'get_by_text', 'get_by_role_button'
        Returns None if nothing found.
        """
        for method, selector in selectors:
            try:
                if method == "get_by_placeholder":
                    candidate = self.page.get_by_placeholder(selector).first
                elif method == "get_by_label":
                    candidate = self.page.get_by_label(selector).first
                elif method == "get_by_text":
                    candidate = self.page.get_by_text(selector).first
                elif method == "get_by_role_button":
                    candidate = self.page.get_by_role("button", name=selector).first
                else:
                    candidate = self.page.locator(selector).first
                if await candidate.is_visible(timeout=timeout):
                    logger.debug(f"Found element using {method}: {selector}")
                    return candidate
            except Exception:
                continue
        return None

    async def _ensure_issuance_page(self):
        """
        Navigate to issuance page and ensure we are logged in.
        If redirected to /auth/login, perform auto-login once.
        """
        await self._ensure_page()

        for attempt in range(2):
            current_url = self.page.url or ""

            if "/auth/login" in current_url:
                logger.warning("Detected login page, trying auto-login...")
                await self._auto_login_if_needed()

            current_url = self.page.url or ""
            if ISSUANCE_URL not in current_url:
                logger.info(f"Navigating to issuance page (current: {current_url})")
                await self.page.goto(ISSUANCE_URL, wait_until="domcontentloaded", timeout=30000)
            else:
                logger.debug("Already on issuance page")

            try:
                search_input = self.page.get_by_placeholder("Search user").first
                await search_input.wait_for(state="visible", timeout=5000)
                return
            except Exception:
                logger.warning("Could not find 'Search user' input, session may have expired")

        raise RuntimeError(
            "Не удалось открыть рабочее место выдачи в eLibra. "
            "Проверь логин/пароль или попробуй /rpa/manual-login."
        )
    
    async def health(self) -> Dict[str, Any]:
        """Check RPA health status."""
        try:
            if not self._initialized:
                try:
                    await self.initialize(headless=False)
                except Exception as e:
                    return {"ok": False, "page_open": False, "url": None, "logged_in": False,
                            "message": f"RPA initialization failed: {str(e)}"}
            
            async with self._lock:
                if not self._initialized or not self.page or self.page.is_closed():
                    return {"ok": False, "page_open": False, "url": None, "logged_in": False,
                            "message": "RPA not initialized or page closed"}
                
                url = self.page.url
                logged_in = False
                try:
                    await self._ensure_issuance_page()
                    search_input = self.page.get_by_placeholder("Search user").first
                    await search_input.wait_for(state="attached", timeout=3000)
                    logged_in = True
                except:
                    logged_in = False
                
                return {"ok": True, "page_open": True, "url": url, "logged_in": logged_in,
                        "message": "RPA is healthy"}
        except Exception as e:
            return {"ok": False, "page_open": False, "url": None, "logged_in": False,
                    "message": f"Health check error: {str(e)}"}
    
    async def manual_login(self) -> Dict[str, Any]:
        """Open browser to issuance page for manual login."""
        try:
            await self._ensure_initialized()
            async with self._lock:
                await self._ensure_page()
                await self.page.goto(ISSUANCE_URL, wait_until="domcontentloaded", timeout=30000)
                return {"ok": True, "message": "Browser opened. Please log in.", "url": self.page.url}
        except Exception as e:
            logger.error(f"Manual login error: {e}", exc_info=True)
            return {"ok": False, "message": f"Failed to open browser: {str(e)}"}

    # ── Shared reader search helper ──────────────────────

    async def _search_and_select_reader(self, search_query: str) -> Dict[str, Any]:
        """
        Search for a reader in the Ant Design Select dropdown and click the matching option.
        Uses event-driven waits — resolves immediately when elements appear.
        
        Returns: {"ok": True} on success, {"ok": False, "message": "..."} on failure.
        Must be called while holding self._lock.
        """
        search_input = self.page.get_by_placeholder("Search user").first
        await search_input.wait_for(state="visible", timeout=10000)

        # Click to activate, clear, type query
        await search_input.click()
        await asyncio.sleep(0.2)
        await search_input.clear()

        # Verify cleared
        current_value = await search_input.input_value()
        if current_value and current_value.strip():
            await search_input.press("Control+a")
            await search_input.press("Delete")
            await asyncio.sleep(0.1)

        await search_input.click()
        await asyncio.sleep(0.1)

        # Type query (char-by-char triggers autocomplete better)
        logger.info(f"Typing search query: {search_query}")
        await search_input.type(search_query, delay=50)

        # Wait for dropdown to appear (event-driven — resolves instantly when visible)
        dropdown_selector = ".ant-select-dropdown:not(.ant-select-dropdown-hidden)"
        try:
            await self.page.locator(dropdown_selector).first.wait_for(state="visible", timeout=5000)
            logger.debug("Dropdown appeared")
        except PlaywrightTimeoutError:
            logger.warning("Dropdown did not appear within 5s, trying to find options directly...")

        # Wait for the MATCHING option to appear (poll with short intervals)
        normalized_query = search_query.strip().lower()
        option_to_click = None

        for attempt in range(20):  # up to ~5s (20 × 0.25s)
            options = []
            try:
                options = await self.page.locator(
                    f"{dropdown_selector} [role='option']"
                ).all()
            except Exception:
                pass

            # Fallback: any visible [role='option']
            if not options:
                try:
                    all_opts = await self.page.locator("[role='option']").all()
                    for opt in all_opts:
                        try:
                            if await opt.is_visible(timeout=100):
                                options.append(opt)
                        except:
                            continue
                except:
                    pass

            # Check each option for a match
            for opt in options:
                try:
                    title = await opt.get_attribute("title") or ""
                    text = await opt.inner_text() or ""
                    combined = f"{title} {text}".lower()
                    if normalized_query in combined:
                        option_to_click = opt
                        logger.info(f"Matched option: {(title or text)[:80]}")
                        break
                except:
                    continue

            if option_to_click:
                break
            await asyncio.sleep(0.25)

        if not option_to_click:
            logger.error(f"No option matched query '{normalized_query}' after polling")
            return {
                "ok": False,
                "message": "Не удалось найти читателя по введенному коду/имени. "
                           "Убедись, что такой читатель существует, или попробуй еще раз."
            }

        # Click the matched option
        await option_to_click.scroll_into_view_if_needed()

        clicked = False
        # Try content div first (most reliable for Ant Design)
        try:
            content = option_to_click.locator(".ant-select-item-option-content").first
            if await content.is_visible(timeout=1000):
                await content.click(timeout=3000)
                clicked = True
                logger.debug("Clicked option content div")
        except Exception:
            pass

        if not clicked:
            try:
                await option_to_click.click(timeout=3000)
                clicked = True
                logger.debug("Clicked option directly")
            except Exception:
                pass

        if not clicked:
            try:
                await self.page.evaluate("el => el.click()", option_to_click)
                clicked = True
                logger.debug("Clicked option via JS")
            except Exception as e:
                logger.error(f"All click methods failed: {e}")
                return {"ok": False, "message": "Не удалось кликнуть на читателя в списке."}

        # Wait for reader card to appear (event-driven — resolves instantly when card shows)
        try:
            await self.page.locator(".ant-card:has(.ant-descriptions)").first.wait_for(
                state="visible", timeout=5000
            )
            logger.info("Reader card appeared")
        except PlaywrightTimeoutError:
            logger.warning("Reader card did not appear within 5s")

        # Final verify
        if await self._verify_reader_selected():
            logger.info("Reader selected and verified")
            return {"ok": True}
        else:
            logger.error("Reader not selected after click")
            return {"ok": False, "message": "Читатель не был выбран после клика."}

    async def _verify_reader_selected(self) -> bool:
        """
        Verify that a reader is selected by checking for Ant Design card with reader details.
        Uses short timeouts — returns immediately on success.
        """
        try:
            # If "Select a reader" warning is visible — NOT selected
            try:
                warning = self.page.locator("text=/Select a reader/i").first
                if await warning.is_visible(timeout=200):
                    return False
            except:
                pass
            
            # Check for Ant Design card with descriptions
            try:
                reader_card = self.page.locator(".ant-card:has(.ant-descriptions)").first
                if await reader_card.is_visible(timeout=500):
                    # Check for known labels
                    for label_text in ["Card barcode", "First Name"]:
                        try:
                            label = reader_card.locator(f"text=/{label_text}/i").first
                            if await label.is_visible(timeout=200):
                                return True
                        except:
                            continue
            except:
                pass
            
            # Check card title
            try:
                card_title = self.page.locator(".ant-card-head-title h4").first
                if await card_title.is_visible(timeout=300):
                    title_text = await card_title.inner_text()
                    if title_text and len(title_text.strip()) > 2:
                        return True
            except:
                pass
            
            # Check descriptions table rows
            try:
                desc_table = self.page.locator(".ant-descriptions table").first
                if await desc_table.is_visible(timeout=300):
                    rows = await desc_table.locator("tbody tr").all()
                    if len(rows) >= 3:
                        return True
            except:
                pass
                
        except Exception as e:
            logger.debug(f"Error verifying reader selection: {e}")
        
        return False

    # ── Search readers (API endpoint) ──────────────────────

    async def search_readers(self, query: str, n: int = 4) -> Dict[str, Any]:
        """
        Search for readers using the UI search input.
        Intercepts the search API response to get results.
        """
        try:
            await self._ensure_initialized()
            async with self._lock:
                await self._ensure_issuance_page()
                
                search_input = self.page.get_by_placeholder("Search user").first
                await search_input.wait_for(state="visible", timeout=10000)
                
                # Intercept search API response
                results = []
                
                async def handle_response(response):
                    url = response.url
                    search_patterns = [
                        "/api/interface-service/issuance/action/reader/profile/list",
                        "reader/profile/list",
                        "/search",
                        "reader/search"
                    ]
                    
                    if any(pattern in url for pattern in search_patterns):
                        try:
                            if 200 <= response.status < 300:
                                data = await response.json()
                                nonlocal results
                                
                                if isinstance(data, list):
                                    results = data
                                elif isinstance(data, dict):
                                    for key in ["result", "results", "data", "items", "list"]:
                                        if key in data and isinstance(data[key], list):
                                            results = data[key]
                                            break
                                
                                # Normalize parentId
                                for result in results:
                                    if isinstance(result, dict) and not result.get("parentId"):
                                        for field in ["readerId", "id", "reader_id"]:
                                            if field in result:
                                                result["parentId"] = result[field]
                                                break
                        except Exception as e:
                            logger.debug(f"Error parsing search response: {e}")
                
                self.page.on("response", handle_response)
                
                # Clear and search
                await search_input.clear()
                await search_input.fill(query)
                await search_input.press("Enter")
                
                # Wait for results (event-driven polling — checks frequently, exits early)
                for _ in range(40):  # up to ~4s
                    if results:
                        break
                    await asyncio.sleep(0.1)
                
                # Small extra wait if nothing came yet
                if not results:
                    await asyncio.sleep(1.0)
                
                self.page.remove_listener("response", handle_response)
                
                # Final parentId normalization
                for result in results:
                    if isinstance(result, dict) and not result.get("parentId"):
                        for field in ["id", "readerId"]:
                            if field in result:
                                result["parentId"] = result[field]
                                break
                
                logger.info(f"Search complete: {len(results)} results")
                
                return {"ok": True, "results": results, "count": len(results)}
        except Exception as e:
            logger.error(f"Search readers error: {e}", exc_info=True)
            return {"ok": False, "results": [], "count": 0, "error": str(e)}

    # ── Issue item ──────────────────────

    async def issue_item(self, barcode: str, reader_id: int, loan_days: int = 14, reader_query: Optional[str] = None) -> Dict[str, Any]:
        """
        Issue a book item via UI.
        
        Flow:
        1. Ensure on issuance page
        2. Click Issuance tab
        3. Search and select reader (using reader_query)
        4. Fill barcode, submit
        5. Fill return-date in modal
        6. Click Issuance button, detect success/failure via network
        """
        try:
            await self._ensure_initialized()
            async with self._lock:
                await self._ensure_issuance_page()
                
                # Step 1: Click Issuance radio tab
                # Structure: label.ant-radio-button-wrapper > span > input[value='issuance']
                try:
                    issuance_label = self.page.locator("label.ant-radio-button-wrapper:has(input[value='issuance'])").first
                    await issuance_label.wait_for(state="visible", timeout=2000)
                    await issuance_label.click()
                    logger.debug("Clicked Issuance tab")
                except PlaywrightTimeoutError:
                    logger.warning("Could not click Issuance tab, continuing...")
                
                # Step 2: Search and select reader
                if reader_query:
                    result = await self._search_and_select_reader(reader_query)
                    if not result["ok"]:
                        return {**result, "barcode": barcode, "reader_id": reader_id}
                else:
                    if not await self._verify_reader_selected():
                        return {
                            "ok": False,
                            "message": "Reader query (card_barcode/name) not provided. Cannot search for reader in UI.",
                            "barcode": barcode, "reader_id": reader_id
                        }
                
                # Final reader check
                if not await self._verify_reader_selected():
                    return {
                        "ok": False,
                        "message": "Reader is NOT selected in the UI.",
                        "barcode": barcode, "reader_id": reader_id
                    }
                
                logger.info("Reader verified — proceeding with barcode input")
                
                # Step 3: Fill barcode and submit
                barcode_input = self.page.locator("input#barcode").first
                await barcode_input.wait_for(state="visible", timeout=10000)
                await barcode_input.clear()
                await barcode_input.fill(barcode)
                await barcode_input.press("Enter")
                
                # Step 4: Wait for modal (exact selector from real DOM)
                try:
                    await self.page.locator(".ant-modal-content:has(.ant-modal-title:has-text('issuance-book'))").first.wait_for(
                        state="visible", timeout=3000
                    )
                    logger.debug("Issue modal appeared")
                except PlaywrightTimeoutError:
                    logger.warning("Issue modal not detected, continuing...")
                
                # Step 5: Fill return-date
                from datetime import datetime, timedelta
                return_date = datetime.now() + timedelta(days=loan_days)
                date_str = return_date.strftime("%Y-%m-%d")
                
                # Exact selector: input#returnDate placeholder="Выберите дату"
                date_input = self.page.locator("#returnDate").first
                try:
                    await date_input.wait_for(state="visible", timeout=1500)
                except PlaywrightTimeoutError:
                    date_input = None
                
                if date_input:
                    await date_input.click()
                    await date_input.press("Control+a")
                    await date_input.fill(date_str)
                    await date_input.press("Tab")
                    value = await date_input.input_value()
                    if value:
                        logger.info(f"Filled return date: {date_str}")
                    else:
                        # Retry with Enter
                        await date_input.click()
                        await date_input.press("Control+a")
                        await date_input.fill(date_str)
                        await date_input.press("Enter")
                        logger.info(f"Filled return date (with Enter): {date_str}")
                else:
                    logger.warning(f"Could not find date input. Calculated: {date_str}")
                
                # Step 6: Intercept issue API response
                issue_response_data = None
                
                async def handle_issue_response(response):
                    if "/api/interface-service/issuance/action/issue/book/item" in response.url:
                        try:
                            nonlocal issue_response_data
                            issue_response_data = await response.json()
                        except:
                            pass
                
                self.page.on("response", handle_issue_response)
                
                # Step 7: Click Issuance submit button in modal
                # Exact selector: button[type='submit'].ant-btn-primary > span:text('Issuance')
                try:
                    submit_btn = self.page.locator(".ant-modal-content button[type='submit'].ant-btn-primary").first
                    await submit_btn.wait_for(state="visible", timeout=1500)
                    await submit_btn.click()
                    logger.debug("Clicked Issuance submit button")
                except PlaywrightTimeoutError:
                    logger.warning("Submit button not found, pressing Enter")
                    await self.page.keyboard.press("Enter")
                
                # Step 8: Wait for API response (event-driven polling)
                for _ in range(20):  # up to ~4s
                    if issue_response_data is not None:
                        break
                    await asyncio.sleep(0.2)
                
                self.page.remove_listener("response", handle_issue_response)
                
                # Determine result
                ok, message = self._parse_api_response(issue_response_data, "Issue")
                
                # If no API response, check UI
                if issue_response_data is None:
                    ok, message = await self._detect_result_from_ui("Issue")
                
                # Close modal on error
                if not ok:
                    await self._close_modal()
                
                return {
                    "ok": ok, "message": message,
                    "barcode": barcode, "reader_id": reader_id,
                    "loan_days": loan_days, "api_response": issue_response_data
                }
                
        except PlaywrightTimeoutError as e:
            logger.error(f"Issue item timeout: {e}")
            return {"ok": False, "message": f"Timeout: {str(e)}", "barcode": barcode, "reader_id": reader_id}
        except Exception as e:
            logger.error(f"Issue item error: {e}", exc_info=True)
            return {"ok": False, "message": f"Error: {str(e)}", "barcode": barcode, "reader_id": reader_id}

    # ── Return item ──────────────────────

    async def return_item(self, barcode: str, reader_id: Optional[int] = None, reader_query: Optional[str] = None) -> Dict[str, Any]:
        """
        Return a book item via UI.
        
        Flow:
        1. Ensure on issuance page
        2. Click Return tab
        3. Search and select reader if reader_query provided
        4. Fill barcode, submit
        5. Handle security warning modal (book given to another reader)
        6. Detect success/failure
        """
        try:
            await self._ensure_initialized()
            async with self._lock:
                await self._ensure_issuance_page()
                
                # Step 1: Click Return radio tab
                try:
                    return_label = self.page.locator("label.ant-radio-button-wrapper:has(input[value='return'])").first
                    await return_label.wait_for(state="visible", timeout=2000)
                    await return_label.click()
                    logger.debug("Clicked Return tab")
                except PlaywrightTimeoutError:
                    logger.warning("Could not click Return tab, continuing...")
                
                # Step 2: Search and select reader if query provided
                if reader_query:
                    result = await self._search_and_select_reader(reader_query)
                    if not result["ok"]:
                        return {**result, "barcode": barcode}
                else:
                    logger.warning("No reader_query provided for return")
                
                # Step 3: Fill barcode
                barcode_input = self.page.locator("input#barcode").first
                await barcode_input.wait_for(state="visible", timeout=10000)
                await barcode_input.clear()
                await barcode_input.fill(barcode)
                
                # Step 4: Intercept return API response
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
                action_button = None
                buttons = await self.page.locator("button:has-text('Return')").all()
                for btn in buttons:
                    aria_selected = await btn.get_attribute("aria-selected")
                    if aria_selected is None:
                        action_button = btn
                        break
                
                if action_button:
                    await action_button.click()
                else:
                    await barcode_input.press("Enter")
                
                # Step 5: Wait for response OR security warning modal
                for _ in range(20):  # up to ~4s
                    await asyncio.sleep(0.2)
                    
                    # Check for security warning modal
                    try:
                        modal = self.page.locator(".ant-modal-confirm").first
                        if await modal.is_visible(timeout=150):
                            modal_text = await modal.inner_text()
                            if "given to another reader" in modal_text.lower() or \
                               ("warning" in modal_text.lower() and "book" in modal_text.lower()):
                                logger.warning("SECURITY: Book given to another reader — rejecting return")
                                
                                # Click Cancel
                                cancel_clicked = False
                                for cancel_selector in [
                                    ".ant-modal-confirm-btns button:has-text('Отмена')",
                                    ".ant-modal-confirm-btns .ant-btn-default",
                                ]:
                                    try:
                                        cancel_btn = modal.locator(cancel_selector).first
                                        if await cancel_btn.is_visible(timeout=300):
                                            await cancel_btn.click()
                                            cancel_clicked = True
                                            break
                                    except:
                                        continue
                                
                                if not cancel_clicked:
                                    await self.page.keyboard.press("Escape")
                                
                                self.page.remove_listener("response", handle_return_response)
                                return {
                                    "ok": False,
                                    "message": "Книга выдана другому читателю. Возврат отклонен.",
                                    "barcode": barcode, "security_warning": True
                                }
                    except:
                        pass
                    
                    if return_response_data is not None:
                        break
                
                self.page.remove_listener("response", handle_return_response)
                
                # Determine result
                ok, message = self._parse_api_response(return_response_data, "Return")
                
                # Fallback to UI detection
                if not ok or return_response_data is None:
                    ui_ok, ui_message = await self._detect_result_from_ui("Return")
                    if return_response_data is None:
                        ok, message = ui_ok, ui_message
                
                # Close modal on error
                if not ok:
                    await self._close_modal()

                return {
                    "ok": ok, "message": message,
                    "barcode": barcode, "api_response": return_response_data
                }
                
        except PlaywrightTimeoutError as e:
            logger.error(f"Return item timeout: {e}")
            return {"ok": False, "message": f"Timeout: {str(e)}", "barcode": barcode}
        except Exception as e:
            logger.error(f"Return item error: {e}", exc_info=True)
            return {"ok": False, "message": f"Error: {str(e)}", "barcode": barcode}

    # ── Shared helpers ──────────────────────

    def _parse_api_response(self, data: Any, action: str) -> tuple:
        """Parse API response and return (ok, message)."""
        if not data:
            return (True, f"{action} action completed")
        
        if isinstance(data, dict):
            status = data.get("status")
            if status == 0 or status == "0" or data.get("success") is True:
                return (True, data.get("message", f"{action} successful"))
            elif status is not None and status != 0:
                return (False, data.get("message", f"{action} failed (status: {status})"))
            elif "error" in str(data).lower() or "fail" in str(data).lower():
                return (False, data.get("message", f"{action} failed"))
            else:
                return (True, data.get("message", f"{action} completed"))
        
        return (True, f"{action} completed")

    async def _detect_result_from_ui(self, action: str) -> tuple:
        """Fallback: detect success/failure from UI elements. Returns (ok, message)."""
        await asyncio.sleep(0.3)
        
        # Check error indicators
        for text in ["error", "failed"]:
            try:
                el = self.page.get_by_text(text, exact=False).first
                if await el.is_visible(timeout=300):
                    return (False, await el.inner_text())
            except:
                pass
        
        # Check success indicators
        for text in ["success", "issued", "completed"]:
            try:
                el = self.page.get_by_text(text, exact=False).first
                if await el.is_visible(timeout=300):
                    return (True, await el.inner_text())
            except:
                pass
        
        # Default: assume success
        return (True, f"{action} action completed")

    async def _close_modal(self):
        """Try to close any open modal dialog."""
        for selector in [
            ".ant-modal-close",
            "[role='dialog'] button[aria-label*='close' i]",
            "[role='dialog'] button:has-text('Close')",
        ]:
            try:
                close_btn = self.page.locator(selector).first
                if await close_btn.is_visible(timeout=300):
                    await close_btn.click()
                    await asyncio.sleep(0.2)
                    return
            except:
                continue
        
        # Fallback: Escape
        try:
            await self.page.keyboard.press("Escape")
        except:
            pass


# Global instance (singleton pattern)
_rpa_instance: Optional[ElibraRPA] = None


def get_rpa() -> ElibraRPA:
    """Get or create global RPA instance."""
    global _rpa_instance
    if _rpa_instance is None:
        _rpa_instance = ElibraRPA()
    return _rpa_instance
