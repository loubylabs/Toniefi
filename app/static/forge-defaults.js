export function createForgeDefaultsCoordinator({ request, onSaveError = () => {} }) {
  let confirmed = null;
  let readPromise = null;
  let writeTail = Promise.resolve();
  let uncertain = false;

  const copy = (options) => ({ ...options });

  async function load() {
    const observedTail = writeTail;
    await observedTail;
    if (observedTail !== writeTail) return load();
    if (confirmed && !uncertain) return copy(confirmed);
    if (!readPromise) {
      readPromise = request("/api/settings/forge-defaults")
        .then((options) => {
          confirmed = copy(options);
          uncertain = false;
          return copy(confirmed);
        })
        .finally(() => { readPromise = null; });
    }
    return readPromise.then(copy);
  }

  function save(options) {
    const selected = copy(options);
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
      onSaveError(error);
      return null;
    });
    return writeTail;
  }

  return { load, save };
}
