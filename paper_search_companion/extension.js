"use strict";

const vscode = require("vscode");

// ══════════════════════════════════════════════════════════════
// Constants
// ══════════════════════════════════════════════════════════════
const VIEW_TYPE = "paperSearchSelector";
const IPC_PIPE_NAME = "\\\\.\\pipe\\paper_search_mcp_selection";

// ══════════════════════════════════════════════════════════════
// Activation
// ══════════════════════════════════════════════════════════════

/**
 * @param {vscode.ExtensionContext} context
 */
function activate(context) {
  console.log("[paper-search-companion] Activated");

  // ── Command: open selector with explicit URL ──────────────
  const openCommand = vscode.commands.registerCommand(
    "paper-search-companion.openSelector",
    async (args) => {
      const url = typeof args === "string" ? args : args?.url;
      if (!url) {
        // No URL provided — try reading from a temp state file
        const fallbackUrl = await _readPendingUrl();
        if (fallbackUrl) {
          return _openSelectorPanel(context, fallbackUrl);
        }
        vscode.window.showWarningMessage(
          "Paper Search Companion: no selection URL provided. " +
          "Run a paper search first."
        );
        return;
      }
      return _openSelectorPanel(context, url);
    }
  );

  // ── Named-pipe poller (Windows) ───────────────────────────
  let pipeServer = null;
  if (process.platform === "win32") {
    pipeServer = _startPipeServer(context);
  }

  context.subscriptions.push(openCommand);
  if (pipeServer) {
    context.subscriptions.push({ dispose: () => pipeServer.close() });
  }
}

// ══════════════════════════════════════════════════════════════
// Webview Panel
// ══════════════════════════════════════════════════════════════

/**
 * Open a Webview Panel that loads the paper selection page.
 * @param {vscode.ExtensionContext} context
 * @param {string} url — localhost URL or data URI
 */
function _openSelectorPanel(context, url) {
  const panel = vscode.window.createWebviewPanel(
    VIEW_TYPE,
    "Paper Selector",
    vscode.ViewColumn.Beside,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [],
    }
  );

  _setPanelHtml(panel, url);

  // ── Listen for selection result from the webview ──────────
  panel.webview.onDidReceiveMessage(
    async (message) => {
      switch (message.type) {
        case "selection-complete": {
          const selected = message.selectedIndices || "";
          const downloadOnly = message.downloadOnly || false;

          vscode.window.showInformationMessage(
            `Selected papers: ${selected || "none"}`
          );

          // Write result for the MCP server to pick up
          await _writeSelectionResult({
            selectedIndices: selected,
            downloadOnly: downloadOnly,
            timestamp: Date.now(),
          });

          // Close the panel after a brief delay
          setTimeout(() => panel.dispose(), 500);
          break;
        }
        case "selection-cancelled": {
          vscode.window.showInformationMessage("Paper selection cancelled.");
          panel.dispose();
          break;
        }
        case "resize": {
          // The webview requests a specific height
          break;
        }
      }
    },
    undefined,
    context.subscriptions
  );

  panel.onDidDispose(() => {
    console.log("[paper-search-companion] Panel disposed");
  });

  return panel;
}

/**
 * Set the panel's HTML — loads the external URL, but injects a
 * bridge script that intercepts form submissions and relays them
 * via VS Code's postMessage API.
 */
function _setPanelHtml(panel, url) {
  // When the URL is a localhost page, we load it via an iframe and
  // inject a bridge.  When it's a data URI or the server is not yet
  // running, show a loading state.
  panel.webview.html = _loadingHtml(url);

  // The actual page loads inside an iframe — once loaded we inject
  // a small bridge script that captures the selection result.
  panel.webview.onDidReceiveMessage(async (msg) => {
    if (msg.type === "iframe-ready") {
      // The iframe has loaded — inject the bridge
      panel.webview.postMessage({
        type: "inject-bridge",
        script: _bridgeScript(),
      });
    }
  }, undefined);
}

// ══════════════════════════════════════════════════════════════
// HTML templates
// ══════════════════════════════════════════════════════════════

