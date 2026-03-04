/**
 * Amplifier Shared Header
 *
 * Renders a consistent app header with wordmark, navigation, feedback slot,
 * and optional auth controls (username + logout).
 *
 * Usage:
 *   AmplifierHeader.init({
 *     appName: 'settings',           // shown after "amplifier" in wordmark
 *     backLink: { url: '/', label: 'Dashboard' },  // optional
 *     feedbackApp: 'settings',       // optional, enables feedback widget
 *     container: document.getElementById('app-header'),  // required
 *   });
 */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ */
  /*  Stylesheet (injected once)                                         */
  /* ------------------------------------------------------------------ */

  var CSS = [
    '.amp-header {',
    '  display: flex;',
    '  align-items: center;',
    '  justify-content: space-between;',
    '  padding: 16px 0;',
    '  border-bottom: 1px solid var(--canvas-mist);',
    '  margin-bottom: 24px;',
    '  animation: fadeIn var(--duration-fast, 200ms) var(--ease-out, ease);',
    '}',

    '.amp-header-left {',
    '  display: flex;',
    '  align-items: center;',
    '  gap: 8px;',
    '  min-width: 0;',
    '}',

    '.amp-header-wordmark {',
    '  font-family: var(--font-heading, "Syne", system-ui, sans-serif);',
    '  font-weight: 700;',
    '  font-size: clamp(1.25rem, 4vw, 1.75rem);',
    '  color: var(--signal, #5B4DE3);',
    '  letter-spacing: -0.03em;',
    '  text-transform: lowercase;',
    '  text-decoration: none;',
    '  transition: color var(--duration-fast, 200ms) var(--ease-out, ease);',
    '  white-space: nowrap;',
    '}',

    '.amp-header-wordmark:hover {',
    '  color: var(--signal-light, #7B6FF0);',
    '}',

    '.amp-header-app-name {',
    '  color: var(--ink-slate, #5C5C5C);',
    '  font-weight: 600;',
    '}',

    '.amp-header-right {',
    '  display: flex;',
    '  align-items: center;',
    '  gap: 12px;',
    '  flex-shrink: 1;',
    '  flex-wrap: wrap;',
    '  justify-content: flex-end;',
    '}',

    '.amp-header-nav-link {',
    '  font-size: 14px;',
    '  font-weight: 600;',
    '  color: var(--signal, #5B4DE3);',
    '  text-decoration: none;',
    '  padding: 6px 14px;',
    '  border: 1px solid var(--signal, #5B4DE3);',
    '  border-radius: var(--radius-button, 14px);',
    '  transition: all var(--duration-fast, 200ms) var(--ease-out, ease);',
    '  white-space: nowrap;',
    '}',

    '.amp-header-nav-link:hover {',
    '  background: var(--signal, #5B4DE3);',
    '  color: #fff;',
    '}',

    '.amp-header-user {',
    '  display: flex;',
    '  align-items: center;',
    '  gap: 8px;',
    '  padding-left: 12px;',
    '  border-left: 1px solid var(--canvas-mist, #EEEAE5);',
    '}',

    '.amp-header-username {',
    '  font-size: 13px;',
    '  color: var(--ink-slate, #5C5C5C);',
    '  font-weight: 500;',
    '}',

    '.amp-header-logout {',
    '  font-size: 13px;',
    '  font-weight: 600;',
    '  color: var(--ink-fog, #7A7A7A);',
    '  background: none;',
    '  border: 1px solid var(--canvas-mist, #EEEAE5);',
    '  border-radius: var(--radius-button, 14px);',
    '  padding: 4px 12px;',
    '  cursor: pointer;',
    '  font-family: var(--font-body, "Epilogue", system-ui, sans-serif);',
    '  transition: all var(--duration-fast, 200ms) var(--ease-out, ease);',
    '}',

    '.amp-header-logout:hover {',
    '  color: var(--error, #C53030);',
    '  border-color: var(--error, #C53030);',
    '}',

    '@media (max-width: 480px) {',
    '  .amp-header {',
    '    flex-direction: column;',
    '    align-items: flex-start;',
    '    gap: 12px;',
    '  }',
    '  .amp-header-right {',
    '    width: 100%;',
    '    flex-wrap: wrap;',
    '  }',
    '}',
  ].join('\n');

  /* ------------------------------------------------------------------ */
  /*  Helpers                                                            */
  /* ------------------------------------------------------------------ */

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'className') {
          node.className = attrs[k];
        } else if (k.slice(0, 2) === 'on') {
          node.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
        } else {
          node.setAttribute(k, attrs[k]);
        }
      });
    }
    (children || []).forEach(function (c) {
      if (typeof c === 'string') {
        node.appendChild(document.createTextNode(c));
      } else if (c) {
        node.appendChild(c);
      }
    });
    return node;
  }

  /* ------------------------------------------------------------------ */
  /*  Style injection                                                    */
  /* ------------------------------------------------------------------ */

  var styleInjected = false;

  function injectStyle() {
    if (styleInjected) return;
    var s = document.createElement('style');
    s.textContent = CSS;
    document.head.appendChild(s);
    styleInjected = true;
  }

  /* ------------------------------------------------------------------ */
  /*  Auth detection                                                     */
  /* ------------------------------------------------------------------ */

  /**
   * Fetch /auth/me to determine auth state.
   * Returns a promise resolving to:
   *   { authEnabled: false }                — 404 means local/no-auth mode
   *   { authEnabled: true, username: str }  — 200 with a logged-in user
   *   { authEnabled: true, username: null } — 401 or other error
   */
  function detectAuth() {
    return fetch('/auth/me', { credentials: 'same-origin' })
      .then(function (res) {
        if (res.status === 404) {
          return { authEnabled: false, username: null };
        }
        if (res.status === 200) {
          return res.json().then(function (data) {
            return { authEnabled: true, username: data.username || null };
          });
        }
        // 401, 403, or any other status — auth is present but no active session
        return { authEnabled: true, username: null };
      })
      .catch(function () {
        // Network failure — assume local mode (fail safe: don't show logout)
        return { authEnabled: false, username: null };
      });
  }

  /* ------------------------------------------------------------------ */
  /*  Logout handler                                                     */
  /* ------------------------------------------------------------------ */

  function doLogout() {
    fetch('/logout', { method: 'POST', redirect: 'follow', credentials: 'same-origin' })
      .then(function () {
        window.location.href = '/login';
      })
      .catch(function () {
        window.location.href = '/login';
      });
  }

  /* ------------------------------------------------------------------ */
  /*  Header builder                                                     */
  /* ------------------------------------------------------------------ */

  /**
   * Build and inject the header DOM into opts.container, then init
   * the feedback widget if requested.
   *
   * @param {Object}      opts
   * @param {HTMLElement} opts.container    — mount target (required)
   * @param {string}      [opts.appName]   — subtitle shown after "amplifier"
   * @param {Object}      [opts.backLink]  — { url, label } for nav link
   * @param {string}      [opts.feedbackApp] — enables feedback widget inline
   * @param {Object}      authResult       — { authEnabled, username }
   * @returns {{ headerEl, feedbackSlot }}
   */
  function buildHeader(opts, authResult) {
    var container = opts.container;

    /* --- Left: wordmark --- */
    var wordmarkChildren = ['amplifier'];
    if (opts.appName) {
      wordmarkChildren.push(
        el('span', { className: 'amp-header-app-name' }, [' ' + opts.appName])
      );
    }
    var wordmarkEl = el(
      'a',
      { className: 'amp-header-wordmark', href: '/' },
      wordmarkChildren
    );

    var leftEl = el('div', { className: 'amp-header-left' }, [wordmarkEl]);

    /* --- Right: nav link + feedback slot + user --- */
    var rightChildren = [];

    if (opts.backLink && opts.backLink.url) {
      rightChildren.push(
        el(
          'a',
          { className: 'amp-header-nav-link', href: opts.backLink.url },
          [opts.backLink.label || 'Back']
        )
      );
    }

    var feedbackSlotEl = null;
    if (opts.feedbackApp) {
      feedbackSlotEl = el('span', { className: 'amp-header-feedback-slot' });
      rightChildren.push(feedbackSlotEl);
    }

    if (authResult.authEnabled && authResult.username) {
      var usernameEl = el(
        'span',
        { className: 'amp-header-username' },
        [authResult.username]
      );
      var logoutBtn = el(
        'button',
        { className: 'amp-header-logout', type: 'button', onClick: doLogout },
        ['Sign out']
      );
      var userEl = el('div', { className: 'amp-header-user' }, [usernameEl, logoutBtn]);
      rightChildren.push(userEl);
    }

    var rightEl = el('div', { className: 'amp-header-right' }, rightChildren);

    /* --- Assemble header --- */
    var headerEl = el('header', { className: 'amp-header' }, [leftEl, rightEl]);

    /* Clear container and inject */
    container.innerHTML = '';
    container.appendChild(headerEl);

    /* --- Init feedback widget if requested --- */
    if (feedbackSlotEl && opts.feedbackApp) {
      if (window.AmplifierFeedback) {
        window.AmplifierFeedback.init({
          mode: 'inline',
          container: feedbackSlotEl,
          context: { app: opts.feedbackApp },
        });
      }
    }

    return { headerEl: headerEl, feedbackSlot: feedbackSlotEl };
  }

  /* ------------------------------------------------------------------ */
  /*  Public API                                                         */
  /* ------------------------------------------------------------------ */

  /**
   * Initialise the shared header.
   *
   * @param {Object}      opts
   * @param {HTMLElement} opts.container    — mount target (required)
   * @param {string}      [opts.appName]   — page subtitle, e.g. 'settings'
   * @param {Object}      [opts.backLink]  — { url: '/', label: 'Dashboard' }
   * @param {string}      [opts.feedbackApp] — enables inline feedback widget
   * @returns {Promise<{ headerEl, feedbackSlot }>}
   */
  function init(opts) {
    opts = opts || {};

    if (!opts.container) {
      console.warn('[AmplifierHeader] opts.container is required');
      return Promise.resolve({ headerEl: null, feedbackSlot: null });
    }

    injectStyle();

    return detectAuth().then(function (authResult) {
      return buildHeader(opts, authResult);
    });
  }

  /* ------------------------------------------------------------------ */
  /*  Export                                                             */
  /* ------------------------------------------------------------------ */

  window.AmplifierHeader = { init: init };
})();
