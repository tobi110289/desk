// The engine's state library for a Node process. The desk core (mod/hooks/core/desk.ts) imports atom, read and
// update from 'claude-code'; in the terminal the engine provides them, on the phone server this file does
// (loader.mjs maps the name to this file). The state lives on the `$` the caller passes: $.state is a Map,
// $.changed() runs after every write.
export function atom(ref, initial) {
  return { ref, initial };
}

export async function read($, source) {
  const key = source.ref.key;
  return $.state.has(key) ? $.state.get(key) : source.initial;
}

export async function update($, source, change) {
  const key = source.ref.key;
  const before = $.state.has(key) ? $.state.get(key) : source.initial;
  const next = change(before);
  if (next !== before) {
    $.state.set(key, next);
    $.changed();
  }
}
