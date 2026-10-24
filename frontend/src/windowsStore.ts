// The Windows plugin store (Overdrive, Control Center): a static list on GitHub Pages in the same format as the
// official store, shown next to it. Pure helpers, so they can be tested without the rest of the loader.

export const WINDOWS_STORE_URL = 'https://musika08.github.io/decky-windows-store/plugins';

type Named = { name: string };
type VersionMap = { [name: string]: unknown[] };

function asList<T extends Named>(value: unknown): T[] {
  return Array.isArray(value) ? (value.filter((p) => p && typeof p.name === 'string') as T[]) : [];
}

function asMap(value: unknown): VersionMap {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as VersionMap) : {};
}

// Official plugins first, then ours; on a name clash ours wins (it is the Windows build of that plugin).
// Either list may be missing (offline, an error reply): the other is still shown.
export function mergePluginLists<T extends Named>(official: unknown, windows: unknown): T[] {
  const ours = asList<T>(windows);
  const ourNames = new Set(ours.map((p) => p.name));
  return [...asList<T>(official).filter((p) => !ourNames.has(p.name)), ...ours];
}

// The update check's {name: [version, hash, artifact?]} maps; ours wins a name clash.
export function mergeVersionLists(official: unknown, windows: unknown): VersionMap {
  return { ...asMap(official), ...asMap(windows) };
}
