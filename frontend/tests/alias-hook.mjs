import { existsSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve as resolvePath } from "node:path";

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));

/** Resolve the `@/*` alias from tsconfig so tests can import app modules as-is. */
export function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("@/")) {
    const target = resolvePath(projectRoot, specifier.slice(2));
    // tsconfig's bundler resolution allows extensionless imports; Node does not.
    const candidates = existsSync(target) ? [target] : [`${target}.ts`, `${target}.tsx`];
    const found = candidates.find((candidate) => existsSync(candidate)) ?? target;
    return nextResolve(pathToFileURL(found).href, context);
  }
  return nextResolve(specifier, context);
}
