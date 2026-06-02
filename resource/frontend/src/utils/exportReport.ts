import { extractAllCSS } from './extractCSS';

/**
 * Export the report overlay as a self-contained HTML file.
 * Uses CSSOM extraction to capture Vite-bundled and Tailwind styles.
 */
export async function exportReportAsHtml(
  overlayElement: HTMLElement,
  company: string,
  theme: string,
): Promise<void> {
  // 1. Extract all active CSS via CSSOM
  const combinedCSS = extractAllCSS();

  // 2. Clone the overlay DOM
  const clone = overlayElement.cloneNode(true) as HTMLElement;

  // 3. Convert images to base64 data URIs (needed for blob/file contexts)
  const clonedImgs = clone.querySelectorAll('img');
  const originalImgs = overlayElement.querySelectorAll('img');
  for (let i = 0; i < clonedImgs.length; i++) {
    const originalImg = originalImgs[i] as HTMLImageElement | undefined;
    if (originalImg && originalImg.complete && originalImg.naturalWidth > 0) {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = originalImg.naturalWidth;
        canvas.height = originalImg.naturalHeight;
        const ctx = canvas.getContext('2d');
        if (ctx) {
          ctx.drawImage(originalImg, 0, 0);
          clonedImgs[i].src = canvas.toDataURL('image/png');
        }
      } catch {
        // CORS or tainted canvas — leave original src
      }
    }
  }

  // 4. Set up slides: all hidden except first, remove React artifacts
  const slides = clone.querySelectorAll('[data-slide-index]');
  slides.forEach((slide, i) => {
    const el = slide as HTMLElement;
    el.classList.remove('active');
    if (i === 0) el.classList.add('active');
    // Remove React event handlers (data-reactroot etc.)
    el.removeAttribute('data-reactroot');
  });
  clone.removeAttribute('data-reactroot');

  // 5. Build navigation script
  const navScript = `
(function() {
  var current = 0;
  var slides = document.querySelectorAll('[data-slide-index]');
  var total = slides.length;
  var counter = document.getElementById('slide-counter');
  function show(idx) {
    slides.forEach(function(s) { s.classList.remove('active'); });
    if (slides[idx]) slides[idx].classList.add('active');
    if (counter) counter.textContent = (idx + 1) + ' / ' + total;
  }
  document.addEventListener('keydown', function(e) {
    if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); current = Math.min(current + 1, total - 1); show(current); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); current = Math.max(current - 1, 0); show(current); }
  });
  var prevBtn = document.getElementById('nav-prev');
  var nextBtn = document.getElementById('nav-next');
  if (prevBtn) prevBtn.onclick = function() { current = Math.max(current - 1, 0); show(current); };
  if (nextBtn) nextBtn.onclick = function() { current = Math.min(current + 1, total - 1); show(current); };
  // Remove the download button in exported version
  var dlBtn = document.querySelector('[title="Download as HTML"]');
  if (dlBtn) dlBtn.parentElement.removeChild(dlBtn);
})();
`;

  // 6. Add IDs to nav buttons for the script
  const navBtns = clone.querySelectorAll('.report-nav-btn');
  if (navBtns[0]) navBtns[0].id = 'nav-prev';
  if (navBtns[1]) navBtns[1].id = 'nav-next';
  // Add ID to slide counter
  const counterSpan = clone.querySelector('.report-nav span');
  if (counterSpan) counterSpan.id = 'slide-counter';

  // 7. Assemble full HTML document
  const date = new Date().toISOString().slice(0, 10);
  const html = `<!DOCTYPE html>
<html data-theme="${theme}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Health Check - ${company} - ${date}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Roboto:wght@400..700&display=swap');
${combinedCSS}
body { margin: 0; padding: 0; overflow: hidden; font-family: 'Roboto', sans-serif; }
</style>
</head>
<body>
${clone.outerHTML}
<script>${navScript}</script>
</body>
</html>`;

  // 8. Download
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `health-check-${company}-${date}.html`;
  a.click();
  URL.revokeObjectURL(a.href);
}
