/**
 * Amplifier Auth Widget
 *
 * Checks if the current user is authenticated via GET /auth/me and renders
 * their username + a "Sign out" button into the provided container element.
 * If auth is not enabled or the user is not authenticated (401 / network error),
 * renders nothing — invisible no-op.
 *
 * Usage:
 *   AmplifierAuth.init({ container: document.getElementById('auth-slot') });
 */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ */
  /*  Stylesheet (injected once)                                         */
  /* ------------------------------------------------------------------ */

  var styleInjected = false;

  var CSS = [
    '.amp-auth-signout-btn {',
    '  font-size: 13px; font-weight: 600; color: var(--signal);',
    '  background: none; border: 1px solid var(--signal);',
    '  border-radius: var(--radius-button); padding: 4px 12px;',
    '  cursor: pointer; font-family: var(--font-body);',
    '  transition: all var(--duration-fast) var(--ease-out);',
    '}',
    '.amp-auth-signout-btn:hover {',
    '  background: var(--signal); color: #fff;',
    '}',
  ].join('\n');

  function injectStyle() {
    if (styleInjected) return;
    var s = document.createElement('style');
    s.textContent = CSS;
    document.head.appendChild(s);
    styleInjected = true;
  }

  /* ------------------------------------------------------------------ */
  /*  Sign-out action                                                    */
  /* ------------------------------------------------------------------ */

  function signOut() {
    fetch('/logout', { method: 'POST', redirect: 'follow' }).then(function () {
      window.location.href = '/login';
    });
  }

  /* ------------------------------------------------------------------ */
  /*  Render                                                             */
  /* ------------------------------------------------------------------ */

  function renderInto(container, username) {
    container.style.display = 'flex';
    container.style.alignItems = 'center';
    container.style.gap = '8px';

    var usernameSpan = document.createElement('span');
    usernameSpan.textContent = username;
    usernameSpan.style.color = 'var(--ink-slate)';
    usernameSpan.style.fontSize = '13px';

    var signOutBtn = document.createElement('button');
    signOutBtn.textContent = 'Sign out';
    signOutBtn.className = 'amp-auth-signout-btn';
    signOutBtn.type = 'button';
    signOutBtn.addEventListener('click', signOut);

    container.appendChild(usernameSpan);
    container.appendChild(signOutBtn);
  }

  /* ------------------------------------------------------------------ */
  /*  Public API                                                         */
  /* ------------------------------------------------------------------ */

  /**
   * Initialise the auth widget.
   *
   * @param {Object}      opts
   * @param {HTMLElement} opts.container  Element to render the widget into (required)
   */
  function init(opts) {
    opts = opts || {};
    var container = opts.container;
    if (!container) return;

    // When PAM auth is not active the server sets __AUTH_ENABLED = false.
    // Skip the /auth/me probe entirely — there is no session infrastructure.
    if (!window.__AUTH_ENABLED) return;

    injectStyle();

    fetch('/auth/me')
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (data) {
        if (!data || !data.username) return;
        renderInto(container, data.username);
      })
      .catch(function () {
        // Auth not enabled or request failed — render nothing
      });
  }

  /* ------------------------------------------------------------------ */
  /*  Export                                                             */
  /* ------------------------------------------------------------------ */

  window.AmplifierAuth = { init: init };
})();
