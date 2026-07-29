"""Bundled non-Claude agent hook/plugin assets written by `yoru init`.

These assets are intentionally self-contained. The Codex hook is registered
with the same Python interpreter that runs the yoru CLI so its `httpx`
dependency is available inside pipx/uv-tool installs.
"""

CODEX_HOOK_SCRIPT: str = """#!/usr/bin/env python3
\"\"\"
Yoru hook for Codex CLI.

Reads Codex hook events from stdin, transforms them to Yoru's event format,
and POSTs to /api/v1/sessions/events with agent="codex".

Designed to be non-blocking: exits 0 even if the backend is unreachable.
\"\"\"
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

CONFIG_PATH = Path.home() / ".config" / "yoru" / "config.json"
DEFAULT_SERVER = "http://localhost:8002"
EVENTS_ENDPOINT = "/api/v1/sessions/events"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"server": DEFAULT_SERVER}


def get_git_context(cwd: str | None) -> tuple[str | None, str | None]:
    if not cwd:
        return None, None

    try:
        remote = subprocess.run(
            ["git", "-C", cwd, "ls-remote", "--get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        branch = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None, None

    git_remote = remote.stdout.strip() if remote.returncode == 0 else None
    git_branch = branch.stdout.strip() if branch.returncode == 0 else None
    return git_remote, git_branch


def parse_codex_event(data: dict) -> dict | None:
    hook_event = data.get("hook_event_name")
    session_id = data.get("session_id")
    cwd = data.get("cwd")

    if not session_id:
        return None

    git_remote, git_branch = get_git_context(cwd)
    event_mapping = {
        "SessionStart": "session_start",
        "SessionEnd": "session_end",
        "PreToolUse": "tool_use",
        "PostToolUse": "tool_use",
        "UserPromptSubmit": "message",
        "Stop": "session_end",
    }
    kind = event_mapping.get(hook_event)
    if not kind:
        return None

    tool = data.get("tool_name")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response")
    path = None
    content = None

    if isinstance(tool_input, dict):
        path = tool_input.get("file_path") or tool_input.get("path")
        if tool == "Bash":
            content = tool_input.get("command")
        if not content:
            content = tool_input.get("query") or tool_input.get("pattern")

    if tool_response and isinstance(tool_response, str) and not content:
        content = tool_response[:400]

    if hook_event == "UserPromptSubmit":
        content = data.get("prompt", "")
        tool = "user"

    return {
        "session_id": session_id,
        "kind": kind,
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "path": path,
        "content": content,
        "cwd": cwd,
        "git_remote": git_remote,
        "git_branch": git_branch,
        "agent": "codex",
        "raw": {"codex_hook": data},
    }


def send_event(server: str, token: str, event: dict) -> bool:
    url = f"{server.rstrip('/')}{EVENTS_ENDPOINT}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(
            url,
            json={"events": [event]},
            headers=headers,
            timeout=2.0,
        )
        return response.status_code in (200, 202)
    except Exception:
        return False


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    event = parse_codex_event(data)
    if not event:
        sys.exit(0)

    cfg = load_config()
    token = cfg.get("token")
    if not token:
        sys.exit(0)

    send_event(cfg.get("server", DEFAULT_SERVER), token, event)
    sys.exit(0)


if __name__ == "__main__":
    main()
"""


