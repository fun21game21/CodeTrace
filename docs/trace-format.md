# TraceDocument v1: обзор для разработчика

Это пояснение существующего формата, не новая specification. Источники истины:
[JSON schema](../contracts/trace-v1.schema.json),
[общие fixtures](../contracts/fixtures), [TS contracts](../contracts/typescript),
[Architecture Specification](../Architecture_Specification_v1.0.md).

TraceDocument содержит `schemaVersion: "1.0"`, backend UUID `runId`, точный
`sourceCode`, `sourceFileName: "<codetrace-user>"`, `pythonVersion: "3.13.16"`,
`checkpointInterval`, effective `limits`, `events`, `states`, `console`, `completion`.
Trace передаётся одним sealed HTTP JSON после выполнения и подтверждённого cleanup.

Event содержит `index`, `kind`, `stateVersion`, `frameId`, `source`, `consoleOffset`,
`payload`. **Step = event.index + 1**. State version — другой счётчик: no-change
события сохраняются, но могут ссылаться на одну версию. LINE фиксируется перед
строкой; одна строка цикла порождает много событий. CALL включает входящий frame,
RETURN ещё содержит выходящий; EXCEPTION записывает наблюдение, не назначает итог.
Известное non-None return value находится в payload и достижимо через `eventRoots`.
Raw None означает `returnValueKnown: false`, а не доказанный возврат None.

State содержит version, globals, frames, stack, objects, eventRoots. Checkpoint 0
обязательно пустой. `states[v].version = v`: каждая версия представлена ровно одним
checkpoint или delta. Default checkpoint interval — 128 **изменений состояния**.
Delta ссылается на `baseVersion = version - 1`, заменяет globals/stack/eventRoots
либо оставляет их через null и содержит полные frames/objects upserts и remove IDs.
Пустой массив и null имеют разные значения. Delta применяется атомарно; после
неё все Ref и stack links обязаны разрешаться. Reverse deltas отсутствуют.

Value — typed scalar (None/bool/int/float/str) либо `{"$ref":"o1"}`. Int/float
хранятся строками, поэтому JS Number не теряет точность при отображении. ObjectId
монотонно назначается внутри Run, сохраняет aliasing и не переиспользуется.
Object table включает только достижимый граф от globals, активных locals и
`eventRoots`; registry может удерживать больше объектов. Cycles разрешены.
Object kinds: list, tuple, dict, set, deque, instance, opaque. FrameId новый при
каждом входе, включая рекурсию. Завершённые frames отсутствуют в следующем state.

ConsoleEntry содержит `index`, `kind: stdout | stderr | input`, `text`, `requestId`.
`consoleOffset` — число записей в видимом префиксе, не bytes и не строки.
Step Console показывает ровно `console[0:consoleOffset]`. Prompt — stdout без
добавленного newline; принятый ввод — отдельный input entry с newline. Никакой
вывод не копируется в каждый snapshot; переход назад скрывает будущие entries.

Последний Event всегда **RUN_END**, единственный, добавленный backend. Он содержит
полный Console prefix и ссылается на final либо last observed state. Его payload
пустой; итог хранится в Completion: outcome, reason, traceComplete, stateAccuracy,
primaryDiagnostic, acceptedAt/finishedAt, activeElapsedMs/waitingElapsedMs,
exitCode, cleanupConfirmed. `traceComplete` описывает полноту предусмотренных
наблюдений; `stateAccuracy` — final / last_observed / unavailable. USER_ERROR
может иметь полную трассу. Принудительный kill не имеет `traceComplete: true`.

Один trace Worker выполняет GET/parse/validation, хранит Trace Core и восстанавливает
Step от ближайшего checkpoint с последующими deltas. Current и predecessor Event
states дают единый immutable SelectedStepView; main получает только этот DTO и
metadata. Все панели публикуются вместе. После успешной загрузки backend получает
release; дальнейшая навигация не делает HTTP и не запускает Python.

`arrayPositionChanges` — **не часть TraceDocument**, NormalizedState или сетевых
протоколов. Это frontend-internal поле SelectedStepView, вычисляемое
Trace Core относительно predecessor Event, включая nested same-Ref mutations. Visualizer
читает готовый diff; config и геометрия не передаются Runner и не сохраняются в trace.
