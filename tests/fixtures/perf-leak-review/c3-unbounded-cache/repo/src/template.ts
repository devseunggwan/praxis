// Template rendering entry point.
import { lookup } from "./registry";

export function render(key: string, fallback: string): string {
  return lookup(key) ?? fallback;
}
