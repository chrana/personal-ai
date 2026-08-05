import os
import pyotp
from playwright.async_api import async_playwright
from tools.base import Tool, ToolResult
from tools.secrets import get_secret
from tools.storage import bill_exists, download_bill, upload_bill

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")


def get_config():
    return get_secret("personal-ai/config")


def resolve_property(property_name: str) -> str:
    """Resolve a property name/address to its slug."""
    config = get_config()
    properties = config["properties"]

    # Direct slug match
    if property_name in properties:
        return property_name

    # Fuzzy match on address or slug keywords
    name_lower = property_name.lower()
    for slug, info in properties.items():
        if name_lower in slug or name_lower in info["address"].lower():
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
        }
        if provider not in providers:
            return ToolResult(
                success=False,
                error=f"Unknown provider: {provider}. Available: {list(providers.keys())}",
            )

        property_slug = resolve_property(property)
        if not property_slug:
            config = get_config()
            return ToolResult(
                success=False,
                error=f"Unknown property: {property}. Available: {list(config['properties'].keys())}",
            )

        return await providers[provider](property_slug, bill_month)

    async def _enbridge(self, property_slug: str, bill_month: str) -> ToolResult:
        # Check S3 first
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

        # Get property config
        config = get_config()
        prop_config = config["properties"][property_slug]
        creds_path = prop_config["utilities"]["enbridge"]["credentials"]
        creds = get_secret(creds_path)

        username = creds["username"]
        password = creds["password"]
        totp = pyotp.TOTP(creds["totp_secret"])

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
