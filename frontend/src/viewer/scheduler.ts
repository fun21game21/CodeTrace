import type { Scheduler } from "./types.ts";

export const browserScheduler: Scheduler = {
  schedule: (callback, delayMs) => setTimeout(callback, delayMs),
  cancel: (token) => clearTimeout(token as ReturnType<typeof setTimeout>),
};