function _loadingHtml(url) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh;
      background: var(--vscode-editor-background);
      color: var(--vscode-editor-foreground);
      font-family: var(--vscode-font-family);
      font-size: 14px;
    }
    .container { text-align: center; }
    .spinner {
      width: 40px; height: 40px;
      border: 3px solid var(--vscode-input-border);
      border-top-color: var(--vscode-button-background);
      border-radius: 50%;
      animation: spin .8s linear infinite;
      margin: 0 auto 16px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .url { font-size: 11px; color: var(--vscode-descriptionForeground); margin-top: 12px; word-break: break-all; }
  </style>
</head>
<body>
  <div class="container">
    <div class="spinner"></div>
    <p>Loading paper selector…</p>
    <p class="url">${_escHtml(url)}</p>
  </div>
  <iframe id="frame" src="${_escHtml(url)}"
    sandbox="allow-scripts allow-forms allow-same-origin"
    style="display:none; position:absolute; inset:0; width:100%; height:100%; border:none;"
    onload="onFrameLoad()">
  </iframe>
  <script>
    const vscodeApi = acquireVsCodeApi();
    const frame = document.getElementById('frame');

    function onFrameLoad() {
      frame.style.display = 'block';
      document.querySelector('.container').style.display = 'none';
      // Tell the extension the iframe is ready
      vscodeApi.postMessage({ type: 'iframe-ready' });
    }

    // Listen for bridge-injection command
    window.addEventListener('message', (e) => {
      const msg = e.data;
      if (msg.type === 'inject-bridge') {
        try {
          frame.contentWindow.postMessage({
            type: 'vscode-bridge',
            script: msg.script,
          }, '*');
        } catch (_) {}
      }
      if (msg.type === 'selection-result') {
        vscodeApi.postMessage(msg);
      }
    });
  </script>
</body>
</html>`;
}

function _bridgeScript() {
  return `
(function() {
  if (window.__vscodeBridgeInjected) return;
  window.__vscodeBridgeInjected = true;

  const vscodeApi = window.parent?.acquireVsCodeApi?.();
  if (!vscodeApi) return;

  // Hook into the existing form submission
  const form = document.querySelector('form');
  if (form) {
    form.addEventListener('submit', function(e) {
      e.preventDefault();
      const selected = Array.from(
        document.querySelectorAll('input[name="paper"]:checked')
      ).map(el => el.value);

      // Relay to VS Code extension
      window.parent.postMessage({
        type: 'selection-result',
        selectedIndices: selected.join(','),
        downloadOnly: window.data?.selection_semantics === 'download_selected_only',
      }, '*');
    }, true);
  }

  // Also hook button clicks
  document.addEventListener('click', function(e) {
    const btn = e.target.closest('#parse, #download');
    if (!btn || btn.disabled) return;
    // Let the original handler run, then capture
    setTimeout(function() {
      const status = document.getElementById('status');
      if (status && status.classList.contains('success')) {
        window.parent.postMessage({
          type: 'selection-result',
          selectedIndices: Array.from(
            document.querySelectorAll('input[name="paper"]:checked')
          ).map(el => el.value).join(','),
          downloadOnly: window.data?.selection_semantics === 'download_selected_only',
        }, '*');
      }
    }, 300);
  });
})();`;
}

// ══════════════════════════════════════════════════════════════
// Named Pipe polling (Windows)
// ══════════════════════════════════════════════════════════════

function _pollPipe(context) {
  return _startPipeServer(context);
  /*
      // Pipe not available yet — that's fine
    });

    // Short timeout so we don't block
  } catch (_) {
    // Pipe polling failed silently
  }
  */
}

// ══════════════════════════════════════════════════════════════
function _startPipeServer(context) {
  try {
    const net = require("net");
    const server = net.createServer((socket) => {
      let data = "";

      socket.on("data", (chunk) => {
        data += chunk.toString("utf8");
      });
      socket.on("end", () => _handlePipeRequest(context, data));
      socket.on("error", () => {});
    });

    server.on("error", (err) => {
      console.warn("[paper-search-companion] IPC pipe unavailable:", err?.message || err);
    });
    server.listen(IPC_PIPE_NAME);
    return server;
  } catch (err) {
    console.warn("[paper-search-companion] IPC pipe setup failed:", err?.message || err);
    return null;
  }
}

function _handlePipeRequest(context, raw) {
  try {
    const request = JSON.parse(raw);
    if (request?.action === "open_selection_page" && request?.params?.url) {
      _openSelectorPanel(context, request.params.url);
    }
  } catch (_) {
    // Ignore malformed messages
  }
}

// Temp-file IPC helpers (cross-platform fallback)
// ══════════════════════════════════════════════════════════════

const { tmpdir } = require("os");
const { join } = require("path");
const { readFile, writeFile, unlink } = require("fs/promises");

const PENDING_URL_FILE = join(tmpdir(), "paper_search_mcp_pending_url.json");
const RESULT_FILE = join(tmpdir(), "paper_search_mcp_selection_result.json");

async function _readPendingUrl() {
  try {
    const raw = await readFile(PENDING_URL_FILE, "utf-8");
    await unlink(PENDING_URL_FILE).catch(() => {});
    const data = JSON.parse(raw);
    return data?.url || null;
  } catch {
    return null;
  }
}

async function _writeSelectionResult(result) {
  try {
    await writeFile(RESULT_FILE, JSON.stringify(result, null, 2), "utf-8");
  } catch (_) {
    // Non-fatal: the MCP server can still work via its own HTTP handlers
  }
}

// ══════════════════════════════════════════════════════════════
// Utilities
// ══════════════════════════════════════════════════════════════

function _escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ══════════════════════════════════════════════════════════════
// Export
// ══════════════════════════════════════════════════════════════

module.exports = { activate };

// Re-evaluate after the module has been loaded (hot-reload compat)
if (typeof module.hot?.accept === "function") {
  module.hot.accept();
}
