"""One-off: drive the live server with a real browser and capture a screenshot.

Not part of the test suite -- this exists to produce a demo image for a human,
using Playwright's already-provisioned Chromium rather than a mocked-up image.
"""

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8123/"
OUT = "/tmp/chat_demo.png"

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
    page = browser.new_page(viewport={"width": 1100, "height": 760})
    page.goto(URL)
    page.wait_for_selector("#question")

    page.fill("#question", "How is Serious Adverse Events calculated, and what would break if I changed it?")
    page.click("#send")

    # Wait for the real Gemini round trip to finish and the thinking indicator to clear.
    page.wait_for_selector(".thinking", state="detached", timeout=30_000)
    page.wait_for_timeout(300)

    page.screenshot(path=OUT)
    browser.close()

print(f"wrote {OUT}")
