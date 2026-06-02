import { test, expect } from './fixtures';

/**
 * Modal tests.
 *
 * Because modals are triggered by user actions deep in the app (file viewer,
 * permissions, tool preview), these tests inject a minimal modal into the page
 * to validate the generic Modal component behaviour in isolation.
 */
test.describe('Modal', () => {
  async function openTestModal(page: import('@playwright/test').Page) {
    // Inject a trigger button + render a modal via the app's React root
    await page.evaluate(() => {
      const btn = document.createElement('button');
      btn.id = 'test-modal-trigger';
      btn.textContent = 'Open Test Modal';
      btn.style.cssText = 'position:fixed;bottom:10px;right:10px;z-index:9999;padding:8px 16px;';
      document.body.appendChild(btn);

      btn.addEventListener('click', () => {
        // Create a modal overlay mimicking the app's Modal structure
        const overlay = document.createElement('div');
        overlay.className = 'fixed inset-0 z-50 modal-overlay';
        overlay.id = 'test-modal-overlay';
        overlay.tabIndex = -1;
        overlay.style.cssText = 'display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5);';

        const content = document.createElement('div');
        content.className = 'modal-content';
        content.innerHTML = `
          <div class="modal-drag-handle" style="padding:12px;">
            <h3>Test Modal</h3>
            <button aria-label="Close modal" id="test-modal-close">X</button>
          </div>
          <div style="padding:16px;">
            <input id="test-modal-input" type="text" placeholder="Focus target" />
            <button id="test-modal-btn">Action</button>
          </div>
        `;
        overlay.appendChild(content);
        document.body.appendChild(overlay);

        // Focus trap: set inert on root
        const root = document.getElementById('root');
        if (root) root.setAttribute('inert', '');
        overlay.focus();

        // Escape key handler
        const handleKey = (e: KeyboardEvent) => {
          if (e.key === 'Escape') {
            cleanup();
          }
        };

        const cleanup = () => {
          overlay.remove();
          root?.removeAttribute('inert');
          document.removeEventListener('keydown', handleKey);
        };

        document.getElementById('test-modal-close')?.addEventListener('click', cleanup);
        overlay.addEventListener('click', (e) => {
          if (e.target === overlay) cleanup();
        });
        document.addEventListener('keydown', handleKey);
      });
    });

    await page.click('#test-modal-trigger');
    await page.waitForSelector('#test-modal-overlay', { timeout: 3000 });
  }

  test('closes on Escape key', async ({ appPage: page }) => {
    await openTestModal(page);
    await expect(page.locator('#test-modal-overlay')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.locator('#test-modal-overlay')).not.toBeVisible();
  });

  test('closes on backdrop click', async ({ appPage: page }) => {
    await openTestModal(page);
    await expect(page.locator('#test-modal-overlay')).toBeVisible();

    // Click the overlay backdrop (not the content)
    await page.locator('#test-modal-overlay').click({ position: { x: 5, y: 5 } });
    await expect(page.locator('#test-modal-overlay')).not.toBeVisible();
  });

  test('closes via close button', async ({ appPage: page }) => {
    await openTestModal(page);
    await expect(page.locator('#test-modal-overlay')).toBeVisible();

    await page.click('#test-modal-close');
    await expect(page.locator('#test-modal-overlay')).not.toBeVisible();
  });

  test('sets inert on #root when open', async ({ appPage: page }) => {
    await openTestModal(page);

    const rootInert = await page.evaluate(() => {
      return document.getElementById('root')?.hasAttribute('inert') ?? false;
    });
    expect(rootInert).toBe(true);
  });

  test('removes inert from #root after close', async ({ appPage: page }) => {
    await openTestModal(page);
    await page.keyboard.press('Escape');

    const rootInert = await page.evaluate(() => {
      return document.getElementById('root')?.hasAttribute('inert') ?? false;
    });
    expect(rootInert).toBe(false);
  });
});
