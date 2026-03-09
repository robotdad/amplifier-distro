/**
 * Theme initialization - runs synchronously in <head> to prevent flash
 * Sets data-theme attribute on <html> based on system preference
 */
(function() {
  try {
    var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

    if (prefersDark) {
      document.documentElement.dataset.theme = 'dark';
      document.documentElement.style.backgroundColor = '#171717';
    } else {
      document.documentElement.style.backgroundColor = '#FDFCFA';
    }
  } catch(e) {
    // Fail silently - defaults to light mode via CSS :root tokens
  }
})();
