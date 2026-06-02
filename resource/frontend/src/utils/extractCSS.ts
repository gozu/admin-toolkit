/**
 * Extract all active CSS rules from the document.
 * Uses the CSSOM API (document.styleSheets) which captures Vite-bundled
 * and Tailwind-processed styles that don't live in <style> tags.
 * Cross-origin sheets (Google Fonts, CDN) are silently skipped.
 */
export function extractAllCSS(): string {
  const rules: string[] = [];
  for (const sheet of document.styleSheets) {
    try {
      for (const rule of sheet.cssRules) {
        rules.push(rule.cssText);
      }
    } catch {
      // CORS — skip cross-origin sheets (Google Fonts, loaders.css, etc.)
    }
  }
  return rules.join('\n');
}
