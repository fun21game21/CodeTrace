import { useEffect, useState } from "react";
import type {
  BindingSelector,
  VisualizerConfig,
  VisualizerKind,
} from "../types.ts";
import { validateConfig } from "../config.ts";
type Fields = Record<string, string>;
function fieldsFor(config: VisualizerConfig): Fields {
  const fields: Fields = {
    valueField: "value",
    leftField: "left",
    rightField: "right",
    mode: "cells",
  };
  if (config.kind === "none" || config.kind === "call_stack") return fields;
  for (const [key, value] of Object.entries(config)) {
    if (typeof value === "string" && key !== "kind") fields[key] = value;
    else if (
      value &&
      typeof value === "object" &&
      "scope" in value &&
      "name" in value
    ) {
      fields[key] = value.name;
      fields[key + "Scope"] = value.scope;
    }
  }
  if (config.kind === "array")
    config.indices.forEach((s, i) => {
      fields["index" + (i + 1)] = s.name;
      fields["index" + (i + 1) + "Scope"] = s.scope;
    });
  return fields;
}

export function VisualizerSettings({
  config,
  onApply,
}: {
  config: VisualizerConfig;
  onApply: (c: VisualizerConfig) => boolean;
}) {
  const [kind, setKind] = useState<VisualizerKind>(config.kind),
    [fields, setFields] = useState<Fields>(() => fieldsFor(config)),
    [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setKind(config.kind);
    setFields(fieldsFor(config));
    setError(null);
  }, [config]);
  const update = (name: string, value: string) => {
    setFields({ ...fields, [name]: value });
    setError(null);
  };
  const selector = (name: string): BindingSelector | undefined =>
    fields[name]
      ? {
          scope:
            fields[name + "Scope"] === "current_frame"
              ? "current_frame"
              : "globals",
          name: fields[name]!,
        }
      : undefined;
  const binding = (name: string, label: string, optional = false) => (
    <label className="viz-binding" key={name}>
      {label}
      {optional ? " (необязательно)" : ""}
      <span>
        <select
          aria-label={label + " scope"}
          value={fields[name + "Scope"] ?? "globals"}
          onChange={(e) => update(name + "Scope", e.target.value)}
        >
          <option value="globals">globals</option>
          <option value="current_frame">current_frame</option>
        </select>
        <input
          aria-label={label}
          value={fields[name] ?? ""}
          onChange={(e) => update(name, e.target.value)}
          placeholder="имя переменной"
          autoComplete="off"
        />
      </span>
    </label>
  );
  const apply = () => {
    let c: VisualizerConfig;
    if (kind === "none" || kind === "call_stack") c = { kind };
    else if (kind === "array")
      c = {
        kind,
        mode: fields["mode"] === "bars" ? "bars" : "cells",
        indices: [selector("index1"), selector("index2")].filter(
          (s): s is BindingSelector => !!s,
        ),
        ...(selector("array") ? { array: selector("array")! } : {}),
      };
    else if (kind === "tree")
      c = {
        kind,
        root: selector("root") ?? { scope: "globals", name: "" },
        valueField: fields["valueField"] ?? "value",
        leftField: fields["leftField"] ?? "left",
        rightField: fields["rightField"] ?? "right",
        ...(selector("current") ? { current: selector("current")! } : {}),
        ...(selector("visited") ? { visited: selector("visited")! } : {}),
      };
    else
      c = {
        kind,
        adjacency: selector("adjacency") ?? { scope: "globals", name: "" },
        ...(selector("current") ? { current: selector("current")! } : {}),
        ...(selector("visited") ? { visited: selector("visited")! } : {}),
        ...(selector("queue") ? { queue: selector("queue")! } : {}),
        ...(selector("stack") ? { stack: selector("stack")! } : {}),
      };
    if (!validateConfig(c) || !onApply(c))
      setError(
        "Проверьте имена переменных и поля. Используйте только имена, без expressions или paths.",
      );
  };
  return (
    <details className="viz-settings">
      <summary>Настройки визуализации</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          apply();
        }}
      >
        <label>
          Визуализатор
          <select
            aria-label="Визуализатор"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as VisualizerKind);
              setError(null);
            }}
          >
            <option value="none">Нет</option>
            <option value="array">Массив</option>
            <option value="tree">Дерево</option>
            <option value="graph">Граф</option>
            <option value="call_stack">Стек вызовов</option>
          </select>
        </label>
        {kind === "array" && (
          <>
            {binding("array", "Массив", true)}
            <button type="button" onClick={() => update("array", "")}>
              Auto
            </button>
            <label>
              Режим
              <select
                aria-label="Режим массива"
                value={fields["mode"] ?? "cells"}
                onChange={(e) => update("mode", e.target.value)}
              >
                <option value="cells">Ячейки</option>
                <option value="bars">Столбцы</option>
              </select>
            </label>
            {binding("index1", "Индекс 1", true)}
            {binding("index2", "Индекс 2", true)}
          </>
        )}
        {kind === "tree" && (
          <>
            {binding("root", "Root")}
            {["valueField", "leftField", "rightField"].map((name) => (
              <label key={name}>
                {name}
                <input
                  aria-label={name}
                  value={fields[name] ?? ""}
                  onChange={(e) => update(name, e.target.value)}
                />
              </label>
            ))}
          </>
        )}
        {kind === "graph" && binding("adjacency", "Adjacency")}
        {(kind === "tree" || kind === "graph") && (
          <>
            {binding("current", "Current", true)}
            {binding("visited", "Visited", true)}
          </>
        )}
        {kind === "graph" && (
          <>
            {binding("queue", "Queue", true)}
            {binding("stack", "Stack", true)}
          </>
        )}
        <button className="primary" type="submit">
          Применить
        </button>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </form>
    </details>
  );
}