OPENCODE_PLUGIN_TS: str = """/**
 * Yoru plugin for OpenCode.
 *
 * Local plugin entrypoint. Reads ~/.config/yoru/config.json and posts
 * OpenCode events to /api/v1/sessions/events with agent="opencode".
 */

// @ts-ignore - @opencode-ai/plugin is only available in OpenCode environment
import type { Plugin } from "@opencode-ai/plugin";

interface YoruConfig {
  server: string;
  token: string;
}

interface YoruEvent {
  session_id: string;
  kind: string;
  ts: string;
  tool?: string;
  path?: string;
  content?: string;
  cwd?: string;
  git_remote?: string;
  git_branch?: string;
  agent: string;
  raw?: Record<string, unknown>;
}

interface OpenCodePluginContext {
  client?: any;
  directory?: string;
}

interface OpenCodeEventEnvelope {
  event: any;
}

async function logYoru(
  client: any,
  level: "info" | "warn" | "error",
  message: string,
  extra?: Record<string, unknown>
): Promise<void> {
  try {
    if (client?.app?.log) {
      await client.app.log({
        body: {
          service: "yoru",
          level,
          message,
          ...(extra && { extra }),
        },
      });
    } else {
      const consoleMethod = level === "error" ? console.error : level === "warn" ? console.warn : console.log;
      consoleMethod(`[Yoru] ${message}`, extra || "");
    }
  } catch {
    const consoleMethod = level === "error" ? console.error : level === "warn" ? console.warn : console.log;
    consoleMethod(`[Yoru] ${message}`, extra || "");
  }
}

async function loadYoruConfig(): Promise<YoruConfig | null> {
  try {
    // @ts-ignore - Node/Bun runtime module; local standalone typecheck has no @types/node
    const fs = await import("fs/promises");
    // @ts-ignore - Node/Bun runtime module; local standalone typecheck has no @types/node
    const path = await import("path");
    // @ts-ignore - Node/Bun runtime module; local standalone typecheck has no @types/node
    const os = await import("os");

    const configPath = path.join(os.homedir(), ".config", "yoru", "config.json");
    const configContent = await fs.readFile(configPath, "utf-8");
    return JSON.parse(configContent);
  } catch {
    return null;
  }
}

export async function sendEventToYoru(config: YoruConfig, event: YoruEvent): Promise<boolean> {
  try {
    const url = `${config.server.replace(/\\/$/, "")}/api/v1/sessions/events`;
    const response = await globalThis.fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${config.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ events: [event] }),
      signal: AbortSignal.timeout(2000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

function mapOpenCodeEventToYoru(event: any, sessionId: string): YoruEvent | null {
  const timestamp = new Date().toISOString();

  switch (event.type) {
    case "session.created":
      return {
        session_id: sessionId,
        kind: "session_start",
        ts: timestamp,
        agent: "opencode",
        raw: { opencode_event: event },
      };
    case "session.idle":
    case "session.status":
      return {
        session_id: sessionId,
        kind: "session_end",
        ts: timestamp,
        agent: "opencode",
        raw: { opencode_event: event },
      };
    case "tool.execute.after":
      return {
        session_id: sessionId,
        kind: "tool_use",
        ts: timestamp,
        tool: event.properties?.tool || "unknown",
        content: event.properties?.output || "",
        agent: "opencode",
        raw: { opencode_event: event },
      };
    case "file.edited":
      return {
        session_id: sessionId,
        kind: "file_change",
        ts: timestamp,
        path: event.properties?.path,
        agent: "opencode",
        raw: { opencode_event: event },
      };
    default:
      return null;
  }
}

export const YoruPlugin: Plugin = async ({ client, directory }: OpenCodePluginContext) => {
  const config = await loadYoruConfig();
  if (!config) {
    await logYoru(client, "warn", "No config found at ~/.config/yoru/config.json - plugin disabled");
    return {};
  }

  await logYoru(client, "info", `Plugin loaded, streaming events to ${config.server}`);

  return {
    event: async ({ event }: OpenCodeEventEnvelope) => {
      const sessionId = event.properties?.sessionID || event.properties?.sessionId;
      if (!sessionId) return;

      const yoruEvent = mapOpenCodeEventToYoru(event, sessionId);
      if (!yoruEvent) return;

      if (directory) {
        yoruEvent.cwd = directory;
      }

      sendEventToYoru(config, yoruEvent).catch((err) => {
        logYoru(client, "error", `Failed to send event: ${err}`, { error: String(err) });
      });
    },
  };
};
"""
