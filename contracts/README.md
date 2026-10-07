# CodeTrace contracts

Общие wire-контракты backend, Runner и frontend. Формат JSON задают schemas;
Python и TypeScript DTOs поддерживаются вручную и проверяются общими fixtures.

- `trace-v1.schema.json` — values, objects, frames, states, events и TraceDocument.
- `ws-v1.schema.json` — Browser ↔ Backend lifecycle и input.
- `runner-v1.schema.json` — NDJSON Backend ↔ Runner.
- `defaults.json` — начальные resource limits.
- `fixtures/valid`, `fixtures/invalid` — общие проверки валидаторов.
- `python`, `typescript` — types, validators и загрузка defaults.

ObjectId / FrameId уникальны внутри Run. Int и float передаются строками;
Python None — tagged scalar, отличный от nullable field. Schema validation
дополняется проверкой последовательности records / events, ссылок и лимитов.

[Формат trace](../docs/trace-format.md) ·
[Архитектура](../Architecture_Specification_v1.0.md)
