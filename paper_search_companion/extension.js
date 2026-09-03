"use strict";

const vscode = require("vscode");
const http = require("http");
const https = require("https");

const VIEW_TYPE = "paperSearchSelector";
const MAX_HTML_BYTES = 2 * 1024 * 1024;
const FETCH_TIMEOUT_MS = 10000;
const MAX_REDIRECTS = 5;

/**
 * @param {vscode.ExtensionContext} context
 */
function activate(context) {
  const openCommand = vscode.commands.registerCommand(
    "paper-search-companion.openSelector",
    async (args) => {
      const url = typeof args === "string" ? args : args?.url;
      if (!url) {
        vscode.window.showWarningMessage(
          "Paper Search Companion: provide a selection URL from the MCP tool."
        );
        return;
      }
      return _openSelectorPanel(context, url);
    }
  );
  context.subscriptions.push(openCommand);
}

/**
 * Open one self-contained Webview for an explicit selection URL.
 * The page talks to the local MCP HTTP endpoint directly; the extension is
 * only responsible for remote/desktop URI mapping and external links.
 */
async function _openSelectorPanel(context, rawUrl) {
  let target;
  try {
    target = _validateHttpUrl(rawUrl);
  } catch (err) {
    vscode.window.showErrorMessage(`Paper Search Companion: ${err.message}`);
    return null;
  }

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
  panel.webview.html = _loadingHtml(target);

  panel.webview.onDidReceiveMessage(
    async (message) => {
      if (message?.type !== "open-external") return;
      try {
        const external = _validateHttpUrl(message.url);
        await vscode.env.openExternal(vscode.Uri.parse(external));
      } catch (err) {
        vscode.window.showErrorMessage(
          `Paper Search Companion: cannot open link (${err.message})`
        );
      }
    },
    undefined,
    context.subscriptions
  );

  try {
    // asExternalUri handles remote extension hosts and forwarded localhost
    // ports without requiring the MCP process to know about VS Code routing.
    const externalUri = await vscode.env.asExternalUri(vscode.Uri.parse(target));
    const html = await _fetchHtml(externalUri.toString());
    panel.webview.html = _prepareWebviewHtml(html, externalUri.toString());
  } catch (err) {
    panel.webview.html = _errorHtml(target, err);
  }

  panel.onDidDispose(() => {
    console.log("[paper-search-companion] selector panel disposed");
  });
  return panel;
}

function _validateHttpUrl(value) {
  const parsed = new URL(String(value || ""));
  if (!/^https?:$/.test(parsed.protocol) || !parsed.hostname) {
    throw new Error("selection URL must use http or https");
  }
  if (parsed.username || parsed.password || parsed.hash) {
    throw new Error("selection URL must not contain credentials or a fragment");
  }
  return parsed.toString();
}

function _fetchHtml(url, redirects = 0) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const transport = parsed.protocol === "https:" ? https : http;
    const request = transport.get(
      parsed,
      { headers: { Accept: "text/html,application/xhtml+xml" } },
      (response) => {
        const status = response.statusCode || 0;
        if (status >= 300 && status < 400 && response.headers.location) {
          response.resume();
          if (redirects >= MAX_REDIRECTS) {
            reject(new Error("selection page redirect limit exceeded"));
            return;
          }
          try {
            const redirect = new URL(response.headers.location, parsed).toString();
            resolve(_fetchHtml(_validateHttpUrl(redirect), redirects + 1));
          } catch (err) {
            reject(err);
          }
          return;
        }
        if (status < 200 || status >= 300) {
          response.resume();
          reject(new Error(`selection page returned HTTP ${status}`));
          return;
        }

        const chunks = [];
        let total = 0;
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          total += Buffer.byteLength(chunk, "utf8");
          if (total > MAX_HTML_BYTES) {
            request.destroy(new Error("selection page is too large"));
            return;
          }
          chunks.push(chunk);
        });
        response.on("end", () => resolve(chunks.join("")));
        response.on("error", reject);
      }
    );
    request.setTimeout(FETCH_TIMEOUT_MS, () => {
      request.destroy(new Error("selection page request timed out"));
    });
    request.on("error", reject);
  });
}

function _prepareWebviewHtml(html, baseUrl) {
  const base = _escHtml(baseUrl);
  const cspOrigin = _escHtml(new URL(baseUrl).origin);
  const bridge = `
<script>
(() => {
  const vscodeApi = acquireVsCodeApi();
  document.addEventListener('click', (event) => {
    const link = event.target.closest && event.target.closest('a[href]');
    if (!link) return;
    let target;
    try { target = new URL(link.href, document.baseURI); } catch (_) { return; }
    if (target.origin !== location.origin) {
      event.preventDefault();
      vscodeApi.postMessage({ type: 'open-external', url: target.toString() });
    }
  }, true);
})();
</script>`;
  let documentHtml = String(html || "");
  documentHtml = documentHtml.replace(/<base\b[^>]*>/gi, "");
  if (/<head\b[^>]*>/i.test(documentHtml)) {
    documentHtml = documentHtml.replace(
      /(<head\b[^>]*>)/i,
      `$1<meta name="paper-search-base" content="${base}"><base href="${base}">`
    );
    documentHtml = documentHtml.replace(
      /(<head\b[^>]*>)/i,
      `$1<meta http-equiv="Content-Security-Policy" content="default-src 'none'; connect-src ${cspOrigin}; img-src data: ${cspOrigin} https:; style-src 'unsafe-inline' ${cspOrigin}; script-src 'unsafe-inline' ${cspOrigin}; font-src data: ${cspOrigin};">`
    );
  } else {
    documentHtml = `<!doctype html><html><head><base href="${base}"></head><body>${documentHtml}</body></html>`;
  }
  if (/<\/body>/i.test(documentHtml)) {
    documentHtml = documentHtml.replace(/<\/body>/i, `${bridge}</body>`);
  } else {
    documentHtml += bridge;
  }
  return documentHtml;
}

function _loadingHtml(url) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body { font-family: var(--vscode-font-family); color: var(--vscode-editor-foreground); background: var(--vscode-editor-background); padding: 32px; }
code { word-break: break-all; }
</style></head><body><p>Loading paper selector...</p><code>${_escHtml(url)}</code></body></html>`;
}

function _errorHtml(url, error) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body { font-family: var(--vscode-font-family); color: var(--vscode-editor-foreground); background: var(--vscode-editor-background); padding: 32px; }
code { word-break: break-all; }
</style></head><body><h2>Paper selector unavailable</h2><p>${_escHtml(error?.message || error)}</p><p>Open this URL in a browser:</p><code>${_escHtml(url)}</code></body></html>`;
}

function _escHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

module.exports = { activate };

if (typeof module.hot?.accept === "function") {
  module.hot.accept();
}
