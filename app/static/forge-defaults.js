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
    let read = readPromise;
    if (!read || read.generation !== writeGeneration) {
      read = { generation: writeGeneration, promise: null };
      read.promise = request("/api/settings/forge-defaults")
        .then(
          (options) => ({ options: copy(options) }),
          (error) => ({ error }),
        )
        .finally(() => {
          if (readPromise === read) readPromise = null;
        });
      readPromise = read;
    }
    const result = await read.promise;
    if (read.generation !== writeGeneration) return load();
    if (Object.hasOwn(result, "error")) throw result.error;
    confirmed = copy(result.options);
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
