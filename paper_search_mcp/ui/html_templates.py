# paper_search_mcp/ui/html_templates.py
"""Static HTML templates. No MCP dependencies."""
from __future__ import annotations
import json
from html import escape as html_escape
from typing import Any, Dict, List
from ..utils import DEFAULT_SAVE_PATH
from ..engine.parse import (
    SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE,
    SELECTION_SEMANTICS_DOWNLOAD_ONLY,
    SELECTION_SEMANTICS_PARSE,
    _selection_semantics_name,
    _workflow_parse_execution_name,
)

PAPER_SELECTION_WIDGET_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light dark;
      --bg: #dce8f5;
      --glass: rgba(255, 255, 255, .48);
      --glass-strong: rgba(255, 255, 255, .70);
      --paper: rgba(255, 255, 255, .52);
      --paper-hover: rgba(255, 255, 255, .74);
      --text: #1a2d4a;
      --muted: #5a7290;
      --line: rgba(26, 45, 74, .10);
      --line-strong: rgba(26, 45, 74, .16);
      --accent: #4a90d9;
      --accent-strong: #357abd;
      --accent-glow: rgba(74, 144, 217, .28);
      --accent-soft: #6db3f2;
      --ink: #162237;
      --danger: #c0392b;
      --disabled: rgba(148, 163, 184, .16);
      --shadow: 0 24px 80px rgba(30, 60, 100, .15);
      --font-title: "Crimson Pro", Georgia, "Times New Roman", serif;
      --font-ui: "Atkinson Hyperlegible", "Segoe UI", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace;
      --scholar: #0d9488;
      --scholar-strong: #0f766e;
      --celebrate: #f97316;
      --ease-out-expo: cubic-bezier(.22, 1, .36, 1);
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0a1628;
        --glass: rgba(18, 32, 56, .58);
        --glass-strong: rgba(25, 42, 70, .72);
        --paper: rgba(20, 36, 58, .48);
        --paper-hover: rgba(28, 48, 76, .68);
        --text: #e2eaf5;
        --muted: #8aa4c0;
        --line: rgba(180, 200, 225, .12);
        --line-strong: rgba(180, 200, 225, .18);
        --accent: #6db3f2;
        --accent-strong: #5b9bd5;
        --accent-glow: rgba(109, 179, 242, .30);
        --accent-soft: #4a90d9;
        --ink: #d0dff0;
        --danger: #f97066;
        --disabled: rgba(71, 85, 105, .24);
        --shadow: 0 24px 80px rgba(0, 0, 0, .35);
      }
    }

    * { box-sizing: border-box; }
    [hidden] { display: none !important; }

    body {
      margin: 0;
      background: radial-gradient(ellipse 70% 50% at 15% 5%, rgba(74, 144, 217, .10), transparent 50%),
        radial-gradient(ellipse 55% 45% at 80% 12%, rgba(109, 179, 242, .08), transparent 48%),
        radial-gradient(ellipse 45% 35% at 50% 90%, rgba(53, 122, 189, .05), transparent 45%),
        linear-gradient(170deg, var(--bg), #e4eef8 40%, #ecf3fb 100%);
      color: var(--text);
      font-family: var(--font-ui);
      font-size: 14px;
      line-height: 1.48;
      -webkit-font-smoothing: antialiased;
    }

    main {
      min-height: 100vh;
      padding: clamp(8px, 2.5vw, 22px);
    }

    .shell {
      width: min(100%, 1120px);
      min-width: 0;
      margin: 0 auto;
      background: linear-gradient(180deg, var(--glass-strong), var(--glass));
      border: 1px solid rgba(255,255,255,.56);
      border-radius: 10px;
      overflow: hidden;
      box-shadow: var(--shadow), inset 0 1px 0 rgba(255,255,255,.20);
      backdrop-filter: blur(28px) saturate(150%);
      -webkit-backdrop-filter: blur(28px) saturate(150%);
      position: relative;
      container-type: inline-size;
    }
    .shell::before {
      content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 0;
      border-radius: inherit;
      background: linear-gradient(135deg, rgba(255,255,255,.08) 0%, transparent 35%, rgba(74,144,217,.04) 65%, transparent 100%);
    }

    header {
      position: relative; z-index: 1;
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      padding: clamp(16px, 3cqw, 24px) clamp(14px, 3.2cqw, 26px) clamp(14px, 2.6cqw, 20px);
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255, 255, 255, .20), transparent);
    }

    h1 {
      margin: 0;
      font-family: var(--font-title);
      font-size: clamp(18px, 3.2cqw, 21px);
      font-weight: 700;
      color: var(--ink);
      letter-spacing: 0;
    }

    h1::after {
      content: "MinerU-ready literature workspace";
      display: block;
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
      letter-spacing: 0;
    }

    .count {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
      padding: 4px 10px;
      background: rgba(255,255,255,.25);
      border: 1px solid var(--line);
      border-radius: 6px;
    }

    .list {
      position: relative; z-index: 1;
      display: grid;
      gap: clamp(8px, 1.6cqw, 10px);
      max-height: min(62vh, 720px);
      overflow: auto;
      padding: clamp(10px, 2cqw, 14px);
      scrollbar-width: thin;
      scrollbar-color: var(--line-strong) transparent;
    }
    .list::-webkit-scrollbar { width: 6px; }
    .list::-webkit-scrollbar-track { background: transparent; }
    .list::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 3px; }

    .paper {
      display: grid;
      grid-template-columns: 24px minmax(0, 1fr);
      gap: clamp(9px, 1.8cqw, 13px);
      align-items: start;
      padding: clamp(11px, 2cqw, 14px) clamp(12px, 2.5cqw, 18px);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--paper);
      box-shadow: 0 2px 12px rgba(30, 60, 100, .06);
      cursor: pointer;
      transition: background .18s ease, border-color .18s ease, box-shadow .18s ease, transform .18s ease;
      position: relative; overflow: hidden;
    }
    .paper::after {
      content: ""; position: absolute; inset: 0; pointer-events: none;
      border-radius: inherit;
      background: radial-gradient(ellipse at 50% 0%, rgba(74,144,217,.05), transparent 65%);
      opacity: 0; transition: opacity .26s ease;
    }

    .paper:hover {
      background: var(--paper-hover);
      border-color: rgba(74, 144, 217, .30);
      box-shadow: 0 8px 28px rgba(48, 96, 160, .12), 0 0 0 1px rgba(74, 144, 217, .15);
      transform: translateY(-1px);
    }
    .paper:hover::after { opacity: 1; }
    .paper.disabled { background: var(--disabled); color: var(--muted); cursor: not-allowed; }
    .paper.disabled:hover { transform: none; box-shadow: 0 2px 12px rgba(30, 60, 100, .06); border-color: var(--line); }
    .paper.disabled:hover::after { opacity: 0; }

    input[type="checkbox"] {
      -webkit-appearance: none; appearance: none;
      width: 19px; height: 19px;
      margin: 2px 0 0;
      border: 2px solid var(--line-strong);
      border-radius: 5px;
      background: rgba(255,255,255,.30);
      cursor: pointer; flex-shrink: 0;
      position: relative;
      transition: all .16s ease;
    }
    input[type="checkbox"]:checked {
      background: var(--accent);
      border-color: var(--accent);
      box-shadow: 0 0 12px var(--accent-glow);
    }
    input[type="checkbox"]:checked::after {
      content: "";
      position: absolute; top: 2px; left: 5px;
      width: 5px; height: 9px;
      border: solid #fff; border-width: 0 2px 2px 0;
      transform: rotate(45deg);
    }
    input[type="checkbox"]:hover:not(:disabled) {
      border-color: var(--accent-soft);
      box-shadow: 0 0 8px var(--accent-glow);
    }
    input[type="checkbox"]:disabled { opacity: .35; cursor: not-allowed; }

    .paper-body { min-width: 0; }

    .index {
      color: var(--accent);
      font-weight: 750;
    }

    .title {
      display: block;
      color: var(--ink);
      font-family: var(--font-title);
      font-size: clamp(14px, 2.4cqw, 15px);
      overflow-wrap: anywhere;
      font-weight: 600;
      line-height: 1.4;
    }

    .meta-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 7px clamp(9px, 2cqw, 14px);
      margin-top: 10px;
      color: var(--muted);
      font-size: 11px;
    }

    .meta-grid span { min-width: 0; }

    .meta-grid b {
      display: block;
      color: color-mix(in srgb, var(--ink), var(--muted) 50%);
      font-size: 9px;
      font-weight: 750;
      text-transform: uppercase;
      letter-spacing: 0;
    }

    .meta-grid em {
      display: block;
      font-style: normal;
      overflow-wrap: anywhere;
    }

    .url-field { grid-column: span 2; }

    .paper-link {
      appearance: none;
      border: 0;
      background: transparent;
      padding: 0;
      color: var(--accent-strong);
      cursor: pointer;
      font: inherit;
      text-align: left;
      text-decoration: none;
    }

    .paper-link:hover { text-decoration: underline; }

    .muted-value { color: var(--muted); }

    .toolbar {
      position: relative; z-index: 1;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 9px;
      padding: clamp(12px, 2.4cqw, 15px) clamp(12px, 2.8cqw, 20px) clamp(13px, 2.8cqw, 17px);
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, .15);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
    }

    button {
      min-height: 38px;
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      background: rgba(255, 255, 255, .40);
      color: var(--text);
      padding: 8px 15px;
      font: inherit;
      font-weight: 620;
      cursor: pointer;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .35);
      transition: transform .14s ease, border-color .14s ease, background .14s ease, box-shadow .14s ease;
      position: relative; overflow: hidden;
    }
    button::after {
      content: ""; position: absolute; inset: 0; pointer-events: none;
      background: radial-gradient(circle at 50% 0%, rgba(255,255,255,.10), transparent 60%);
      opacity: 0; transition: opacity .18s ease;
    }

    button.primary {
      border-color: transparent;
      background: linear-gradient(135deg, #357abd, #4a90d9);
      color: #ffffff;
      font-weight: 680;
      box-shadow: 0 8px 22px rgba(53, 122, 189, .25);
      letter-spacing: 0;
    }

    button:hover:not(:disabled) {
      transform: translateY(-1px);
      border-color: rgba(74, 144, 217, .35);
      background: rgba(255, 255, 255, .50);
      box-shadow: 0 4px 16px rgba(48, 96, 160, .12);
    }
    button:hover:not(:disabled)::after { opacity: 1; }

    button.primary:hover:not(:disabled) {
      background: linear-gradient(135deg, #4a90d9, #6db3f2);
      color: #ffffff;
      border-color: transparent;
      box-shadow: 0 10px 30px rgba(53, 122, 189, .35);
      transform: translateY(-2px);
    }

    button:active:not(:disabled) { transform: scale(.97); }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.50;
    }

    .status {
      min-height: 20px;
      margin-left: auto;
      color: var(--muted);
      font-size: 12px;
      white-space: pre-wrap;
    }
    .status:empty { display: none; }

    .status.error { color: var(--danger); font-weight: 600; }
    .status.success { color: #08795f; font-weight: 650; }

    .decision-panel {
      position: relative;
      z-index: 1;
      display: grid;
      gap: 6px;
      padding: 14px 20px;
      border-top: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(185, 228, 255, .28), rgba(191, 244, 223, .34));
      color: var(--ink);
    }
    .decision-panel[hidden] { display: none; }
    .decision-panel strong {
      font-family: var(--font-title);
      font-size: 17px;
      font-weight: 700;
    }
    .decision-panel span {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .selection-count {
      min-width: 74px;
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      background: rgba(255, 255, 255, .40);
      color: var(--ink);
      padding: 9px 14px;
      font-size: 15px;
      font-weight: 780;
      text-align: center;
      font-variant-numeric: tabular-nums;
    }

    .countdown-timer {
      min-width: 64px;
      border: 1px solid var(--line-strong);
      border-radius: 8px;
      background: rgba(255, 255, 255, .40);
      color: var(--muted);
      padding: 9px 12px;
      font-size: 14px;
      font-weight: 700;
      text-align: center;
      font-variant-numeric: tabular-nums;
      transition: color .3s ease, border-color .3s ease;
    }
    .countdown-timer.urgent {
      color: var(--danger);
      border-color: var(--danger);
      animation: pulse-urgent 1s ease-in-out infinite;
    }
    @keyframes pulse-urgent {
      0%, 100% { opacity: 1; }
      50% { opacity: .55; }
    }

    .toolbar-right-group {
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 9px;
    }

    .progress-panel {
      position: relative; z-index: 1;
      margin: 0;
      padding: 18px 22px 20px;
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, .18);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      animation: panelSlideIn .35s var(--ease-out-expo);
      overflow: hidden;
    }
    .progress-panel.celebrating {
      animation: panelCelebrate .55s var(--ease-out-expo);
    }
    @keyframes panelSlideIn {
      from { opacity: 0; transform: translateY(10px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes panelCelebrate {
      0% { box-shadow: inset 0 0 0 rgba(249,115,22,0), 0 0 0 rgba(249,115,22,0); }
      45% { box-shadow: inset 0 0 0 1px rgba(249,115,22,.32), 0 18px 55px rgba(249,115,22,.18); }
      100% { box-shadow: inset 0 0 0 rgba(249,115,22,0), 0 0 0 rgba(249,115,22,0); }
    }

    .progress-head {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: flex-start;
      margin-bottom: 10px;
    }

    .progress-title {
      margin: 0;
      color: var(--ink);
      font-size: 15px;
      font-weight: 750;
      display: flex; align-items: center; gap: 10px;
      font-family: var(--font-title);
      letter-spacing: 0;
    }

    .progress-dot {
      display: inline-block; width: 9px; height: 9px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 10px var(--accent-glow);
      animation: dotPulse 1.5s ease-in-out infinite;
    }
    .progress-dot.acquiring { background: var(--accent); }
    .progress-dot.parsing { background: var(--scholar); box-shadow: 0 0 10px rgba(13,148,136,.34); }
    .progress-dot.done { background: #4ec9b0; box-shadow: 0 0 10px rgba(78,201,176,.35); animation: none; }
    .progress-dot.error { background: var(--danger); box-shadow: 0 0 10px rgba(249,112,102,.35); animation: none; }
    @keyframes dotPulse {
      0%,100% { opacity:1; transform:scale(1); }
      50% { opacity:.5; transform:scale(1.3); }
    }

    .progress-current {
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
    }

    .progress-meta {
      color: var(--muted);
      font-size: 12px;
      text-align: right;
      font-variant-numeric: tabular-nums;
      line-height: 1.5;
    }
    .progress-meta .speed { color: var(--accent); font-weight: 680; }
    .progress-meta .eta { color: var(--muted); }

    /* ── Dual-color overall progress bar ── */
    .progress-bar {
      height: 10px; overflow: hidden;
      border: 1px solid var(--line-strong);
      border-radius: 999px;
      background: rgba(26, 45, 74, .06);
      position: relative; display: flex;
    }
    .progress-bar::after {
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,.42), transparent);
      transform: translateX(-120%);
      animation: progressSheen 1.7s ease-in-out infinite;
    }
    .progress-bar.idle::after,
    .progress-bar.complete::after {
      animation: none;
      opacity: 0;
    }
    @keyframes progressSheen {
      0% { transform: translateX(-120%); }
      100% { transform: translateX(120%); }
    }
    .progress-bar .seg-download {
      height: 100%;
      background: linear-gradient(90deg, #357abd, #4a90d9);
      transition: width .35s var(--ease-out-expo);
      position: relative;
    }
    .progress-bar .seg-parse {
      height: 100%;
      background: linear-gradient(90deg, #2ea88a, #4ec9b0);
      transition: width .35s var(--ease-out-expo);
      position: relative;
    }
    .progress-bar .seg-error {
      height: 100%;
      background: linear-gradient(90deg, #c0392b, #f97066);
      transition: width .35s var(--ease-out-expo);
    }
    .progress-bar .seg-done {
      height: 100%;
      background: linear-gradient(90deg, #4ec9b0, #6dd5c0);
      transition: width .35s var(--ease-out-expo);
    }

    /* ── Per-paper progress list ── */
    .progress-list {
      display: grid; gap: 8px;
      margin-top: 12px;
      max-height: 280px; overflow: auto; padding-right: 4px;
      scrollbar-width: thin;
    }
    .progress-list::-webkit-scrollbar { width: 5px; }
    .progress-list::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 3px; }

    .progress-item {
      display: grid;
      grid-template-columns: 26px minmax(0, 1fr);
      gap: 10px; align-items: start;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, .30);
      animation: itemSlideIn .28s var(--ease-out-expo) both;
      transition: border-color .22s ease, box-shadow .22s ease;
    }
    .progress-section-title {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 10px 2px 2px;
      color: var(--muted);
      font-size: 10px;
      font-weight: 750;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .progress-section-title::after {
      content: "";
      height: 1px;
      flex: 1;
      background: var(--line);
    }
    @keyframes itemSlideIn {
      from { opacity: 0; transform: translateX(-6px); }
      to { opacity: 1; transform: translateX(0); }
    }
    .progress-item.stage-downloading {
      border-color: rgba(74,144,217,.30);
      box-shadow: 0 0 18px var(--accent-glow);
    }
    .progress-item.stage-parsing {
      border-color: rgba(78,201,176,.28);
      box-shadow: 0 0 18px rgba(78,201,176,.20);
    }
    .progress-item.stage-completed {
      border-color: rgba(78,201,176,.18);
    }
    .progress-item.stage-error {
      border-color: rgba(249,112,102,.25);
      background: rgba(249,112,102,.06);
    }

    /* Stage icon */
    .progress-icon {
      width: 24px; height: 24px; flex-shrink: 0;
      display: grid; place-items: center;
      border-radius: 50%;
    }
    .progress-icon::before {
      content: "";
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: currentColor;
      box-shadow: 0 0 10px currentColor;
    }
    .progress-icon.queued   { color: var(--muted); }
    .progress-icon.downloading {
      color: var(--accent);
      animation: spinIcon 1.2s linear infinite;
    }
    .progress-icon.parsing {
      color: #4ec9b0;
      animation: spinIcon 1.6s linear infinite;
    }
    .progress-icon.completed { color: #4ec9b0; }
    .progress-icon.error     { color: var(--danger); }
    .progress-icon.skipped   { color: var(--muted); }
    @keyframes spinIcon {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }

    .progress-body { min-width: 0; }

    .progress-name strong {
      display: block;
      color: var(--ink);
      font-family: var(--font-title);
      font-weight: 600;
      font-size: 13px; line-height: 1.35; overflow-wrap: anywhere;
    }
    .progress-name .stage-label {
      display: inline-block; margin-top: 3px;
      color: var(--muted); font-size: 10px;
      font-weight: 650; text-transform: uppercase; letter-spacing: 0;
      padding: 1px 6px; border-radius: 4px;
      background: rgba(255,255,255,.25);
      border: 1px solid var(--line);
    }
    .progress-name .stage-label.downloading { color: var(--accent); border-color: rgba(74,144,217,.25); }
    .progress-name .stage-label.parsing { color: #2ea88a; border-color: rgba(78,201,176,.25); }
    .progress-name .stage-label.completed { color: #4ec9b0; }
    .progress-name .stage-label.error { color: var(--danger); }

    /* Mini progress bars (dual-phase) */
    .mini-bars {
      display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
      margin-top: 8px;
    }
    .mini-bar-wrap { min-width: 0; }
    .mini-bar-label {
      display: flex; justify-content: space-between;
      font-size: 10px; color: var(--muted);
      margin-bottom: 3px;
    }
    .mini-bar {
      height: 6px; overflow: hidden;
      border-radius: 999px;
      background: rgba(26,45,74,.08);
      border: 1px solid var(--line);
    }
    .mini-bar-fill {
      height: 100%; border-radius: inherit;
      transition: width .35s ease;
    }
    .mini-bar-fill.dl  { background: linear-gradient(90deg, #357abd, #5b9bd5); }
    .mini-bar-fill.pr  { background: linear-gradient(90deg, #2ea88a, #4ec9b0); }

    .download-skeleton {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }
    .download-step {
      position: relative;
      min-height: 56px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, .26);
      padding: 10px 12px;
      color: var(--muted);
      font-size: 11px;
    }
    .download-step strong {
      display: block;
      margin-bottom: 3px;
      color: var(--ink);
      font-family: var(--font-title);
      font-size: 14px;
      font-weight: 600;
    }
    .download-step::after {
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,.32), transparent);
      transform: translateX(-120%);
      animation: progressSheen 1.6s ease-in-out infinite;
    }
    .download-step:nth-child(2)::after { animation-delay: .18s; }
    .download-step:nth-child(3)::after { animation-delay: .34s; }
    .download-summary {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }
    .summary-metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,.30);
      padding: 10px 12px;
    }
    .summary-metric b {
      display: block;
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .summary-metric strong {
      display: block;
      margin-top: 2px;
      color: var(--ink);
      font-family: var(--font-mono);
      font-size: 18px;
      font-weight: 750;
    }
    .celebration-burst {
      position: absolute;
      inset: 0;
      pointer-events: none;
      overflow: hidden;
    }
    .celebration-burst span {
      position: absolute;
      left: var(--x);
      top: var(--y);
      width: 7px;
      height: 7px;
      border-radius: 2px;
      background: var(--c);
      opacity: 0;
      transform: translate(-50%, -50%) scale(.45);
      animation: celebratePop .9s var(--ease-out-expo) forwards;
      animation-delay: var(--d);
    }
    @keyframes celebratePop {
      0% { opacity: 0; transform: translate(-50%, -50%) scale(.45) rotate(0deg); }
      14% { opacity: 1; }
      100% { opacity: 0; transform: translate(calc(-50% + var(--tx)), calc(-50% + var(--ty))) scale(1) rotate(180deg); }
    }

    .empty {
      padding: 34px 20px;
      color: var(--muted);
      text-align: center;
    }

    @media (max-width: 1100px) {
      .shell { width: min(100%, 960px); min-width: 0; }
      .meta-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }

    @container (max-width: 720px) {
      header {
        display: grid;
        gap: 10px;
      }
      .count {
        width: fit-content;
        max-width: 100%;
        white-space: normal;
      }
      .list {
        max-height: min(64vh, 620px);
      }
      .meta-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .url-field {
        grid-column: 1 / -1;
      }
      .toolbar {
        align-items: stretch;
      }
      .status {
        width: 100%;
        margin-left: 0;
        order: 10;
      }
    }

    @container (max-width: 520px) {
      .paper {
        grid-template-columns: 22px minmax(0, 1fr);
      }
      .meta-grid {
        grid-template-columns: 1fr;
      }
      .url-field {
        grid-column: auto;
      }
      button {
        flex: 1 1 46%;
        padding-inline: 10px;
      }
      button.primary {
        flex-basis: 100%;
      }
      .selection-count {
        min-width: 62px;
        padding-inline: 10px;
      }
      .countdown-timer {
        min-width: 52px;
        padding-inline: 8px;
        font-size: 13px;
      }
      .download-skeleton,
      .download-summary,
      .mini-bars {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 640px) {
      main { padding: 10px; }
      header { display: grid; }
      .toolbar { align-items: stretch; }
      .status { width: 100%; margin-left: 0; }
      .meta-grid { grid-template-columns: 1fr; }
      .url-field { grid-column: auto; }
      .download-skeleton,
      .download-summary,
      .mini-bars { grid-template-columns: 1fr; }
    }

    @media (prefers-reduced-motion: reduce) {
      *,
      *::before,
      *::after {
        animation-duration: 1ms !important;
        transition-duration: 1ms !important;
        scroll-behavior: auto !important;
      }
    }
  </style>
</head>
<body>
  <script>/*__EXT_APPS_BUNDLE__*/</script>
  <main>
    <section class="shell" aria-labelledby="paper-selector-title">
      <header>
        <h1 id="paper-selector-title">Paper selector</h1>
        <div class="count" id="count"></div>
      </header>
      <form id="form">
        <div class="list" id="list"></div>
        <div class="toolbar">
          <button type="submit" class="primary" id="parse">Parse selected with MinerU</button>
          <button type="button" id="skip-mineru" hidden>Skip MinerU</button>
          <button type="button" id="select-all">All</button>
          <button type="button" id="clear">Clear</button>
          <div class="status" id="status"></div>
          <div class="toolbar-right-group">
            <div class="countdown-timer" id="countdown-timer" hidden></div>
            <div class="selection-count" id="selection-count">0/0</div>
          </div>
        </div>
      </form>
      <section class="decision-panel" id="decision-panel" hidden>
        <strong>MinerU parsing is optional</strong>
        <span id="decision-text">PDFs were saved. Choose whether to start MinerU parsing.</span>
      </section>
      <section class="progress-panel" id="progress-panel" hidden>
        <div class="progress-head">
          <div>
            <p class="progress-title" id="progress-title">
              <span class="progress-dot" id="progress-dot"></span>
              <span id="progress-title-text">Starting</span>
            </p>
            <div class="progress-current" id="progress-current"></div>
          </div>
          <div class="progress-meta" id="progress-meta"></div>
        </div>
        <div class="progress-bar" aria-hidden="true">
          <span class="seg-download" id="seg-download" style="width:0%"></span>
          <span class="seg-parse" id="seg-parse" style="width:0%"></span>
          <span class="seg-error" id="seg-error" style="width:0%"></span>
          <span class="seg-done" id="seg-done" style="width:0%"></span>
        </div>
        <div class="progress-list" id="progress-list"></div>
        <div class="celebration-burst" id="celebration-burst" hidden></div>
      </section>
    </section>
  </main>
  <script>
    const list = document.getElementById("list");
    const count = document.getElementById("count");
    const form = document.getElementById("form");
    const parseButton = document.getElementById("parse");
    const skipButton = document.getElementById("skip-mineru");
    const selectAllButton = document.getElementById("select-all");
    const clearButton = document.getElementById("clear");
    const statusNode = document.getElementById("status");
    const countdownNode = document.getElementById("countdown-timer");
    const selectionCountNode = document.getElementById("selection-count");
    const decisionPanel = document.getElementById("decision-panel");
    const decisionText = document.getElementById("decision-text");
    const progressPanel = document.getElementById("progress-panel");
    const progressTitleText = document.getElementById("progress-title-text");
    const progressCurrent = document.getElementById("progress-current");
    const progressMeta = document.getElementById("progress-meta");
    const progressDot = document.getElementById("progress-dot");
    const segDownload = document.getElementById("seg-download");
    const segParse = document.getElementById("seg-parse");
    const segError = document.getElementById("seg-error");
    const segDone = document.getElementById("seg-done");
    const progressList = document.getElementById("progress-list");
    const celebrationBurst = document.getElementById("celebration-burst");
    let rpcId = 1;
    const pending = new Map();
    let pollTimer = null;
    let eventSource = null;
    let countdownTimer = null;
    let selectionTimeoutTimer = null;
    let downloadTimer = null;
    let jobStartEpoch = 0;
    let parseDecision = null;
    let data = {};  // populated by initApp() via feature detection
    const clientInstanceId = ((window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : String(Date.now()) + Math.random())).replace(/[^a-zA-Z0-9._-]/g, "");
    let restoreAttempted = false;
    let saveStateTimer = null;

    /* ── Stage icon map ── */
    const STAGE_CLASS = {
      queued: 'queued', downloading: 'downloading', ready: 'queued',
      parsing: 'parsing', completed: 'completed', error: 'error', skipped: 'skipped',
    };

    function unwrapToolOutput(value) {
      if (value?.result && typeof value.result === "object" && !Array.isArray(value.result)) {
        return value.result;
      }
      return value || {};
    }

    function normalizeSelectionData(value) {
      const root = value && typeof value === "object" ? value : {};
      const prompt = root.parse_prompt && typeof root.parse_prompt === "object" ? root.parse_prompt : null;
      if (prompt && Array.isArray(prompt.papers) && prompt.selection_token) {
        const needsSelection = !!prompt.parse_decision_required
          || prompt.recommended_tool === "render_paper_selection_app"
          || prompt.interaction === "backend_session_numbered_selection"
          || prompt.interaction === "mcp_app";
        return {
          ...root,
          ...prompt,
          save_path: prompt.save_path || root.save_path,
          use_scihub: prompt.use_scihub ?? root.use_scihub,
          mode: prompt.mode || root.mode,
          backend: prompt.backend ?? root.backend,
          force: prompt.force ?? root.force,
          custom_save_path_confirmed: prompt.custom_save_path_confirmed ?? root.custom_save_path_confirmed,
          selection_not_required: !needsSelection,
        };
      }
      return root;
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;",
      })[ch]);
    }

    function setStatus(message, kind = "") {
      statusNode.textContent = message || "";
      statusNode.className = kind ? `status ${kind}` : "status";
    }

    function selectedIndices() {
      return Array.from(document.querySelectorAll('input[name="paper"]:checked')).map((item) => item.value);
    }

    function currentSelectionRevision() {
      return String(data.persisted_selection?.selection_revision || data.selection_revision || "");
    }

    function selectionStorageKey() {
      return "paper-search-selection:" + String(data.selection_token || "");
    }

    function applySelectedIndices(indices, options = {}) {
      const selected = new Set((Array.isArray(indices) ? indices : []).map((value) => String(value)));
      document.querySelectorAll('input[name="paper"]').forEach((item) => {
        item.checked = selected.has(String(item.value));
        if (options.locked) item.disabled = true;
      });
      if (options.locked) {
        selectAllButton.disabled = true;
        clearButton.disabled = true;
      }
      updateSelectionCount();
    }

    function readLocalSelectionState() {
      if (!data.selection_token) return [];
      try {
        const raw = window.localStorage?.getItem(selectionStorageKey());
        const parsed = raw ? JSON.parse(raw) : null;
        const revision = currentSelectionRevision();
        if (revision && parsed?.selection_revision && String(parsed.selection_revision) !== revision) {
          window.localStorage?.removeItem(selectionStorageKey());
          return [];
        }
        return Array.isArray(parsed?.selected_indices) ? parsed.selected_indices : [];
      } catch (_) {
        return [];
      }
    }

    function writeLocalSelectionState(indices) {
      if (!data.selection_token) return;
      try {
        window.localStorage?.setItem(selectionStorageKey(), JSON.stringify({
          selected_indices: indices,
          selection_revision: currentSelectionRevision(),
          updated_at: new Date().toISOString(),
        }));
      } catch (_) {}
    }

    function scheduleSaveSelectionState() {
      if (data.persisted_selection?.large_batch_selection_satisfied) return;
      const indices = selectedIndices();
      writeLocalSelectionState(indices);
      if (!data.selection_token) return;
      if (saveStateTimer) window.clearTimeout(saveStateTimer);
      saveStateTimer = window.setTimeout(async () => {
        try {
          await callTool("save_paper_selection_state", {
            selection_token: data.selection_token || "",
            selected_indices: indices.join(","),
            client_instance_id: clientInstanceId,
            selection_revision: currentSelectionRevision(),
          });
        } catch (_) {}
      }, 180);
    }

    function applyPersistedSelection() {
      const persisted = data.persisted_selection && typeof data.persisted_selection === "object"
        ? data.persisted_selection
        : null;
      if (!persisted) return false;
      const indices = Array.isArray(persisted.selected_indices) ? persisted.selected_indices : [];
      if (!indices.length && !persisted.has_saved_state) return false;
      applySelectedIndices(indices, { locked: !!persisted.large_batch_selection_satisfied });
      if (persisted.has_saved_state) {
        writeLocalSelectionState(indices);
      }
      if (persisted.large_batch_selection_satisfied) {
        setStatus("Restored confirmed paper selection.", "success");
      }
      return true;
    }

    async function restoreSelectionState() {
      if (restoreAttempted || !data.selection_token || data.selection_not_required) return;
      restoreAttempted = true;
      if (data.persisted_selection?.large_batch_selection_satisfied) return;
      const localIndices = readLocalSelectionState();
      if (localIndices.length) applySelectedIndices(localIndices);
      try {
        const result = await callTool("get_paper_selection_state", {
          selection_token: data.selection_token || "",
        });
        const body = structured(result);
        if (body?.large_batch_selection_satisfied) {
          data.persisted_selection = body;
          applySelectedIndices(body.selected_indices || [], { locked: true });
          setStatus("Restored confirmed paper selection.", "success");
        } else if (
          body?.has_saved_state
          && (!currentSelectionRevision() || String(body.selection_revision || "") === currentSelectionRevision())
        ) {
          data.persisted_selection = body;
          applySelectedIndices(body.selected_indices || []);
          writeLocalSelectionState(body.selected_indices || []);
        } else if (
          Array.isArray(body?.selected_indices)
          && body.selected_indices.length
          && (!currentSelectionRevision() || String(body.selection_revision || "") === currentSelectionRevision())
        ) {
          applySelectedIndices(body.selected_indices);
        }
      } catch (_) {}
    }

    function updateSelectionCount() {
      const all = Array.from(document.querySelectorAll('input[name="paper"]'));
      const selected = all.filter((item) => item.checked);
      selectionCountNode.textContent = `${selected.length}/${all.length}`;
    }

    function fieldValue(value) {
      const text = String(value ?? "").trim();
      return text || "Not available";
    }

    function originalUrl(paper) {
      return String(paper.original_url || paper.url || paper.pdf_url || "").trim();
    }

    function renderField(label, value, extraClass = "") {
      return `<span class="${extraClass}"><b>${escapeHtml(label)}</b><em>${escapeHtml(fieldValue(value))}</em></span>`;
    }

    function renderUrlField(paper) {
      const url = originalUrl(paper);
      const body = url
        ? `<button class="paper-link open-paper-link" type="button" data-paper-index="${escapeHtml(paper.index)}" data-url-kind="paper">${escapeHtml(url)}</button>`
        : '<span class="muted-value">Not available</span>';
      return `<span class="url-field"><b>Original URL</b><em>${body}</em></span>`;
    }

    function render() {
      const downloadOnly = data.selection_semantics === "download_selected_only";
      const downloadAndParse = data.selection_semantics === "download_and_parse_selected_only";
      parseButton.textContent = parseDecision
        ? "Parse with MinerU"
        : (downloadOnly || downloadAndParse)
        ? "Download selected"
        : "Parse selected with MinerU";
      skipButton.hidden = !parseDecision;
      decisionPanel.hidden = !parseDecision;
      if (data.selection_not_required) {
        count.textContent = "";
        parseButton.disabled = true;
        skipButton.disabled = true;
        selectAllButton.disabled = true;
        clearButton.disabled = true;
        updateSelectionCount();
        list.innerHTML = `<div class="empty">${escapeHtml(data.message || "No paper selection is required for this tool call.")}</div>`;
        updateSelectionCount();
        return;
      }

      const papers = Array.isArray(data.papers) ? data.papers : [];
      const ready = papers.filter((paper) => paper.parse_ready !== false);
      const fullTotal = Number(data.full_total || data.raw_total || data.total || papers.length);
      const requested = Number(data.requested_count || 0);
      const scopeLabel = fullTotal > papers.length
        ? `${papers.length}/${fullTotal} candidates`
        : `${ready.length}/${papers.length} ready`;
      count.textContent = requested > 0 && fullTotal > papers.length
        ? `${scopeLabel} | requested ${requested}`
        : scopeLabel;
      parseButton.disabled = ready.length === 0;
      skipButton.disabled = !parseDecision;
      selectAllButton.disabled = ready.length === 0;
      clearButton.disabled = ready.length === 0;

      if (!papers.length) {
        list.innerHTML = '<div class="empty">No papers available.</div>';
        updateSelectionCount();
        return;
      }

      list.innerHTML = papers.map((paper) => {
        const index = Number(paper.index);
        const disabled = paper.parse_ready === false || !Number.isFinite(index);
        return `
          <label class="paper${disabled ? " disabled" : ""}">
            <input type="checkbox" name="paper" value="${escapeHtml(index)}" ${disabled ? "disabled" : ""}>
            <span class="paper-body">
              <span class="title"><span class="index">${escapeHtml(index)}.</span> ${escapeHtml(paper.title || "Untitled")}</span>
              <span class="meta-grid">
                ${renderField("Published", paper.published_date || paper.year)}
                ${renderField("Journal / Venue", paper.publication_venue)}
                ${renderField("Source", paper.source || "unknown")}
                ${renderField("Paper ID", paper.paper_id)}
                ${renderField("DOI", paper.doi)}
                ${renderUrlField(paper)}
              </span>
            </span>
          </label>
        `;
      }).join("");
      updateSelectionCount();
      applyPersistedSelection();
      restoreSelectionState();
    }

    function rpcRequest(method, params) {
      const id = rpcId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
        window.setTimeout(() => {
          if (!pending.has(id)) return;
          pending.delete(id);
          reject(new Error("Timed out waiting for host response."));
        }, 120000);
      });
    }

    async function callTool(name, args) {
      /* ── Priority 1: standard MCP Apps App class (Claude Desktop) ── */
      if (app && app.callServerTool) {
        return app.callServerTool({ name, arguments: args });
      }
      /* ── Priority 2: window.openai (Codex / OpenAI) ── */
      if (window.openai?.callTool) {
        return window.openai.callTool(name, args);
      }
      /* ── Priority 3: postMessage JSON-RPC fallback ── */
      return rpcRequest("tools/call", { name, arguments: args });
    }

    function structured(result) {
      const body = result?.structuredContent || result?.structured_content || result;
      return body?.result || body;
    }

    function terminalJob(status) {
      return ["completed", "error", "canceled", "not_found", "invalid_selection"].includes(String(status || "").toLowerCase());
    }


    function setSegments(downloadPct, parsePct, errorPct = 0, donePct = 0) {
      segDownload.style.width = Math.max(0, Math.min(100, downloadPct)) + "%";
      segParse.style.width = Math.max(0, Math.min(100, parsePct)) + "%";
      segError.style.width = Math.max(0, Math.min(100, errorPct)) + "%";
      segDone.style.width = Math.max(0, Math.min(100, donePct)) + "%";
      const bar = segDownload.parentElement;
      const total = downloadPct + parsePct + errorPct + donePct;
      if (bar) {
        bar.className = "progress-bar" + (total <= 0 ? " idle" : (total >= 99 && !errorPct ? " complete" : ""));
      }
    }

    function celebrateDownload() {
      if (!celebrationBurst) return;
      const colors = ["#0d9488", "#2dd4bf", "#f97316", "#357abd", "#4ec9b0"];
      celebrationBurst.innerHTML = Array.from({ length: 22 }, (_, index) => {
        const angle = (Math.PI * 2 * index) / 22;
        const radius = 52 + (index % 5) * 12;
        const tx = Math.cos(angle) * radius;
        const ty = Math.sin(angle) * radius;
        const x = 50 + Math.cos(angle) * 10;
        const y = 40 + Math.sin(angle) * 8;
        return `<span style="--x:${x}%;--y:${y}%;--tx:${tx}px;--ty:${ty}px;--c:${colors[index % colors.length]};--d:${(index % 6) * 34}ms"></span>`;
      }).join("");
      celebrationBurst.hidden = false;
      progressPanel.classList.add("celebrating");
      window.setTimeout(() => {
        celebrationBurst.hidden = true;
        celebrationBurst.innerHTML = "";
        progressPanel.classList.remove("celebrating");
      }, 1200);
    }

    function startDownloadProgress(total) {
      if (downloadTimer) window.clearInterval(downloadTimer);
      let pct = 0;
      const stages = [
        [8, "Preparing selected papers"],
        [24, "Resolving PDF routes"],
        [48, "Downloading papers"],
        [76, "Validating PDF files"],
        [90, "Writing manifest"],
      ];
      progressPanel.hidden = false;
      progressPanel.classList.remove("celebrating");
      progressDot.className = "progress-dot acquiring";
      progressTitleText.textContent = "Downloading papers";
      progressCurrent.textContent = "Preparing selected papers.";
      progressMeta.textContent = "0% | " + total + " selected";
      setSegments(0, 0);
      progressList.innerHTML = `
        <div class="download-skeleton" aria-live="polite">
          <div class="download-step"><strong>Resolve</strong><span>Checking source and PDF routes.</span></div>
          <div class="download-step"><strong>Acquire</strong><span>Saving selected PDFs.</span></div>
          <div class="download-step"><strong>Verify</strong><span>Validating files and manifest.</span></div>
        </div>`;
      downloadTimer = window.setInterval(() => {
        pct = Math.min(94, pct + Math.max(1, Math.round((96 - pct) * 0.08)));
        const stage = stages.filter(([mark]) => pct >= mark).pop();
        progressCurrent.textContent = (stage ? stage[1] : "Preparing selected papers") + ".";
        progressMeta.textContent = pct + "% | " + total + " selected";
        setSegments(pct, 0);
      }, 420);
    }

    function finishDownloadProgress(body) {
      if (downloadTimer) window.clearInterval(downloadTimer);
      downloadTimer = null;
      progressPanel.hidden = false;
      progressDot.className = "progress-dot done";
      progressTitleText.textContent = "Download complete";
      progressCurrent.textContent = body?.manifest_path ? "Manifest: " + body.manifest_path : "PDF files were saved.";
      progressMeta.textContent = "Saved " + (body?.total || 0) + " PDFs";
      setSegments(100, 0, body?.failed ? Math.min(100, Number(body.failed) * 8) : 0, body?.failed ? 0 : 100);
      progressList.innerHTML = `
        <div class="download-summary">
          <div class="summary-metric"><b>Downloaded</b><strong>${escapeHtml(body?.downloaded || 0)}</strong></div>
          <div class="summary-metric"><b>Existing</b><strong>${escapeHtml(body?.skipped_existing || 0)}</strong></div>
          <div class="summary-metric"><b>Failed</b><strong>${escapeHtml(body?.failed || 0)}</strong></div>
        </div>`;
      if (!body?.failed) celebrateDownload();
    }

    function clearCountdown() {
      if (countdownTimer) window.clearInterval(countdownTimer);
      countdownTimer = null;
    }

    function formatCountdownDisplay(seconds) {
      if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
      const m = Math.floor(seconds / 60);
      const s = Math.floor(seconds % 60);
      return m + ":" + String(s).padStart(2, "0");
    }

    function updateCountdownDisplay(remaining) {
      countdownNode.textContent = formatCountdownDisplay(remaining);
      countdownNode.setAttribute("title", Math.max(0, Math.ceil(remaining)) + " seconds remaining");
    }

    function renderTerminalNoParse(body) {
      clearCountdown();
      parseDecision = null;
      skipButton.hidden = true;
      decisionPanel.hidden = false;
      parseButton.disabled = true;
      skipButton.disabled = true;
      decisionText.textContent = body?.message || "PDFs were saved. MinerU parsing was not started.";
      setStatus(decisionText.textContent, "success");
    }

    function isParseReadyPrompt(prompt) {
      if (!prompt || typeof prompt !== "object") return false;
      if (!prompt.selection_token) return false;
      const terminalStatus = String(prompt.status || "").toLowerCase();
      if (["timed_out_no_parse", "completed_no_parse"].includes(terminalStatus)) return false;
      const recommended = String(prompt.default_parse_selected_indices || prompt.recommended_selected_indices || "").trim();
      const papers = Array.isArray(prompt.papers) ? prompt.papers : [];
      const hasReadyPaper = papers.some((paper) => paper?.parse_ready !== false);
      return Boolean(prompt.parse_decision_required)
        || Boolean(prompt.requires_user_parse_decision)
        || Number(prompt.parse_ready_total || 0) > 0
        || Boolean(recommended)
        || hasReadyPaper;
    }

    async function dismissParsePrompt(reason) {
      const prompt = parseDecision || data;
      const token = prompt.download_selection_token || prompt.selection_token || "";
      const result = await callTool("dismiss_parse_prompt", {
        selection_token: token,
        prompt_id: prompt.prompt_id || "",
        reason: reason || "timeout",
      });
      const body = structured(result);
      renderTerminalNoParse(body);
      return body;
    }

    function startParseDecision(prompt) {
      parseDecision = prompt || data;
      data = normalizeSelectionData({ parse_prompt: parseDecision });
      render();
      document.querySelectorAll('input[name="paper"]:not(:disabled)').forEach((item) => {
        item.checked = true;
      });
      updateSelectionCount();
      const timeoutSeconds = Math.max(1, Number(parseDecision.timeout_seconds || 120));
      let remaining = timeoutSeconds;
      decisionText.textContent = parseDecision.timeout_message || "MinerU parsing is optional. If no action is taken, this prompt will close and keep the PDFs only.";
      clearCountdown();
      countdownNode.hidden = false;
      updateCountdownDisplay(remaining);
      countdownTimer = window.setInterval(async () => {
        remaining -= 1;
        updateCountdownDisplay(remaining);
        if (remaining <= 0) {
          clearCountdown();
          parseButton.disabled = true;
          skipButton.disabled = true;
          try {
            await dismissParsePrompt("timeout");
          } catch (error) {
            setStatus(error?.message || String(error), "error");
          }
        }
      }, 1000);
    }

    /* ── ETA helper ── */
    function formatETA(seconds) {
      if (!Number.isFinite(seconds) || seconds <= 0) return "";
      if (seconds < 60) return `~${Math.round(seconds)}s`;
      const m = Math.floor(seconds / 60);
      const s = Math.round(seconds % 60);
      return `~${m}m ${s}s`;
    }

    function formatDuration(s) {
      if (!Number.isFinite(s) || s <= 0) return "";
      if (s < 1) return `${Math.round(s*1000)}ms`;
      if (s < 60) return `${s.toFixed(1)}s`;
      const m = Math.floor(s/60);
      const sec = Math.round(s%60);
      return `${m}m ${sec}s`;
    }

    /* ── Dual-phase progress render ── */
    function renderProgress(job) {
      if (!job || typeof job !== "object") return;
      const total = Number(job.total || 0);
      const done = Number(job.completed_items || 0);
      const parsed = Number(job.parsed || 0);
      const failed = Number(job.failed || 0);
      const skipped = Number(job.skipped || 0);
      const dlCount = Number(job.phase_downloading || job.downloading || 0);
      const prCount = Number(job.phase_parsing || job.parsing || 0);
      const errCount = Number(job.phase_error || 0);
      const doneCount = Number(job.phase_completed || 0);
      const status = String(job.status || "running");
      const isDone = status === "completed";
      const isError = status === "error";

      progressPanel.hidden = false;
      progressTitleText.textContent = isDone ? 'Parsing Complete'
        : (isError ? 'Job Interrupted' : 'Processing Papers');

      progressDot.className = 'progress-dot'
        + (isDone ? ' done' : (isError ? ' error' : ' parsing'));

      progressCurrent.textContent = job.current || job.message || (job.job_id ? 'Job ' + job.job_id : '');

      /* Meta line: percentage + speed/ETA */
      const elapsed = jobStartEpoch ? (Date.now()/1000 - jobStartEpoch) : 0;
      const speed = elapsed > 0 && done > 0 ? (done / elapsed).toFixed(1) + ' papers/s' : '';
      const remaining = speed && done < total ? (total - done) / parseFloat(speed) : 0;
      const etaStr = formatETA(remaining);
      progressMeta.innerHTML = [
        (speed ? `<span class="speed">${speed}</span>` : ''),
        (etaStr ? `<span class="eta">${etaStr} remaining</span>` : ''),
        `<span>${done}/${total} done</span>`,
      ].filter(Boolean).join(' | ');

      const t = Math.max(1, total);
      setSegments(
        dlCount / t * 100,
        prCount / t * 100,
        errCount / t * 100,
        doneCount / t * 100,
      );

      const items = Array.isArray(job.items) ? job.items : [];
      const renderItem = (item, idx) => {
        const stage = item.stage || 'queued';
        const cls = STAGE_CLASS[stage] || 'queued';
        const title = item.title || ('Paper ' + (item.index || ''));
        const stLabel = item.status || stage;
        const detail = item.message || '';

        /* Dual mini-bars: download phase + parse phase */
        const isDownloadPhase = stage === 'downloading' || stage === 'ready';
        const isParsePhase = stage === 'parsing';
        const dlDone = stage === 'completed' || stage === 'parsing' || stage === 'ready';
        const prDone = stage === 'completed';
        const dlPct = stage === 'queued' ? 0 : (dlDone ? 100 : (isDownloadPhase ? (Number(item.progress_percent) || 0) : 100));
        const prPct = prDone ? 100 : (isParsePhase ? (Number(item.progress_percent) || 0) : 0);

        /* Timing: per-paper elapsed */
        const dlStart = Number(item.download_started_epoch || 0);
        const prStart = Number(item.parse_started_epoch || 0);
        const now = Date.now() / 1000;
        const dlElapsed = dlStart ? formatDuration(now - dlStart) : '';
        const prElapsed = prStart ? formatDuration(now - prStart) : '';

        const labelCls = `stage-label ${stage}`;
        return `
          <div class="progress-item stage-${stage}" style="animation-delay:${idx*40}ms">
            <span class="progress-icon ${cls}" aria-hidden="true"></span>
            <div class="progress-body">
              <div class="progress-name">
                <strong>${escapeHtml(item.index ? item.index + '. ' + title : title)}</strong>
                <span class="${labelCls}">${escapeHtml(stLabel)}${detail ? ' | ' + escapeHtml(detail) : ''}</span>
              </div>
              <div class="mini-bars">
                <div class="mini-bar-wrap">
                  <div class="mini-bar-label"><span>Download</span><span>${dlElapsed ? dlElapsed + ' ' : ''}${Math.round(dlPct)}%</span></div>
                  <div class="mini-bar"><div class="mini-bar-fill dl" style="width:${dlPct}%"></div></div>
                </div>
                <div class="mini-bar-wrap">
                  <div class="mini-bar-label"><span>Parse</span><span>${prElapsed ? prElapsed + ' ' : ''}${Math.round(prPct)}%</span></div>
                  <div class="mini-bar"><div class="mini-bar-fill pr" style="width:${prPct}%"></div></div>
                </div>
              </div>
            </div>
          </div>
        `;
      };
      const normalizedStage = (item) => String(item.stage || item.status || "queued").toLowerCase();
      const groups = [
        ["Active", items.filter((item) => ["downloading", "parsing", "ready"].includes(normalizedStage(item)))],
        ["Queued", items.filter((item) => ["", "queued"].includes(normalizedStage(item)))],
        ["Completed", items.filter((item) => normalizedStage(item) === "completed")],
        ["Attention", items.filter((item) => ["error", "failed", "skipped"].includes(normalizedStage(item)))],
      ].filter(([, groupItems]) => groupItems.length);
      progressList.innerHTML = groups.map(([groupName, groupItems]) => `
        <div class="progress-section-title">${escapeHtml(groupName)}</div>
        ${groupItems.map(renderItem).join('')}
      `).join('');

      if (terminalJob(status)) {
        parseButton.disabled = false;
        if (isDone) {
          setStatus('All ' + parsed + ' papers parsed successfully!', 'success');
          if (!failed) celebrateDownload();
        } else {
          setStatus(job.message || ('Job ' + status + '.'), 'error');
        }
      }
    }

    /* ── SSE stream with polling fallback ── */
    function connectSSE(jobId) {
      if (!jobId || typeof EventSource === 'undefined') {
        /* Fallback to enhanced polling (1s interval) */
        pollJobFallback(jobId);
        return;
      }
      const url = '/api/progress-stream/' + encodeURIComponent(jobId);
      eventSource = new EventSource(url);
      eventSource.onmessage = (e) => {
        try {
          const job = JSON.parse(e.data);
          renderProgress(job);
        } catch (_) {}
      };
      eventSource.addEventListener('done', () => {
        if (eventSource) { eventSource.close(); eventSource = null; }
      });
      eventSource.onerror = () => {
        if (eventSource) { eventSource.close(); eventSource = null; }
        /* Fallback to polling on SSE failure */
        pollJobFallback(jobId);
      };
    }

    function disconnectSSE() {
      if (eventSource) { eventSource.close(); eventSource = null; }
      if (pollTimer) { window.clearTimeout(pollTimer); pollTimer = null; }
    }

    async function pollJobFallback(jobId) {
      if (!jobId) return;
      try {
        const result = await callTool("get_parse_job_status", { job_id: jobId });
        const body = structured(result);
        renderProgress(body);
        if (!terminalJob(body?.status)) {
          pollTimer = window.setTimeout(() => pollJobFallback(jobId), 1000);
        }
      } catch (error) {
        setStatus(error?.message || String(error), "error");
        parseButton.disabled = false;
      }
    }

    function clearSelectionTimeout() {
      if (selectionTimeoutTimer) window.clearInterval(selectionTimeoutTimer);
      selectionTimeoutTimer = null;
      if (!parseDecision) {
        countdownNode.hidden = true;
        countdownNode.classList.remove("urgent");
      }
    }

    async function expireDownloadSelection() {
      clearSelectionTimeout();
      countdownNode.hidden = true;
      countdownNode.classList.remove("urgent");
      const isDownloadSelection = data.selection_semantics === "download_selected_only"
        || data.selection_semantics === "download_and_parse_selected_only";
      if (!isDownloadSelection || parseButton.disabled) return;
      parseButton.disabled = true;
      selectAllButton.disabled = true;
      clearButton.disabled = true;
      try {
        await callTool("delete_search_session", { selection_token: data.selection_token || "" });
      } catch (_) {}
      setStatus("Selection expired. No PDFs were downloaded.", "error");
      decisionPanel.hidden = false;
      decisionText.textContent = "Selection expired. Start a new search to download papers.";
    }

    function startHiddenSelectionTimeout() {
      clearSelectionTimeout();
      const isDownloadSelection = data.selection_semantics === "download_selected_only"
        || data.selection_semantics === "download_and_parse_selected_only";
      if (!isDownloadSelection || data.selection_not_required) return;
      const timeoutSeconds = Math.max(1, Number(data.selection_timeout_seconds || data.timeout_seconds || 120));
      let remaining = timeoutSeconds;
      countdownNode.hidden = false;
      updateCountdownDisplay(remaining);
      selectionTimeoutTimer = window.setInterval(() => {
        remaining -= 1;
        updateCountdownDisplay(remaining);
        if (remaining <= 30) {
          countdownNode.classList.add("urgent");
        }
        if (remaining <= 0) {
          expireDownloadSelection();
        }
      }, 1000);
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const selected = selectedIndices();
      if (!selected.length) {
        setStatus("Select at least one paper.", "error");
        return;
      }

      parseButton.disabled = true;
      skipButton.disabled = true;
      disconnectSSE();
      clearCountdown();
      clearSelectionTimeout();
      const downloadOnly = data.selection_semantics === "download_selected_only";
      const downloadAndParse = data.selection_semantics === "download_and_parse_selected_only";
      setStatus((downloadOnly || downloadAndParse) ? "Downloading selected papers..." : "Submitting parse job...");
      progressPanel.hidden = false;
      progressTitleText.textContent = (downloadOnly || downloadAndParse) ? "Download Started" : "Processing Papers";
      progressDot.className = "progress-dot";
      progressCurrent.textContent = (downloadOnly || downloadAndParse) ? "Preparing downloads." : "Submitting selected papers.";
      progressMeta.innerHTML = "";
      setSegments(0, 0);
      progressList.innerHTML = "";
      jobStartEpoch = Date.now() / 1000;
      if (downloadOnly || downloadAndParse) startDownloadProgress(selected.length);
      try {
        const commonArgs = {
          selection_token: data.selection_token || "",
          selected_indices: selected.join(","),
          save_path: data.save_path || "~/Desktop/papers",
          use_scihub: !!data.use_scihub,
          mode: data.mode || "auto",
          backend: data.backend || "",
          force: !!data.force,
          custom_save_path_confirmed: !!data.custom_save_path_confirmed,
        };
        const result = (downloadOnly || downloadAndParse)
          ? await callTool("download_confirmed_paper_selection", {
              ...commonArgs,
              parse_execution: "none",
            })
          : await callTool("submit_parse_job", commonArgs);
        const body = structured(result);
        if ((downloadOnly || downloadAndParse) && body?.status && body.status !== "invalid_selection" && body.status !== "not_found") {
          data.persisted_selection = {
            selection_token: data.selection_token || "",
            selected_indices: selected.map((value) => Number(value)).filter((value) => Number.isFinite(value)),
            selected_indices_arg: selected.join(","),
            confirmed_selected_indices: selected.join(","),
            large_batch_selection_satisfied: true,
            selection_revision: currentSelectionRevision(),
            submitted: true,
          };
          writeLocalSelectionState(selected);
        }
        if (downloadOnly || downloadAndParse) {
          finishDownloadProgress(body);
          const prompt = body?.parse_prompt && typeof body.parse_prompt === "object" ? body.parse_prompt : null;
          const terminalStatus = String(prompt?.status || body?.status || "").toLowerCase();
          if (["timed_out_no_parse", "completed_no_parse"].includes(terminalStatus)) {
            renderTerminalNoParse(prompt || body);
          } else if (isParseReadyPrompt(prompt)) {
            startParseDecision(prompt);
          } else {
            setStatus(body?.message || `Downloaded ${body?.downloaded || 0} paper(s).`, body?.failed ? "error" : "success");
            parseButton.disabled = false;
          }
          return;
        }
        const jobId = body?.job_id || "";
        renderProgress(body);
        if (jobId) {
          setStatus(`Processing: ${jobId}`);
          connectSSE(jobId);
        } else {
          setStatus(body?.message || body?.status || "Unable to submit parse job.", "error");
          parseButton.disabled = false;
        }
      } catch (error) {
        if (downloadTimer) window.clearInterval(downloadTimer);
        setStatus(error?.message || String(error), "error");
        parseButton.disabled = false;
      }
    });

    skipButton.addEventListener("click", async () => {
      skipButton.disabled = true;
      parseButton.disabled = true;
      try {
        await dismissParsePrompt("skip");
      } catch (error) {
        setStatus(error?.message || String(error), "error");
        skipButton.disabled = false;
        parseButton.disabled = false;
      }
    });

    selectAllButton.addEventListener("click", () => {
      document.querySelectorAll('input[name="paper"]:not(:disabled)').forEach((item) => {
        item.checked = true;
      });
      updateSelectionCount();
      scheduleSaveSelectionState();
      setStatus("");
    });

    clearButton.addEventListener("click", () => {
      document.querySelectorAll('input[name="paper"]').forEach((item) => {
        item.checked = false;
      });
      updateSelectionCount();
      scheduleSaveSelectionState();
      setStatus("");
    });

    form.addEventListener("change", (event) => {
      if (event.target?.matches?.('input[name="paper"]')) {
        updateSelectionCount();
        scheduleSaveSelectionState();
      }
    });

    list.addEventListener("click", async (event) => {
      const trigger = event.target?.closest?.(".open-paper-link");
      if (!trigger) return;
      event.preventDefault();
      event.stopPropagation();
      const paperIndex = Number(trigger.getAttribute("data-paper-index") || 0);
      const urlKind = trigger.getAttribute("data-url-kind") || "paper";
      try {
        const result = await callTool("open_paper_url_in_browser", {
          selection_token: data.selection_token || "",
          paper_index: paperIndex,
          url_kind: urlKind,
        });
        const body = structured(result);
        setStatus(body?.opened ? "Opened in browser." : (body?.url ? "Could not open automatically. URL: " + body.url : body?.message || "Could not open link."), body?.opened ? "success" : "error");
      } catch (error) {
        setStatus(error?.message || String(error), "error");
      }
    });

    window.addEventListener("message", (event) => {
      if (event.source !== window.parent) return;
      const message = event.data;
      if (!message || message.jsonrpc !== "2.0") return;

      if (message.id && pending.has(message.id)) {
        const waiter = pending.get(message.id);
        pending.delete(message.id);
        if (message.error) {
          waiter.reject(new Error(message.error.message || "Host returned an error."));
        } else {
          waiter.resolve(message.result);
        }
        return;
      }

      if (message.method === "ui/notifications/tool-result") {
        const next = message.params?.structuredContent;
        if (next && typeof next === "object") {
          data = unwrapToolOutput(next);
          render();
        }
      }
    }, { passive: true });

    window.addEventListener("openai:set_globals", () => {
      if (window.openai?.toolOutput) {
        data = normalizeSelectionData(unwrapToolOutput(window.openai.toolOutput));
        render();
      }
    }, { passive: true });

    /* ── Platform detection & initialisation ── */
    const HAS_EXT_APPS = typeof globalThis.ExtApps !== 'undefined';
    const HAS_OPENAI  = typeof window.openai !== 'undefined' && window.openai.toolOutput;
    let app = null;  // MCP Apps App instance (Claude Desktop / standard MCP Apps)

    async function initApp() {
      if (HAS_EXT_APPS) {
        /* ── Claude Desktop / standard MCP Apps path ── */
        const { App } = globalThis.ExtApps;
        app = new App({ name: "paper-selector", version: "1.0.0" });

        /* Required protocol handlers (all four must be registered before connect) */
        app.onteardown      = () => ({});
        app.ontoolinput     = () => ({});
        app.ontoolcancelled = () => ({});
        app.onerror         = (err) => console.error("MCP App error:", err);

        /* Receive tool-result data pushed by the host */
        app.ontoolresult = (toolResult) => {
          const sc = toolResult?.structuredContent;
          if (sc) {
            data = normalizeSelectionData(unwrapToolOutput(sc));
            render();
          }
        };

        await app.connect();

        /* Some hosts provide initial data via host context */
        const ctx = app.getHostContext();
        if (ctx?.toolOutput) {
          data = normalizeSelectionData(unwrapToolOutput(ctx.toolOutput));
        }
        render();
        startHiddenSelectionTimeout();
      } else if (HAS_OPENAI) {
        /* ── Codex / OpenAI path (existing logic, unchanged) ── */
        data = normalizeSelectionData(unwrapToolOutput(window.openai.toolOutput));
        render();
        startHiddenSelectionTimeout();
      } else {
        /* ── Pure postMessage fallback ── */
        render();
        startHiddenSelectionTimeout();
      }
    }

    initApp();
  </script>
</body>
</html>"""


MINERU_KEY_WIDGET_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      color-scheme: light dark;
      --bg: #eef3f7;
      --glass: rgba(255, 255, 255, .70);
      --glass-strong: rgba(255, 255, 255, .86);
      --input: rgba(255, 255, 255, .52);
      --text: #111827;
      --muted: #667085;
      --line: rgba(15, 23, 42, .13);
      --accent: #0f766e;
      --accent-strong: #0b5f59;
      --danger: #b42318;
      --shadow: 0 24px 70px rgba(15, 23, 42, .18);
    }

    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0d1117;
        --glass: rgba(22, 27, 34, .70);
        --glass-strong: rgba(31, 37, 46, .86);
        --input: rgba(15, 23, 42, .34);
        --text: #f8fafc;
        --muted: #aeb7c5;
        --line: rgba(226, 232, 240, .14);
        --accent: #2dd4bf;
        --accent-strong: #5eead4;
        --danger: #f97066;
        --shadow: 0 28px 80px rgba(0, 0, 0, .38);
      }
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: linear-gradient(135deg, var(--bg), color-mix(in srgb, var(--bg), #dbeafe 28%));
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }

    main {
      min-height: 100vh;
      padding: 18px;
      display: grid;
      place-items: start center;
    }

    .panel {
      width: min(100%, 590px);
      background: linear-gradient(180deg, var(--glass-strong), var(--glass));
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(22px) saturate(150%);
      -webkit-backdrop-filter: blur(22px) saturate(150%);
    }

    h1 {
      margin: 0 0 7px;
      font-size: 19px;
      font-weight: 700;
    }

    p {
      margin: 0 0 18px;
      color: var(--muted);
      max-width: 52ch;
    }

    label {
      display: grid;
      gap: 8px;
      margin-bottom: 14px;
      color: var(--text);
      font-weight: 650;
    }

    input {
      min-height: 42px;
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--input);
      color: var(--text);
      padding: 9px 12px;
      font: inherit;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .32);
    }

    input:focus {
      border-color: color-mix(in srgb, var(--accent), var(--line));
      outline: 3px solid color-mix(in srgb, var(--accent), transparent 74%);
      outline-offset: 0;
    }

    .row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }

    button {
      min-height: 38px;
      border: 1px solid transparent;
      border-radius: 8px;
      background: linear-gradient(180deg, var(--accent-strong), var(--accent));
      color: #fff;
      padding: 8px 14px;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
      box-shadow: 0 10px 24px rgba(15, 118, 110, .25);
      transition: transform .14s ease, filter .14s ease;
    }

    button:hover:not(:disabled) {
      transform: translateY(-1px);
      filter: brightness(1.04);
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.58;
    }

    .status {
      min-height: 20px;
      color: var(--muted);
      font-size: 12px;
      white-space: pre-wrap;
    }

    .status.error { color: var(--danger); }

    @media (max-width: 560px) {
      main { padding: 10px; }
      .panel { padding: 16px; }
      .row { align-items: stretch; }
      .status { width: 100%; }
    }
  </style>
</head>
<body>
  <main>
    <form class="panel" id="form">
      <h1 id="title">Configure MinerU API key</h1>
      <p id="message">Enter your MinerU API key to enable official extract parsing.</p>
      <label>
        MinerU API key
        <input id="api-key" type="password" autocomplete="off" spellcheck="false" placeholder="Paste API key">
      </label>
      <div class="row">
        <button id="save" type="submit">Save key</button>
        <span class="status" id="status"></span>
      </div>
    </form>
  </main>
  <script>
    const form = document.getElementById("form");
    const input = document.getElementById("api-key");
    const button = document.getElementById("save");
    const message = document.getElementById("message");
    const statusNode = document.getElementById("status");
    let rpcId = 1;
    const pending = new Map();
    let data = window.openai?.toolOutput || {};

    function setStatus(text, kind = "") {
      statusNode.textContent = text || "";
      statusNode.className = kind ? `status ${kind}` : "status";
    }

    function render() {
      if (data.message) message.textContent = data.message;
      if (data.env_file_path) {
        input.setAttribute("aria-description", `Will save to ${data.env_file_path}`);
      }
    }

    function rpcRequest(method, params) {
      const id = rpcId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
        window.setTimeout(() => {
          if (!pending.has(id)) return;
          pending.delete(id);
          reject(new Error("Timed out waiting for host response."));
        }, 60000);
      });
    }

    async function callTool(name, args) {
      if (window.openai?.callTool) {
        return window.openai.callTool(name, args);
      }
      return rpcRequest("tools/call", { name, arguments: args });
    }

    function structured(result) {
      return result?.structuredContent || result?.structured_content || result;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = input.value.trim();
      if (!value) {
        setStatus("Paste a MinerU API key first.", "error");
        return;
      }

      button.disabled = true;
      setStatus("Saving...");
      try {
        const result = await callTool("configure_mineru_api_key", {
          api_key: value,
        });
        const body = structured(result);
        input.value = "";
        setStatus(body?.message || "Saved.");
      } catch (error) {
        setStatus(error?.message || String(error), "error");
      } finally {
        button.disabled = false;
      }
    });

    window.addEventListener("message", (event) => {
      if (event.source !== window.parent) return;
      const payload = event.data;
      if (!payload || payload.jsonrpc !== "2.0") return;

      if (payload.id && pending.has(payload.id)) {
        const waiter = pending.get(payload.id);
        pending.delete(payload.id);
        if (payload.error) {
          waiter.reject(new Error(payload.error.message || "Host returned an error."));
        } else {
          waiter.resolve(payload.result);
        }
        return;
      }

      if (payload.method === "ui/notifications/tool-result") {
        const next = payload.params?.structuredContent;
        if (next && typeof next === "object") {
          data = next;
          render();
        }
      }
    }, { passive: true });

    window.addEventListener("openai:set_globals", () => {
      if (window.openai?.toolOutput) {
        data = window.openai.toolOutput;
        render();
      }
    }, { passive: true });

    render();
  </script>
</body>
</html>"""



# ---------------------------------------------------------------------------
# Local selection page renderer (extracted from server.py)
# ---------------------------------------------------------------------------

def _render_local_selection_html(page_id: str, page: Dict[str, Any]) -> str:
    papers = page.get("papers", [])
    rows = []
    for paper in papers if isinstance(papers, list) else []:
        index = paper.get("index")
        disabled = paper.get("parse_ready") is False or not isinstance(index, int)
        published = str(paper.get("published_date") or paper.get("year") or "Not available")
        venue = str(paper.get("publication_venue") or "Not available")
        original_url = str(paper.get("original_url") or paper.get("url") or paper.get("pdf_url") or "")
        doi = str(paper.get("doi") or "")
        source = str(paper.get("source") or "unknown")
        paper_id = str(paper.get("paper_id") or "")
        link_html = (
            '<a class="paper-link" href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'.format(
                href=html_escape(original_url, quote=True),
                label=html_escape(original_url),
            )
            if original_url
            else '<span class="muted-value">Not available</span>'
        )
        rows.append(
            """
            <label class="paper{disabled_class}">
              <input class="paper-check" type="checkbox" name="paper" value="{index}" {disabled}>
              <span class="paper-body">
                <span class="paper-title"><span class="index-no">{index}.</span> {title}</span>
                <span class="meta-grid">
                  <span><b>Published</b><em>{published}</em></span>
                  <span><b>Journal / Venue</b><em>{venue}</em></span>
                  <span><b>Source</b><em>{source}</em></span>
                  <span><b>Paper ID</b><em>{paper_id}</em></span>
                  <span><b>DOI</b><em>{doi}</em></span>
                  <span class="url-field"><b>Original URL</b><em>{link}</em></span>
                </span>
              </span>
            </label>
            """.format(
                disabled_class=" disabled" if disabled else "",
                index=html_escape(str(index or "")),
                disabled="disabled" if disabled else "",
                title=html_escape(str(paper.get("title") or "Untitled")),
                published=html_escape(published),
                venue=html_escape(venue),
                source=html_escape(source),
                paper_id=html_escape(paper_id or "Not available"),
                doi=html_escape(doi or "Not available"),
                link=link_html,
            )
        )

    body = "\n".join(rows) if rows else '<div class="empty">No papers available.</div>'
    data_json = html_escape(
        json.dumps(
            {
                "page_id": page_id,
                "selection_token": page.get("selection_token", ""),
                "save_path": page.get("save_path", DEFAULT_SAVE_PATH),
                "use_scihub": bool(page.get("use_scihub")),
                "mode": page.get("mode", "auto"),
                "backend": page.get("backend", ""),
                "force": bool(page.get("force")),
                "custom_save_path_confirmed": bool(page.get("custom_save_path_confirmed")),
                "selection_semantics": page.get("selection_semantics", SELECTION_SEMANTICS_PARSE),
                "parse_execution": page.get("parse_execution", "background"),
                "confirmation_token": page.get("confirmation_token", ""),
                "selection_timeout_seconds": int(page.get("selection_timeout_seconds") or 0),
                "selection_expires_at": page.get("selection_expires_at", ""),
            }
        ),
        quote=True,
    )
    semantics = _selection_semantics_name(str(page.get("selection_semantics") or SELECTION_SEMANTICS_PARSE))
    if semantics in {SELECTION_SEMANTICS_DOWNLOAD_ONLY, SELECTION_SEMANTICS_DOWNLOAD_AND_PARSE}:
        action_label = "Download selected"
    else:
        action_label = "Parse selected with MinerU"
    action_title = "Parse selected PDFs with MinerU" if semantics == SELECTION_SEMANTICS_PARSE else "Download selected papers"
    script = r"""
    const form = document.getElementById("form");
    const parseButton = document.getElementById("parse");
    const skipButton = document.getElementById("skip-mineru");
    const statusNode = document.getElementById("status");
    const countdownNode = document.getElementById("countdown-timer");
    const selectionCountNode = document.getElementById("selection-count");
    const decisionPanel = document.getElementById("decision-panel");
    const decisionText = document.getElementById("decision-text");
    const progressPanel = document.getElementById("progress-panel");
    const progressTitleText = document.getElementById("progress-title-text");
    const progressCurrent = document.getElementById("progress-current");
    const progressMeta = document.getElementById("progress-meta");
    const segDownload = document.getElementById("seg-download");
    const segParse = document.getElementById("seg-parse");
    const segError = document.getElementById("seg-error");
    const segDone = document.getElementById("seg-done");
    const progressList = document.getElementById("progress-list");
    const celebrationBurst = document.getElementById("celebration-burst");
    const data = JSON.parse(form.dataset.page || "{}");
    let eventSource = null;
    let pollTimer = null;
    let workflowStage = data.selection_semantics === "parse_selected" ? "direct-parse" : "download";
    let lockedSelectedIndices = [];
    let parseSelectionToken = "";
    let parseSelectedIndices = "all";
    let lastParsePrompt = null;
    let countdownTimer = null;
    let selectionTimeoutTimer = null;
    let downloadTimer = null;
    const terminalStatuses = new Set(["completed", "error", "canceled", "not_found", "invalid_selection"]);

    function selectedIndices() {
      return Array.from(document.querySelectorAll('input[name="paper"]:checked')).map((item) => item.value);
    }

    function updateSelectionCount() {
      const all = Array.from(document.querySelectorAll('input[name="paper"]'));
      const selected = all.filter((item) => item.checked);
      selectionCountNode.textContent = selected.length + "/" + all.length;
    }

    function setStatus(message, kind) {
      statusNode.textContent = message || "";
      statusNode.className = kind ? "status " + kind : "status";
    }

    function isDownloadThenParseFlow() {
      return data.selection_semantics === "download_and_parse_selected_only"
        || data.selection_semantics === "download_selected_only"
        || String(data.parse_execution || "").toLowerCase() === "none";
    }

    function isParseReadyPrompt(prompt) {
      if (!prompt || typeof prompt !== "object") return false;
      if (!prompt.selection_token) return false;
      return Number(prompt.parse_ready_total || 0) > 0
        || Boolean(prompt.parse_decision_required)
        || Boolean(prompt.requires_user_parse_decision);
    }

    function lockSelection() {
      document.querySelectorAll('input[name="paper"]').forEach((item) => {
        item.disabled = true;
      });
      const selectAll = document.getElementById("select-all");
      const clear = document.getElementById("clear");
      if (selectAll) selectAll.disabled = true;
      if (clear) clear.disabled = true;
      updateSelectionCount();
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[ch]);
    }

    function clearProgress() {
      progressTitleText.textContent = "Processing papers";
      progressCurrent.textContent = "";
      progressMeta.textContent = "";
      segDownload.style.width = "0%";
      segParse.style.width = "0%";
      segError.style.width = "0%";
      segDone.style.width = "0%";
      progressList.innerHTML = "";
    }

    function setSegments(downloadPct, parsePct, errorPct = 0, donePct = 0) {
      segDownload.style.width = Math.max(0, Math.min(100, downloadPct)) + "%";
      segParse.style.width = Math.max(0, Math.min(100, parsePct)) + "%";
      segError.style.width = Math.max(0, Math.min(100, errorPct)) + "%";
      segDone.style.width = Math.max(0, Math.min(100, donePct)) + "%";
      const bar = segDownload.parentElement;
      const total = downloadPct + parsePct + errorPct + donePct;
      if (bar) {
        bar.className = "progress-bar" + (total <= 0 ? " idle" : (total >= 99 && !errorPct ? " complete" : ""));
      }
    }

    function celebrateDownload() {
      if (!celebrationBurst) return;
      const colors = ["#0d9488", "#2dd4bf", "#f97316", "#357abd", "#4ec9b0"];
      celebrationBurst.innerHTML = Array.from({ length: 22 }, (_, index) => {
        const angle = (Math.PI * 2 * index) / 22;
        const radius = 52 + (index % 5) * 12;
        const tx = Math.cos(angle) * radius;
        const ty = Math.sin(angle) * radius;
        const x = 50 + Math.cos(angle) * 10;
        const y = 40 + Math.sin(angle) * 8;
        return `<span style="--x:${x}%;--y:${y}%;--tx:${tx}px;--ty:${ty}px;--c:${colors[index % colors.length]};--d:${(index % 6) * 34}ms"></span>`;
      }).join("");
      celebrationBurst.hidden = false;
      progressPanel.classList.add("celebrating");
      window.setTimeout(() => {
        celebrationBurst.hidden = true;
        celebrationBurst.innerHTML = "";
        progressPanel.classList.remove("celebrating");
      }, 1200);
    }

    function startDownloadProgress(total) {
      if (downloadTimer) window.clearInterval(downloadTimer);
      let pct = 0;
      const stages = [
        [8, "Preparing selected papers"],
        [24, "Resolving PDF routes"],
        [48, "Downloading papers"],
        [76, "Validating PDF files"],
        [90, "Writing manifest"],
      ];
      progressPanel.hidden = false;
      progressTitleText.textContent = "Downloading papers";
      progressCurrent.textContent = "Preparing selected papers.";
      progressMeta.textContent = total + " selected";
      setSegments(0, 0);
      progressList.innerHTML = `
        <div class="download-skeleton" aria-live="polite">
          <div class="download-step"><strong>Resolve</strong><span>Checking source and PDF routes.</span></div>
          <div class="download-step"><strong>Acquire</strong><span>Saving selected PDFs.</span></div>
          <div class="download-step"><strong>Verify</strong><span>Validating files and manifest.</span></div>
        </div>`;
      downloadTimer = window.setInterval(() => {
        pct = Math.min(94, pct + Math.max(1, Math.round((96 - pct) * 0.08)));
        const stage = stages.filter(([mark]) => pct >= mark).pop();
        progressCurrent.textContent = (stage ? stage[1] : "Preparing selected papers") + ".";
        progressMeta.textContent = total + " selected";
        setSegments(pct, 0);
      }, 420);
    }

    function finishDownloadProgress(body) {
      if (downloadTimer) window.clearInterval(downloadTimer);
      downloadTimer = null;
      progressPanel.hidden = false;
      progressTitleText.textContent = "Download complete";
      progressCurrent.textContent = body?.manifest_path ? "Manifest: " + body.manifest_path : "PDF files were saved.";
      progressMeta.textContent = "Saved " + (body?.total || 0) + " PDFs";
      setSegments(100, 0, body?.failed ? Math.min(100, Number(body.failed) * 8) : 0, body?.failed ? 0 : 100);
      progressList.innerHTML = `
        <div class="download-summary">
          <div class="summary-metric"><b>Downloaded</b><strong>${escapeHtml(body?.downloaded || 0)}</strong></div>
          <div class="summary-metric"><b>Existing</b><strong>${escapeHtml(body?.skipped_existing || 0)}</strong></div>
          <div class="summary-metric"><b>Failed</b><strong>${escapeHtml(body?.failed || 0)}</strong></div>
        </div>`;
      if (!body?.failed) celebrateDownload();
    }

    function clearCountdown() {
      if (countdownTimer) window.clearInterval(countdownTimer);
      countdownTimer = null;
    }

    function formatCountdownDisplay(seconds) {
      if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
      const m = Math.floor(seconds / 60);
      const s = Math.floor(seconds % 60);
      return m + ":" + String(s).padStart(2, "0");
    }

    function updateCountdownDisplay(remaining) {
      countdownNode.textContent = formatCountdownDisplay(remaining);
      countdownNode.setAttribute("title", Math.max(0, Math.ceil(remaining)) + " seconds remaining");
    }

    function clearSelectionTimeout() {
      if (selectionTimeoutTimer) window.clearInterval(selectionTimeoutTimer);
      selectionTimeoutTimer = null;
      if (!lastParsePrompt) {
        countdownNode.hidden = true;
        countdownNode.classList.remove("urgent");
      }
    }

    async function expireDownloadSelection() {
      clearSelectionTimeout();
      countdownNode.hidden = true;
      countdownNode.classList.remove("urgent");
      if (workflowStage !== "download" || parseButton.disabled) return;
      parseButton.disabled = true;
      document.getElementById("select-all").disabled = true;
      document.getElementById("clear").disabled = true;
      try {
        const response = await fetch("/api/download-selection-timeout/" + encodeURIComponent(data.page_id), {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({reason: "timeout"}),
        });
        const body = await response.json();
        setStatus(body.message || "Selection expired. No PDFs were downloaded.", "error");
      } catch (error) {
        setStatus(error?.message || String(error), "error");
      }
      decisionPanel.hidden = false;
      decisionText.textContent = "Selection expired. Start a new search to download papers.";
      workflowStage = "terminal";
    }

    function startHiddenSelectionTimeout() {
      clearSelectionTimeout();
      if (workflowStage !== "download") return;
      const timeoutSeconds = Math.max(1, Number(data.selection_timeout_seconds || 120));
      let remaining = timeoutSeconds;
      countdownNode.hidden = false;
      updateCountdownDisplay(remaining);
      selectionTimeoutTimer = window.setInterval(() => {
        remaining -= 1;
        updateCountdownDisplay(remaining);
        if (remaining <= 30) {
          countdownNode.classList.add("urgent");
        }
        if (remaining <= 0) {
          expireDownloadSelection();
        }
      }, 1000);
    }

    function renderTerminalNoParse(body) {
      clearCountdown();
      lastParsePrompt = null;
      workflowStage = "terminal";
      skipButton.hidden = true;
      skipButton.disabled = true;
      decisionPanel.hidden = false;
      decisionText.textContent = body?.message || "PDFs were saved. MinerU parsing was not started.";
      parseButton.disabled = true;
      parseButton.textContent = "MinerU not started";
      setStatus(decisionText.textContent, "success");
    }

    async function dismissLocalParsePrompt(reason) {
      const prompt = lastParsePrompt || {};
      const response = await fetch("/api/parse-prompt-timeout/" + encodeURIComponent(data.page_id), {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          download_selection_token: prompt.download_selection_token || data.selection_token || "",
          prompt_id: prompt.prompt_id || "",
          reason: reason || "timeout",
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.message || "Parse prompt timeout request failed.");
      renderTerminalNoParse(body);
      return body;
    }

    function startParseDecision(prompt, selected, responseBody) {
      lastParsePrompt = prompt || {};
      parseSelectionToken = String(lastParsePrompt.selection_token || "");
      parseSelectedIndices = String(lastParsePrompt.default_parse_selected_indices || lastParsePrompt.recommended_selected_indices || "all");
      data.confirmation_token = String(responseBody?.confirmation_token || "");
      lockedSelectedIndices = selected.slice();
      lockSelection();
      decisionPanel.hidden = false;
      skipButton.hidden = false;
      skipButton.disabled = false;
      workflowStage = "parse";
      parseButton.textContent = "Parse with MinerU";
      parseButton.disabled = !parseSelectionToken || !data.confirmation_token;
      decisionText.textContent = lastParsePrompt.timeout_message
        || "MinerU parsing is optional. If no action is taken, this prompt will close and keep the PDFs only.";
      let remaining = Math.max(1, Number(lastParsePrompt.timeout_seconds || 120));
      clearCountdown();
      countdownNode.hidden = false;
      updateCountdownDisplay(remaining);
      countdownTimer = window.setInterval(async () => {
        remaining -= 1;
        updateCountdownDisplay(remaining);
        if (remaining <= 0) {
          clearCountdown();
          parseButton.disabled = true;
          skipButton.disabled = true;
          try {
            await dismissLocalParsePrompt("timeout");
          } catch (error) {
            setStatus(error?.message || String(error), "error");
          }
        }
      }, 1000);
      setStatus(
        responseBody?.message || ("Downloaded " + (responseBody?.downloaded || 0) + " paper(s). Choose whether to start MinerU parsing."),
        responseBody?.status === "failed" || !parseSelectionToken ? "error" : "success"
      );
    }

    function renderProgress(job) {
      if (!job || typeof job !== "object") return;
      const total = Number(job.total || 0);
      const completed = Number(job.completed_items || job.parsed || 0);
      const failed = Number(job.failed || 0);
      const status = String(job.status || "running").toLowerCase();
      const done = status === "completed";
      const errored = status === "error" || status === "failed";
      const denominator = Math.max(1, total);
      progressPanel.hidden = false;
      progressTitleText.textContent = done ? "Parsing complete" : (errored ? "Job interrupted" : "Processing papers");
      progressCurrent.textContent = job.current || job.message || (job.job_id ? "Job " + job.job_id : "");
      progressMeta.textContent = total ? completed + "/" + total + " done" : "";
      setSegments(
        Number(job.phase_downloading || job.downloading || 0) / denominator * 100,
        Number(job.phase_parsing || job.parsing || 0) / denominator * 100,
        Number(job.phase_error || failed || 0) / denominator * 100,
        Number(job.phase_completed || completed || 0) / denominator * 100,
      );

      const items = Array.isArray(job.items) ? job.items : [];
      const renderItem = (item) => {
        const stage = String(item.stage || item.status || "queued").toLowerCase();
        const title = item.title || ("Paper " + (item.index || ""));
        return '<div class="progress-item stage-' + escapeHtml(stage) + '">'
          + '<strong>' + escapeHtml((item.index ? item.index + ". " : "") + title) + '</strong>'
          + '<span>' + escapeHtml(stage + (item.message ? " - " + item.message : "")) + '</span>'
          + '</div>';
      };
      const stageOf = (item) => String(item.stage || item.status || "queued").toLowerCase();
      const groups = [
        ["Active", items.filter((item) => ["downloading", "parsing", "ready"].includes(stageOf(item)))],
        ["Queued", items.filter((item) => ["", "queued"].includes(stageOf(item)))],
        ["Completed", items.filter((item) => stageOf(item) === "completed")],
        ["Attention", items.filter((item) => ["error", "failed", "skipped"].includes(stageOf(item)))],
      ].filter(([, groupItems]) => groupItems.length);
      progressList.innerHTML = groups.map(([groupName, groupItems]) =>
        '<div class="progress-section-title">' + escapeHtml(groupName) + '</div>'
        + groupItems.map(renderItem).join("")
      ).join("");

      if (terminalStatuses.has(status)) {
        disconnectProgress();
        parseButton.disabled = false;
        setStatus(job.message || (done ? "Done." : "Job stopped."), done ? "success" : "error");
        if (done && !failed) celebrateDownload();
      }
    }

    function disconnectProgress() {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      if (pollTimer) {
        window.clearTimeout(pollTimer);
        pollTimer = null;
      }
    }

    function connectProgress(jobId) {
      if (!jobId) return;
      disconnectProgress();
      if (typeof EventSource !== "undefined") {
        eventSource = new EventSource("/api/progress-stream/" + encodeURIComponent(jobId));
        eventSource.onmessage = (event) => {
          try {
            renderProgress(JSON.parse(event.data));
          } catch (_) {}
        };
        eventSource.addEventListener("done", disconnectProgress);
        eventSource.onerror = () => {
          disconnectProgress();
          pollProgress(jobId);
        };
      } else {
        pollProgress(jobId);
      }
    }

    async function pollProgress(jobId) {
      try {
        const response = await fetch("/api/parse-job/" + encodeURIComponent(jobId));
        const body = await response.json();
        renderProgress(body);
        if (!terminalStatuses.has(String(body.status || "").toLowerCase())) {
          pollTimer = window.setTimeout(() => pollProgress(jobId), 1000);
        }
      } catch (error) {
        parseButton.disabled = false;
        setStatus(error?.message || String(error), "error");
      }
    }

    document.getElementById("select-all").addEventListener("click", () => {
      document.querySelectorAll('input[name="paper"]:not(:disabled)').forEach((item) => {
        item.checked = true;
      });
      updateSelectionCount();
      setStatus("");
    });

    document.getElementById("clear").addEventListener("click", () => {
      document.querySelectorAll('input[name="paper"]').forEach((item) => {
        item.checked = false;
      });
      updateSelectionCount();
      setStatus("");
    });

    form.addEventListener("change", (event) => {
      if (event.target?.matches?.('input[name="paper"]')) updateSelectionCount();
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (workflowStage === "terminal") return;
      const selected = workflowStage === "parse" ? lockedSelectedIndices : selectedIndices();
      if (!selected.length) {
        setStatus("Select at least one paper.", "error");
        return;
      }
      const downloadThenParse = isDownloadThenParseFlow();
      const endpoint = workflowStage === "direct-parse"
        ? "/api/parse-selection/"
        : workflowStage === "download"
        ? "/api/download-selection/"
        : "/api/parse-downloaded-selection/";
      parseButton.disabled = true;
      skipButton.disabled = true;
      clearCountdown();
      clearSelectionTimeout();
      progressPanel.hidden = false;
      clearProgress();
      progressTitleText.textContent = workflowStage === "download" ? "Downloading papers" : "Starting MinerU parsing";
      progressCurrent.textContent = "Preparing selected papers.";
      setStatus(workflowStage === "download" ? "Downloading..." : "Submitting MinerU parse job...");
      if (workflowStage === "download") startDownloadProgress(selected.length);
      try {
        const response = await fetch(endpoint + encodeURIComponent(data.page_id), {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            selected_indices: workflowStage === "parse" ? parseSelectedIndices : selected.join(","),
            parse_selection_token: parseSelectionToken,
            confirmation_token: data.confirmation_token || "",
          }),
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.message || "Selection request failed.");
        if (workflowStage === "download" && downloadThenParse) {
          finishDownloadProgress(body);
          const prompt = body?.parse_prompt && typeof body.parse_prompt === "object" ? body.parse_prompt : {};
          if (["timed_out_no_parse", "completed_no_parse"].includes(String(prompt.status || body.status || ""))) {
            renderTerminalNoParse(prompt.status ? prompt : body);
            return;
          }
          if (!isParseReadyPrompt(prompt)) {
            parseButton.disabled = false;
            setStatus(
              body.message || ("Downloaded " + (body.downloaded || 0) + " paper(s)."),
              body.status === "failed" ? "error" : "success"
            );
            return;
          }
          startParseDecision(prompt, selected, body);
          return;
        }
        renderProgress(body);
        if (body.job_id) {
          setStatus("MinerU parsing: " + body.job_id);
          connectProgress(body.job_id);
        } else {
          parseButton.disabled = false;
          setStatus(body.message || body.status || "Unable to submit parse job.", "error");
        }
      } catch (error) {
        if (downloadTimer) window.clearInterval(downloadTimer);
        downloadTimer = null;
        parseButton.disabled = false;
        skipButton.disabled = workflowStage !== "parse";
        setStatus(error?.message || String(error), "error");
      }
    });

    skipButton.addEventListener("click", async () => {
      skipButton.disabled = true;
      parseButton.disabled = true;
      try {
        await dismissLocalParsePrompt("skip");
      } catch (error) {
        setStatus(error?.message || String(error), "error");
        skipButton.disabled = false;
        parseButton.disabled = false;
      }
    });

    updateSelectionCount();
    startHiddenSelectionTimeout();
    """
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Paper Selection</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #eef8ff;
      --bg-2: #ecfff8;
      --panel: rgba(255,255,255,.78);
      --paper: rgba(255,255,255,.84);
      --text: #102033;
      --muted: #53687d;
      --line: rgba(16,32,51,.14);
      --accent: #3f8fd2;
      --green: #2bbf91;
      --danger: #b42318;
      --success: #08795f;
      --shadow: 0 24px 80px rgba(38,98,132,.18);
      --font-title: "Crimson Pro", Georgia, "Times New Roman", serif;
      --font-ui: "Atkinson Hyperlegible", "Segoe UI", system-ui, sans-serif;
      --font-mono: ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0b1424;
        --panel: rgba(18,31,52,.82);
        --paper: rgba(27,43,68,.82);
        --text: #e7edf6;
        --muted: #9aabc0;
        --line: rgba(231,237,246,.14);
        --accent: #78bff3;
        --danger: #ff8a7a;
        --success: #58d5b8;
      }}
    }}
    * {{ box-sizing: border-box; }}
    [hidden] {{ display: none !important; }}
    body {{
      margin: 0;
      background: linear-gradient(145deg, var(--bg), var(--bg-2));
      color: var(--text);
      font: 14px/1.45 var(--font-ui);
    }}
    main {{
      min-height: 100vh;
      padding: 24px;
      display: grid;
      place-items: start center;
    }}
    .shell {{
      width: min(100%, 1180px);
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: hidden;
      backdrop-filter: blur(24px) saturate(150%);
      -webkit-backdrop-filter: blur(24px) saturate(150%);
    }}
    header, .toolbar, .progress-panel {{
      padding: 16px 20px;
      border-bottom: 1px solid var(--line);
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
    }}
    h1 {{
      margin: 0;
      font-family: var(--font-title);
      font-size: 19px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .token-badge {{
      color: var(--muted);
      font: 11px ui-monospace, "Cascadia Code", monospace;
      overflow-wrap: anywhere;
      text-align: right;
    }}
    .list {{
      display: grid;
      gap: 10px;
      max-height: 62vh;
      overflow: auto;
      padding: 14px;
    }}
    .paper {{
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      gap: 12px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--paper);
      cursor: pointer;
      transition: background .18s ease, border-color .18s ease, box-shadow .18s ease, transform .18s ease;
    }}
    .paper:hover {{
      border-color: rgba(63,143,210,.32);
      box-shadow: 0 10px 28px rgba(38,98,132,.12);
      transform: translateY(-1px);
    }}
    .paper.disabled {{
      opacity: .55;
      cursor: not-allowed;
    }}
    .paper-check {{
      width: 20px;
      height: 20px;
      margin-top: 2px;
      accent-color: var(--accent);
    }}
    .paper-title {{
      display: block;
      font-family: var(--font-title);
      font-weight: 650;
      overflow-wrap: anywhere;
    }}
    .index-no {{
      color: var(--accent);
      font-weight: 750;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 7px 14px;
      margin-top: 9px;
      color: var(--muted);
      font-size: 11px;
    }}
    .meta-grid b {{
      display: block;
      color: var(--text);
      font-size: 9px;
      text-transform: uppercase;
    }}
    .meta-grid em {{
      display: block;
      font-style: normal;
      overflow-wrap: anywhere;
    }}
    .url-field {{
      grid-column: span 2;
    }}
    .paper-link {{
      color: var(--accent);
    }}
    .toolbar {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      border-top: 1px solid var(--line);
      border-bottom: 0;
    }}
    button {{
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--paper);
      color: var(--text);
      padding: 7px 13px;
      font: inherit;
      cursor: pointer;
      transition: background .18s ease, border-color .18s ease, box-shadow .18s ease, transform .18s ease;
    }}
    button:hover:not(:disabled) {{
      border-color: rgba(63,143,210,.34);
      box-shadow: 0 8px 22px rgba(38,98,132,.12);
      transform: translateY(-1px);
    }}
    button.primary {{
      border-color: transparent;
      background: linear-gradient(135deg, var(--accent), var(--green));
      color: white;
      font-weight: 650;
    }}
    button:disabled {{
      cursor: not-allowed;
      opacity: .55;
    }}
    .status {{
      margin-left: auto;
      color: var(--muted);
      white-space: pre-wrap;
    }}
    .status:empty {{ display: none; }}
    .status.error {{ color: var(--danger); }}
    .status.success {{ color: var(--success); }}
    .decision-panel {{
      display: grid;
      gap: 6px;
      padding: 14px 20px;
      border-top: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(185,228,255,.34), rgba(191,244,223,.40));
    }}
    .decision-panel[hidden] {{ display: none; }}
    .decision-panel strong {{
      font-family: var(--font-title);
      font-size: 17px;
    }}
    .decision-panel span {{
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .selection-count {{
      min-width: 68px;
      text-align: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 12px;
      font-weight: 700;
    }}
    .countdown-timer {{
      min-width: 62px;
      text-align: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      color: var(--muted);
      transition: color .3s ease, border-color .3s ease;
    }}
    .countdown-timer.urgent {{
      color: var(--danger);
      border-color: var(--danger);
      animation: pulse-urgent 1s ease-in-out infinite;
    }}
    @keyframes pulse-urgent {{
      0%, 100% {{ opacity: 1; }}
      50% {{ opacity: .55; }}
    }}
    .toolbar-right-group {{
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 9px;
    }}
    .progress-panel {{
      border-top: 1px solid var(--line);
      border-bottom: 0;
      position: relative;
      overflow: hidden;
    }}
    .progress-panel.celebrating {{
      animation: panelCelebrate .55s cubic-bezier(.22,1,.36,1);
    }}
    @keyframes panelCelebrate {{
      45% {{ box-shadow: inset 0 0 0 1px rgba(249,115,22,.32), 0 18px 55px rgba(249,115,22,.18); }}
    }}
    .progress-head {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 10px;
    }}
    .progress-title {{
      margin: 0;
      font-family: var(--font-title);
      font-weight: 700;
    }}
    .progress-current, .progress-meta {{
      color: var(--muted);
      font-size: 12px;
    }}
    .progress-bar {{
      display: flex;
      height: 9px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(128,128,128,.18);
      position: relative;
    }}
    .progress-bar::after {{
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,.42), transparent);
      transform: translateX(-120%);
      animation: progressSheen 1.7s ease-in-out infinite;
    }}
    .progress-bar.idle::after,
    .progress-bar.complete::after {{
      animation: none;
      opacity: 0;
    }}
    @keyframes progressSheen {{
      from {{ transform: translateX(-120%); }}
      to {{ transform: translateX(120%); }}
    }}
    .progress-bar span {{
      height: 100%;
      transition: width .25s ease;
    }}
    .seg-download {{ background: #3f8fd2; }}
    .seg-parse {{ background: #18a585; }}
    .seg-error {{ background: #c9352b; }}
    .seg-done {{ background: #58c78f; }}
    .progress-list {{
      display: grid;
      gap: 7px;
      margin-top: 10px;
      max-height: 240px;
      overflow: auto;
    }}
    .progress-item {{
      display: grid;
      gap: 3px;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 9px 11px;
      background: var(--paper);
    }}
    .progress-item strong {{
      font-family: var(--font-title);
      font-weight: 600;
    }}
    .progress-section-title {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 10px 2px 2px;
      color: var(--muted);
      font-size: 10px;
      font-weight: 750;
      text-transform: uppercase;
    }}
    .progress-section-title::after {{
      content: "";
      height: 1px;
      flex: 1;
      background: var(--line);
    }}
    .download-skeleton, .download-summary {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }}
    .download-step, .summary-metric {{
      position: relative;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--paper);
      padding: 10px 12px;
      color: var(--muted);
      font-size: 11px;
    }}
    .download-step strong {{
      display: block;
      color: var(--text);
      font-family: var(--font-title);
      font-size: 14px;
    }}
    .download-step::after {{
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,.30), transparent);
      transform: translateX(-120%);
      animation: progressSheen 1.6s ease-in-out infinite;
    }}
    .summary-metric b {{
      display: block;
      color: var(--muted);
      font-size: 10px;
      text-transform: uppercase;
    }}
    .summary-metric strong {{
      display: block;
      margin-top: 2px;
      color: var(--text);
      font-family: var(--font-mono);
      font-size: 18px;
    }}
    .celebration-burst {{
      position: absolute;
      inset: 0;
      pointer-events: none;
      overflow: hidden;
    }}
    .celebration-burst span {{
      position: absolute;
      left: var(--x);
      top: var(--y);
      width: 7px;
      height: 7px;
      border-radius: 2px;
      background: var(--c);
      opacity: 0;
      transform: translate(-50%, -50%) scale(.45);
      animation: celebratePop .9s cubic-bezier(.22,1,.36,1) forwards;
      animation-delay: var(--d);
    }}
    @keyframes celebratePop {{
      14% {{ opacity: 1; }}
      100% {{ opacity: 0; transform: translate(calc(-50% + var(--tx)), calc(-50% + var(--ty))) scale(1) rotate(180deg); }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *,
      *::before,
      *::after {{
        animation-duration: 1ms !important;
        transition-duration: 1ms !important;
        scroll-behavior: auto !important;
      }}
    }}
    .empty {{
      padding: 30px;
      text-align: center;
      color: var(--muted);
    }}
    @media (max-width: 720px) {{
      main {{ padding: 10px; }}
      header {{ display: grid; }}
      .meta-grid {{ grid-template-columns: 1fr; }}
      .url-field {{ grid-column: auto; }}
      .status {{ width: 100%; margin-left: 0; }}
      .download-skeleton, .download-summary {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="shell">
      <header>
        <div>
          <h1>Paper Selection</h1>
          <div class="muted-value">{html_escape(action_title)}</div>
        </div>
        <div class="token-badge">{html_escape(str(page.get("selection_token", "")))}</div>
      </header>
      <form id="form" data-page="{data_json}">
        <div class="list">{body}</div>
        <div class="toolbar">
          <button class="primary" id="parse" type="submit">{html_escape(action_label)}</button>
          <button id="skip-mineru" type="button" hidden>Skip MinerU</button>
          <button id="select-all" type="button">Select All</button>
          <button id="clear" type="button">Clear</button>
          <span class="status" id="status"></span>
          <div class="toolbar-right-group">
            <span class="countdown-timer" id="countdown-timer" hidden></span>
            <span class="selection-count" id="selection-count">0/0</span>
          </div>
        </div>
      </form>
      <section class="decision-panel" id="decision-panel" hidden>
        <strong>MinerU parsing is optional</strong>
        <span id="decision-text">PDFs were saved. Choose whether to start MinerU parsing.</span>
      </section>
      <section class="progress-panel" id="progress-panel" hidden>
        <div class="progress-head">
          <div>
            <p class="progress-title" id="progress-title-text">Processing papers</p>
            <div class="progress-current" id="progress-current"></div>
          </div>
          <div class="progress-meta" id="progress-meta"></div>
        </div>
        <div class="progress-bar" aria-hidden="true">
          <span class="seg-download" id="seg-download" style="width:0%"></span>
          <span class="seg-parse" id="seg-parse" style="width:0%"></span>
          <span class="seg-error" id="seg-error" style="width:0%"></span>
          <span class="seg-done" id="seg-done" style="width:0%"></span>
        </div>
        <div class="progress-list" id="progress-list"></div>
        <div class="celebration-burst" id="celebration-burst" hidden></div>
      </section>
    </section>
  </main>
  <script>{script}</script>
</body>
</html>"""
