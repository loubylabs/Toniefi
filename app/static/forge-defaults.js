export function createForgeDefaultsCoordinator({ request, onSaveError = () => {} }) {
  let confirmed = null;
  let readPromise = null;
  let writeTail = Promise.resolve();
  let writeGeneration = 0;
  let uncertain = false;

  const copy = (options) => ({ ...options });

  async function load() {
    const observedTail = writeTail;
    await observedTail;
    if (observedTail !== writeTail) return load();
    if (confirmed && !uncertain) return copy(confirmed);
    if (!readPromise) {
      const observedGeneration = writeGeneration;
      readPromise = request("/api/settings/forge-defaults")
        .then((options) => ({ options: copy(options), generation: observedGeneration }))
        .finally(() => { readPromise = null; });
    }
    const read = await readPromise;
    if (read.generation !== writeGeneration) return load();
    confirmed = copy(read.options);
    uncertain = false;
    return copy(confirmed);
  }

  function save(options) {
    const selected = copy(options);
    writeGeneration += 1;
    const write = writeTail.then(async () => {
      const saved = await request("/api/settings/forge-defaults", {
        method: "PUT",
        body: JSON.stringify(selected),
        keepalive: true,
      });
      confirmed = copy(saved || selected);
      uncertain = false;
      return copy(confirmed);
    });
    writeTail = write.catch((error) => {
      uncertain = true;
      try {
        onSaveError(error);
      } catch {}
      return null;
    });
    return writeTail;
  }

  return { load, save };
}
