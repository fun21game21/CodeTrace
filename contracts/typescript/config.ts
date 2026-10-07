import defaults from "../defaults.json" with { type: "json" };
import type { ResourceLimits } from "./types.ts";
import { validateContract } from "./validators.ts";

export const SCHEMA_VERSION = "1.0";
export const API_VERSION = 1;
export const PROTOCOL_VERSION = 1;
export const PYTHON_VERSION = "3.13.16";
export const SOURCE_FILE_NAME = "<codetrace-user>";
export const BATCH_TARGET_BYTES = 65536;
export const DIAGNOSTIC_BYTES = 16384;
export const RECORDER_DISK_BYTES = 120 * 1024 * 1024;
export const CONTAINER_UID = 65532;
export const CONTAINER_GID = 65532;
export interface ConfigDefaults {
  host: "127.0.0.1";
  port: 8000;
  python_version: "3.13.16";
  image_ref: "codetrace-runner:1.0";
  limits: ResourceLimits;
}
export function defaultConfig(): ConfigDefaults {
  const value = structuredClone(defaults) as ConfigDefaults;
  validateContract(value.limits, "trace", "ResourceLimits");
  return value;
}
export function defaultLimits(): ResourceLimits {
  return defaultConfig().limits;
}
