import os
import re
import pyotp
from playwright.async_api import async_playwright
from tools.base import Tool, ToolResult
from tools.secrets import get_secret
from tools.storage import bill_exists, download_bill, upload_bill
from config import PROPERTIES

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")


def resolve_property(property_name: str) -> str:
    """Resolve a property name to its slug."""
    if property_name in PROPERTIES:
        return property_name

    name_lower = property_name.lower()
    for slug in PROPERTIES:
        if name_lower in slug:
            return slug
        for word in name_lower.split():
            if word in slug:
                return slug

    return ""


class BrowserTool(Tool):
    name = "browser"
    description = "Automates browser interactions to download files from websites"
    permissions = ["network", "filesystem"]

    async def run(self, action: str, **kwargs) -> ToolResult:
        actions = {
            "download_utility_bill": self._download_utility_bill,
        }
        if action not in actions:
            return ToolResult(success=False, error=f"Unknown action: {action}")
        return await actions[action](**kwargs)

    async def _download_utility_bill(self, provider: str, property: str, bill_month: str) -> ToolResult:
        providers = {
            "enbridge": self._enbridge,
            "peel-water": self._peel_water,
            "alectra": self._alectra,
        }
        if provider not in providers:
            return ToolResult(
                success=False,
                error=f"Unknown provider: {provider}. Available: {list(providers.keys())}",
            )

        property_slug = resolve_property(property)
        if not property_slug:
            return ToolResult(
                success=False,
                error=f"Unknown property: {property}. Available: {list(PROPERTIES.keys())}",
            )

        return await providers[provider](property_slug, bill_month)

    def _get_creds(self, property_slug: str, provider: str) -> dict:
        secret_name = PROPERTIES[property_slug]["credentials_secret"]
        all_creds = get_secret(secret_name)
        return all_creds[provider]

    async def _enbridge(self, property_slug: str, bill_month: str) -> ToolResult:
        if bill_exists(property_slug, "enbridge", bill_month):
            local_path = download_bill(property_slug, "enbridge", bill_month)
            return ToolResult(
                success=True,
                data={
                    "provider": "enbridge",
                    "property": property_slug,
                    "bill_month": bill_month,
                    "filepath": local_path,
                    "source": "cache",
                },
            )

        creds = self._get_creds(property_slug, "enbridge")
        username = creds["username"]
        password = creds["password"]
        totp_secret = creds.get("totp_secret", "")
        mfa_method = creds.get("mfa_method", "totp")

        os.makedirs(STORAGE_DIR, exist_ok=True)
        filename = f"{property_slug}_enbridge_{bill_month}.pdf"
        filepath = os.path.join(STORAGE_DIR, filename)

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()

                # Login page
                await page.goto("https://myaccount.enbridgegas.com/sign-in")
                await page.wait_for_selector("#okta-signin-username", timeout=15000)

                await page.fill("#okta-signin-username", username)
                await page.fill("#okta-signin-password", password)
                await page.click("#okta-signin-submit")

                # MFA page
                await page.wait_for_timeout(5000)

                if mfa_method == "email" or not totp_secret:
                    from tools.mfa import get_mfa_code
                    mfa_code = get_mfa_code(sender_filter="enbridge", max_wait=60)
                    if not mfa_code:
                        await browser.close()
                        return ToolResult(success=False, error="MFA code not received via email within 60s")
                else:
                    totp = pyotp.TOTP(totp_secret)
                    mfa_code = totp.now()

                mfa_input = page.locator('input[name="answer"], input[name="credentials.passcode"], input[type="tel"]').first
                await mfa_input.wait_for(timeout=10000)
                await mfa_input.fill(mfa_code)

                submit = page.locator('input[type="submit"], button[type="submit"]').first
                await submit.click()

                # Wait for dashboard
                await page.wait_for_timeout(8000)

                # Dismiss any popup modals
                for dismiss_text in ["Don't show again.", "Ok", "X"]:
                    btn = page.locator(f'button:has-text("{dismiss_text}"), a:has-text("{dismiss_text}")').first
                    if await btn.is_visible():
                        await btn.click()
                        await page.wait_for_timeout(1000)
                        break

                # Click "View My Bill" — may open new tab or navigate
                view_bill = page.locator('a:has-text("View My Bill"), button:has-text("View My Bill")').first
                await view_bill.wait_for(timeout=10000)

                # Try download first, fall back to new page/PDF capture
                try:
                    async with page.expect_download(timeout=10000) as download_info:
                        await view_bill.click()
                    download = await download_info.value
                    await download.save_as(filepath)
                except:
                    # Might open in new tab
                    try:
                        async with context.expect_page(timeout=10000) as new_page_info:
                            await view_bill.click()
                        new_page = await new_page_info.value
                        await new_page.wait_for_load_state("load", timeout=15000)
                        await new_page.wait_for_timeout(3000)

                        url = new_page.url
                        if ".pdf" in url:
                            response = await new_page.request.get(url)
                            with open(filepath, "wb") as f:
                                f.write(await response.body())
                        else:
                            await new_page.pdf(path=filepath)
                        await new_page.close()
                    except:
                        # Neither download nor new tab — navigated in same page
                        await page.wait_for_timeout(5000)
                        url = page.url
                        if ".pdf" in url:
                            response = await page.request.get(url)
                            with open(filepath, "wb") as f:
                                f.write(await response.body())
                        else:
                            await page.pdf(path=filepath)

                await browser.close()

            # Upload to S3 for future requests
            upload_bill(filepath, property_slug, "enbridge", bill_month)

            return ToolResult(
                success=True,
                data={
                    "provider": "enbridge",
                    "property": property_slug,
                    "bill_month": bill_month,
                    "filepath": filepath,
                    "source": "downloaded",
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def _peel_water(self, property_slug: str, bill_month: str) -> ToolResult:
        if bill_exists(property_slug, "peel-water", bill_month):
            local_path = download_bill(property_slug, "peel-water", bill_month)
            return ToolResult(
                success=True,
                data={
                    "provider": "peel-water",
                    "property": property_slug,
                    "bill_month": bill_month,
                    "filepath": local_path,
                    "source": "cache",
                },
            )

        creds = self._get_creds(property_slug, "peel-water")
        username = creds["username"]
        password = creds["password"]

        os.makedirs(STORAGE_DIR, exist_ok=True)
        filename = f"{property_slug}_peel-water_{bill_month}.pdf"
        filepath = os.path.join(STORAGE_DIR, filename)

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                page = await context.new_page()

                await page.goto("https://peelregion.idoxs.ca/authentication/login")
                await page.wait_for_selector("#bannerSignInUsername", timeout=15000)
                await page.fill("#bannerSignInUsername", username)
                await page.fill("#bannerSignInPassword", password)
                await page.click('button[type="submit"]')
                await page.wait_for_load_state("load", timeout=15000)
                await page.wait_for_timeout(5000)

                # Navigate to Bills page via visible link
                await page.evaluate('() => { const links = document.querySelectorAll("a"); for (const l of links) { if (l.href.includes("Bills.aspx") && l.offsetParent !== null) { l.click(); break; } } }')
                await page.wait_for_timeout(5000)

                # Hover row to reveal actions, click View Bill
                row = page.locator('[id$="_divRow"]').first
                await row.hover()
                await page.wait_for_timeout(1000)
                view_link = page.locator('[id$="_lnkViewBill"]').first
                await view_link.click()
                await page.wait_for_load_state("load", timeout=15000)
                await page.wait_for_timeout(5000)

                # Click PDF download button
                async with page.expect_download(timeout=15000) as dl_info:
                    await page.locator("#ibPDFDownload").click()
                download_file = await dl_info.value
                await download_file.save_as(filepath)

                await browser.close()

            upload_bill(filepath, property_slug, "peel-water", bill_month)

            return ToolResult(
                success=True,
                data={
                    "provider": "peel-water",
                    "property": property_slug,
                    "bill_month": bill_month,
                    "filepath": filepath,
                    "source": "downloaded",
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def _alectra(self, property_slug: str, bill_month: str) -> ToolResult:
        if bill_exists(property_slug, "alectra", bill_month):
            local_path = download_bill(property_slug, "alectra", bill_month)
            return ToolResult(
                success=True,
                data={
                    "provider": "alectra",
                    "property": property_slug,
                    "bill_month": bill_month,
                    "filepath": local_path,
                    "source": "cache",
                },
            )

        creds = self._get_creds(property_slug, "alectra")
        username = creds["username"]
        password = creds["password"]

        os.makedirs(STORAGE_DIR, exist_ok=True)
        filename = f"{property_slug}_alectra_{bill_month}.pdf"
        filepath = os.path.join(STORAGE_DIR, filename)

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                )
                page = await context.new_page()
                await page.add_init_script('() => { Object.defineProperty(navigator, "webdriver", { get: () => undefined }); }')

                await page.goto("https://myalectra.alectrautilities.com/portal/#/login")
                await page.wait_for_load_state("load", timeout=15000)
                await page.wait_for_timeout(5000)

                await page.click("#username")
                await page.keyboard.type(username, delay=80)
                await page.click("#password")
                await page.keyboard.type(password, delay=80)
                await page.wait_for_timeout(1000)
                await page.click('button:has-text("Log In")')
                await page.wait_for_timeout(10000)

                await page.click('a[href="#/ViewBill"]')
                await page.wait_for_timeout(5000)

                pdf_btn = page.locator('button:has-text("View your detailed bill PDF"), a:has-text("View your detailed bill PDF")').first
                async with context.expect_page(timeout=15000) as new_page_info:
                    await pdf_btn.click()
                viewer_page = await new_page_info.value
                await viewer_page.wait_for_load_state("load", timeout=15000)
                await viewer_page.wait_for_timeout(5000)

                btn = viewer_page.locator("#main_PDF")
                onclick = await btn.get_attribute("onclick")
                match = re.search(r"document\.location\.href='([^']+)'", onclick)
                if not match:
                    await browser.close()
                    return ToolResult(success=False, error="Could not find PDF download URL in viewer")

                pdf_path = match.group(1)
                base_url = viewer_page.url.rsplit("/", 1)[0]
                pdf_url = base_url + "/" + pdf_path

                response = await viewer_page.request.get(pdf_url)
                body = await response.body()

                if body[:4] != b"%PDF":
                    await browser.close()
                    return ToolResult(success=False, error="Downloaded content is not a valid PDF")

                with open(filepath, "wb") as f:
                    f.write(body)

                await browser.close()

            upload_bill(filepath, property_slug, "alectra", bill_month)

            return ToolResult(
                success=True,
                data={
                    "provider": "alectra",
                    "property": property_slug,
                    "bill_month": bill_month,
                    "filepath": filepath,
                    "source": "downloaded",
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))
