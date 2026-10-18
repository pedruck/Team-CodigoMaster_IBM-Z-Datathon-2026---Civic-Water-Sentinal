// Optional browser regression check. Run against the single-alert synthetic playback fixture.
// See docs/dashboard-design.md for Playwright setup and commands.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const baseUrl = process.argv[2] || "http://127.0.0.1:8501";
const outputDir = path.resolve(
  process.argv[3] || path.join(os.tmpdir(), "civicwater-ui-check"),
);
fs.mkdirSync(outputDir, { recursive: true });
(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_EXECUTABLE || undefined,
    headless: true,
  });
  const report = [];
  try {
    for (const width of [360, 390, 768, 1024, 1280, 1440, 1920]) {
      const page = await browser.newPage({
        viewport: { width, height: 900 },
        reducedMotion: "reduce",
      });
      const errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.goto(baseUrl);
      await page.waitForFunction(() => window.civicWaterMotion);
      await page.evaluate(() => document.fonts.ready);
      const sidebar = page.locator('[data-testid="stSidebar"]');
      const close = page.locator(
        '[data-testid="stSidebarCollapseButton"] button',
      );
      const expand = page.getByRole("button", { name: "Expand sidebar" });
      if (!(await close.isVisible())) await expand.click();
      await close.waitFor({ state: "visible" });
      await close.click();
      await expand.waitFor({ state: "visible" });
      await page.waitForTimeout(400);
      assert.ok(
        (await page.locator('[data-testid="stMain"]').boundingBox()).x <= 1,
        `collapsed sidebar reserves space: ${width}`,
      );
      await expand.click();
      await close.waitFor({ state: "visible" });
      await page.screenshot({ path: `${outputDir}/ui-sidebar-${width}.png` });
      const openMain = await page
        .locator('[data-testid="stMain"]')
        .evaluate((el) => ({ w: el.clientWidth, scroll: el.scrollWidth }));
      assert.ok(
        openMain.scroll <= openMain.w + 1,
        `open sidebar page overflow ${width}`,
      );
      const footer = page.locator(".sidebar-footer");
      await footer.scrollIntoViewIfNeeded();
      assert.ok(await footer.isVisible());
      const toggleColor = await close
        .locator('[data-testid="stIconMaterial"]')
        .evaluate((el) => getComputedStyle(el).color);
      assert.equal(toggleColor, "rgb(237, 247, 244)");
      await page.locator('.workspace-nav a[href="#network-map"]').click();
      await page.waitForFunction(
        () => document.activeElement.id === "network-map",
      );
      await page.waitForFunction(
        () =>
          document
            .querySelector('.workspace-nav a[href="#network-map"]')
            .getAttribute("aria-current") === "location",
      );
      if (width <= 768)
        assert.ok(
          await expand.isVisible(),
          `mobile navigation did not close: ${width}`,
        );
      else await close.click();
      await page.locator(".st-key-recent-panel").scrollIntoViewIfNeeded();
      const geometry = await page.evaluate(() => {
        const box = (e) => {
          const r = e.getBoundingClientRect();
          return { x: r.x, y: r.y, w: r.width, h: r.height };
        };
        const main = document.querySelector('[data-testid="stMain"]');
        const shell = document.querySelector(".st-key-sentinel-shell");
        const refresh = document.querySelector(
          ".st-key-refresh-controls button",
        );
        const button = document.querySelector(
          '[class*="st-key-issued-"] button',
        );
        const heading = document.querySelector(".st-key-recent-heading h2");
        return {
          mainWidth: main.clientWidth,
          mainScroll: main.scrollWidth,
          shellWidth: shell.clientWidth,
          shellScroll: shell.scrollWidth,
          refresh: box(refresh),
          evidence: box(button),
          heading: box(heading),
          fonts: [
            heading,
            button,
            document.querySelector(".alert-preview p"),
          ].map((e) => getComputedStyle(e).fontFamily),
        };
      });
      assert.ok(
        geometry.mainScroll <= geometry.mainWidth + 1,
        JSON.stringify({ width, geometry }),
      );
      assert.ok(geometry.shellScroll <= geometry.shellWidth + 1);
      assert.ok(geometry.fonts.every((f) => f.includes("IBM Plex Sans")));
      for (const rect of [geometry.refresh, geometry.evidence])
        assert.ok(rect.h >= 44 && rect.w >= 44);
      if (width >= 768)
        assert.ok(
          Math.abs(
            geometry.refresh.y +
              geometry.refresh.h / 2 -
              (geometry.heading.y + geometry.heading.h / 2),
          ) < 2,
          `header controls misaligned ${width}: ${JSON.stringify(geometry)}`,
        );
      await page.screenshot({ path: `${outputDir}/ui-fixed-${width}.png` });
      await page
        .getByRole("button", { name: "View evidence", exact: true })
        .click();
      await page.waitForFunction(
        () => document.activeElement.id === "alert-detail",
      );
      await page
        .getByText("Full record & audit reference", { exact: true })
        .click();
      await page
        .locator('[data-testid="stExpander"]')
        .filter({
          has: page.getByText("Full record & audit reference", { exact: true }),
        })
        .locator('[data-testid="stTable"]')
        .waitFor({ state: "visible" });
      await page
        .getByRole("textbox", { name: "Region", exact: true })
        .fill("missing-region");
      await page
        .getByRole("textbox", { name: "Region", exact: true })
        .press("Enter");
      await page
        .getByText("No alerts match the history filters.", { exact: true })
        .waitFor();
      await page.getByRole("textbox", { name: "Region", exact: true }).fill("");
      await page
        .getByRole("textbox", { name: "Region", exact: true })
        .press("Enter");
      await page.getByText("Page 1 of 1", { exact: false }).waitFor();
      await page
        .getByText("System status · sensors, processing and history buffer", {
          exact: true,
        })
        .click();
      await page.evaluate(() => {
        document.documentElement.style.setProperty(
          "font-size",
          "200%",
          "important",
        );
        return true;
      });
      await page.waitForTimeout(250);
      const zoom = await page
        .locator('[data-testid="stMain"]')
        .evaluate((el) => ({ w: el.clientWidth, scroll: el.scrollWidth }));
      await page.screenshot({ path: `${outputDir}/ui-200-${width}.png` });
      if (zoom.scroll > zoom.w + 1)
        console.log(
          await page.evaluate(() =>
            [...document.querySelectorAll('[data-testid="stMain"] *')]
              .filter(
                (e) =>
                  e.checkVisibility() &&
                  !e.closest(".network-map-scroll") &&
                  e.getBoundingClientRect().right > innerWidth + 1,
              )
              .map((e) => ({
                tag: e.tagName,
                testid: e.dataset.testid,
                cls: e.className,
                text: e.textContent.slice(0, 90),
                box: e.getBoundingClientRect().toJSON(),
              }))
              .slice(0, 30),
          ),
        );
      assert.ok(
        zoom.scroll <= zoom.w + 1,
        `200% page overflow ${width}: ${JSON.stringify(zoom)}`,
      );
      await expand.click();
      await footer.scrollIntoViewIfNeeded();
      const sidebarZoom = await page
        .locator('[data-testid="stSidebarContent"]')
        .evaluate((el) => ({ w: el.clientWidth, scroll: el.scrollWidth }));
      if (sidebarZoom.scroll > sidebarZoom.w + 1)
        console.log(
          await sidebar.evaluate((root) =>
            [...root.querySelectorAll("*")]
              .filter(
                (e) =>
                  e.checkVisibility() &&
                  e.getBoundingClientRect().right >
                    root.getBoundingClientRect().right - 10,
              )
              .map((e) => ({
                tag: e.tagName,
                id: e.dataset.testid,
                text: e.textContent.slice(0, 70),
                right: e.getBoundingClientRect().right,
                scroll: e.scrollWidth,
                client: e.clientWidth,
              }))
              .slice(0, 25),
          ),
        );
      assert.ok(
        sidebarZoom.scroll <= sidebarZoom.w + 1,
        `200% sidebar overflow ${width}: ${JSON.stringify(sidebarZoom)}`,
      );
      await page.screenshot({
        path: `${outputDir}/ui-sidebar-200-${width}.png`,
      });
      assert.deepEqual(errors, []);
      report.push({ width, geometry, zoom, errors });
      await page.close();
    }
    fs.writeFileSync(
      path.join(outputDir, "ui-responsive-report.json"),
      JSON.stringify(report, null, 2),
    );
    console.log(JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
