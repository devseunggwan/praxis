// Resolved-template registry.
//
// The map lives for the lifetime of the module: entries put here are reachable
// until the process exits.
const RESOLVED = new Map<string, string>();

export function lookup(key: string): string | undefined {
  return RESOLVED.get(key);
}

export function size(): number {
  return RESOLVED.size;
}
