# Проверки CodeTrace

Выполните установку зависимостей из [installation.md](installation.md).
Unit tests не требуют Docker; реальные integration tests требуют собранный
Runner image и `backend/local-image.json` из documented build command.

## Frontend

Публичный набор: **1440 TypeScript/React tests**. Перед очисткой релиза полный
набор также содержал 11 проверок сохранённых результатов benchmarks; они удалены
вместе с привязкой к прежнему окружению. Тесты корректности приложения сохранены.

```sh
pnpm test
pnpm lint
pnpm typecheck
pnpm format:check
pnpm build
```

Публичный suite сохраняет проверки contracts, replay, Worker ownership,
Viewer, visualizers, History и examples. Проверки сохранённых отчётов прошлого
запуска не входят в suite. Их удаление не меняет runtime или correctness fixtures.

## Python и Docker

Всего **1905 Python tests**: 1807 unit/HTTP, 34 Docker Adapter, 21 watchdog,
19 recorder, 15 transport, 8 sealed trace и 1 scale test.

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q backend/integration
.venv/bin/python -m pytest -q backend/integration_watchdog
.venv/bin/python -m pytest -q backend/integration_recorder
.venv/bin/python -m pytest -q backend/integration_runs
.venv/bin/python -m pytest -q frontend/tests/trace/test_real_sealed.py
.venv/bin/python -m pytest -q backend/scale_recorder
```

Последняя команда включает ресурсную проверку большой трассы и может работать
дольше unit suite. Docker tests проверяют не только outcome, но и cleanup / slot reuse.

```sh
.venv/bin/python -m ruff check contracts/python contracts/tests/python runner backend
.venv/bin/python -m mypy
```

## Production и browser

После `pnpm build` и сборки образа:

```sh
.venv/bin/python frontend/scripts/examples_production_smoke.py
```

Этот smoke использует реальный FastAPI / Docker / Worker и проверяет семь примеров,
input, ошибки, timeout, release и History lifecycle. Его Node-порт IndexedDB
дополнен отдельными native browser checks.

Внешний browser automation runtime требуется только для этой проверки:

```sh
npm install --prefix .test-tools --no-save --no-package-lock playwright
npm exec --prefix .test-tools -- playwright install chromium
export CODETRACE_PLAYWRIGHT_MODULE="$PWD/.test-tools/node_modules/playwright/package.json"
```

`CODETRACE_PLAYWRIGHT_MODULE` указывает на установленный `playwright/package.json`. Приложение этой зависимости
не требует. Отдельный сценарий release recovery работает с установленным bundle:

```sh
node frontend/scripts/release_recovery_browser.mjs dist/local .test-results after
```

Он запускает bundle, использует отдельный профиль Chromium, срывает только trace GET,
проверяет Edit / History Open / release и следующий настоящий Run, удаляет собственные
History records и закрывает browser / backend. Порт 8000 должен быть свободен.

## Benchmarks

Source локального benchmark сохранён; результатов прежнего окружения в repository нет.

```sh
pnpm benchmark -- --round local-direct --mode direct --browser /path/to/chromium
pnpm benchmark -- --round local-worker --mode worker --browser /path/to/chromium
```

Используются одинаковые fixtures и seed, отдельные load / seek measurements,
raw samples и p95. Reports создаются в игнорируемом `frontend/benchmarks/results`.
Не интерпретируйте одно измерение как универсальную гарантию производительности.
