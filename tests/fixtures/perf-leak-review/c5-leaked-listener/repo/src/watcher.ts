// Config file watcher.
import { EventEmitter } from "events";

export class ConfigWatcher {
  private readonly emitter: EventEmitter;

  constructor(emitter: EventEmitter) {
    this.emitter = emitter;
  }

  dispose(): void {
    // Drops the watcher's own state. Registrations made on the shared emitter
    // outlive this call unless they are removed here.
    this.current = undefined;
  }

  private current?: string;

  protected apply(path: string): void {
    this.current = path;
  }
}
