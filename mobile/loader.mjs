// Lets Node load the desk core as it is written for the engine. Two rules:
//   'claude-code'          resolves to engine.mjs (the state library for a Node process)
//   './pure', '../health'  a relative import with no extension resolves to the .ts file next to it
// Node strips the types itself (Node 23.6 or newer). Import this file before the core.
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ENGINE = pathToFileURL(
  path.join(path.dirname(fileURLToPath(import.meta.url)), "engine.mjs"),
).href;

registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "claude-code") {
      return { url: ENGINE, shortCircuit: true };
    }
    const parent = context.parentURL ?? "";
    if (
      specifier.startsWith(".") &&
      parent.startsWith("file:") &&
      path.extname(specifier) === ""
    ) {
      const file = path.resolve(
        path.dirname(fileURLToPath(parent)),
        `${specifier}.ts`,
      );
      if (existsSync(file)) {
        return { url: pathToFileURL(file).href, shortCircuit: true };
      }
    }
    return next(specifier, context);
  },
});
