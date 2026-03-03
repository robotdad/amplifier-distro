/**
 * Theme initialization - runs synchronously in <head> to prevent flash
 * Auto-detects system preference (no user toggle for landing page)
 */
(function() {
  try {
    // Always use system preference for landing page
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    
    if (prefersDark) {
      document.documentElement.classList.add('dark');
      // Set bg immediately before CSS parses to prevent white flash
      document.documentElement.style.backgroundColor = '#0a0a0a';
    } else {
      document.documentElement.style.backgroundColor = '#ffffff';
    }
  } catch(e) {
    // Fail silently - defaults to light mode via CSS
  }
})();
