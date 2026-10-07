FROM python:3.13.16-slim-bookworm AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY contracts/python/ contracts/python/
COPY contracts/*.schema.json contracts/defaults.json contracts/
COPY runner/codetrace_runner/ runner/codetrace_runner/
COPY backend/codetrace_backend/ backend/codetrace_backend/
COPY runner/requirements-runtime.lock /runtime.lock
RUN python -m pip install --no-cache-dir setuptools==80.9.0 \
    && python -m pip wheel --no-cache-dir --wheel-dir /wheels -r /runtime.lock \
    && python -m pip wheel --no-deps --no-build-isolation --no-cache-dir --wheel-dir /wheels .

FROM python:3.13.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONSAFEPATH=1
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --no-cache-dir --find-links=/wheels codetrace-contracts==1.0.0 \
    && rm -rf /wheels \
    && python -c "from codetrace_runner.runner import runtime_supported; assert runtime_supported()"
WORKDIR /tmp
USER 65532:65532
ENTRYPOINT ["python", "-m", "codetrace_runner.runner"]
