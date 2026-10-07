export class TraceLoadError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "TraceLoadError";
  }
}
export class TraceIntegrityError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TraceIntegrityError";
  }
}
export class StepOutOfRangeError extends RangeError {
  constructor(step: number, total: number) {
    super(`Step ${step} must be an integer in 1..${total}`);
    this.name = "StepOutOfRangeError";
  }
}
