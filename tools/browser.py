import os
import pyotp
from playwright.async_api import async_playwright
from tools.base import Tool, ToolResult
from tools.secrets import get_secret

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")


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

    async def _download_utility_bill(self, provider: str, account: str, bill_month: str) -> ToolResult:
        providers = {
            "enbridge": self._enbridge,
        }
        if provider not in providers:
            return ToolResult(
                success=False,
                error=f"Unknown provider: {provider}. Available: {list(providers.keys())}",
            )
        return await providers[provider](account, bill_month)

    async def _enbridge(self, account: str, bill_month: str) -> ToolResult:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        filename = f"enbridge_{account}_{bill_month}.pdf"
        filepath = os.path.join(STORAGE_DIR, filename)

        try:
            creds = get_secret("personal-ai/enbridge")
            username = creds["username"]
            password = creds["password"]
            totp = pyotp.TOTP(creds["totp_secret"])

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
                await page.screenshot(path="/tmp/enbridge_mfa.png")

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

                await page.screenshot(path="/tmp/enbridge_post_login.png")

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
                        await new_page.screenshot(path="/tmp/enbridge_bill_page.png")

                        url = new_page.url
                        if ".pdf" in url:
                            response = await new_page.request.get(url)
                            with open(filepath, "wb") as f:
                                f.write(await response.body())
                        else:
                            await new_page.pdf(path=filepath)
                        await new_page.close()
                    except:
                        # Neither download nor new tab — it navigated in same page
                        await page.wait_for_timeout(5000)
                        await page.screenshot(path="/tmp/enbridge_bill_page.png")
                        url = page.url
                        if ".pdf" in url:
                            response = await page.request.get(url)
                            with open(filepath, "wb") as f:
                                f.write(await response.body())
                        else:
                            await page.pdf(path=filepath)

                await browser.close()

            return ToolResult(
                success=True,
                data={
                    "provider": "enbridge",
                    "account": account,
                    "bill_month": bill_month,
                    "filepath": filepath,
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))
