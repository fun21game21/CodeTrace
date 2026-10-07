import type { RunDependencies, SocketPort } from "./types.ts";

import { browserTraceLoader } from "../execution/browser.ts";

export function browserDependencies(): RunDependencies {
  return {
    traceLoader: browserTraceLoader(),
    uuid: () => crypto.randomUUID(),
    async fetch(url, options) {
      return fetch(url, {
        ...options,
        cache: "no-store",
        signal: AbortSignal.timeout(20000),
      });
    },
    socket(): SocketPort {
      const url = new URL("/api/runs", window.location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(url);
      return {
        onMessage: (handler) =>
          socket.addEventListener("message", (e) => handler(e.data)),
        onClose: (handler) => socket.addEventListener("close", handler),
        onError: (handler) => socket.addEventListener("error", handler),
        send: (text) => socket.send(text),
        close: () => socket.close(),
      };
    },
  };
}
