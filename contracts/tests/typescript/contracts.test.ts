import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";
import { Ajv2020 } from "ajv/dist/2020.js";
import {
  defaultConfig,
  defaultLimits,
  ContractValidationError,
  validateContract,
  validateTrace,
  validateWs,
  validateRunner,
} from "../../typescript/index.ts";
import type { Contract } from "../../typescript/index.ts";

const fixtures = new URL("../../fixtures/", import.meta.url);
interface Case {
  file: string;
  contract: Contract;
  definition: string | null;
  valid: boolean;
}
const manifest = JSON.parse(
  readFileSync(new URL("manifest.json", fixtures), "utf8"),
) as Case[];
for (const entry of manifest) {
  test(`shared fixture: ${entry.file}`, () => {
    const value: unknown = JSON.parse(
      readFileSync(new URL(entry.file, fixtures), "utf8"),
    );
    const original = structuredClone(value);
    if (entry.valid)
      validateContract(value, entry.contract, entry.definition ?? undefined);
    else
      assert.throws(
        () =>
          validateContract(
            value,
            entry.contract,
            entry.definition ?? undefined,
          ),
        ContractValidationError,
      );
    assert.deepEqual(value, original);
  });
}
for (const name of ["trace", "ws", "runner"]) {
  test(`Draft 2020-12 schema: ${name}`, () => {
    const schema = JSON.parse(
      readFileSync(
        new URL(`../../${name}-v1.schema.json`, import.meta.url),
        "utf8",
      ),
    );
    const ajv = new Ajv2020();
    assert.equal(ajv.validateSchema(schema), true);
  });
}
test("manifest lists every fixture exactly once", () => {
  const actual = ["valid", "invalid"].flatMap((group) =>
    readdirSync(new URL(`${group}/`, fixtures))
      .filter((name) => name.endsWith(".json"))
      .map((name) => `${group}/${name}`),
  );
  assert.deepEqual(manifest.map((e) => e.file).sort(), actual.sort());
  assert.equal(new Set(manifest.map((e) => e.file)).size, manifest.length);
  assert.ok(manifest.every((e) => e.valid === e.file.startsWith("valid/")));
});
const scalarCases: [string, unknown, boolean][] = [
  ["bool", true, true],
  ["bool", false, true],
  ["bool", 1, false],
  ["bool", "true", false],
  ["int", "9007199254740993", true],
  ["int", "-12345678901234567890", true],
  ["int", true, false],
  ["int", 1, false],
  ["int", "01", false],
  ["int", "+1", false],
  ["float", "nan", true],
  ["float", "inf", true],
  ["float", "-inf", true],
  ["float", "-0.0", true],
  ["float", "1e+20", true],
  ["float", "Infinity", false],
  ["float", "NaN", false],
  ["float", 1.0, false],
  ["float", "1e+309", false],
  ["none", null, true],
  ["none", "null", false],
];
scalarCases.forEach(([type, value, accepted], index) => {
  test(`scalar without coercion ${index}: ${type}`, () => {
    const scalar = { kind: "scalar", type, value };
    if (accepted) validateContract(scalar, "trace", "Value");
    else
      assert.throws(
        () => validateContract(scalar, "trace", "Value"),
        ContractValidationError,
      );
  });
});
[null, true, 1, "1", { $ref: "o1", value: null }].forEach((value, i) => {
  test(`None and Ref require exact encoded variant ${i}`, () => {
    assert.throws(
      () => validateContract(value, "trace", "Value"),
      ContractValidationError,
    );
  });
});
for (const [definition, prefix] of [
  ["ObjectId", "o"],
  ["FrameId", "f"],
]) {
  test(`identity pattern ${definition}`, () => {
    for (const suffix of ["1", "2", "12345"])
      validateContract(prefix + suffix, "trace", definition);
    for (const suffix of ["0", "01", "-1", "1\n"]) {
      assert.throws(
        () => validateContract(prefix + suffix, "trace", definition),
        ContractValidationError,
      );
    }
  });
}
test("UUID is not an ObjectId", () => {
  validateContract("8ba106dd-35b6-4945-a5db-e1d3120c743d", "trace", "RunId");
  assert.throws(
    () => validateContract("o1", "trace", "RunId"),
    ContractValidationError,
  );
});
test("defaults equal normative table and callers own their copy", () => {
  const expected = {
    active_runs: 1,
    retained_results: 1,
    code_bytes: 65536,
    max_events: 100000,
    trace_bytes: 52428800,
    terminal_reserve_bytes: 2097152,
    registry_objects: 10000,
    state_objects: 2000,
    state_bytes: 1048576,
    collection_items: 1000,
    instance_attributes: 1000,
    string_bytes: 16384,
    integer_decimal_digits: 1000,
    active_frames: 128,
    console_bytes: 65536,
    output_chunk_bytes: 4096,
    input_value_bytes: 8192,
    input_requests: 32,
    checkpoint_interval: 128,
    active_timeout_ms: 10000,
    input_idle_timeout_ms: 120000,
    container_lifetime_ms: 300000,
    startup_timeout_ms: 15000,
    result_ttl_ms: 300000,
    cleanup_command_timeout_ms: 3000,
    cleanup_attempts: 2,
    finalization_timeout_ms: 120000,
    watchdog_tick_ms: 50,
    memory_bytes: 536870912,
    memory_plus_swap_bytes: 536870912,
    cpus: 1.0,
    pids_limit: 16,
    tmpfs_bytes: 16777216,
    runner_message_bytes: 2097152,
    ws_message_bytes: 524288,
    native_stderr_bytes: 16384,
    history_entries: 100,
    max_visual_nodes: 200,
    visible_array_items: 200,
  };
  assert.deepEqual(defaultLimits(), expected);
  const config = defaultConfig();
  assert.deepEqual(
    [config.host, config.port, config.python_version, config.image_ref],
    ["127.0.0.1", 8000, "3.13.16", "codetrace-runner:1.0"],
  );
  const schema = JSON.parse(
    readFileSync(
      new URL("../../trace-v1.schema.json", import.meta.url),
      "utf8",
    ),
  ) as {
    $defs: {
      ResourceLimits: { properties: Record<string, { default: number }> };
    };
  };
  assert.deepEqual(
    Object.fromEntries(
      Object.entries(schema.$defs.ResourceLimits.properties).map(
        ([key, data]) => [key, data.default],
      ),
    ),
    expected,
  );
  config.limits.max_events = 1;
  assert.equal(defaultLimits().max_events, 100000);
});
for (const [filename, validate] of [
  ["trace-minimal-success.json", validateTrace],
  ["ws-start.json", validateWs],
  ["runner-start.json", validateRunner],
] as const) {
  test(`typed entry point ${filename}`, () => {
    const value: unknown = JSON.parse(
      readFileSync(new URL(`valid/${filename}`, fixtures), "utf8"),
    );
    assert.equal(validate(value), value);
  });
}
[
  NaN,
  Infinity,
  -Infinity,
  undefined,
  1n,
  new Date(),
  { [Symbol("key")]: 1 },
].forEach((value, i) => {
  test(`reject non-JSON JS values ${i}`, () => {
    assert.throws(
      () => validateContract(value, "trace", "Value"),
      ContractValidationError,
    );
  });
});
test("live reference cycles are not wire cycles", () => {
  const cycle: unknown[] = [];
  cycle.push(cycle);
  assert.throws(
    () => validateContract(cycle, "trace", "Value"),
    ContractValidationError,
  );
});
test("unknown definition names are not ignored", () => {
  assert.throws(
    () => validateContract({}, "trace", "InventedContract"),
    /Unknown contract definition/,
  );
});
